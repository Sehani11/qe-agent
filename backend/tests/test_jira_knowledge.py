"""Tests for Jira knowledge ingestion API and service (Story 4.2).

Covers:
- POST /api/v1/knowledge/ingest/jira
  - 200 StreamingResponse for valid project_key
  - 422 when project_key is missing/empty
  - 401 when Authorization header is missing

- knowledge_service.ingest_jira (async generator)
  - Yields progress and complete events on success
  - Namespace isolation: Pinecone upsert uses {user_id}:knowledge
  - JiraServiceError → SSE error event
  - KnowledgeSource DB row created on success (source_type="jira")
  - Empty ticket list → complete with ingested_count=0
  - Cross-user namespace isolation
  - Embed failure → progress event + failed DB row

Strategy:
- Use app.dependency_overrides for get_current_user and get_db.
- Mock jira_service calls with AsyncMock/patch.
- Mock Pinecone to capture namespace and vector IDs.
- Use pytest-asyncio for service generator tests.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.schemas.knowledge import JiraIngestRequest
from app.services.jira_service import JiraServiceError, JiraTicketContent
from app.services.knowledge_service import ingest_jira

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_A = "user-a-id"
USER_B = "user-b-id"


def _make_ticket(
    ticket_id: str = "PROJ-1", summary: str = "Implement login flow"
) -> JiraTicketContent:
    return JiraTicketContent(
        ticket_id=ticket_id,
        summary=summary,
        description="Users need to authenticate via email and password.",
        acceptance_criteria=(
            "Given a valid email and password, "
            "When the user submits the form, "
            "Then they are redirected to the dashboard."
        ),
        labels=["auth", "frontend"],
        linked_issues=[],
    )


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


async def _collect_sse_events(gen) -> list[dict]:
    events = []
    async for chunk in gen:
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Route tests — POST /api/v1/knowledge/ingest/jira
# ---------------------------------------------------------------------------


class TestIngestJiraRoute:
    def test_valid_project_key_returns_200(self, client):
        """POST with valid project_key returns 200 StreamingResponse."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        ticket = _make_ticket()

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ),
        ):
            response = client.post(
                "/api/v1/knowledge/ingest/jira",
                json={"project_key": "PROJ"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_valid_project_key_with_filters_returns_200(self, client):
        """POST with project_key, sprint, and label returns 200."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[]),
            ),
        ):
            response = client.post(
                "/api/v1/knowledge/ingest/jira",
                json={"project_key": "PROJ", "sprint": "Sprint 1", "label": "qa"},
            )

        assert response.status_code == 200

    def test_missing_project_key_returns_422(self, client):
        """POST without project_key (field absent) returns 422."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        response = client.post(
            "/api/v1/knowledge/ingest/jira",
            json={},
        )

        assert response.status_code == 422

    def test_empty_project_key_returns_422(self, client):
        """POST with project_key='' (invalid format) returns 422."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        response = client.post(
            "/api/v1/knowledge/ingest/jira",
            json={"project_key": ""},
        )

        assert response.status_code == 422

    def test_lowercase_project_key_is_normalized_and_accepted(self, client):
        """POST with lowercase project_key is auto-normalized to uppercase."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        with patch(
            "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
            new=AsyncMock(return_value=[]),
        ):
            response = client.post(
                "/api/v1/knowledge/ingest/jira",
                json={"project_key": "proj"},
            )

        assert response.status_code == 200

    def test_missing_auth_returns_401(self):
        """POST without Authorization header returns 401."""
        saved_overrides = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/knowledge/ingest/jira",
                json={"project_key": "PROJ"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "UNAUTHORIZED"
        finally:
            app.dependency_overrides.update(saved_overrides)


# ---------------------------------------------------------------------------
# Service tests — knowledge_service.ingest_jira (async generator)
# ---------------------------------------------------------------------------


class TestIngestJiraService:
    @pytest.mark.asyncio
    async def test_success_yields_progress_and_complete_events(self):
        """Successful ingestion yields progress events and a final complete event."""
        ticket = _make_ticket()
        db = _make_db()
        request = JiraIngestRequest(project_key="PROJ")

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ),
        ):
            events = await _collect_sse_events(ingest_jira(USER_A, request, db))

        types = [e["type"] for e in events]
        assert "progress" in types
        assert "complete" in types
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["ingested_count"] == 1

    @pytest.mark.asyncio
    async def test_namespace_isolation_uses_user_id_knowledge(self):
        """Pinecone upsert namespace is '{user_id}:knowledge' — never dev_user_id."""
        ticket = _make_ticket()
        db = _make_db()
        request = JiraIngestRequest(project_key="PROJ")

        upsert_calls = []

        async def _capture_upsert(
            source_id, chunks, embeddings, namespace, source="jira", **kwargs
        ):
            upsert_calls.append(namespace)

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=_capture_upsert,
            ),
        ):
            async for _ in ingest_jira(USER_A, request, db):
                pass

        assert len(upsert_calls) == 1
        assert upsert_calls[0] == f"{USER_A}:knowledge"
        assert upsert_calls[0] != f"{USER_B}:knowledge"
        assert "dev-stub" not in upsert_calls[0]

    @pytest.mark.asyncio
    async def test_jira_service_error_yields_sse_error_event(self):
        """JiraServiceError produces an SSE error event; generator terminates."""
        db = _make_db()
        request = JiraIngestRequest(project_key="BADPROJECT")

        with patch(
            "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
            new=AsyncMock(
                side_effect=JiraServiceError(
                    "Jira credentials not configured. "
                    "Set JIRA_BASE_URL and JIRA_API_TOKEN.",
                    code="JIRA_NOT_CONFIGURED",
                )
            ),
        ):
            events = await _collect_sse_events(ingest_jira(USER_A, request, db))

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["error"] == "JIRA_NOT_CONFIGURED"
        assert "JIRA_BASE_URL" in error_events[0]["message"]
        assert not any(e["type"] == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_knowledge_source_row_created_on_success(self):
        """KnowledgeSource row added with source_type='jira' and status='completed'."""
        ticket = _make_ticket(ticket_id="PROJ-42", summary="Fix auth bug")
        db = _make_db()
        request = JiraIngestRequest(project_key="PROJ")

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ),
        ):
            async for _ in ingest_jira(USER_A, request, db):
                pass

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.user_id == USER_A
        assert added.source_type == "jira"
        assert added.ingestion_status == "completed"
        assert added.title == "Fix auth bug"
        assert "PROJ-42" in (added.source_url or "")

    @pytest.mark.asyncio
    async def test_empty_ticket_list_yields_complete_with_zero(self):
        """When Jira returns no tickets, complete event has ingested_count=0."""
        db = _make_db()
        request = JiraIngestRequest(project_key="EMPTY")

        with patch(
            "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
            new=AsyncMock(return_value=[]),
        ):
            events = await _collect_sse_events(ingest_jira(USER_A, request, db))

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["ingested_count"] == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_cross_user_namespace_isolation(self):
        """Two concurrent ingestions for different users use distinct namespaces."""
        ticket = _make_ticket()
        db_a = _make_db()
        db_b = _make_db()
        request_a = JiraIngestRequest(project_key="PROJ")
        request_b = JiraIngestRequest(project_key="PROJ")

        namespaces_seen = []

        async def _capture_upsert(
            source_id, chunks, embeddings, namespace, source="jira", **kwargs
        ):
            namespaces_seen.append(namespace)

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=_capture_upsert,
            ),
        ):
            async for _ in ingest_jira(USER_A, request_a, db_a):
                pass
            async for _ in ingest_jira(USER_B, request_b, db_b):
                pass

        assert len(namespaces_seen) == 2
        assert namespaces_seen[0] == f"{USER_A}:knowledge"
        assert namespaces_seen[1] == f"{USER_B}:knowledge"
        assert namespaces_seen[0] != namespaces_seen[1]

    @pytest.mark.asyncio
    async def test_embed_failure_yields_progress_and_creates_failed_record(self):
        """When _embed_chunks raises, a progress event is yielded and a failed
        DB record is created; the generator still yields a complete event."""
        ticket = _make_ticket()
        db = _make_db()
        request = JiraIngestRequest(project_key="PROJ")

        with (
            patch(
                "app.services.knowledge_service.jira_service.fetch_tickets_from_project",
                new=AsyncMock(return_value=[ticket]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(side_effect=RuntimeError("OpenAI quota exceeded")),
            ),
        ):
            events = await _collect_sse_events(ingest_jira(USER_A, request, db))

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["ingested_count"] == 0

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.user_id == USER_A
        assert added.source_type == "jira"
        assert added.ingestion_status == "failed"


# ---------------------------------------------------------------------------
# Jira service unit tests
# ---------------------------------------------------------------------------


class TestJiraServiceHelpers:
    @pytest.mark.asyncio
    async def test_fetch_tickets_raises_when_credentials_missing(self):
        """fetch_tickets_from_project raises JIRA_NOT_CONFIGURED without credentials."""
        from app.core.config import settings
        from app.services.jira_service import fetch_tickets_from_project

        original_base_url = settings.jira_base_url
        original_token = settings.jira_api_token
        settings.jira_base_url = ""
        settings.jira_api_token = ""
        try:
            with pytest.raises(JiraServiceError) as exc_info:
                await fetch_tickets_from_project("PROJ")
            assert exc_info.value.code == "JIRA_NOT_CONFIGURED"
        finally:
            settings.jira_base_url = original_base_url
            settings.jira_api_token = original_token
