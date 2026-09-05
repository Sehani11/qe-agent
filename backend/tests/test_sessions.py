"""Tests for the Sessions API routes.

Covers:
- GET /api/v1/sessions         (paginated list with bdd_status)
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

from app.api.v1.sessions import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
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
    s.jira_ticket_url = "https://acme.atlassian.net/browse/PROJ-1"
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
    # Explicitly None so model_validate doesn't read a MagicMock auto-attr
    # (Story 4.4 added rag_context to StoredVerificationResult).
    vr.rag_context = None
    # Same reason: the verification source columns are nullable, and a row from
    # before they existed reports None rather than a MagicMock.
    vr.verification_mode = None
    vr.github_input = None
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


def _make_list_db(sessions, bdd_rows=(), total=None, verified_ids=()) -> AsyncMock:
    """Mock db for GET /sessions: a scalar() total, then page, bdd and verified.

    `total` defaults to the number of sessions handed in, so tests that do not
    care about paging read naturally; paging tests pass a total larger than the
    page they mock.

    `verified_ids` are session ids that have verification verdicts. The route
    selects a single column, so each row is a one-tuple, as the driver returns.
    """
    db = AsyncMock()
    db.scalar = AsyncMock(return_value=len(sessions) if total is None else total)
    db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result(list(sessions)),
            _make_scalars_result(list(bdd_rows)),
            _make_scalars_result([(sid,) for sid in verified_ids]),
        ]
    )
    return db


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
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["user_id"] == USER_A
    assert str(data["items"][0]["id"]) == SESSION_ID_A
    assert data["items"][0]["jira_ticket_url"] == "https://acme.atlassian.net/browse/PROJ-1"


def test_list_sessions_tolerates_missing_ticket_url(client_as_user_a):
    """Sessions predating the column (and upload-created ones) serialize as null."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_a.jira_ticket_url = None
    db = _make_list_db([session_a])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["items"][0]["jira_ticket_url"] is None


def test_list_sessions_returns_empty_when_no_sessions(client_as_user_a):
    """GET /sessions returns an empty page when the user has no sessions."""
    db = _make_list_db([])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 0


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
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["bdd_status"] == "none"


def test_list_sessions_returns_bdd_status_generated(client_as_user_a):
    """GET /sessions returns bdd_status='generated' when a generated bdd_files row exists."""
    bdd_row = _make_bdd_row(SESSION_ID_A, "generated", datetime(2026, 4, 11, tzinfo=UTC))
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)], [bdd_row])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["items"][0]["bdd_status"] == "generated"


def test_list_sessions_returns_bdd_status_uploaded(client_as_user_a):
    """GET /sessions returns bdd_status='uploaded' when the latest bdd_files row is uploaded."""
    bdd_row = _make_bdd_row(SESSION_ID_A, "uploaded", datetime(2026, 4, 11, tzinfo=UTC))
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)], [bdd_row])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["items"][0]["bdd_status"] == "uploaded"


def test_list_sessions_returns_bdd_status_edited(client_as_user_a):
    """GET /sessions survives an 'edited' latest row (POST /bdd/save writes that source)."""
    bdd_row = _make_bdd_row(SESSION_ID_A, "edited", datetime(2026, 4, 11, tzinfo=UTC))
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)], [bdd_row])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json()["items"][0]["bdd_status"] == "edited"


# ---------------------------------------------------------------------------
# GET /api/v1/sessions — pagination
# ---------------------------------------------------------------------------

def test_list_sessions_defaults_to_first_page(client_as_user_a):
    """Without query params the endpoint reports the default page window."""
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions")

    assert response.status_code == 200
    data = response.json()
    assert data["limit"] == DEFAULT_PAGE_SIZE
    assert data["offset"] == 0


def test_list_sessions_applies_limit_and_offset_to_the_query(client_as_user_a):
    """limit/offset reach SQL — not applied in Python after loading every row."""
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)], total=57)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions?limit=5&offset=10")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 57
    assert data["limit"] == 5
    assert data["offset"] == 10

    page_query = db.execute.await_args_list[0].args[0]
    sql = str(page_query.compile(compile_kwargs={"literal_binds": True}))
    assert "LIMIT 5" in sql
    assert "OFFSET 10" in sql


def test_list_sessions_total_counts_all_sessions_not_just_the_page(client_as_user_a):
    """total is the user's whole session count, so the client can size the pager."""
    page = [_make_session(SESSION_ID_A, USER_A), _make_session(SESSION_ID_B, USER_A)]
    db = _make_list_db(page, total=42)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions?limit=2")

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    assert data["total"] == 42


def test_list_sessions_offset_past_the_end_returns_empty_page_with_total(client_as_user_a):
    """An out-of-range offset still reports total, so the client can step back."""
    db = _make_list_db([], total=3)
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/sessions?offset=100")

    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total"] == 3


