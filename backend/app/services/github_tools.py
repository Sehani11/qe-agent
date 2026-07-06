"""GitHub tool functions for agentic verification.

Provides search_code, get_file_contents, and list_directory as async functions
that the LLM agent can call during agentic verification. Each function maps
directly to a GitHub API endpoint and uses the same _github_get retry helper
from github_service.py.

All functions raise GitHubServiceError on failure.
"""

import json
from urllib.parse import quote

import httpx

from app.services.github_service import (
    GitHubServiceError,
    _auth_headers,
    _decode_base64_content,
    _github_get,
)

GITHUB_API_BASE = "https://api.github.com"

# Cap file content to control token use
_FILE_CONTENT_CAP = 8_000


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


async def get_file_contents(repo: str, path: str, pat: str, ref: str = "HEAD") -> str:
    """Fetch the full contents of a specific file from GitHub.

    Args:
        repo: GitHub repo in owner/repo format.
        path: File path relative to repo root.
        pat:  GitHub personal access token.
        ref:  Branch, tag or commit SHA. Default: HEAD.

    Returns:
        The raw file text (capped at 8 000 chars).

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
        if data.get("encoding") != "base64" or "content" not in data:
            raise GitHubServiceError(
                f"Unexpected response format for '{path}'."
            )
        content = _decode_base64_content(data["content"])
        return content[:_FILE_CONTENT_CAP]


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
                "Fetch the full contents of a specific file from GitHub. "
                "Use after search_code to read the implementation."
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
