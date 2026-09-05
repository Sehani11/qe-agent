"""Tests for the Knowledge Base API and service (Story 4.1).

Covers:
- POST /api/v1/knowledge/ingest/confluence
  - 200 StreamingResponse for valid space_key
  - 200 StreamingResponse for valid page_id
  - 422 when neither space_key nor page_id is provided
  - 401 when Authorization header is missing

- knowledge_service.ingest_confluence (async generator)
  - Yields progress and complete events on success
  - Namespace isolation: Pinecone upsert uses {user_id}:knowledge
  - ConfluenceServiceError → SSE error event
  - KnowledgeSource DB row created on success
  - KnowledgeSource DB row created with status=failed on embed error

- confluence_service
  - HTML stripping removes tags and decodes entities
  - Missing credentials → ConfluenceServiceError(code=CONFLUENCE_NOT_CONFIGURED)

Strategy:
- Use app.dependency_overrides for get_current_user and get_db.
- Mock confluence_service calls with AsyncMock/patch.
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
from app.schemas.knowledge import ConfluenceIngestRequest
from app.services.confluence_service import ConfluencePage, ConfluenceServiceError
from app.services.knowledge_service import ingest_confluence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

USER_A = "user-a-id"
USER_B = "user-b-id"


def _make_confluence_page(
    page_id: str = "101", title: str = "Architecture Overview"
) -> ConfluencePage:
    return ConfluencePage(
        id=page_id,
        title=title,
        url=f"https://example.atlassian.net/wiki/spaces/viewpage.action?pageId={page_id}",
        body="This document describes the architecture of our system.",
    )


def _make_db() -> MagicMock:
    """Return a mock AsyncSession that supports add() and commit()."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


async def _collect_sse_events(gen) -> list[dict]:
    """Drain an SSE async generator and return parsed JSON events."""
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
    """TestClient authenticated as USER_A."""

    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


# ---------------------------------------------------------------------------
# Route tests — POST /api/v1/knowledge/ingest/confluence
# ---------------------------------------------------------------------------


class TestIngestConfluenceRoute:
    def test_valid_space_key_returns_200(self, client):
        """POST with valid space_key returns 200 StreamingResponse."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        page = _make_confluence_page()

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
                new=AsyncMock(return_value=[page]),
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
                "/api/v1/knowledge/ingest/confluence",
                json={"space_key": "MYSPACE"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_valid_page_id_returns_200(self, client):
        """POST with valid page_id returns 200 StreamingResponse."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        page = _make_confluence_page()

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_page_by_id",
                new=AsyncMock(return_value=page),
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
                "/api/v1/knowledge/ingest/confluence",
                json={"page_id": "101"},
            )

        assert response.status_code == 200

    def test_missing_space_key_and_page_id_returns_422(self, client):
        """POST without space_key or page_id returns 422 with error envelope."""
        db = _make_db()
        app.dependency_overrides[get_db] = lambda: db

        response = client.post(
            "/api/v1/knowledge/ingest/confluence",
            json={},
        )

        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "INVALID_REQUEST"
        assert "space_key" in body["message"] or "page_id" in body["message"]

    def test_missing_auth_returns_401(self):
        """POST without Authorization header returns 401."""
        saved_overrides = dict(app.dependency_overrides)
        app.dependency_overrides.clear()
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/knowledge/ingest/confluence",
                json={"space_key": "MYSPACE"},
            )
            assert response.status_code == 401
            assert response.json()["error"] == "UNAUTHORIZED"
        finally:
            app.dependency_overrides.update(saved_overrides)


# ---------------------------------------------------------------------------
# Service tests — knowledge_service.ingest_confluence (async generator)
# ---------------------------------------------------------------------------