@pytest.mark.parametrize(
    "query",
    ["limit=0", "limit=-1", f"limit={MAX_PAGE_SIZE + 1}", "offset=-1", "limit=abc"],
)
def test_list_sessions_rejects_out_of_range_paging(client_as_user_a, query):
    """Paging params are validated rather than silently clamped."""
    db = _make_list_db([_make_session(SESSION_ID_A, USER_A)])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions?{query}")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/sessions/{session_id} — get single session
# ---------------------------------------------------------------------------

def test_get_session_returns_session_for_owner(client_as_user_a):
    """GET /sessions/{id} returns the session when the requester is the owner."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_result = _make_scalars_result([session_a])
    bdd_result = _make_scalars_result([])  # no bdd_files → bdd_status="none"
    # No verification rows either → verification_status="none".
    verified_result = _make_scalars_result([])

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[session_result, bdd_result, verified_result]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}")

    assert response.status_code == 200
    data = response.json()
    assert str(data["id"]) == SESSION_ID_A
    assert data["user_id"] == USER_A
    assert data["bdd_status"] == "none"
    # The submitted URL round-trips so the ticket field can be restored on revisit.
    assert data["jira_ticket_url"] == "https://acme.atlassian.net/browse/PROJ-1"


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


def test_get_session_verification_results_includes_rag_context(client_as_user_a):
    """Story 4.4: stored rag_context is returned so it renders on session revisit."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    vr = _make_verification_result(SESSION_ID_A)
    vr.rag_context = [
        {
            "source": "confluence",
            "source_id": "12345",
            "snippet": "Architecture decision: JWT with RS256.",
            "title": "Auth Design",
            "url": "https://wiki.example.com/pages/12345",
        }
    ]

    session_result = _make_scalars_result([session_a])
    vr_result = _make_scalars_result([vr])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, vr_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    rag = data[0]["rag_context"]
    assert rag is not None
    assert rag[0]["source"] == "confluence"
    assert rag[0]["title"] == "Auth Design"
    assert rag[0]["url"] == "https://wiki.example.com/pages/12345"


def test_get_session_verification_results_rag_context_null_when_absent(client_as_user_a):
    """Story 4.4: rag_context is null in the payload when the row has none."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    vr = _make_verification_result(SESSION_ID_A)  # rag_context defaults to None

    session_result = _make_scalars_result([session_a])
    vr_result = _make_scalars_result([vr])

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[session_result, vr_result])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    assert response.json()[0]["rag_context"] is None


def test_get_session_verification_results_includes_verification_source(client_as_user_a):
    """A revisited session can see, and restore, what the run was checked against.

    `github_links` cannot stand in for this: those are links the LLM cited for a
    single scenario, not the source the run was scoped to.
    """
    session_a = _make_session(SESSION_ID_A, USER_A)
    vr = _make_verification_result(SESSION_ID_A)
    vr.verification_mode = "exact_files"
    vr.github_input = (
        "https://github.com/org/repo/blob/main/a.py\n"
        "https://github.com/org/repo/blob/main/b.py"
    )

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_make_scalars_result([session_a]), _make_scalars_result([vr])]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["verification_mode"] == "exact_files"
    assert row["github_input"].splitlines() == [
        "https://github.com/org/repo/blob/main/a.py",
        "https://github.com/org/repo/blob/main/b.py",
    ]


def test_get_session_verification_results_source_null_when_absent(client_as_user_a):
    """Rows written before these columns report null rather than failing to serialise."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    vr = _make_verification_result(SESSION_ID_A)  # source defaults to None

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_make_scalars_result([session_a]), _make_scalars_result([vr])]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}/verification-results")

    assert response.status_code == 200
    row = response.json()[0]
    assert row["verification_mode"] is None
    assert row["github_input"] is None


# ---------------------------------------------------------------------------
# verification_status — has this session produced any verdicts?
# ---------------------------------------------------------------------------


