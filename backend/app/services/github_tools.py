"""GitHub tool functions for agentic verification.

Provides search_code, get_file_contents, and list_directory as async functions
that the LLM agent can call during agentic verification. Each function maps
directly to a GitHub API endpoint and uses the same _github_get retry helper
from github_service.py.

All functions raise GitHubServiceError on failure.
"""

import json
import logging
from urllib.parse import quote

import httpx

from app.services.github_service import (
    GitHubServiceError,
    _auth_headers,
    _decode_base64_content,
    _github_get,
)

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"

# Cap file content per read, to control token use. Sized so a typical source
# file arrives whole — at the old 8 000 a 9 700-char React page lost its last
# third, and the agent reported the buttons rendered there as missing. Files
# above the cap are paginated with ``offset`` rather than silently cut.
_FILE_CONTENT_CAP = 40_000


def _window(content: str, offset: int) -> str:
    """Return the ``_FILE_CONTENT_CAP``-sized window of ``content`` at ``offset``.

    Truncation is announced, never silent. A reader shown the first N characters
    of a file cannot distinguish a feature that is absent from one that is merely
    below the cut, so it reports the second as the first — which is precisely how
    a verdict comes to call working code unimplemented. The notice says how much
    was withheld and how to ask for the rest.

    A file that fits under the cap is returned byte-for-byte, so the common case
    carries no annotation at all.
    """
    total = len(content)
    start = max(0, offset)

    if total and start >= total:
        return (
            f"[EMPTY WINDOW — offset {start} is at or past the end of this file "
            f"({total} chars); it has already been read in full.]"
        )

    end = min(start + _FILE_CONTENT_CAP, total)
    parts: list[str] = []

    if start > 0:
        parts.append(f"[CONTINUED — chars {start}-{end} of {total}.]\n\n")

    parts.append(content[start:end])

    if end < total:
        parts.append(
            f"\n\n[TRUNCATED — {end} of {total} chars shown; this is NOT the whole "
            f"file. Call get_file_contents again with offset={end} to read the "
            f"rest BEFORE concluding that anything is missing from it.]"
        )

    return "".join(parts)


async def search_code(query: str, repo: str, pat: str) -> str:
    """Search for code in a GitHub repository matching a query.

    Args:
        query: Search terms (e.g. 'user registration form validation').
        repo:  GitHub repo in owner/repo format (e.g. 'org/myapp').
        pat:   GitHub personal access token.

    Returns:
        JSON string: [{"path": "...", "url": "..."}] (top 10 results).

    Raises:
        GitHubServiceError: On API failure.
    """
    encoded_q = quote(f"{query}+repo:{repo}", safe="+:/")
    url = f"{GITHUB_API_BASE}/search/code?q={encoded_q}&per_page=10"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        headers = _auth_headers(pat)
        resp = await _github_get(client, url, headers)
        if resp.status_code in (403, 422):
            # GitHub code search is unavailable for private repos or unindexed repos.
            # Return an empty result so the agent falls back to list_directory + get_file_contents.
            return json.dumps([])
        if not resp.is_success:
            raise GitHubServiceError(
                f"Code search failed: {resp.status_code}"
            )
        data = resp.json()
        results = [
            {"path": item["path"], "url": item["html_url"]}
            for item in data.get("items", [])
        ]
        return json.dumps(results)


async def get_file_contents(
    repo: str, path: str, pat: str, ref: str = "HEAD", offset: int = 0
) -> str:
    """Fetch the contents of a specific file from GitHub.

    Args:
        repo:   GitHub repo in owner/repo format.
        path:   File path relative to repo root.
        pat:    GitHub personal access token.
        ref:    Branch, tag or commit SHA. Default: HEAD.
        offset: Character offset to start reading from. Default 0 (the start).
            A file longer than the per-read cap comes back truncated with a
            notice naming the offset to pass for the next window, so a large
            file can be read in full across several calls.

    Returns:
        The raw file text from ``offset``, capped at ``_FILE_CONTENT_CAP`` chars
        and annotated when the window is not the whole file.

    Raises:
        GitHubServiceError: On API failure or decode error.
    """
    path = path.lstrip("/")
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}?ref={ref}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        headers = _auth_headers(pat)
        resp = await _github_get(client, url, headers)
        if resp.status_code == 404:
            raise GitHubServiceError(f"File not found: '{path}' in {repo}@{ref}")
        if resp.status_code == 401:
            raise GitHubServiceError("GitHub authentication failed. Check your PAT.")
        if not resp.is_success:
            raise GitHubServiceError(
                f"GitHub API returned {resp.status_code} for '{path}'."
            )
        data = resp.json()
        # A directory comes back as a JSON ARRAY from the same endpoint, and the
        # model guesses directory paths routinely. Without this the next line
        # calls .get() on a list, which escapes as an unexpected exception and
        # kills the whole scenario. Phrased as an instruction because the
        # executor hands this text back to the model, which then recovers by
        # calling list_directory itself — the mirror guard to the one in that
        # tool, which already rejects being pointed at a file.
        if isinstance(data, list):
            raise GitHubServiceError(
                f"'{path}' is a directory, not a file. "
                "Use list_directory to see what is inside it."
            )
        if data.get("encoding") != "base64" or "content" not in data:
            raise GitHubServiceError(
                f"Unexpected response format for '{path}'."
            )
        content = _decode_base64_content(data["content"])
        return _window(content, offset)


