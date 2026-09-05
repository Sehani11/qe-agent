"""GitHub code fetching service.

Fetches the code evidence for agentic verification:
  - exact_files: fetch specific files by GitHub blob URL (slash-containing
    branch names are resolved via the matching-refs API)
  - pull_request: fetch PR diff/patch for changed files (paginated up to
    GitHub's 3 000-file listing limit), plus the PR head SHA for ref pinning

full_repo mode needs no prefetch — the agent explores the repository through
its GitHub tools.

All public functions raise GitHubServiceError on failure.
GitHub API 429 responses are retried with exponential backoff (3 retries).
"""

import asyncio
import base64
import re
from urllib.parse import quote

import httpx

from app.schemas.verification import FetchedFile

GITHUB_API_BASE = "https://api.github.com"

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

    The ref segment is captured as a single path component. Branch names
    containing slashes (e.g. feature/my-branch) therefore mis-split here;
    fetch_exact_files_resolved recovers by resolving the real branch via the
    matching-refs API when the first-segment fetch 404s. Slashed TAG names
    remain unsupported (matching-refs is queried for heads only).
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


async def _resolve_slashed_ref(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    owner: str,
    repo: str,
    ref_and_path: str,
) -> tuple[str, str] | None:
    """Resolve a slash-containing branch name inside a blob URL.

    ``ref_and_path`` is everything after ``/blob/`` (e.g.
    ``feature/my-branch/src/auth.py``). Asks GitHub for every branch starting
    with the first segment and picks the longest one that prefixes the string;
    the remainder is the file path. Returns None when no multi-segment branch
    matches (plain ref, tag, or SHA — the caller's original split stands).
    """
    first_segment = ref_and_path.split("/", 1)[0]
    url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/git/matching-refs/heads/{quote(first_segment, safe='')}"
    resp = await _github_get(client, url, headers)
    if not resp.is_success:
        return None

    branches = [
        item["ref"].removeprefix("refs/heads/")
        for item in resp.json()
        if isinstance(item, dict) and item.get("ref", "").startswith("refs/heads/")
    ]
    # Longest match wins: with branches "feature" and "feature/my-branch" both
    # present, the URL's own segmentation is only recoverable greedily.
    best = max(
        (b for b in branches if "/" in b and ref_and_path.startswith(b + "/")),
        key=len,
        default=None,
    )
    if best is None:
        return None
    return best, ref_and_path[len(best) + 1 :]


# ---------------------------------------------------------------------------
# Public fetch functions
# ---------------------------------------------------------------------------

async def fetch_exact_files_resolved(
    paths_input: str, pat: str
) -> tuple[list[FetchedFile], str]:
    """Fetch specific files by blob URL; also return the first URL's resolved ref.

    The resolved ref is what agentic verification pins its GitHub tools to, so
    it must reflect slash-ref resolution — the raw URL split may be wrong.

    Args:
        paths_input: Newline-separated GitHub blob URLs.
        pat:         GitHub personal access token.

    Returns:
        (files, first_ref): decoded files and the resolved ref of the first URL.

    Raises:
        GitHubServiceError: On any fetch failure.
    """
    if not pat:
        raise GitHubServiceError("GitHub PAT not configured.")

    lines = [line.strip() for line in paths_input.splitlines() if line.strip()]
    if not lines:
        raise GitHubServiceError("No file URLs provided.")

    fetched: list[FetchedFile] = []
    first_ref: str | None = None

    async with httpx.AsyncClient() as client:
        headers = _auth_headers(pat)
        for url in lines:
            owner, repo, ref, path = _parse_github_blob_url(url)
            api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={ref}"
            response = await _github_get(client, api_url, headers)

            if response.status_code == 404:
                # The single-segment ref split may have cut a slash-containing
                # branch name in half. Resolve the real branch and retry once.
                resolved = await _resolve_slashed_ref(
                    client, headers, owner, repo, f"{ref}/{path}"
                )
                if resolved is not None:
                    ref, path = resolved
                    api_url = f"{GITHUB_API_BASE}/repos/{owner}/{repo}/contents/{path}?ref={quote(ref, safe='')}"
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

            if first_ref is None:
                first_ref = ref
            content = _decode_base64_content(data["content"])
            github_url = f"https://github.com/{owner}/{repo}/blob/{ref}/{path}"
            fetched.append(FetchedFile(path=path, content=content, github_url=github_url))

    # lines is non-empty, so at least one iteration set first_ref
    return fetched, first_ref or "HEAD"


async def _get_pr_head_sha(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    owner: str,
    repo: str,
    pr_number: int,
) -> str:
    """Return the head commit SHA of a pull request."""
    resp = await _github_get(
        client, f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}", headers
    )
    if resp.status_code == 404:
        raise GitHubServiceError(
            f"Pull request #{pr_number} not found in {owner}/{repo}"
        )
    if resp.status_code == 401:
        raise GitHubServiceError("GitHub authentication failed. Check your PAT.")
    if not resp.is_success:
        raise GitHubServiceError(
            f"GitHub API returned {resp.status_code} fetching PR #{pr_number}."
        )
    try:
        return resp.json()["head"]["sha"]
    except (KeyError, TypeError) as exc:
        raise GitHubServiceError(
            f"Unexpected response shape for PR #{pr_number} metadata."
        ) from exc


async def get_pr_head_sha(pr_url: str, pat: str) -> str:
    """Return the head commit SHA for a PR URL.

    Used by agentic verification to pin its GitHub tools to the PR's branch —
    reading the default branch instead would judge code the PR doesn't contain.
    """
    if not pat:
        raise GitHubServiceError("GitHub PAT not configured.")
    owner, repo, pr_number = _parse_pr_url(pr_url)
    async with httpx.AsyncClient() as client:
        return await _get_pr_head_sha(client, _auth_headers(pat), owner, repo, pr_number)


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
        # Links must use the PR's head commit: blob/HEAD resolves to the default
        # branch, which 404s for files that only exist on the PR branch.
        head_sha = await _get_pr_head_sha(client, headers, owner, repo, pr_number)
        # per_page=100 is the GitHub maximum for this endpoint; page through
        # the listing. GitHub itself lists at most 3 000 files per PR, so the
        # loop is bounded at 30 pages.
        files: list[dict] = []
        listing_truncated = False
        for page in range(1, 31):
            api_url = (
                f"{GITHUB_API_BASE}/repos/{owner}/{repo}/pulls/{pr_number}/files"
                f"?per_page=100&page={page}"
            )
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

            batch = response.json()
            files.extend(batch)
            if len(batch) < 100:
                break
        else:
            # 30 full pages — GitHub's listing limit; more files may exist.
            listing_truncated = True

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
            github_url = f"https://github.com/{owner}/{repo}/blob/{head_sha}/{path}"
            fetched.append(FetchedFile(path=path, content=patch, github_url=github_url))

    if not fetched:
        raise GitHubServiceError(
            "No verifiable files found in the pull request "
            "(all files were removed or binary)."
        )

    if listing_truncated:
        fetched.insert(
            0,
            FetchedFile(
                path="[WARNING] PR file listing hit GitHub's 3 000-file limit — the diff set is incomplete",
                content=(
                    "GitHub lists at most 3 000 changed files per pull request. "
                    "Files beyond that limit were not fetched."
                ),
            ),
        )

    return fetched
