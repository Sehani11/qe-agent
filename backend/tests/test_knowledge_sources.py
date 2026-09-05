"""Tests for GET /api/v1/knowledge/sources (Story 4.5).

Covers:
- Owner receives only their own knowledge sources (user isolation, NFR-S8)
- Empty list when the user has ingested nothing
- Response shape matches KnowledgeSourceResponse

Strategy:
- Override get_current_user + get_db (same pattern as test_sessions.py).
- Mock db.execute to return controlled KnowledgeSource rows.
"""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app

USER_A = "user-a-id"
USER_B = "user-b-id"


def _make_source(
    source_type: str = "confluence",
    title: str = "Auth Design",
    source_url: str | None = "https://wiki.example.com/pages/12345",
    project_id: uuid.UUID | None = None,
) -> MagicMock:
    """Build a mock KnowledgeSource ORM object with all schema-read fields set.

    `project_id` is set explicitly rather than left to MagicMock's auto-attribute:
    it decides which Pinecone namespace a delete reaches, so an auto-created Mock
    there would make a wrong-namespace delete look like a correct one.
    """
    s = MagicMock()
    s.id = uuid.uuid4()
    s.user_id = USER_A
    s.project_id = project_id
    s.source_type = source_type
    s.source_url = source_url
    s.title = title
    s.page_count = 1
    s.ingestion_status = "completed"
    s.created_at = datetime(2026, 7, 4, tzinfo=UTC)
    return s


def _make_scalars_result(items) -> MagicMock:
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = items
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars
    return mock_result


def _make_scalar_one_result(item) -> MagicMock:
    """Result whose scalar_one_or_none() returns the given row (or None)."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = item
    return r


@pytest.fixture
def client_as_user_a():
    """TestClient authenticated as USER_A."""
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_list_sources_returns_users_sources(client_as_user_a):
    """GET /knowledge/sources returns the user's sources with full shape."""
    src = _make_source()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([src]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/knowledge/sources")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["source_type"] == "confluence"
    assert data[0]["title"] == "Auth Design"
    assert data[0]["source_url"] == "https://wiki.example.com/pages/12345"
    assert data[0]["ingestion_status"] == "completed"
    assert data[0]["page_count"] == 1


def test_list_sources_returns_empty_list_when_none(client_as_user_a):
    """GET /knowledge/sources returns [] when the user has ingested nothing."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/knowledge/sources")

    assert response.status_code == 200
    assert response.json() == []


def test_list_sources_query_scoped_to_current_user(client_as_user_a):
    """The query filters by the authenticated user's id (NFR-S8)."""
    from app.models.knowledge_source import KnowledgeSource

    captured = {}

    async def _capture_execute(statement, *args, **kwargs):
        captured["sql"] = str(statement.compile())
        return _make_scalars_result([])

    db = AsyncMock()
    db.execute = _capture_execute
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/knowledge/sources")

    assert response.status_code == 200
    # The compiled statement filters on knowledge_sources.user_id
    assert "knowledge_sources.user_id" in captured["sql"]
    assert KnowledgeSource.__tablename__ == "knowledge_sources"


def test_list_sources_handles_null_optional_fields(client_as_user_a):
    """Sources with null title/url still serialize (schema allows None)."""
    src = _make_source(source_type="jira", title=None, source_url=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([src]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/knowledge/sources")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["title"] is None
    assert data[0]["source_url"] is None
    assert data[0]["source_type"] == "jira"


# ---------------------------------------------------------------------------
# DELETE /api/v1/knowledge/sources/{id} (Story 4.7)
# ---------------------------------------------------------------------------


def test_delete_source_removes_row_and_vectors(client_as_user_a):
    """204, deletes the DB row, and removes the source's vectors."""
    src = _make_source(source_type="document")
    src.source_ref = "doc-uuid-1"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalar_one_result(src))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_source_vectors",
        new=AsyncMock(),
    ) as del_vecs:
        resp = client_as_user_a.delete(f"/api/v1/knowledge/sources/{src.id}")

    assert resp.status_code == 204
    db.delete.assert_awaited_once_with(src)
    db.commit.assert_awaited()
    del_vecs.assert_awaited_once_with(USER_A, "document", "doc-uuid-1", None)


def test_delete_source_targets_the_namespace_it_was_ingested_into(client_as_user_a):
    """The row's own project decides the namespace — not a default, not a param.

    Deleting with no project reaches the pre-projects namespace, where a
    project-scoped source has nothing. The row would vanish from the list while
    its vectors kept answering retrieval: a delete that reports success and
    removes nothing the user can see the effect of.
    """
    project_id = uuid.uuid4()
    src = _make_source(source_type="document", project_id=project_id)
    src.source_ref = "doc-uuid-1"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalar_one_result(src))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_source_vectors",
        new=AsyncMock(),
    ) as del_vecs:
        resp = client_as_user_a.delete(f"/api/v1/knowledge/sources/{src.id}")

    assert resp.status_code == 204
    del_vecs.assert_awaited_once_with(USER_A, "document", "doc-uuid-1", project_id)


