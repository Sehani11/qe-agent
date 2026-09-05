"""Tests for the ingestion API endpoint."""

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.models.project import Project
from app.services.jira_service import JiraTicketContent, JiraServiceError
from app.services.vector_service import VectorServiceError


def _stub_project() -> Project:
    """A project with no credentials, so resolution falls back to the env.

    The db in these tests is an AsyncMock, so a real `ensure_project` would
    return a coroutine where a Project is expected. Patching it keeps these
    tests about ingestion.
    """
    return Project(id=uuid.uuid4(), user_id="test-user", name="Project-1")


async def _mock_auth() -> str:
    return "dev-stub"


@pytest.fixture
def client():
    """TestClient fixture with auth bypassed."""
    app.dependency_overrides[get_current_user] = _mock_auth
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_ingest_ticket_success(client):
    """Test successful Jira ticket ingestion with a standard JSON response."""
    mock_ticket = JiraTicketContent(
        ticket_id="PROJ-123",
        summary="Test Summary",
        description="Test description",
        acceptance_criteria="Given it works, Then profit",
        labels=["test"],
        linked_issues=[]
    )
    
    with patch(
        "app.api.v1.ingestion.ensure_project",
        new_callable=AsyncMock,
        return_value=_stub_project(),
    ), patch("app.api.v1.ingestion.fetch_ticket_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_ticket
        
        with patch("app.api.v1.ingestion.embed_and_index_ticket", new_callable=AsyncMock) as mock_embed:
            
            # We mock the get_db dependency to skip actual DB insert in end-to-end API test,
            # or mock the db.add and db.commit inside the route.
            with patch("app.api.v1.ingestion.Session") as mock_session_class:
                from unittest.mock import MagicMock
                mock_db = AsyncMock()
                mock_db.add = MagicMock()
                from app.core.database import get_db
                app.dependency_overrides[get_db] = lambda: mock_db
                
                # Mock the Session ID
                mock_session_instance = mock_session_class.return_value
                mock_session_instance.id = "c3f8e5ee-e64d-452f-8a0f-155554f67c9c"

                response = client.post(
                    "/api/v1/ingestion/ingest",
                    json={"ticket_id_or_url": "PROJ-123"}
                )
                
                app.dependency_overrides.clear()
                
                assert response.status_code == 200
                payload = response.json()
                assert payload["session_id"] == "c3f8e5ee-e64d-452f-8a0f-155554f67c9c"
                assert payload["jira_ticket_id"] == "PROJ-123"
                assert payload["jira_ticket_url"] == "PROJ-123"
                assert payload["acceptance_criteria"] == "Given it works, Then profit"
                assert payload["status"] == "ready_for_bdd"
                
                # Verify DB insertion calls. The session is stamped with a
                # project: sessions.project_id is NOT NULL, so ingestion has to
                # resolve one before it can insert.
                assert mock_session_class.call_count == 1
                kwargs = mock_session_class.call_args.kwargs
                assert kwargs["user_id"] == "dev-stub"
                assert kwargs["jira_ticket_id"] == "PROJ-123"
                assert kwargs["jira_ticket_url"] == "PROJ-123"
                assert kwargs["project_id"] is not None
                mock_db.add.assert_called_once()
                mock_db.commit.assert_awaited_once()


def test_ingest_ticket_jira_fetch_failure(client):
    """Test ingest returns a standard error response on Jira fetch failure."""
    with patch(
        "app.api.v1.ingestion.ensure_project",
        new_callable=AsyncMock,
        return_value=_stub_project(),
    ), patch("app.api.v1.ingestion.fetch_ticket_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.side_effect = JiraServiceError("Jira authentication failed. Please check your credentials.")
        
        mock_db = AsyncMock()
        from app.core.database import get_db
        app.dependency_overrides[get_db] = lambda: mock_db
        
        response = client.post(
            "/api/v1/ingestion/ingest",
            json={"ticket_id_or_url": "INVALID-123"}
        )
        
        app.dependency_overrides.clear()
        
        assert response.status_code == 400
        payload = response.json()
        assert payload["error"] == "HTTP_ERROR"
        assert payload["code"] == 400
        assert "authentication failed" in payload["message"]


def test_ingest_ticket_pinecone_failure(client):
    """Test ingest returns a standard error response on vector indexing failure."""
    mock_ticket = JiraTicketContent(
        ticket_id="PROJ-123",
        summary="Test Summary",
        description="Test description",
        acceptance_criteria="Given it works, Then profit",
        labels=[],
        linked_issues=[]
    )
    
    with patch(
        "app.api.v1.ingestion.ensure_project",
        new_callable=AsyncMock,
        return_value=_stub_project(),
    ), patch("app.api.v1.ingestion.fetch_ticket_content", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = mock_ticket
        
        with patch("app.api.v1.ingestion.embed_and_index_ticket", new_callable=AsyncMock) as mock_embed:
            mock_embed.side_effect = VectorServiceError("Failed to connect to Pinecone.", code="VECTOR_INDEX_FAILED")
            
            from unittest.mock import MagicMock
            mock_db = AsyncMock()
            mock_db.add = MagicMock()
            from app.core.database import get_db
            app.dependency_overrides[get_db] = lambda: mock_db
            
            response = client.post(
                "/api/v1/ingestion/ingest",
                json={"ticket_id_or_url": "PROJ-123"}
            )
            
            app.dependency_overrides.clear()
            
            assert response.status_code == 502
            payload = response.json()
            assert payload["error"] == "HTTP_ERROR"
            assert payload["code"] == 502
            assert "Failed to" in payload["message"]
