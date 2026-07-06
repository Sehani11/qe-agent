"""Tests for the BDD generation and upload API endpoints."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.bdd_file import BddFile
from app.services.bdd_model.provider import BDDModelProviderError


async def _mock_auth() -> str:
    return "test-user-id"


def _make_mock_db() -> AsyncMock:
    """Return a mock async DB session (for generate endpoint — no execute needed)."""
    db = AsyncMock()
    db.add = MagicMock()
    return db


def _make_upload_db_mock(session_obj) -> AsyncMock:
    """Return a mock db for the upload endpoint.

    Handles both the session ownership SELECT and db.add/commit calls.
    session_obj: mock Session ORM object, or None to simulate session-not-found.
    """
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = session_obj

    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    db.add = MagicMock()
    return db


@pytest.fixture(autouse=True)
def override_auth():
    app.dependency_overrides[get_current_user] = _mock_auth
    yield
    app.dependency_overrides.clear()


client = TestClient(app)


# ---------------------------------------------------------------------------
# POST /api/v1/bdd/generate
# ---------------------------------------------------------------------------

def test_generate_bdd_success() -> None:
    session_id = str(uuid.uuid4())
    mock_bdd_response = {
        "scenarios": [
            {
                "source_ac_clause": "FR1",
                "feature": "User Login",
                "scenario": "Valid login",
                "given": "Given user is on login page",
                "when": "When valid credentials are provided",
                "then": "Then the dashboard loads",
            }
        ]
    }

    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch(
        "app.services.bdd_service.get_bdd_model_provider"
    ) as mock_get_provider:
        mock_provider = AsyncMock()
        mock_provider.generate_bdd.return_value = mock_bdd_response
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/bdd/generate",
            json={
                "session_id": session_id,
                "acceptance_criteria": "FR1: User logs in.",
            },
        )

    assert response.status_code == 200
    data = response.json()
    assert "scenarios" in data
    assert len(data["scenarios"]) == 1
    assert data["scenarios"][0]["feature"] == "User Login"

    # Verify a BddFile row was persisted to the database
    mock_db.add.assert_called_once()
    added_obj = mock_db.add.call_args[0][0]
    assert isinstance(added_obj, BddFile)
    assert added_obj.session_id == session_id
    assert added_obj.user_id == "test-user-id"
    assert added_obj.source == "generated"
    mock_db.commit.assert_awaited_once()


def test_generate_bdd_service_failure_returns_envelope_and_does_not_persist() -> None:
    """On BDD service failure: returns BDD_GENERATION_FAILED envelope and writes nothing to DB."""
    session_id = str(uuid.uuid4())

    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch(
        "app.services.bdd_service.get_bdd_model_provider"
    ) as mock_get_provider:
        mock_provider = AsyncMock()
        mock_provider.generate_bdd.side_effect = BDDModelProviderError("Model API down")
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/bdd/generate",
            json={
                "session_id": session_id,
                "acceptance_criteria": "Random text.",
            },
        )

    assert response.status_code == 500
    data = response.json()
    assert data["error"] == "BDD_GENERATION_FAILED"
    assert data["code"] == 500
    assert "Model API down" in data["message"]
    # DB must not be touched on failure
    mock_db.add.assert_not_called()
    mock_db.commit.assert_not_awaited()


def test_generate_bdd_missing_payload() -> None:
    # Trigger 422 unprocessable entity, verifying it triggers the default
    # envelope structured exception handler for Validation.
    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    response = client.post("/api/v1/bdd/generate", json={})
    assert response.status_code == 422
    data = response.json()
    assert data["error"] == "VALIDATION_ERROR"
    assert "details" in data


# ---------------------------------------------------------------------------
# POST /api/v1/bdd/upload
# ---------------------------------------------------------------------------

def _make_mock_session(
    session_id: str,
    user_id: str = "test-user-id",
    jira_ticket_id: str = "TEST-123",
) -> MagicMock:
    """Build a minimal mock Session ORM object for upload validation."""
    s = MagicMock()
    s.id = uuid.UUID(session_id)
    s.user_id = user_id
    s.jira_ticket_id = jira_ticket_id
    return s


def test_upload_bdd_success_persists_with_source_uploaded() -> None:
    """POST /bdd/upload persists bdd_files row and calls storage upload."""
    session_id = str(uuid.uuid4())
    mock_session = _make_mock_session(session_id)
    db = _make_upload_db_mock(mock_session)
    app.dependency_overrides[get_db] = lambda: db

    feature_content = b"Feature: Login\n  Scenario: Valid\n    Given user is logged in"

    with patch(
        "app.api.v1.bdd.storage_service.upload_file",
        new_callable=AsyncMock,
        return_value=f"test-user-id/{session_id}/login.feature",
    ) as mock_storage_upload:
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("login.feature", feature_content, "text/plain")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    assert "Given user is logged in" in data["content"]

    # Verify bdd_files row created with source='uploaded'
    db.add.assert_called_once()
    added_obj = db.add.call_args[0][0]
    assert isinstance(added_obj, BddFile)
    assert added_obj.session_id == session_id
    assert added_obj.user_id == "test-user-id"
    assert added_obj.source == "uploaded"
    assert "Given user is logged in" in added_obj.content
    db.commit.assert_awaited_once()

    # Verify storage upload was called with the correct folder and path
    mock_storage_upload.assert_awaited_once()
    call_kwargs = mock_storage_upload.call_args
    assert call_kwargs.kwargs["folder"] == "feature-files"
    assert session_id in call_kwargs.kwargs["path"]
    assert "login.feature" in call_kwargs.kwargs["path"]
    assert call_kwargs.kwargs["user_id"] == "test-user-id"


def test_upload_bdd_creates_session_when_missing() -> None:
    """POST /bdd/upload creates the session row on the fly when it doesn't exist.

    Supports the manual-upload-first flow: a user lands on /session/<uuid>
    without ingesting a Jira ticket and uploads a .feature file directly.
    """
    session_id = str(uuid.uuid4())
    db = _make_upload_db_mock(None)  # session not found → triggers on-demand creation
    db.flush = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.api.v1.bdd.storage_service.upload_file",
        new_callable=AsyncMock,
        return_value=f"test-user-id/{session_id}/manual.feature",
    ):
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("manual.feature", b"Feature: Manual", "text/plain")},
        )

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id
    assert data["jira_ticket_id"] == "Manual upload: manual.feature"

    # Both the Session row and the BddFile row should be persisted.
    added_types = [type(call.args[0]).__name__ for call in db.add.call_args_list]
    assert "Session" in added_types
    assert "BddFile" in added_types
    db.commit.assert_awaited_once()


def test_upload_bdd_returns_403_when_session_owned_by_other_user() -> None:
    """POST /bdd/upload returns 403 when the session_id belongs to another user."""
    session_id = str(uuid.uuid4())
    other_user_session = _make_mock_session(session_id, user_id="someone-else")
    db = _make_upload_db_mock(other_user_session)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/upload",
        data={"session_id": session_id},
        files={"file": ("test.feature", b"Feature: x", "text/plain")},
    )

    assert response.status_code == 403
    db.add.assert_not_called()


def test_upload_bdd_returns_422_for_non_feature_file() -> None:
    """POST /bdd/upload returns 422 when filename does not end in .feature."""
    session_id = str(uuid.uuid4())
    mock_session = _make_mock_session(session_id)
    db = _make_upload_db_mock(mock_session)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/upload",
        data={"session_id": session_id},
        files={"file": ("test.txt", b"not a feature file", "text/plain")},
    )

    assert response.status_code == 422
    db.add.assert_not_called()


def test_upload_bdd_returns_413_for_oversized_file() -> None:
    """POST /bdd/upload returns 413 when file exceeds 512 KB."""
    from app.api.v1.bdd import _MAX_UPLOAD_BYTES

    session_id = str(uuid.uuid4())
    mock_session = _make_mock_session(session_id)
    db = _make_upload_db_mock(mock_session)
    app.dependency_overrides[get_db] = lambda: db

    oversized = b"x" * (_MAX_UPLOAD_BYTES + 1)

    response = client.post(
        "/api/v1/bdd/upload",
        data={"session_id": session_id},
        files={"file": ("big.feature", oversized, "text/plain")},
    )

    assert response.status_code == 413
    db.add.assert_not_called()


def test_upload_bdd_without_token_returns_401() -> None:
    """POST /bdd/upload without Authorization header returns 401."""
    app.dependency_overrides.clear()
    unauthed_client = TestClient(app)

    response = unauthed_client.post(
        "/api/v1/bdd/upload",
        data={"session_id": str(uuid.uuid4())},
        files={"file": ("test.feature", b"Feature: x", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


def test_upload_bdd_returns_422_for_non_utf8_content() -> None:
    """POST /bdd/upload returns 422 when .feature file contains non-UTF-8 bytes."""
    session_id = str(uuid.uuid4())
    mock_session = _make_mock_session(session_id)
    db = _make_upload_db_mock(mock_session)
    app.dependency_overrides[get_db] = lambda: db

    # Latin-1 byte sequence that is invalid in UTF-8
    invalid_utf8 = b"Feature: Login\n  \xff\xfe invalid bytes"

    with patch("app.api.v1.bdd.storage_service.upload_file", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("login.feature", invalid_utf8, "text/plain")},
        )

    assert response.status_code == 422
    db.add.assert_not_called()


def test_upload_bdd_storage_failure_still_succeeds() -> None:
    """Storage failure is non-fatal: HTTP 200 returned, PostgreSQL row persisted."""
    from app.services.storage_service import StorageServiceError

    session_id = str(uuid.uuid4())
    mock_session = _make_mock_session(session_id)
    db = _make_upload_db_mock(mock_session)
    app.dependency_overrides[get_db] = lambda: db

    feature_content = b"Feature: Resilient\n  Scenario: Storage fails gracefully"

    with patch(
        "app.api.v1.bdd.storage_service.upload_file",
        new_callable=AsyncMock,
        side_effect=StorageServiceError("Supabase unreachable", code="STORAGE_ERROR"),
    ):
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("resilient.feature", feature_content, "text/plain")},
        )

    # Storage failure must NOT propagate as an HTTP error
    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == session_id

    # PostgreSQL persistence must still have occurred
    db.add.assert_called_once()
    db.commit.assert_awaited_once()