def test_delete_source_404_when_missing(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalar_one_result(None))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/knowledge/sources/{uuid.uuid4()!s}")
    assert resp.status_code == 404


def test_delete_source_403_for_non_owner(client_as_user_a):
    src = _make_source()
    src.user_id = USER_B  # owned by someone else
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalar_one_result(src))
    db.delete = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/knowledge/sources/{src.id}")
    assert resp.status_code == 403
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_source_vectors_noop_without_ref_or_pinecone():
    """delete_source_vectors is a no-op (no raise) when unconfigured / no ref."""
    from app.services import knowledge_service

    # No source_ref → returns immediately
    with patch.object(knowledge_service.settings, "pinecone_api_key", "key"):
        await knowledge_service.delete_source_vectors(USER_A, "document", None)

    # No pinecone key → returns immediately even with a ref
    with patch.object(knowledge_service.settings, "pinecone_api_key", ""):
        await knowledge_service.delete_source_vectors(USER_A, "document", "ref")


@pytest.mark.asyncio
async def test_delete_source_vectors_swallows_backend_errors():
    """A Pinecone failure is logged, not raised (DB row still gets removed)."""
    from app.services import knowledge_service

    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", "key"),
        patch(
            "app.services.knowledge_service.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("pinecone down")),
        ),
    ):
        # Must not raise
        await knowledge_service.delete_source_vectors(USER_A, "jira", "PROJ-1")


@pytest.mark.asyncio
async def test_delete_source_vectors_warns_on_missing_ref(caplog):
    """L1: a legacy row (no source_ref) logs that vectors may persist."""
    import logging

    from app.services import knowledge_service

    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", "key"),
        caplog.at_level(logging.WARNING),
    ):
        await knowledge_service.delete_source_vectors(USER_A, "document", None)

    assert any("no source_ref" in r.message for r in caplog.records)


class _FakeIndex:
    """Minimal Pinecone Index stand-in recording delete calls (Story 4.7 M1)."""

    def __init__(self, list_pages=None, list_raises=False):
        self._list_pages = list_pages or []
        self._list_raises = list_raises
        self.deleted_ids: list[str] = []
        self.filter_deletes: list[dict] = []
        self.list_prefix: str | None = None
        self.list_namespace: str | None = None

    def list(self, prefix=None, namespace=None):
        self.list_prefix = prefix
        self.list_namespace = namespace
        if self._list_raises:
            raise RuntimeError("list unsupported on this index")
        yield from self._list_pages

    def delete(self, ids=None, filter=None, namespace=None):
        if ids is not None:
            self.deleted_ids.extend(ids)
        if filter is not None:
            self.filter_deletes.append(filter)


def _patch_pinecone(index):
    fake_pc = MagicMock()
    fake_pc.Index.return_value = index
    return patch(
        "app.services.knowledge_service.pinecone.Pinecone", return_value=fake_pc
    )


