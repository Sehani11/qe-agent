"""Tests for the GitHub verification service and API endpoint."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.github_service import (
    GitHubServiceError,
    _parse_github_blob_url,
    _parse_pr_url,
    _parse_repo_url,
    fetch_exact_files,
    fetch_full_repo,
    fetch_pull_request,
    fetch_github_code,
)


async def _mock_auth() -> str:
    return "test-user-id"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _mock_auth
    yield TestClient(app)
    app.dependency_overrides.clear()


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
# fetch_full_repo tests
# ---------------------------------------------------------------------------

class TestFetchFullRepo:
    @pytest.mark.asyncio
    async def test_happy_path_returns_text_files(self) -> None:
        tree_resp = _mock_response(200, {
            "tree": [
                {"path": "src/main.py", "type": "blob", "size": 100},
                {"path": "src/utils.py", "type": "blob", "size": 200},
                {"path": "image.png", "type": "blob", "size": 50},   # binary — skipped
                {"path": "src/", "type": "tree", "size": 0},         # tree — skipped
            ],
            "truncated": False,
        })
        file_content_resp = _mock_response(200, {
            "content": _encoded("print('hello')"),
            "encoding": "base64",
        })

        call_count = 0

        async def mock_get(url, headers, timeout=30.0):
            nonlocal call_count
            call_count += 1
            if "git/trees" in url:
                return tree_resp
            return file_content_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_full_repo("https://github.com/org/repo", pat="test-pat")

        # Only 2 text blobs (png and tree entry skipped)
        assert len(result) == 2
        assert all(f.content == "print('hello')" for f in result)

    @pytest.mark.asyncio
    async def test_binary_extensions_filtered_out(self) -> None:
        tree_resp = _mock_response(200, {
            "tree": [
                {"path": "logo.png", "type": "blob", "size": 100},
                {"path": "font.woff2", "type": "blob", "size": 100},
                {"path": "package-lock.json.lock", "type": "blob", "size": 100},
            ],
            "truncated": False,
        })

        async def mock_get(url, headers, timeout=30.0):
            return tree_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="No qualifying text files"):
                await fetch_full_repo("https://github.com/org/repo", pat="test-pat")

    @pytest.mark.asyncio
    async def test_invalid_repo_url_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Invalid GitHub repository URL"):
            await fetch_full_repo("https://github.com/org", pat="test-pat")

    @pytest.mark.asyncio
    async def test_missing_pat_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="PAT not configured"):
            await fetch_full_repo("https://github.com/org/repo", pat="")

    @pytest.mark.asyncio
    async def test_truncated_tree_prepends_warning(self) -> None:
        tree_resp = _mock_response(200, {
            "tree": [
                {"path": "src/main.py", "type": "blob", "size": 100},
            ],
            "truncated": True,  # GitHub signals more files exist beyond the limit
        })
        file_content_resp = _mock_response(200, {
            "content": _encoded("print('hello')"),
            "encoding": "base64",
        })

        async def mock_get(url, headers, timeout=30.0):
            if "git/trees" in url:
                return tree_resp
            return file_content_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            result = await fetch_full_repo("https://github.com/org/repo", pat="test-pat")

        # Warning entry is prepended; actual file follows
        assert len(result) == 2
        assert result[0].path.startswith("[WARNING]")
        assert "truncated" in result[0].path.lower()
        assert result[1].path == "src/main.py"

    @pytest.mark.asyncio
    async def test_repo_404_raises(self) -> None:
        tree_resp = _mock_response(404, {"message": "Not Found"})
        mock_get = AsyncMock(return_value=tree_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="not found"):
                await fetch_full_repo("https://github.com/org/missing-repo", pat="test-pat")


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
        pr_resp = _mock_response(200, pr_files)
        mock_get = AsyncMock(return_value=pr_resp)
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
        pr_resp = _mock_response(200, pr_files)
        captured_urls: list[str] = []

        async def mock_get(url, headers, timeout=30.0):
            captured_urls.append(url)
            return pr_resp

        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            await fetch_pull_request("https://github.com/org/repo/pull/42", pat="test-pat")

        assert len(captured_urls) == 1
        assert "per_page=100" in captured_urls[0]

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
        pr_resp = _mock_response(200, pr_files)
        mock_get = AsyncMock(return_value=pr_resp)
        cm = _make_async_client(mock_get)

        with patch("app.services.github_service.httpx.AsyncClient", return_value=cm):
            with pytest.raises(GitHubServiceError, match="No verifiable files"):
                await fetch_pull_request(
                    "https://github.com/org/repo/pull/7", pat="test-pat"
                )


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


# ---------------------------------------------------------------------------
# fetch_github_code dispatch tests
# ---------------------------------------------------------------------------

class TestFetchGithubCodeDispatch:
    @pytest.mark.asyncio
    async def test_unknown_mode_raises(self) -> None:
        with pytest.raises(GitHubServiceError, match="Unknown verification mode"):
            await fetch_github_code("invalid_mode", "some-input", "test-pat")


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------

class TestVerificationEndpoint:
    def test_fetch_endpoint_calls_service_and_returns_response(self, client: TestClient) -> None:
        from app.schemas.verification import FetchedFile

        with patch(
            "app.api.v1.verification.fetch_github_code",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = [
                FetchedFile(path="src/auth.py", content="def login(): pass")
            ]

            response = client.post(
                "/api/v1/verification/fetch",
                json={
                    "session_id": "session-abc",
                    "mode": "exact_files",
                    "github_input": "https://github.com/org/repo/blob/main/src/auth.py",
                },
            )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == "session-abc"
        assert data["mode"] == "exact_files"
        assert len(data["fetched_files"]) == 1
        assert data["fetched_files"][0]["path"] == "src/auth.py"

    def test_fetch_endpoint_returns_422_on_service_error(self, client: TestClient) -> None:
        with patch(
            "app.api.v1.verification.fetch_github_code",
            new_callable=AsyncMock,
            side_effect=GitHubServiceError("File not found", code="GITHUB_FETCH_FAILED"),
        ):
            response = client.post(
                "/api/v1/verification/fetch",
                json={
                    "session_id": "session-abc",
                    "mode": "exact_files",
                    "github_input": "https://github.com/org/repo/blob/main/missing.py",
                },
            )

        assert response.status_code == 422
        body = response.json()
        # Architecture error envelope: {"error": "...", "message": "...", "code": 422}
        assert body["error"] == "GITHUB_FETCH_FAILED"
        assert "File not found" in body["message"]
        assert body["code"] == 422

    def test_fetch_endpoint_invalid_mode_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/verification/fetch",
            json={
                "session_id": "session-abc",
                "mode": "invalid_mode",
                "github_input": "something",
            },
        )
        assert response.status_code == 422
        body = response.json()
        # Pydantic validation errors go through main.py's RequestValidationError
        # handler, which returns the architecture envelope with VALIDATION_ERROR.
        assert body["error"] == "VALIDATION_ERROR"
        assert body["code"] == 422
        assert "message" in body