class TestIngestConfluenceService:
    @pytest.mark.asyncio
    async def test_success_yields_progress_and_complete_events(self):
        """Successful ingestion yields progress events and a final complete event."""
        page = _make_confluence_page()
        db = _make_db()
        request = ConfluenceIngestRequest(space_key="MYSPACE")

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
                new=AsyncMock(return_value=[page]),
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
            events = await _collect_sse_events(
                ingest_confluence(USER_A, request, db)
            )

        types = [e["type"] for e in events]
        assert "progress" in types
        assert "complete" in types
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["ingested_count"] == 1

    @pytest.mark.asyncio
    async def test_namespace_isolation_uses_user_id_knowledge(self):
        """Pinecone upsert namespace is '{user_id}:knowledge' — never dev_user_id."""
        page = _make_confluence_page()
        db = _make_db()
        request = ConfluenceIngestRequest(space_key="MYSPACE")

        upsert_calls = []

        async def _capture_upsert(
            source_id, chunks, embeddings, namespace, source="confluence", **kwargs
        ):
            upsert_calls.append(namespace)

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
                new=AsyncMock(return_value=[page]),
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
            async for _ in ingest_confluence(USER_A, request, db):
                pass

        assert len(upsert_calls) == 1
        assert upsert_calls[0] == f"{USER_A}:knowledge"
        # Critically: must NOT use dev_user_id or another user's namespace
        assert upsert_calls[0] != f"{USER_B}:knowledge"
        assert "dev-stub" not in upsert_calls[0]

    @pytest.mark.asyncio
    async def test_confluence_service_error_yields_sse_error_event(self):
        """ConfluenceServiceError produces an SSE error event; generator terminates."""
        db = _make_db()
        request = ConfluenceIngestRequest(space_key="BADSPACE")

        with patch(
            "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
            new=AsyncMock(
                side_effect=ConfluenceServiceError(
                    "Space not found.", code="CONFLUENCE_FETCH_FAILED"
                )
            ),
        ):
            events = await _collect_sse_events(
                ingest_confluence(USER_A, request, db)
            )

        error_events = [e for e in events if e["type"] == "error"]
        assert len(error_events) == 1
        assert error_events[0]["error"] == "CONFLUENCE_FETCH_FAILED"
        assert "Space not found" in error_events[0]["message"]
        # No complete event after an early error
        assert not any(e["type"] == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_knowledge_source_row_created_on_success(self):
        """KnowledgeSource row is added to DB with status='completed' on success."""
        page = _make_confluence_page(page_id="201", title="Design Guide")
        db = _make_db()
        request = ConfluenceIngestRequest(page_id="201")

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_page_by_id",
                new=AsyncMock(return_value=page),
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
            async for _ in ingest_confluence(USER_A, request, db):
                pass

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.user_id == USER_A
        assert added.source_type == "confluence"
        assert added.ingestion_status == "completed"
        assert added.title == "Design Guide"
        # Story 4.7: the Pinecone vector-id prefix (page id) MUST be persisted,
        # or the source's vectors can't be deleted later (they'd be orphaned).
        assert added.source_ref == "201"

    @pytest.mark.asyncio
    async def test_empty_page_list_yields_complete_with_zero(self):
        """When Confluence returns no pages, complete event has ingested_count=0."""
        db = _make_db()
        request = ConfluenceIngestRequest(space_key="EMPTYSPACE")

        with patch(
            "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
            new=AsyncMock(return_value=[]),
        ):
            events = await _collect_sse_events(
                ingest_confluence(USER_A, request, db)
            )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["ingested_count"] == 0
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_embed_failure_yields_progress_and_creates_failed_record(self):
        """When _embed_chunks raises, a progress event is yielded, a failed DB
        record is created, and the generator still yields a complete event."""
        page = _make_confluence_page()
        db = _make_db()
        request = ConfluenceIngestRequest(space_key="MYSPACE")

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
                new=AsyncMock(return_value=[page]),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(side_effect=RuntimeError("OpenAI quota exceeded")),
            ),
        ):
            events = await _collect_sse_events(
                ingest_confluence(USER_A, request, db)
            )

        complete_events = [e for e in events if e["type"] == "complete"]
        assert len(complete_events) == 1
        assert complete_events[0]["ingested_count"] == 0

        db.add.assert_called_once()
        added = db.add.call_args[0][0]
        assert added.user_id == USER_A
        assert added.ingestion_status == "failed"

    @pytest.mark.asyncio
    async def test_cross_user_namespace_isolation(self):
        """Two concurrent ingestions for different users use distinct namespaces."""
        page = _make_confluence_page()
        db_a = _make_db()
        db_b = _make_db()
        request_a = ConfluenceIngestRequest(space_key="SPACE")
        request_b = ConfluenceIngestRequest(space_key="SPACE")

        namespaces_seen = []

        async def _capture_upsert(
            source_id, chunks, embeddings, namespace, source="confluence", **kwargs
        ):
            namespaces_seen.append(namespace)

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_pages_from_space",
                new=AsyncMock(return_value=[page]),
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
            async for _ in ingest_confluence(USER_A, request_a, db_a):
                pass
            async for _ in ingest_confluence(USER_B, request_b, db_b):
                pass

        assert len(namespaces_seen) == 2
        assert namespaces_seen[0] == f"{USER_A}:knowledge"
        assert namespaces_seen[1] == f"{USER_B}:knowledge"
        assert namespaces_seen[0] != namespaces_seen[1]


# ---------------------------------------------------------------------------
# Confluence service unit tests
# ---------------------------------------------------------------------------


class TestConfluenceServiceHelpers:
    def test_strip_html_removes_tags(self):
        """_strip_html strips all HTML tags from the raw body."""
        from app.services.confluence_service import _strip_html

        result = _strip_html("<p>Hello <strong>world</strong></p>")
        assert result == "Hello world"

    def test_strip_html_decodes_entities(self):
        """_strip_html decodes HTML entities like &amp; and &lt;."""
        from app.services.confluence_service import _strip_html

        result = _strip_html("&lt;code&gt;x &amp; y&lt;/code&gt;")
        assert result == "<code>x & y</code>"

    def test_strip_html_empty_string(self):
        """_strip_html handles empty input without error."""
        from app.services.confluence_service import _strip_html

        assert _strip_html("") == ""

    @pytest.mark.asyncio
    async def test_fetch_pages_raises_when_credentials_missing(self):
        """fetch_pages_from_space raises when credentials are not configured."""
        from app.core.config import settings
        from app.services.confluence_service import fetch_pages_from_space

        original_base_url = settings.confluence_base_url
        settings.confluence_base_url = ""
        try:
            with pytest.raises(ConfluenceServiceError) as exc_info:
                await fetch_pages_from_space("MYSPACE")
            assert exc_info.value.code == "CONFLUENCE_NOT_CONFIGURED"
        finally:
            settings.confluence_base_url = original_base_url

    @pytest.mark.asyncio
    async def test_fetch_page_by_id_raises_when_credentials_missing(self):
        """fetch_page_by_id raises CONFLUENCE_NOT_CONFIGURED without credentials."""
        from app.core.config import settings
        from app.services.confluence_service import fetch_page_by_id

        original_token = settings.confluence_api_token
        settings.confluence_api_token = ""
        try:
            with pytest.raises(ConfluenceServiceError) as exc_info:
                await fetch_page_by_id("101")
            assert exc_info.value.code == "CONFLUENCE_NOT_CONFIGURED"
        finally:
            settings.confluence_api_token = original_token