def test_delete_vectors_sync_lists_prefix_and_deletes_by_id():
    """M1 (serverless path): list ids by the source's prefix, delete by id."""
    from app.services import knowledge_service

    idx = _FakeIndex(
        list_pages=[["document_ref-1_chunk_0", "document_ref-1_chunk_1"]]
    )
    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", "key"),
        _patch_pinecone(idx),
    ):
        knowledge_service._delete_vectors_sync(
            "user:knowledge", "document", "ref-1"
        )

    assert idx.list_prefix == "document_ref-1_chunk_"
    assert idx.list_namespace == "user:knowledge"
    assert idx.deleted_ids == ["document_ref-1_chunk_0", "document_ref-1_chunk_1"]
    assert idx.filter_deletes == []  # fallback NOT used when list works


def test_delete_vectors_sync_falls_back_to_filter_delete():
    """M1 (pod path): when list is unsupported, delete by metadata filter."""
    from app.services import knowledge_service

    idx = _FakeIndex(list_raises=True)
    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", "key"),
        _patch_pinecone(idx),
    ):
        knowledge_service._delete_vectors_sync("user:knowledge", "jira", "PROJ-1")

    assert idx.deleted_ids == []  # no id-based delete
    assert idx.filter_deletes == [{"source": "jira", "source_id": "PROJ-1"}]


def test_delete_source_400_for_malformed_id(client_as_user_a):
    """L2: a non-UUID id returns 404, not a 500 from the DB driver."""
    db = AsyncMock()
    db.execute = AsyncMock()  # must never be reached
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete("/api/v1/knowledge/sources/not-a-uuid")

    assert resp.status_code == 404
    db.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Delete all sources
# ---------------------------------------------------------------------------


def test_delete_all_sources_removes_every_row_and_wipes_the_namespace(client_as_user_a):
    """One namespace wipe, not a loop of per-source deletes.

    The bulk path also clears vectors no per-source delete could target — legacy
    rows without a source_ref, and orphans from an earlier failed deletion.
    """
    first = _make_source(title="Auth Design")
    second = _make_source(source_type="jira", title="PROJ-1")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([first, second]))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_all_source_vectors",
        new=AsyncMock(),
    ) as wipe:
        resp = client_as_user_a.delete("/api/v1/knowledge/sources")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 2}
    assert db.delete.await_count == 2
    db.commit.assert_awaited()
    # No project_id on the request, so the pre-projects namespace is the target.
    wipe.assert_awaited_once_with(USER_A, None)


def test_delete_all_sources_with_nothing_to_delete_is_a_success(client_as_user_a):
    """An empty knowledge base is not a 404 — the end state is what was asked for."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([]))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_all_source_vectors",
        new=AsyncMock(),
    ) as wipe:
        resp = client_as_user_a.delete("/api/v1/knowledge/sources")

    assert resp.status_code == 200
    assert resp.json() == {"deleted": 0}
    db.delete.assert_not_awaited()
    db.commit.assert_not_awaited()
    # Nothing to wipe, so no vector call either.
    wipe.assert_not_awaited()


def test_delete_all_sources_only_selects_the_callers_rows(client_as_user_a):
    """Scoped by user_id in the query — there is no way to ask for another user's."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_make_scalars_result([]))
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_all_source_vectors", new=AsyncMock()
    ):
        client_as_user_a.delete("/api/v1/knowledge/sources")

    where_clause = str(db.execute.await_args.args[0])
    assert "user_id" in where_clause


def test_delete_all_sources_is_scoped_to_one_project(client_as_user_a):
    """Clearing one project's knowledge base must not touch another's.

    Both halves have to agree on the project: the ROW query and the NAMESPACE
    wipe. Filtering only by user would delete every project's rows, and wiping
    the wrong namespace would leave those projects' vectors orphaned — still
    answering retrieval, with no row left that could ever remove them.
    """
    project_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=_make_scalars_result([_make_source(project_id=project_id)])
    )
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.knowledge_service.delete_all_source_vectors",
        new=AsyncMock(),
    ) as wipe:
        resp = client_as_user_a.delete(
            f"/api/v1/knowledge/sources?project_id={project_id}"
        )

    assert resp.status_code == 200
    where_clause = str(db.execute.await_args.args[0])
    assert "project_id = " in where_clause, "row delete ignored the project scope"
    wipe.assert_awaited_once_with(USER_A, project_id)