def test_list_reports_verification_completed(client_as_user_a):
    """A session with verdicts is reported as verified."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    db = _make_list_db([session_a], verified_ids=[SESSION_ID_A])
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get("/api/v1/sessions").json()

    assert body["items"][0]["verification_status"] == "completed"


def test_list_reports_no_verification_when_there_are_no_verdicts(client_as_user_a):
    session_a = _make_session(SESSION_ID_A, USER_A)
    db = _make_list_db([session_a], verified_ids=[])
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get("/api/v1/sessions").json()

    assert body["items"][0]["verification_status"] == "none"


def test_verification_status_is_per_session_not_per_page(client_as_user_a):
    """One verified session must not mark the rest of the page verified.

    The status is built from a single batched query, so a bug there would apply
    one row's answer to every session on the page.
    """
    session_a = _make_session(SESSION_ID_A, USER_A)
    session_b = _make_session(SESSION_ID_B, USER_A)
    db = _make_list_db([session_a, session_b], verified_ids=[SESSION_ID_B])
    app.dependency_overrides[get_db] = lambda: db

    items = client_as_user_a.get("/api/v1/sessions").json()["items"]
    by_id = {item["id"]: item["verification_status"] for item in items}

    assert by_id[SESSION_ID_A] == "none"
    assert by_id[SESSION_ID_B] == "completed"


def test_verification_status_is_independent_of_bdd_status(client_as_user_a):
    """Verification does not overwrite what the BDD state is.

    They are separate fields precisely so a verified session still reports the
    BDD it was verified against — folding them into one would lose that.
    """
    session_a = _make_session(SESSION_ID_A, USER_A)
    bdd_row = MagicMock()
    bdd_row.session_id = SESSION_ID_A
    bdd_row.source = "edited"
    bdd_row.created_at = datetime(2026, 4, 11, tzinfo=UTC)
    db = _make_list_db([session_a], bdd_rows=[bdd_row], verified_ids=[SESSION_ID_A])
    app.dependency_overrides[get_db] = lambda: db

    item = client_as_user_a.get("/api/v1/sessions").json()["items"][0]

    assert item["verification_status"] == "completed"
    assert item["bdd_status"] == "edited"


def test_get_session_reports_verification_status(client_as_user_a):
    """The detail endpoint answers the same question as the list."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([session_a]),
            _make_scalars_result([]),                 # no bdd rows
            _make_scalars_result([uuid.uuid4()]),     # one verdict exists
        ]
    )
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get(f"/api/v1/sessions/{SESSION_ID_A}").json()

    assert body["verification_status"] == "completed"


# ---------------------------------------------------------------------------
# DELETE /api/v1/sessions/{session_id}
# ---------------------------------------------------------------------------

def test_delete_session_removes_session_and_related_rows(client_as_user_a):
    """Owner can delete their session; its bdd/verification/chat rows go too."""
    session_a = _make_session(SESSION_ID_A, USER_A)
    db = AsyncMock()
    db.delete = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([session_a]),  # lookup
            MagicMock(),                        # delete bdd_files
            MagicMock(),                        # delete verification_results
            MagicMock(),                        # delete chat_messages
        ]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(f"/api/v1/sessions/{SESSION_ID_A}")

    assert response.status_code == 204
    db.delete.assert_awaited_once_with(session_a)
    db.commit.assert_awaited_once()


def test_delete_session_returns_404_for_nonexistent_session(client_as_user_a):
    """DELETE /sessions/{id} returns 404 when the session does not exist."""
    db = _make_db_mock([])  # scalar_one_or_none returns None
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(f"/api/v1/sessions/{str(uuid.uuid4())}")

    assert response.status_code == 404
    assert "not found" in response.json()["message"].lower()


def test_delete_session_returns_403_for_other_users_session(client_as_user_a):
    """DELETE /sessions/{id} returns 403 when the session belongs to another user."""
    session_b = _make_session(SESSION_ID_B, USER_B)
    db = _make_db_mock([session_b])
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(f"/api/v1/sessions/{SESSION_ID_B}")

    assert response.status_code == 403
    assert "access denied" in response.json()["message"].lower()
    db.delete.assert_not_called()


# ---------------------------------------------------------------------------
# DELETE /api/v1/sessions — bulk delete
# ---------------------------------------------------------------------------

def test_delete_all_sessions_removes_every_session_and_related_rows(client_as_user_a):
    """DELETE /sessions removes every session for the user and its related rows."""
    ids = [uuid.UUID(SESSION_ID_A), uuid.UUID(SESSION_ID_B)]
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result(ids),  # id lookup
            MagicMock(),  # delete bdd_files
            MagicMock(),  # delete verification_results
            MagicMock(),  # delete chat_messages
            MagicMock(),  # delete sessions
        ]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    db.commit.assert_awaited_once()


def test_delete_all_sessions_reports_zero_when_none_exist(client_as_user_a):
    """DELETE /sessions with nothing to delete is a success, not a 404."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete("/api/v1/sessions")

    assert response.status_code == 200
    assert response.json() == {"deleted": 0}
    db.commit.assert_not_awaited()


def test_delete_all_sessions_accepts_project_filter(client_as_user_a):
    """project_id narrows which sessions get removed, matching the list route."""
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _make_scalars_result([uuid.UUID(SESSION_ID_A)]),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
        ]
    )
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(
        "/api/v1/sessions", params={"project_id": str(uuid.uuid4())}
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": 1}
