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
from app.services.bdd_service import BDDServiceError


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
def configured_llm(monkeypatch):
    """Give the deployment a usable OpenAI credential.

    `/bdd/generate` builds the selected provider up front so that a missing key
    is a 400 naming the variable to set, rather than a 500 several calls deep.
    These tests are about persistence and envelopes, so they simulate the
    ordinary case: a deployment whose key is configured. The 400 itself is
    covered by TestAMissingCredentialIsABadRequest.
    """
    monkeypatch.setattr(
        "app.services.llm.factory.settings.openai_api_key", "sk-test-openai"
    )


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


def test_generate_bdd_persists_acceptance_criteria_and_format() -> None:
    """Story 6.4: the AC that produced the output is stored on the same row.

    Without this the generated Gherkin is an orphan - there is no way to
    reconstruct what input produced it, and no training pair exists.
    """
    session_id = str(uuid.uuid4())
    acceptance_criteria = "AC1: A registered user can log in with valid credentials."
    mock_bdd_response = {
        "scenarios": [
            {
                "source_ac_clause": "AC1",
                "feature": "Login",
                "scenario": "Valid login",
                "given": "Given a registered user",
                "when": "When valid credentials are submitted",
                "then": "Then the dashboard loads",
            }
        ]
    }

    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch("app.services.bdd_service.get_bdd_model_provider") as mock_get_provider:
        mock_provider = AsyncMock()
        mock_provider.generate_bdd.return_value = mock_bdd_response
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/bdd/generate",
            json={
                "session_id": session_id,
                "acceptance_criteria": acceptance_criteria,
            },
        )

    assert response.status_code == 200
    added_obj = mock_db.add.call_args[0][0]
    assert added_obj.acceptance_criteria == acceptance_criteria
    assert added_obj.content_format == "json"


def test_upload_bdd_records_gherkin_content_format() -> None:
    """Uploaded rows hold raw Gherkin, not JSON - the row must say so."""
    session_id = str(uuid.uuid4())
    db = _make_upload_db_mock(_make_mock_session(session_id))
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.api.v1.bdd.storage_service.upload_file", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("x.feature", b"Feature: Login", "text/plain")},
        )

    assert response.status_code == 200
    added_obj = db.add.call_args[0][0]
    assert added_obj.content_format == "gherkin"


# ---------------------------------------------------------------------------
# POST /api/v1/bdd/save  (Story 6.4)
# ---------------------------------------------------------------------------


def _make_save_db_mock(session_obj, parent_row=None) -> AsyncMock:
    """Mock db for the save endpoint: session lookup, then parent lookup."""
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = session_obj
    parent_result = MagicMock()
    parent_result.scalar_one_or_none.return_value = parent_row

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, parent_result])
    db.add = MagicMock()
    return db


def _make_parent_row(acceptance_criteria: str = "AC1: original criteria") -> MagicMock:
    parent = MagicMock()
    parent.id = uuid.uuid4()
    parent.acceptance_criteria = acceptance_criteria
    parent.source = "generated"
    return parent


def test_save_bdd_creates_edited_row_linked_to_generated_parent() -> None:
    """The correction is a NEW row - the generated original must survive."""
    session_id = str(uuid.uuid4())
    parent = _make_parent_row()
    db = _make_save_db_mock(_make_mock_session(session_id), parent_row=parent)
    app.dependency_overrides[get_db] = lambda: db

    edited = "Feature: Login\n\n  # Source AC: AC1\n  Scenario: Corrected by a human"

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": edited},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["source"] == "edited"
    assert data["session_id"] == session_id

    db.add.assert_called_once()
    added_obj = db.add.call_args[0][0]
    assert isinstance(added_obj, BddFile)
    assert added_obj.source == "edited"
    assert added_obj.content_format == "gherkin"
    assert added_obj.content == edited
    assert added_obj.parent_id == parent.id
    # The pair must be self-contained: the edit inherits the parent's input half
    assert added_obj.acceptance_criteria == "AC1: original criteria"
    db.commit.assert_awaited_once()


def test_save_bdd_leaves_the_generated_row_intact() -> None:
    """AC2/AC8: the original must survive — the PAIR is the training signal.

    An implementation that updated the generated row in place would satisfy
    every other test here while destroying exactly what this story exists to
    capture: what the model produced vs. what the human changed it to.
    """
    session_id = str(uuid.uuid4())
    parent = _make_parent_row()
    original_content = "ORIGINAL GENERATED CONTENT"
    parent.content = original_content
    parent_source = parent.source

    db = _make_save_db_mock(_make_mock_session(session_id), parent_row=parent)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: Human rewrote this"},
    )

    assert response.status_code == 200
    # Exactly one row written, and it is the new edited one - not the parent
    db.add.assert_called_once()
    assert db.add.call_args[0][0] is not parent
    # The parent object was never mutated
    assert parent.content == original_content
    assert parent.source == parent_source
    assert parent.acceptance_criteria == "AC1: original criteria"


