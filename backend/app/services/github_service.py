"""GitHub code fetching service.

Supports three source modes for the verification pipeline:
  - exact_files: fetch specific files by GitHub blob URL
  - full_repo:   fetch all text files in a repository (capped at 100)
  - pull_request: fetch PR diff/patch for changed files

All public functions raise GitHubServiceError on failure.
GitHub API 429 responses are retried with exponential backoff (3 retries).
"""

import asyncio
import base64
import re
from pathlib import PurePosixPath

import httpx

from app.schemas.verification import FetchedFile

GITHUB_API_BASE = "https://api.github.com"

# File size cap for full-repo fetches (512 KB)
_MAX_FILE_SIZE_BYTES = 524_288

# Maximum number of files fetched in full-repo mode
_MAX_REPO_FILES = 100

# Extensions that are not useful for code verification
_BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
    ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".pdf", ".zip", ".gz", ".tar", ".bz2",
    ".bin", ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyo", ".class",
    ".lock",
}


class GitHubServiceError(Exception):
    """Raised when a GitHub API interaction fails."""

    def __init__(self, message: str, code: str = "GITHUB_FETCH_FAILED") -> None:
        self.message = message
        self.code = code
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _auth_headers(pat: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


async def _github_get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
) -> httpx.Response:
    """Perform a GET request with exponential-backoff retry on 429."""
    for attempt in range(4):  # attempts 0, 1, 2, 3
        response = await client.get(url, headers=headers, timeout=30.0)
        if response.status_code != 429:
            return response
        if attempt < 3:
            await asyncio.sleep(2 ** attempt)  # 1 s, 2 s, 4 s

    raise GitHubServiceError(
        "GitHub API rate limit exceeded after retries. Please try again later."
    )


def _decode_base64_content(encoded: str) -> str:
    """Decode GitHub's base64-encoded file content to a UTF-8 string."""
    # GitHub inserts newlines in the base64 string — strip them first
    clean = encoded.replace("\n", "").replace(" ", "")
    try:
        return base64.b64decode(clean).decode("utf-8", errors="replace")
    except Exception as exc:
        raise GitHubServiceError(f"Failed to decode file content: {exc}") from exc


def _parse_github_blob_url(url: str) -> tuple[str, str, str, str]:
    """Parse a GitHub blob URL into (owner, repo, ref, path).

    Accepted formats:
      https://github.com/org/repo/blob/main/src/auth.py

    Known limitation: the ref segment is captured as a single path component
    (no slashes). Branch names containing slashes (e.g. feature/my-branch)
    are not supported — only the first component is captured as ref, and the
    remainder becomes part of the file path. Such URLs will produce a 404 from
    the GitHub API. MVP scope only supports simple branch/tag/SHA refs.
    """
    pattern = r"https?://github\.com/([^/]+)/([^/]+)/blob/([^/]+)/(.+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise GitHubServiceError(
            f"Invalid GitHub blob URL: '{url}'. "
            "Expected format: https://github.com/owner/repo/blob/ref/path/to/file"
        )
    owner, repo, ref, path = match.groups()
    return owner, repo, ref, path


def _parse_repo_url(url: str) -> tuple[str, str]:
    """Parse a GitHub repo URL into (owner, repo).

    Accepted formats:
      https://github.com/org/repo
      https://github.com/org/repo/
    """
    pattern = r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$"
    match = re.match(pattern, url.strip())
    if not match:
        raise GitHubServiceError(
            f"Invalid GitHub repository URL: '{url}'. "
            "Expected format: https://github.com/owner/repo"
        )
    return match.group(1), match.group(2)


