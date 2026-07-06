"""Tests for the Sessions API routes.

Covers:
- GET /api/v1/sessions         (list sessions with bdd_status)
- GET /api/v1/sessions/{id}    (get single session)
- GET /api/v1/sessions/{id}/bdd
- GET /api/v1/sessions/{id}/verification-results

Strategy:
- Use app.dependency_overrides for both get_current_user and get_db.
- Mock db.execute to return controlled result sets.
- Verify data isolation: users can only access their own sessions.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_A = "user-a-id"
USER_B = "user-b-id"
SESSION_ID_A = str(uuid.uuid4())
SESSION_ID_B = str(uuid.uuid4())
BDD_FILE_ID = str(uuid.uuid4())
VR_ID = str(uuid.uuid4())
SCENARIO_ID = str(uuid.uuid4())


def _make_session(session_id: str, user_id: str) -> MagicMock:
    """Build a mock Session ORM object."""
    s = MagicMock()
    s.id = uuid.UUID(session_id)
    s.user_id = user_id
    s.jira_ticket_id = "PROJ-1"
    s.created_at = datetime(2026, 4, 11, tzinfo=UTC)
    return s


def _make_bdd_file(session_id: str, source: str = "generated") -> MagicMock:
    """Build a mock BddFile ORM object."""
    b = MagicMock()
    b.id = uuid.UUID(BDD_FILE_ID)
    b.session_id = session_id
    b.user_id = USER_A
    b.content = '{"scenarios": []}'
    b.source = source
    b.created_at = datetime(2026, 4, 11, tzinfo=UTC)
    return b


def _make_bdd_row(session_id: str, source: str, created_at: datetime) -> MagicMock:
    """Build a lightweight mock row for the BDD status query (session_id, source, created_at)."""
    row = MagicMock()
    row.session_id = session_id
    row.source = source
    row.created_at = created_at
    return row


def _make_verification_result(session_id: str) -> MagicMock:
    """Build a mock VerificationResult ORM object."""
    vr = MagicMock()
    vr.id = uuid.UUID(VR_ID)
    vr.session_id = session_id
    vr.scenario_id = uuid.UUID(SCENARIO_ID)
    vr.scenario_title = "Scenario 1"
    vr.status = "pass"
    vr.justification = "All checks passed."
    vr.code_reference = {"file": "auth.py", "line": 42}
    vr.github_links = []
    vr.implementation_suggestion = None
    vr.created_at = datetime(2026, 4, 11, tzinfo=UTC)
    return vr


class _MultiExecuteMock:
    """AsyncMock db that returns different results for sequential execute() calls."""

    def __init__(self, results: list):
        self._results = results
        self._call_count = 0

    async def execute(self, *_args, **_kwargs):
        idx = self._call_count
        self._call_count += 1
        return self._results[idx % len(self._results)]


def _make_scalars_result(items) -> MagicMock:
    """Wrap items in a mock result with .scalars().all() and .scalar_one_or_none()."""
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = items

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_result.scalar_one_or_none.return_value = items[0] if items else None
    mock_result.all.return_value = items
    return mock_result


def _make_db_mock(scalars_result) -> AsyncMock:
    """Return an AsyncMock db that yields scalars_result from execute()."""
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = scalars_result

    mock_scalar_one_or_none = scalars_result[0] if scalars_result else None

    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    mock_result.scalar_one_or_none.return_value = mock_scalar_one_or_none

    db = AsyncMock()
    db.execute = AsyncMock(return_value=mock_result)
    return db


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client_as_user_a():
    """TestClient authenticated as USER_A."""
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# GET /api/v1/sessions — list sessions
# ---------------------------------------------------------------------------

def test_list_sessions_returns_only_current_users_sessions(client_as_user_a):
    """GET /sessions returns only the authenticated user's sessions."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    sessions_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([])  # no bdd files

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[sessions_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["user_id"] == USER_A
    assert str(data[0]["id"]) == SESSION_ID_A


def test_list_sessions_returns_empty_when_no_sessions(client_as_user_a):
    """GET /sessions returns an empty list when user has no sessions."""
    db = _make_db_mock([])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json() == []


def test_list_sessions_without_token_returns_401():
    """GET /sessions without Authorization header returns 401."""
    saved_overrides = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    try:
        client = TestClient(app)
        response = client.get("/api/v1/sessions")
        assert response.status_code == 401
        assert response.json()["error"] == "UNAUTHORIZED"
    finally:
        app.dependency_overrides.update(saved_overrides)


def test_list_sessions_returns_bdd_status_none_when_no_bdd_files(client_as_user_a):
    """GET /sessions returns bdd_status='none' when no bdd_files exist for the session."""
    session_a = _make_session(SESSION_ID_A, USER_A)

    # First execute call: sessions query; second: bdd_files query (empty)
    sessions_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[sessions_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["bdd_status"] == "none"


def test_list_sessions_returns_bdd_status_generated(client_as_user_a):
    """GET /sessions returns bdd_status='generated' when a generated bdd_files row exists."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    bdd_row = _make_bdd_row(SESSION_ID_A, "generated", datetime(2026, 4, 11, tzinfo=UTC))

    sessions_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([bdd_row])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[sessions_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["bdd_status"] == "generated"


def test_list_sessions_returns_bdd_status_uploaded(client_as_user_a):
    """GET /sessions returns bdd_status='uploaded' when the latest bdd_files row is uploaded."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    bdd_row = _make_bdd_row(SESSION_ID_A, "uploaded", datetime(2026, 4, 11, tzinfo=UTC))

    sessions_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([bdd_row])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[sessions_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()[0]["bdd_status"] == "uploaded"


# ---------------------------------------------------------------------------
# GET /api/v1/sessions/{session_id} — get single session
# ---------------------------------------------------------------------------

def test_get_session_returns_session_for_owner(client_as_user_a):
    """GET /sessions/{id} returns the session when the requester is the owner."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([])  # no bdd_files → bdd_status="none"

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}")

    assert response.status_code == 200
    data = response.json()
    assert str(data["id"]) == SESSION_ID_A
    assert data["user_id"] == USER_A
    assert data["bdd_status"] == "none"


def test_get_session_returns_404_for_nonexistent_session(client_as_user_a):
    """GET /sessions/{id} returns 404 when the session does not exist."""
    db = _make_db_mock([])  # scalar_one_or_none returns None
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{str(uuid.uuid4())}")

    assert response.status_code == 404
    data = response.json()
    assert "not found" in data["message"].lower()
    assert data["code"] == 404


def test_get_session_returns_403_for_other_users_session(client_as_user_a):
    """GET /sessions/{id} returns 403 when the session belongs to a different user (data isolation)."""
    session_b = _make_session(SESSION_ID_B, USER_B)  # owned by USER_B
    db = _make_db_mock([session_b])
    app.dependency_overrides[get_db] = lambda: db

    # USER_A requests USER_B's session
    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_B}")

    assert response.status_code == 403
    data = response.json()
    assert "access denied" in data["message"].lower()
    assert data["code"] == 403


# ---------------------------------------------------------------------------
# GET /api/v1/sessions/{session_id}/bdd
# ---------------------------------------------------------------------------

def test_get_session_bdd_returns_content_for_owner(client_as_user_a):
    """GET /sessions/{id}/bdd returns SessionBDDResponse for the session owner."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    bdd_file = _make_bdd_file(SESSION_ID_A, "generated")

    # First execute: session lookup; second: bdd_files lookup
    session_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([bdd_file])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/bdd")

    assert response.status_code == 200
    data = response.json()
    assert data["session_id"] == SESSION_ID_A
    assert data["source"] == "generated"
    assert data["content"] == '{"scenarios": []}'


def test_get_session_bdd_returns_403_for_non_owner(client_as_user_a):
    """GET /sessions/{id}/bdd returns 403 when requester is not the session owner."""
    session_b = _make_session(SESSION_ID_B, USER_B)
    session_result = _make_scalars_result([session_b])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=session_result)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_B}/bdd")

    assert response.status_code == 403
    assert "access denied" in response.json()["message"].lower()


def test_get_session_bdd_returns_404_when_session_not_found(client_as_user_a):
    """GET /sessions/{id}/bdd returns 404 when session does not exist."""
    session_result = _make_scalars_result([])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=session_result)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{str(uuid.uuid4())}/bdd")

    assert response.status_code == 404
    assert "not found" in response.json()["message"].lower()