async def list_directory(repo: str, path: str, pat: str, ref: str = "HEAD") -> str:
    """List files and directories at a path in the GitHub repo.

    Args:
        repo: GitHub repo in owner/repo format.
        path: Directory path (e.g. 'src/auth' or '' for root).
        pat:  GitHub personal access token.
        ref:  Branch, tag or commit SHA. Default: HEAD.

    Returns:
        JSON string: list of file/dir names.

    Raises:
        GitHubServiceError: On API failure.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/contents/{path}?ref={ref}"
    async with httpx.AsyncClient(follow_redirects=True) as client:
        headers = _auth_headers(pat)
        resp = await _github_get(client, url, headers)
        if resp.status_code == 404:
            raise GitHubServiceError(
                f"Directory not found: '{path}' in {repo}@{ref}"
            )
        if resp.status_code == 401:
            raise GitHubServiceError("GitHub authentication failed. Check your PAT.")
        if not resp.is_success:
            raise GitHubServiceError(
                f"GitHub API returned {resp.status_code} for '{path}'."
            )
        items = resp.json()
        if not isinstance(items, list):
            raise GitHubServiceError(
                f"Expected directory listing but got a file for '{path}'."
            )
        names = [
            item["name"] + ("/" if item.get("type") == "dir" else "")
            for item in items
        ]
        return json.dumps(names)


#: Cap on paths listed in a prefetched tree. A large monorepo can carry tens of
#: thousands, which would cost more than the directory walk it replaces.
_TREE_PATH_CAP = 1_500

#: Directories whose contents are never the implementation under verification.
#: Excluding them is what keeps a real repository under the path cap.
_TREE_SKIP_SEGMENTS = frozenset(
    {
        ".git",
        ".github",
        ".next",
        ".venv",
        "__pycache__",
        "build",
        "coverage",
        "dist",
        "node_modules",
        "site-packages",
        "target",
        "vendor",
    }
)


def _is_noise_path(path: str) -> bool:
    """True for a path inside a dependency, build or cache directory."""
    return any(segment in _TREE_SKIP_SEGMENTS for segment in path.split("/"))


async def get_repo_tree(repo: str, pat: str, ref: str = "HEAD") -> list[str]:
    """List every source file path in the repo in one call.

    Returns a sorted list of paths, capped at ``_TREE_PATH_CAP`` and stripped of
    dependency and build directories. Returns an empty list rather than raising:
    a tree is an accelerator, and a run that cannot get one must still work
    exactly as it did before by walking directories.

    The point is round-trips. GitHub's code search returns nothing for private
    repositories (it answers 403/422, which ``search_code`` turns into an empty
    result), so the agent falls back to ``list_directory`` and discovers the
    layout one directory at a time — several LLM rounds per scenario spent
    before any code is read. One recursive tree call replaces all of that, for
    every scenario in the run at once.

    Accuracy benefits as much as speed: an agent that can see the real paths
    stops guessing at them, and a guessed path that 404s is a step towards
    concluding a feature is absent when it is merely somewhere else.
    """
    url = f"{GITHUB_API_BASE}/repos/{repo}/git/trees/{quote(ref, safe='')}?recursive=1"
    try:
        async with httpx.AsyncClient(follow_redirects=True) as client:
            resp = await _github_get(client, url, _auth_headers(pat))
            if not resp.is_success:
                logger.info(
                    "Repo tree unavailable for %s@%s (HTTP %s); "
                    "the agent will explore with list_directory instead.",
                    repo,
                    ref,
                    resp.status_code,
                )
                return []
            data = resp.json()
    except Exception as exc:
        # Deliberately broad. This function is an accelerator with a documented
        # empty-list fallback, and the run behaves correctly without it — so
        # there is no failure here worth ending a verification over. Logged
        # rather than swallowed, because a tree that never loads turns into a
        # slow, expensive run and the reason should be findable.
        logger.warning(
            "Repo tree fetch failed for %s@%s: %s; "
            "the agent will explore with list_directory instead.",
            repo,
            ref,
            exc,
        )
        return []

    if not isinstance(data, dict):
        return []

    paths = sorted(
        entry["path"]
        for entry in data.get("tree", [])
        if isinstance(entry, dict)
        and entry.get("type") == "blob"
        and isinstance(entry.get("path"), str)
        and not _is_noise_path(entry["path"])
    )
    return paths[:_TREE_PATH_CAP]


def _compact_tree_lines(paths: list[str]) -> list[str]:
    """Group paths by directory: the directory once, its files indented under it.

    Measures 34-46% smaller than one full path per line, because a deep
    repository spends most of the block re-listing the same directory prefixes.

    Off by default all the same — see ``format_repo_tree``. It is kept because
    the saving scales with repository depth, and on a repo that fills the path
    cap it is worth an order of magnitude more than it was where it was
    measured against verdicts.
    """
    root_files: list[str] = []
    directories: dict[str, list[str]] = {}
    for path in paths:
        head, _, tail = path.rpartition("/")
        if head:
            directories.setdefault(head, []).append(tail)
        else:
            root_files.append(tail)

    lines = [
        "REPOSITORY FILE TREE (every source file, grouped by directory to save "
        "space). A line ending in \"/\" is a DIRECTORY and the indented lines "
        "under it are its files: join them to get the path, so \"src/auth/\" "
        "followed by \"  login.py\" means \"src/auth/login.py\". An unindented "
        "line not ending in \"/\" is already a complete path. Use this to pick "
        "paths to read instead of walking directories:",
        *sorted(root_files),
    ]
    for directory in sorted(directories):
        lines.append(f"{directory}/")
        lines.extend(f"  {name}" for name in sorted(directories[directory]))
    return lines


def format_repo_tree(paths: list[str], compact: bool = False) -> str:
    """Render prefetched paths as a prompt block, or "" when there are none.

    One full path per line by default. That is more tokens than grouping them
    by directory, and the choice is deliberate: grouping asks the model to
    rejoin a directory header to a filename, and a path it reconstructs wrongly
    is a 404 — which by this module's own reasoning is a step towards
    concluding a feature is absent.

    That is not hypothetical. Grouping saved 2-4% on the repository it was
    measured against, and in the same run two scenarios moved from ``pass`` to
    ``inconclusive``: the agent read less and then could not decide. A single
    run cannot prove the format caused it, but a few percent is not worth any
    doubt about a verdict, so the default went back to the safe rendering and
    the saving became opt-in via ``VERIFICATION_COMPACT_TREE``.

    Truncation is announced for the same reason it is announced on a file read:
    an agent shown a partial list that looks complete will conclude a file it
    cannot see does not exist.
    """
    if not paths:
        return ""

    if compact:
        lines = _compact_tree_lines(paths)
    else:
        lines = [
            "REPOSITORY FILE TREE (every source file; use it to pick paths to "
            "read instead of walking directories):",
            *paths,
        ]

    if len(paths) >= _TREE_PATH_CAP:
        lines.append(
            f"[TRUNCATED — first {_TREE_PATH_CAP} paths shown; this is NOT the "
            "whole tree. Use list_directory to explore anything not listed here.]"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenAI tool schemas for the three GitHub tools
# ---------------------------------------------------------------------------

GITHUB_TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": (
                "Search for code in a GitHub repository matching a query. "
                "Use to find files related to a feature, function, or concept."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search terms (e.g. 'user registration form validation')",
                    },
                    "repo": {
                        "type": "string",
                        "description": "GitHub repo in owner/repo format (e.g. 'org/myapp')",
                    },
                },
                "required": ["query", "repo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_file_contents",
            "description": (
                "Fetch the contents of a specific file from GitHub. "
                "Use after search_code to read the implementation. "
                "A large file comes back truncated with a notice naming the "
                "offset to pass for the next window — always follow that up "
                "before deciding something is missing from the file."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "GitHub repo in owner/repo format",
                    },
                    "path": {
                        "type": "string",
                        "description": "File path relative to repo root (e.g. 'src/auth/login.py')",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag or commit SHA. Default: HEAD",
                        "default": "HEAD",
                    },
                    "offset": {
                        "type": "integer",
                        "description": (
                            "Character offset to start reading from. Default 0. "
                            "Pass the offset given in a [TRUNCATED ...] notice to "
                            "read the next part of a long file."
                        ),
                        "default": 0,
                    },
                },
                "required": ["repo", "path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and directories at a path in the GitHub repo. "
                "Use to explore project structure."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {
                        "type": "string",
                        "description": "GitHub repo in owner/repo format",
                    },
                    "path": {
                        "type": "string",
                        "description": "Directory path (e.g. 'src/auth' or '' for root)",
                    },
                    "ref": {
                        "type": "string",
                        "description": "Branch, tag or commit SHA. Default: HEAD",
                        "default": "HEAD",
                    },
                },
                "required": ["repo", "path"],
            },
        },
    },
]