def _parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, pr_number).

    Accepted format:
      https://github.com/org/repo/pull/42
    """
    pattern = r"https?://github\.com/([^/]+)/([^/]+)/pull/(\d+)"
    match = re.match(pattern, url.strip())
    if not match:
        raise GitHubServiceError(
            f"Invalid GitHub PR URL: '{url}'. "
            "Expected format: https://github.com/owner/repo/pull/42"
        )
    owner, repo, pr_num = match.groups()
    return owner, repo, int(pr_num)


def _is_binary_path(path: str) -> bool:
    suffix = PurePosixPath(path).suffix.lower()
    return suffix in _BINARY_EXTENSIONS


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

async def fetch_exact_files(paths_input: str, pat: str) -> list[FetchedFile]:
    """Fetch specific files from GitHub by blob URL.

    Args:
        paths_input: Newline-separated GitHub blob URLs.
        pat:         GitHub personal access token.

    Returns:
        List of FetchedFile with decoded content.

    Raises:
        GitHubServiceError: On any fetch failure.
    """
    if not pat:
        raise GitHubServiceError("GitHub PAT not configured.")

    lines = [line.strip() for line in paths_input.splitlines() if line.strip()]
    if not lines:
        raise GitHubServiceError("No file URLs provided.")

    fetched: list[FetchedFile] = []

    async with httpx.AsyncClient() as client:
        headers = _auth_headers(pat)
        for url in lines:
            owner, repo, ref, path = _parse_github_blob_url(url)
            api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
            response = await _github_get(client, api_url, headers)

            if response.status_code == 404:
                raise GitHubServiceError(
                    f"File not found: '{path}' in {owner}/{repo}@{ref}"
                )
            if response.status_code == 401:
                raise GitHubServiceError(
                    "GitHub authentication failed. Check your PAT."
                )
            if not response.is_success:
                raise GitHubServiceError(
                    f"GitHub API returned {response.status_code} for '{path}'."
                )

            data = response.json()
            encoding = data.get("encoding", "base64")
            if encoding != "base64":
                raise GitHubServiceError(
                    f"Unexpected encoding '{encoding}' for '{path}'. Only base64 is supported."
                )

            content = _decode_base64_content(data["content"])
            github_url = f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"
            fetched.append(FetchedFile(path=path, content=content, github_url=github_url))

    return fetched


async def fetch_full_repo(repo_url: str, pat: str) -> list[FetchedFile]:
    """Fetch all qualifying text files from a GitHub repository.

    Files larger than 512 KB and binary extensions are skipped.
    Capped at 100 files.

    Args:
        repo_url: Full GitHub repository URL.
        pat:      GitHub personal access token.

    Returns:
        List of FetchedFile with decoded content.

    Raises:
        GitHubServiceError: On any fetch failure.
    """
    if not pat:
        raise GitHubServiceError("GitHub PAT not configured.")

    owner, repo = _parse_repo_url(repo_url)

    async with httpx.AsyncClient() as client:
        headers = _auth_headers(pat)

        # Fetch repo metadata to get the default branch name for link construction
        repo_info_resp = await _github_get(
            client, f"{GITHUB_API_BASE}/repos/{owner}/{repo}", headers
        )
        default_branch = "main"
        if repo_info_resp.is_success:
            default_branch = repo_info_resp.json().get("default_branch", "main")

        # Step 1: fetch the recursive tree
        tree_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/trees/HEAD?recursive=1"
        tree_resp = await _github_get(client, tree_url, headers)

        if tree_resp.status_code == 404:
            raise GitHubServiceError(f"Repository not found: {owner}/{repo}")
        if tree_resp.status_code == 401:
            raise GitHubServiceError("GitHub authentication failed. Check your PAT.")
        if not tree_resp.is_success:
            raise GitHubServiceError(
                f"GitHub API returned {tree_resp.status_code} fetching repo tree."
            )

        tree_data = tree_resp.json()
        is_truncated = tree_data.get("truncated", False)
        all_items = tree_data.get("tree", [])

        # Filter: blobs only, no binary, within size limit
        qualifying = [
            item
            for item in all_items
            if (
                item.get("type") == "blob"
                and not _is_binary_path(item.get("path", ""))
                and item.get("size", 0) <= _MAX_FILE_SIZE_BYTES
            )
        ][:_MAX_REPO_FILES]

        if not qualifying:
            raise GitHubServiceError(
                f"No qualifying text files found in {owner}/{repo}."
            )

        # Step 2: fetch content for each qualifying file
        fetched: list[FetchedFile] = []
        for item in qualifying:
            path = item["path"]
            contents_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}"
            file_resp = await _github_get(client, contents_url, headers)

            if not file_resp.is_success:
                # Skip files that can't be fetched rather than aborting entirely
                continue

            data = file_resp.json()
            if data.get("encoding") != "base64" or "content" not in data:
                continue

            content = _decode_base64_content(data["content"])
            github_url = f"https://github.com/{owner}/{repo}/blob/{default_branch}/{path}"
            fetched.append(FetchedFile(path=path, content=content, github_url=github_url))

        # Surface truncation warning in the file list so callers know the
        # result is incomplete. Story 2.3 should filter entries whose path
        # starts with "[WARNING]" before passing them to the LLM.
        if is_truncated:
            fetched.insert(
                0,
                FetchedFile(
                    path="[WARNING] GitHub repository tree was truncated — file list is incomplete",
                    content=(
                        "The GitHub API truncated the repository tree response. "
                        "Not all files were fetched. Consider using Exact File Paths "
                        "mode for large repositories."
                    ),
                ),
            )

    return fetched


async def fetch_pull_request(pr_url: str, pat: str) -> list[FetchedFile]:
    """Fetch changed files and their diff/patch from a GitHub pull request.

    Removed files and binary files (no patch) are skipped.

    Args:
        pr_url: Full GitHub PR URL.
        pat:    GitHub personal access token.

    Returns:
        List of FetchedFile where content is the unified diff patch.

    Raises:
        GitHubServiceError: On any fetch failure.
    """
    if not pat:
        raise GitHubServiceError("GitHub PAT not configured.")

    owner, repo, pr_number = _parse_pr_url(pr_url)

    async with httpx.AsyncClient() as client:
        headers = _auth_headers(pat)
        # per_page=100 is the GitHub maximum for this endpoint.
        # PRs with more than 100 changed files are silently truncated — no
        # pagination is performed (MVP scope). Surface a warning if needed.
        api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files?per_page=100"
        response = await _github_get(client, api_url, headers)

        if response.status_code == 404:
            raise GitHubServiceError(
                f"Pull request #{pr_number} not found in {owner}/{repo}"
            )
        if response.status_code == 401:
            raise GitHubServiceError("GitHub authentication failed. Check your PAT.")
        if not response.is_success:
            raise GitHubServiceError(
                f"GitHub API returned {response.status_code} fetching PR #{pr_number}."
            )

        files = response.json()
        fetched: list[FetchedFile] = []

        for file_entry in files:
            # Skip removed files — nothing to verify against
            if file_entry.get("status") == "removed":
                continue

            patch = file_entry.get("patch")
            # Skip binary files (no patch)
            if patch is None:
                continue

            path = file_entry["filename"]
            github_url = f"https://github.com/{owner}/{repo}/blob/HEAD/{path}"
            fetched.append(FetchedFile(path=path, content=patch, github_url=github_url))

    if not fetched:
        raise GitHubServiceError(
            "No verifiable files found in the pull request "
            "(all files were removed or binary)."
        )

    return fetched


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

async def fetch_github_code(
    mode: str,
    github_input: str,
    pat: str,
) -> list[FetchedFile]:
    """Dispatch to the appropriate fetch strategy based on mode.

    Args:
        mode:         One of "exact_files", "full_repo", "pull_request".
        github_input: User-supplied input (URLs or paths).
        pat:          GitHub personal access token.

    Returns:
        List of FetchedFile.

    Raises:
        GitHubServiceError: On any failure.
    """
    if mode == "exact_files":
        return await fetch_exact_files(github_input, pat)
    elif mode == "full_repo":
        return await fetch_full_repo(github_input, pat)
    elif mode == "pull_request":
        return await fetch_pull_request(github_input, pat)
    else:
        raise GitHubServiceError(
            f"Unknown verification mode: '{mode}'.",
            code="GITHUB_FETCH_FAILED",
        )