def test_save_bdd_scopes_parent_lookup_to_the_calling_user() -> None:
    """The parent's AC is copied onto the caller's row, so it must be user-scoped."""
    session_id = str(uuid.uuid4())
    db = _make_save_db_mock(
        _make_mock_session(session_id), parent_row=_make_parent_row()
    )
    app.dependency_overrides[get_db] = lambda: db

    client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: x"},
    )

    # Second execute is the parent lookup; its WHERE clause must mention user_id
    parent_query = str(db.execute.await_args_list[1].args[0])
    assert "user_id" in parent_query
    assert "source" in parent_query


def test_save_bdd_without_generated_parent_still_succeeds() -> None:
    """Editing uploaded content has no generated ancestor - that is valid."""
    session_id = str(uuid.uuid4())
    db = _make_save_db_mock(_make_mock_session(session_id), parent_row=None)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: Uploaded then edited"},
    )

    assert response.status_code == 200
    added_obj = db.add.call_args[0][0]
    assert added_obj.parent_id is None
    assert added_obj.acceptance_criteria is None
    assert added_obj.source == "edited"


def test_save_bdd_returns_403_when_session_owned_by_other_user() -> None:
    """Per-user isolation is enforced in the route layer (no RLS in this repo)."""
    session_id = str(uuid.uuid4())
    other = _make_mock_session(session_id, user_id="someone-else")
    db = _make_save_db_mock(other)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: Nope"},
    )

    assert response.status_code == 403
    db.add.assert_not_called()


def test_save_bdd_returns_404_for_unknown_session() -> None:
    """Unlike /upload, saving an edit must NOT create a session on the fly."""
    session_id = str(uuid.uuid4())
    db = _make_save_db_mock(None)
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: Orphan"},
    )

    assert response.status_code == 404
    db.add.assert_not_called()


def test_save_bdd_returns_413_for_oversized_content() -> None:
    """Reuses the same size ceiling as /upload."""
    from app.api.v1.bdd import _MAX_UPLOAD_BYTES

    session_id = str(uuid.uuid4())
    db = _make_save_db_mock(_make_mock_session(session_id))
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "x" * (_MAX_UPLOAD_BYTES + 1)},
    )

    assert response.status_code == 413
    db.add.assert_not_called()


def test_save_bdd_without_token_returns_401() -> None:
    """POST /bdd/save without Authorization header returns 401."""
    app.dependency_overrides.clear()
    unauthed_client = TestClient(app)

    response = unauthed_client.post(
        "/api/v1/bdd/save",
        json={"session_id": str(uuid.uuid4()), "content": "Feature: x"},
    )

    assert response.status_code == 401
    assert response.json()["error"] == "UNAUTHORIZED"


# ---------------------------------------------------------------------------
# TRAINING_DATA_OPT_IN  (Story 6.6)
# ---------------------------------------------------------------------------


def _generate_and_capture_row(monkeypatch: pytest.MonkeyPatch, opt_in: bool):
    """Run /bdd/generate with the given setting and return the persisted row."""
    monkeypatch.setattr("app.api.v1.bdd.settings.training_data_opt_in", opt_in)
    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    with patch("app.services.bdd_service.get_bdd_model_provider") as mock_get_provider:
        provider = AsyncMock()
        provider.generate_bdd.return_value = {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "F",
                    "scenario": "S",
                    "given": "Given a",
                    "when": "When b",
                    "then": "Then c",
                }
            ]
        }
        mock_get_provider.return_value = provider
        response = client.post(
            "/api/v1/bdd/generate",
            json={"session_id": str(uuid.uuid4()), "acceptance_criteria": "AC1: x"},
        )

    assert response.status_code == 200
    return mock_db.add.call_args[0][0], response


def _upload_and_capture_row(monkeypatch: pytest.MonkeyPatch, opt_in: bool):
    """Run /bdd/upload with the given setting and return the persisted row."""
    monkeypatch.setattr("app.api.v1.bdd.settings.training_data_opt_in", opt_in)
    session_id = str(uuid.uuid4())
    db = _make_upload_db_mock(_make_mock_session(session_id))
    app.dependency_overrides[get_db] = lambda: db

    with patch("app.api.v1.bdd.storage_service.upload_file", new_callable=AsyncMock):
        response = client.post(
            "/api/v1/bdd/upload",
            data={"session_id": session_id},
            files={"file": ("x.feature", b"Feature: Login", "text/plain")},
        )

    assert response.status_code == 200
    return db.add.call_args[0][0], response


