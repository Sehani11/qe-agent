"""Project API — ownership, and credentials that never come back out.

The security property under test is one-directional: a token can be written
and used server-side, but no response, however requested, contains it.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.crypto import decrypt, encrypt, generate_key
from app.core.database import get_db
from app.main import app
from app.models.project import Project

USER_A = "user-a"
USER_B = "user-b"


@pytest.fixture(autouse=True)
def _configured_key(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "app.core.crypto.settings.credential_encryption_key", generate_key()
    )


def _make_project(user_id: str = USER_A, **over) -> Project:
    now = datetime.now(UTC)
    project = Project(
        id=uuid.uuid4(),
        user_id=user_id,
        name="Project-1",
        jira_base_url="https://acme.atlassian.net",
        jira_user_email="qa@acme.test",
        jira_api_token_encrypted="",
        confluence_base_url="",
        confluence_user_email="",
        confluence_api_token_encrypted="",
        github_repo="acme/app",
        github_access_token_encrypted="",
        llm_provider="",
        llm_model="",
        # Listed explicitly like every other column: this instance is never
        # flushed, so the model's defaults do not apply and an omitted field
        # would arrive at the response schema as None.
        embedding_provider="",
        pinecone_index_name="",
        created_at=now,
        updated_at=now,
    )
    for key, value in over.items():
        setattr(project, key, value)
    return project


def _scalar_one(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


def _scalars(items):
    result = MagicMock()
    scalars = MagicMock()
    scalars.all.return_value = items
    result.scalars.return_value = scalars
    return result


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


class TestCredentialsNeverLeave:
    def test_response_reports_presence_not_the_secret(self, client_as_user_a) -> None:
        """A masked value would still have to be sent to be masked."""
        project = _make_project(
            jira_api_token_encrypted=encrypt("super-secret-jira-token")
        )
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.get(f"/api/v1/projects/{project.id}")

        assert resp.status_code == 200
        body = resp.json()
        assert body["has_jira_token"] is True
        assert "super-secret-jira-token" not in resp.text
        assert project.jira_api_token_encrypted not in resp.text
        assert not any("token" in k and k.startswith("jira_api") for k in body)

    def test_unset_credential_reports_absent(self, client_as_user_a) -> None:
        project = _make_project(jira_api_token_encrypted="")
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        body = client_as_user_a.get(f"/api/v1/projects/{project.id}").json()
        assert body["has_jira_token"] is False


class TestCredentialWrites:
    def test_a_supplied_token_is_stored_encrypted(self, client_as_user_a) -> None:
        project = _make_project()
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.patch(
            f"/api/v1/projects/{project.id}",
            json={"jira_api_token": "new-token"},
        )

        assert resp.status_code == 200
        # Stored as ciphertext...
        assert project.jira_api_token_encrypted != "new-token"
        # ...and recoverable, which is the point of encrypting rather than hashing.
        assert decrypt(project.jira_api_token_encrypted) == "new-token"

    def test_an_omitted_token_is_left_alone(self, client_as_user_a) -> None:
        """Editing the Jira URL must not blank the GitHub token."""
        existing = encrypt("keep-me")
        project = _make_project(github_access_token_encrypted=existing)
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        client_as_user_a.patch(
            f"/api/v1/projects/{project.id}",
            json={"jira_base_url": "https://new.atlassian.net"},
        )

        assert project.github_access_token_encrypted == existing
        assert project.jira_base_url == "https://new.atlassian.net"

    def test_an_empty_token_clears_it(self, client_as_user_a) -> None:
        """The only way to remove a credential, and it must be reachable."""
        project = _make_project(jira_api_token_encrypted=encrypt("old"))
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        client_as_user_a.patch(
            f"/api/v1/projects/{project.id}", json={"jira_api_token": ""}
        )

        assert project.jira_api_token_encrypted == ""


class TestOwnership:
    def test_another_users_project_is_not_found(self, client_as_user_a) -> None:
        """404 not 403: a 403 confirms the id exists, which is an oracle."""
        db = AsyncMock()
        # The query filters on user_id, so someone else's row simply misses.
        db.execute = AsyncMock(return_value=_scalar_one(None))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.get(f"/api/v1/projects/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_listing_is_scoped_to_the_caller(self, client_as_user_a) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_scalars([_make_project()]))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.get("/api/v1/projects")
        assert resp.status_code == 200
        assert [p["name"] for p in resp.json()] == ["Project-1"]


class TestAnyProjectCanBeDeleted:
    def test_deleting_the_only_project_is_allowed(self, client_as_user_a) -> None:
        """No last-project guard: an empty list is a state the switcher handles."""
        project = _make_project()
        db = AsyncMock()
        db.delete = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.delete(f"/api/v1/projects/{project.id}")

        assert resp.status_code == 204
        db.delete.assert_awaited_once()

    def test_deleting_one_of_several_is_allowed(self, client_as_user_a) -> None:
        project = _make_project()
        db = AsyncMock()
        db.delete = AsyncMock()
        db.execute = AsyncMock(return_value=_scalar_one(project))
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.delete(f"/api/v1/projects/{project.id}")

        assert resp.status_code == 204
        db.delete.assert_awaited_once()


class TestSessionsAreScopedToTheProject:
    """The sessions list must show one project's work, not every project's."""

    def test_project_filter_narrows_both_the_count_and_the_page(
        self, client_as_user_a
    ) -> None:
        """Filtering only the page would leave the pager sized for everything.

        The total drives how many pages the client offers, so a count that
        ignores the filter offers pages that come back empty.
        """
        captured: list[object] = []

        async def capture(statement, *args, **kwargs):
            captured.append(str(statement))
            result = MagicMock()
            result.scalar.return_value = 0
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
            result.all.return_value = []
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=capture)
        db.scalar = AsyncMock(side_effect=lambda stmt: captured.append(str(stmt)) or 0)
        app.dependency_overrides[get_db] = lambda: db

        project_id = uuid.uuid4()
        resp = client_as_user_a.get(f"/api/v1/sessions?project_id={project_id}")

        assert resp.status_code == 200
        # Both the COUNT and the page SELECT carry the project PREDICATE.
        # Matching on "project_id" alone would pass on the SELECT column list,
        # which says nothing about filtering.
        assert len(captured) >= 2
        assert all("project_id = " in sql for sql in captured[:2])

    def test_omitting_the_project_lists_everything(self, client_as_user_a) -> None:
        """Back-compat: a client with no project still gets its sessions."""
        captured: list[str] = []

        async def capture(statement, *args, **kwargs):
            captured.append(str(statement))
            result = MagicMock()
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
            result.all.return_value = []
            return result

        db = AsyncMock()
        db.execute = AsyncMock(side_effect=capture)
        db.scalar = AsyncMock(side_effect=lambda stmt: captured.append(str(stmt)) or 0)
        app.dependency_overrides[get_db] = lambda: db

        resp = client_as_user_a.get("/api/v1/sessions")

        assert resp.status_code == 200
        assert all("project_id = " not in sql for sql in captured[:2])