def test_get_session_bdd_returns_404_when_no_bdd_content(client_as_user_a):
    """GET /sessions/{id}/bdd returns 404 when session exists but has no BDD content."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([])  # no bdd_files

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, bdd_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/bdd")

    assert response.status_code == 404
    assert "no bdd content" in response.json()["message"].lower()


# ---------------------------------------------------------------------------
# GET /api/v1/sessions/{session_id}/verification-results
# ---------------------------------------------------------------------------

def test_get_session_verification_results_returns_results_for_owner(client_as_user_a):
    """GET /sessions/{id}/verification-results returns results list for the owner."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    vr = _make_verification_result(SESSION_ID_A)

    session_result = _make_scalars_result([session_a])
    vr_result = _make_scalars_result([vr])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, vr_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["session_id"] == SESSION_ID_A
    assert data[0]["status"] == "pass"
    assert data[0]["scenario_title"] == "Scenario 1"


def test_get_session_verification_results_returns_empty_list_when_none(client_as_user_a):
    """GET /sessions/{id}/verification-results returns [] when no results exist."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_result = _make_scalars_result([session_a])
    vr_result = _make_scalars_result([])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, vr_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    assert response.json() == []


def test_get_session_verification_results_returns_403_for_non_owner(client_as_user_a):
    """GET /sessions/{id}/verification-results returns 403 for non-owner."""
    session_b = _make_session(SESSION_ID_B, USER_B)
    session_result = _make_scalars_result([session_b])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=session_result)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_B}/verification-results")

    assert response.status_code == 403
    assert "access denied" in response.json()["message"].lower()


def test_get_session_verification_results_returns_404_when_session_not_found(client_as_user_a):
    """GET /sessions/{id}/verification-results returns 404 when session does not exist."""
    session_result = _make_scalars_result([])

    db = AsyncMock()
    db.execute = AsyncMock(return_value=session_result)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{str(uuid.uuid4())}/verification-results")

    assert response.status_code == 404
    assert "not found" in response.json()["message"].lower()