@pytest.mark.asyncio
async def test_delete_all_source_vectors_swallows_backend_errors():
    """A vector-store hiccup must not block removing the DB rows."""
    from app.services import knowledge_service

    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", "test-key"),
        patch(
            "app.services.knowledge_service._delete_namespace_sync",
            side_effect=RuntimeError("pinecone down"),
        ),
    ):
        await knowledge_service.delete_all_source_vectors(USER_A)  # must not raise


@pytest.mark.asyncio
async def test_delete_all_source_vectors_noop_without_pinecone():
    """No Pinecone configured — nothing to wipe, and no crash."""
    from app.services import knowledge_service

    with (
        patch.object(knowledge_service.settings, "pinecone_api_key", ""),
        patch("app.services.knowledge_service._delete_namespace_sync") as sync_delete,
    ):
        await knowledge_service.delete_all_source_vectors(USER_A)

    sync_delete.assert_not_called()


# ---------------------------------------------------------------------------
# Ingest by URL / explicit refs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "expected"),
    [
        ("https://acme.atlassian.net/wiki/spaces/ENG/pages/123456/Auth+Design", "123456"),
        ("https://acme.atlassian.net/wiki/spaces/ENG/pages/123456", "123456"),
        ("https://acme.atlassian.net/pages/viewpage.action?pageId=987", "987"),
        ("https://acme.atlassian.net/x?a=1&pageId=42", "42"),
        ("123456", "123456"),
        ("  123456  ", "123456"),
    ],
)
def test_extract_page_id_accepts_every_supported_shape(ref, expected):
    from app.services import confluence_service

    assert confluence_service.extract_page_id(ref) == expected


@pytest.mark.parametrize(
    "ref",
    [
        "",
        "   ",
        "https://acme.atlassian.net/wiki/x/AbCdEf",  # tiny link carries no id
        "not a url",
    ],
)
def test_extract_page_id_rejects_refs_without_an_id(ref):
    """A bad line names itself, so a pasted list says which line to fix."""
    from app.services import confluence_service
    from app.services.confluence_service import ConfluenceServiceError

    with pytest.raises(ConfluenceServiceError) as exc:
        confluence_service.extract_page_id(ref)
    assert exc.value.code == "CONFLUENCE_INVALID_PAGE_REF"


def test_confluence_request_drops_blank_page_refs():
    """Blank lines are how a pasted list ends, not an error."""
    from app.schemas.knowledge import ConfluenceIngestRequest

    req = ConfluenceIngestRequest(page_refs=["123", "  ", "", "456"])
    assert req.page_refs == ["123", "456"]

    # All-blank collapses to None so the route's guard still rejects the body.
    assert ConfluenceIngestRequest(page_refs=["  ", ""]).page_refs is None


def test_jira_request_accepts_ticket_refs_without_a_project_key():
    from app.schemas.knowledge import JiraIngestRequest

    req = JiraIngestRequest(ticket_refs=["PROJ-1", "https://acme.atlassian.net/browse/PROJ-2"])
    assert req.project_key is None
    assert len(req.ticket_refs) == 2


def test_jira_request_rejects_a_body_with_neither_source():
    """Same 422 callers got when project_key was mandatory."""
    import pydantic

    from app.schemas.knowledge import JiraIngestRequest

    with pytest.raises(pydantic.ValidationError):
        JiraIngestRequest()


def test_jira_request_still_validates_project_key_format():
    import pydantic

    from app.schemas.knowledge import JiraIngestRequest

    with pytest.raises(pydantic.ValidationError):
        JiraIngestRequest(project_key="not a key!")
