"""Tests for the GitHub code-fetching service (agentic verification evidence)."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.github_service import (
    GitHubServiceError,
    _parse_github_blob_url,
    _parse_pr_url,
    _parse_repo_url,
    fetch_exact_files_resolved,
    fetch_pull_request,
)


async def fetch_exact_files(paths_input: str, pat: str):
    """Files-only view of fetch_exact_files_resolved, for concise assertions."""
    files, _ = await fetch_exact_files_resolved(paths_input, pat)
    return files


def _encoded(text: str) -> str:
    """Encode text as base64 the way GitHub does (with newlines every 60 chars)."""
    raw = base64.b64encode(text.encode()).decode()
    # GitHub inserts newlines — our decoder strips them so this is fine either way
    return raw


def _mock_response(status_code: int, json_data: object) -> MagicMock:
    """Build a mock httpx.Response."""
    mock = MagicMock()
    mock.status_code = status_code
    mock.is_success = 200 <= status_code < 300
    mock.json.return_value = json_data
    return mock


def _make_async_client(get_side_effect) -> MagicMock:
    """Build an async context-manager mock for httpx.AsyncClient."""
    mock_client = AsyncMock()
    mock_client.get = get_side_effect
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


# ---------------------------------------------------------------------------
# URL parsing unit tests
# ---------------------------------------------------------------------------

class TestParseGithubBlobUrl:
    def test_standard_blob_url(self) -> None:
        owner, repo, ref, path = _parse_github_blob_url(
            "https://github.com/org/repo/blob/main/src/auth.py"
        )
        assert owner == "org"
        assert repo == "repo"
        assert ref == "main"
        assert path == "src/auth.py"

    def test_branch_with_slashes(self) -> None:
        # Known limitation: the ref segment captures only one path component.
        # Branch names containing slashes (e.g. feature/my-branch) are NOT
        # supported — only the first component is captured as ref and the
        # remainder becomes the path prefix, which will 404 against the API.
        owner, repo, ref, path = _parse_github_blob_url(
            "https://github.com/org/repo/blob/feature/my-branch/src/deep/file.py"
        )
        assert owner == "org"
        assert repo == "repo"
        assert ref == "feature"          # only first segment captured
        assert path == "my-branch/src/deep/file.py"  # remainder is path prefix

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub blob URL"):
            _parse_github_blob_url("https://github.com/org/repo")

    def test_plain_path_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub blob URL"):
            _parse_github_blob_url("src/auth/routes.py")


class TestParseRepoUrl:
    def test_standard_repo_url(self) -> None:
        owner, repo = _parse_repo_url("https://github.com/org/repo")
        assert owner == "org"
        assert repo == "repo"

    def test_trailing_slash(self) -> None:
        owner, repo = _parse_repo_url("https://github.com/org/repo/")
        assert owner == "org"
        assert repo == "repo"

    def test_dot_git_suffix(self) -> None:
        owner, repo = _parse_repo_url("https://github.com/org/repo.git")
        assert owner == "org"
        assert repo == "repo"

    def test_invalid_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub repository URL"):
            _parse_repo_url("https://github.com/org")

    def test_pr_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub repository URL"):
            _parse_repo_url("https://github.com/org/repo/pull/42")


class TestParsePrUrl:
    def test_standard_pr_url(self) -> None:
        owner, repo, pr_num = _parse_pr_url("https://github.com/org/repo/pull/42")
        assert owner == "org"
        assert repo == "repo"
        assert pr_num == 42

    def test_invalid_pr_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub PR URL"):
            _parse_pr_url("https://github.com/org/repo")

    def test_non_numeric_pr_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub PR URL"):
            _parse_pr_url("https://github.com/org/repo/pull/abc")


# ---------------------------------------------------------------------------
# fetch_exact_files tests
# ---------------------------------------------------------------------------

class TestFetchExactFiles:
    @pytest.mark.asyncio
    async def test_happy_path_returns_decoded_content(self) -> None:
        content = "def hello(): pass"
        api_resp = _mock_response(200, {
            "content": _encoded(content),
            "encoding": "base64",
            "path": "src/auth.py",
        })
        mock_get = AsyncMock(return_value=api_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_exact_files(
                "https://github.com/org/repo/blob/main/src/auth.py",
                pat="test-pat",
            )

        assert len(result) == 1
        assert result[0].path == "src/auth.py"
        assert result[0].content == content

    @pytest.mark.asyncio
    async def test_404_raises_service_error(self) -> None:
        api_resp = _mock_response(404, {"message": "Not Found"})
        mock_get = AsyncMock(return_value=api_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="not found"):
                await fetch_exact_files(
                    "https://github.com/org/repo/blob/main/src/missing.py",
                    pat="test-pat",
                )

    @pytest.mark.asyncio
    async def test_invalid_url_raises_service_error(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub blob URL"):
            await fetch_exact_files("src/auth/routes.py", pat="test-pat")

    @pytest.mark.asyncio
    async def test_missing_pat_raises_service_error(self) -> None:
        with pytest.raises(GitHubServiceError, match="PAT not configured"):
            await fetch_exact_files(
                "https://github.com/org/repo/blob/main/src/auth.py",
                pat="",
            )

    @pytest.mark.asyncio
    async def test_multiple_files_fetched(self) -> None:
        async def side_effect(url, headers, timeout=30.0):
            if "file1" in url:
                return _mock_response(200, {
                    "content": _encoded("content1"),
                    "encoding": "base64",
                    "path": "src/file1.py",
                })
            return _mock_response(200, {
                "content": _encoded("content2"),
                "encoding": "base64",
                "path": "src/file2.py",
            })

        mock_get = AsyncMock(side_effect=side_effect)
        cm = _make_async_client(mock_get)

        urls = (
            "https://github.com/org/repo/blob/main/src/file1.py\n"
            "https://github.com/org/repo/blob/main/src/file2.py"
        )
        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_exact_files(urls, pat="test-pat")

        assert len(result) == 2


# ---------------------------------------------------------------------------
# fetch_pull_request tests
# ---------------------------------------------------------------------------

class TestFetchPullRequest:
    @pytest.mark.asyncio
    async def test_happy_path_returns_patches(self) -> None:
        pr_files = [
            {"filename": "src/auth.py", "status": "modified", "patch": "@@ -1 +1 @@\n+def new(): pass"},
            {"filename": "src/utils.py", "status": "added", "patch": "@@ -0,0 +1 @@\n+# utils"},
            {"filename": "old.py", "status": "removed", "patch": None},     # removed — skipped
            {"filename": "logo.png", "status": "modified", "patch": None},  # binary — skipped
        ]
        meta_resp = _mock_response(200, {"head": {"sha": "abc123def"}})
        pr_resp = _mock_response(200, pr_files)
        mock_get = AsyncMock(side_effect=[meta_resp, pr_resp])
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_pull_request(
                "https://github.com/org/repo/pull/42", pat="test-pat"
            )

        assert len(result) == 2
        paths = {f.path for f in result}
        assert "src/auth.py" in paths
        assert "src/utils.py" in paths
        assert "old.py" not in paths
        # Evidence links point at the PR head commit — blob/HEAD would 404 for
        # files that only exist on the PR branch.
        assert all("/blob/abc123def/" in f.github_url for f in result)

    @pytest.mark.asyncio
    async def test_invalid_pr_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub PR URL"):
            await fetch_pull_request("https://github.com/org/repo", pat="test-pat")

    @pytest.mark.asyncio
    async def test_missing_pat_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="PAT not configured"):
            await fetch_pull_request(
                "https://github.com/org/repo/pull/42", pat=""
            )

    @pytest.mark.asyncio
    async def test_401_raises_auth_error(self) -> None:
        pr_resp = _mock_response(401, {"message": "Bad credentials"})
        mock_get = AsyncMock(return_value=pr_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="authentication failed"):
                await fetch_pull_request(
                    "https://github.com/org/repo/pull/42", pat="invalid-pat"
                )

    @pytest.mark.asyncio
    async def test_pr_files_url_uses_per_page_100(self) -> None:
        """Ensure the PR files request includes per_page=100 to reduce silent truncation."""
        pr_files = [
            {"filename": "src/auth.py", "status": "modified", "patch": "@@ -1 +1 @@\n+def new(): pass"},
        ]
        meta_resp = _mock_response(200, {"head": {"sha": "abc123def"}})
        pr_resp = _mock_response(200, pr_files)
        captured_urls: list[str] = []

        async def mock_get(url, headers, timeout=30.0):
            captured_urls.append(url)
            return meta_resp if url.endswith("/pulls/42") else pr_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            await fetch_pull_request("https://github.com/org/repo/pull/42", pat="test-pat")

        # First call fetches PR metadata (head sha); second lists the files.
        assert len(captured_urls) == 2
        assert "per_page=100" in captured_urls[1]

    @pytest.mark.asyncio
    async def test_pr_not_found_raises(self) -> None:
        pr_resp = _mock_response(404, {"message": "Not Found"})
        mock_get = AsyncMock(return_value=pr_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="not found"):
                await fetch_pull_request(
                    "https://github.com/org/repo/pull/999", pat="test-pat"
                )

    @pytest.mark.asyncio
    async def test_all_files_removed_raises(self) -> None:
        pr_files = [{"filename": "old.py", "status": "removed", "patch": None}]
        meta_resp = _mock_response(200, {"head": {"sha": "abc123def"}})
        pr_resp = _mock_response(200, pr_files)
        mock_get = AsyncMock(side_effect=[meta_resp, pr_resp])
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="No verifiable files"):
                await fetch_pull_request(
                    "https://github.com/org/repo/pull/7", pat="test-pat"
                )


# ---------------------------------------------------------------------------
# Limitation fixes: slashed branch refs, full-repo selection, PR pagination
# ---------------------------------------------------------------------------

class TestSlashedBranchRefs:
    @pytest.mark.asyncio
    async def test_slashed_branch_ref_is_resolved_and_retried(self) -> None:
        """blob/feature/my-branch/src/auth.py: the first-segment split 404s,
        matching-refs resolves the real branch, and the retry succeeds."""
        content_resp = _mock_response(200, {
            "content": _encoded("def login(): pass"),
            "encoding": "base64",
        })
        matching_refs_resp = _mock_response(200, [
            {"ref": "refs/heads/feature"},            # decoy: shorter prefix
            {"ref": "refs/heads/feature/my-branch"},  # the real branch
        ])
        captured_urls: list[str] = []

        async def mock_get(url, headers, timeout=30.0):
            captured_urls.append(url)
            if "matching-refs" in url:
                return matching_refs_resp
            # First contents attempt uses the mis-split ref → 404
            if "ref=feature&" in url or url.endswith("ref=feature"):
                return _mock_response(404, {"message": "Not Found"})
            return content_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            from app.services.github_service import fetch_exact_files_resolved
            files, first_ref = await fetch_exact_files_resolved(
                "https://github.com/org/repo/blob/feature/my-branch/src/auth.py",
                pat="test-pat",
            )

        assert first_ref == "feature/my-branch"
        assert len(files) == 1
        assert files[0].path == "src/auth.py"
        assert files[0].github_url == (
            "https://github.com/org/repo/blob/feature/my-branch/src/auth.py"
        )
        # The retry asked for the URL-encoded resolved ref
        assert any("ref=feature%2Fmy-branch" in u for u in captured_urls)

    @pytest.mark.asyncio
    async def test_unresolvable_slashed_ref_still_404s_clearly(self) -> None:
        """When matching-refs has no multi-segment branch, the original
        file-not-found error stands."""
        matching_refs_resp = _mock_response(200, [{"ref": "refs/heads/main"}])

        async def mock_get(url, headers, timeout=30.0):
            if "matching-refs" in url:
                return matching_refs_resp
            return _mock_response(404, {"message": "Not Found"})

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="File not found"):
                await fetch_exact_files(
                    "https://github.com/org/repo/blob/missing/src/auth.py",
                    pat="test-pat",
                )


class TestPullRequestPagination:
    @pytest.mark.asyncio
    async def test_pr_with_more_than_100_files_is_fully_paged(self) -> None:
        meta_resp = _mock_response(200, {"head": {"sha": "abc123"}})
        page1 = [
            {"filename": f"src/f{i}.py", "status": "modified", "patch": "@@ -1 +1 @@"}
            for i in range(100)
        ]
        page2 = [
            {"filename": f"src/g{i}.py", "status": "modified", "patch": "@@ -1 +1 @@"}
            for i in range(30)
        ]
        captured_urls: list[str] = []

        async def mock_get(url, headers, timeout=30.0):
            captured_urls.append(url)
            if url.endswith("/pulls/42"):
                return meta_resp
            if url.endswith("&page=1"):
                return _mock_response(200, page1)
            return _mock_response(200, page2)

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_pull_request(
                "https://github.com/org/repo/pull/42", pat="test-pat"
            )

        assert len(result) == 130
        assert any(u.endswith("&page=2") for u in captured_urls)
        # A short page ends the loop — no page=3 request
        assert not any(u.endswith("&page=3") for u in captured_urls)


# ---------------------------------------------------------------------------
# Retry / backoff tests
# ---------------------------------------------------------------------------

class TestRetryBackoff:
    @pytest.mark.asyncio
    async def test_429_retry_succeeds_on_third_attempt(self) -> None:
        """First 2 calls return 429; third returns 200 — should succeed."""
        success_data = {
            "content": _encoded("def ok(): pass"),
            "encoding": "base64",
            "path": "src/auth.py",
        }
        responses = [
            _mock_response(429, {}),
            _mock_response(429, {}),
            _mock_response(200, success_data),
        ]
        call_iter = iter(responses)
        mock_get = AsyncMock(side_effect=lambda *a, **kw: next(call_iter))
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with patch("app.services.github_service.asyncio.sleep", new_callable=AsyncMock):
                result = await fetch_exact_files(
                    "https://github.com/org/repo/blob/main/src/auth.py",
                    pat="test-pat",
                )

        assert len(result) == 1
        assert result[0].content == "def ok(): pass"

    @pytest.mark.asyncio
    async def test_429_all_retries_exhausted_raises(self) -> None:
        """All 4 attempts return 429 — should raise GitHubServiceError."""
        mock_get = AsyncMock(return_value=_mock_response(429, {}))
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with patch("app.services.github_service.asyncio.sleep", new_callable=AsyncMock):
                with pytest.raises(GitHubServiceError, match="rate limit"):
                    await fetch_exact_files(
                        "https://github.com/org/repo/blob/main/src/auth.py",
                        pat="test-pat",
                    )