def _save_and_capture_row(monkeypatch: pytest.MonkeyPatch, opt_in: bool):
    """Run /bdd/save with the given setting and return the persisted row."""
    monkeypatch.setattr("app.api.v1.bdd.settings.training_data_opt_in", opt_in)
    session_id = str(uuid.uuid4())
    db = _make_save_db_mock(
        _make_mock_session(session_id), parent_row=_make_parent_row()
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client.post(
        "/api/v1/bdd/save",
        json={"session_id": session_id, "content": "Feature: Edited"},
    )

    assert response.status_code == 200
    return db.add.call_args[0][0], response


def test_generated_rows_are_flagged_opted_in_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row, _ = _generate_and_capture_row(monkeypatch, opt_in=True)
    assert row.training_opt_in is True


def test_generated_rows_are_flagged_out_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opting out must not stop the row being written — only its eligibility."""
    row, _ = _generate_and_capture_row(monkeypatch, opt_in=False)
    assert row.training_opt_in is False
    assert row.content  # history is still captured for debugging
    assert row.acceptance_criteria == "AC1: x"


def test_uploaded_rows_respect_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """Uploads are build_dataset.py's DEFAULT source — missing them guts the control."""
    row, _ = _upload_and_capture_row(monkeypatch, opt_in=False)
    assert row.training_opt_in is False

    row_in, _ = _upload_and_capture_row(monkeypatch, opt_in=True)
    assert row_in.training_opt_in is True


def test_edited_rows_respect_the_opt_out(monkeypatch: pytest.MonkeyPatch) -> None:
    row, _ = _save_and_capture_row(monkeypatch, opt_in=False)
    assert row.training_opt_in is False

    row_in, _ = _save_and_capture_row(monkeypatch, opt_in=True)
    assert row_in.training_opt_in is True


def test_opt_in_flag_is_never_exposed_in_api_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Operator configuration must not leak into user-facing payloads."""
    _, gen = _generate_and_capture_row(monkeypatch, opt_in=False)
    _, up = _upload_and_capture_row(monkeypatch, opt_in=False)
    _, sv = _save_and_capture_row(monkeypatch, opt_in=False)

    for response in (gen, up, sv):
        assert "training_opt_in" not in response.text


def test_model_default_marks_rows_opted_in() -> None:
    """Pre-existing rows predate the flag and were captured under always-on.

    Asserts the default is actually TRUE, not merely present - a server_default
    of false would satisfy "is not None" while breaking AC5 entirely.
    """
    column = BddFile.__table__.c.training_opt_in
    assert column.nullable is False
    assert column.server_default is not None
    rendered = str(column.server_default.arg).lower()
    assert "true" in rendered, f"expected a TRUE server default, got {rendered!r}"


def test_omitting_the_flag_inherits_the_configured_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The control must FAIL CLOSED.

    A future write path that forgets to pass training_opt_in must inherit the
    deployment's actual choice, not silently default to "yes, train on this".
    """
    column = BddFile.__table__.c.training_opt_in
    assert column.default is not None, "no Python-side default: control fails open"

    monkeypatch.setattr(
        "app.models.bdd_file.settings.training_data_opt_in", False
    )
    assert column.default.arg(None) is False

    monkeypatch.setattr(
        "app.models.bdd_file.settings.training_data_opt_in", True
    )
    assert column.default.arg(None) is True


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


class TestAMissingCredentialIsABadRequest:
    """A key the operator never set is not a server fault.

    The BDD path hands its selection to the provider layer, which resolves the
    credential several calls deep — where bdd_service's catch-all turned it
    into a 500 titled "Failed to parse or validate model output", about a model
    call that never happened. Every other endpoint gets a 400 naming the
    setting; this one now does too.
    """

    def test_the_selected_provider_without_a_key_is_a_400(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Picking Claude in the model selector on a deployment that only ever
        # had an OpenAI key — the reported case.
        monkeypatch.setattr(
            "app.services.llm.factory.settings.openai_api_key", "sk-test-openai"
        )
        monkeypatch.setattr("app.services.llm.factory.settings.anthropic_api_key", "")
        monkeypatch.setattr("app.services.llm.factory.settings.llm_provider", "openai")
        app.dependency_overrides[get_db] = lambda: _make_mock_db()

        with TestClient(app) as client:
            response = client.post(
                "/api/v1/bdd/generate",
                json={
                    "session_id": str(uuid.uuid4()),
                    "acceptance_criteria": "AC1: a user can log in",
                    "llm_provider": "claude",
                    "llm_model": "claude-sonnet-5",
                },
            )

        assert response.status_code == 400
        # Names the variable to set, rather than describing a parse failure.
        assert "ANTHROPIC_API_KEY" in response.json()["message"]

    def test_the_fine_tuned_path_is_not_blocked_by_a_missing_llm_key(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """It serves its own model over HTTP and needs no LLM credential.
        Refusing here would break a working fine-tuned-only deployment over a
        key it never uses."""
        monkeypatch.setattr("app.services.llm.factory.settings.openai_api_key", "")
        monkeypatch.setattr("app.services.llm.factory.settings.anthropic_api_key", "")
        app.dependency_overrides[get_db] = lambda: _make_mock_db()

        # Fails INSIDE the service, which is the point: the request got that
        # far instead of being refused for a credential it does not need.
        with (
            patch(
                "app.api.v1.bdd.generate_bdd_scenarios",
                AsyncMock(side_effect=BDDServiceError("endpoint down")),
            ) as generate,
            TestClient(app) as client,
        ):
            response = client.post(
                "/api/v1/bdd/generate",
                json={
                    "session_id": str(uuid.uuid4()),
                    "acceptance_criteria": "AC1: a user can log in",
                    "bdd_model_provider": "fine_tuned",
                },
            )

        # Reached the service rather than being refused up front.
        assert generate.await_count == 1
        assert response.status_code != 400


# ---------------------------------------------------------------------------
# Clause coverage on the generation path (Finding 5)
# ---------------------------------------------------------------------------


def _generate_with_scenarios(criteria: str, clauses: list[str]) -> dict:
    """Run /generate with a stubbed model that cites exactly `clauses`."""
    mock_db = _make_mock_db()
    app.dependency_overrides[get_db] = lambda: mock_db

    payload = {
        "scenarios": [
            {
                "source_ac_clause": clause,
                "feature": "Cancellation",
                "scenario": f"Scenario for {clause}",
                "given": "g",
                "when": "w",
                "then": "t",
            }
            for clause in clauses
        ]
    }

    with patch("app.services.bdd_service.get_bdd_model_provider") as mock_get_provider:
        mock_provider = AsyncMock()
        mock_provider.generate_bdd.return_value = payload
        mock_get_provider.return_value = mock_provider

        response = client.post(
            "/api/v1/bdd/generate",
            json={
                "session_id": str(uuid.uuid4()),
                "acceptance_criteria": criteria,
            },
        )

    assert response.status_code == 200
    return response.json()


def test_generate_reports_full_coverage() -> None:
    data = _generate_with_scenarios("AC1: cancel\nAC2: refund", ["AC1", "AC2"])

    assert data["coverage"]["ratio"] == 1.0
    assert data["coverage"]["uncovered"] == []


def test_generate_names_the_clauses_no_scenario_covered() -> None:
    """The failure this exists to catch: a generation that looks complete
    because the clauses it skipped produced no scenarios to notice."""
    criteria = "AC1: cancel\nAC2: refund policy\nAC3: notify both parties"

    data = _generate_with_scenarios(criteria, ["AC1"])

    assert data["coverage"]["total_clauses"] == 3
    assert data["coverage"]["covered_clauses"] == 1
    assert data["coverage"]["uncovered"] == ["AC2", "AC3"]


def test_generate_measures_coverage_against_the_submitted_criteria() -> None:
    """Scored against what the user submitted, not what the model echoed back,
    so a clause lost on the way in still counts against coverage."""
    criteria = (
        "## Business Rules\n"
        "1. cancelled is terminal.\n"
        "\n"
        "## Acceptance Criteria\n"
        "- **AC1** - the candidate can cancel\n"
        "- **AC2** - the interviewer can cancel\n"
    )

    data = _generate_with_scenarios(criteria, ["1. cancelled is terminal."])

    assert data["coverage"]["uncovered"] == ["AC1", "AC2"]
    assert data["coverage"]["ratio"] == 0.0


def test_generate_reports_undefined_coverage_for_unnumbered_prose() -> None:
    """None, not 0.0: the ticket cannot be measured, which is not the same as
    the model having covered nothing."""
    data = _generate_with_scenarios("Let the user cancel a booking.", ["the ticket"])

    assert data["coverage"]["ratio"] is None
    assert data["coverage"]["total_clauses"] == 0


def test_coverage_is_not_part_of_the_model_response_schema() -> None:
    """`BDDGenerateResponse` is handed to the model as `response_format`. If
    coverage leaked into it, the model would be asked to grade itself."""
    from app.schemas.bdd import BDDGenerateResponse

    assert "coverage" not in BDDGenerateResponse.model_json_schema()["properties"]
