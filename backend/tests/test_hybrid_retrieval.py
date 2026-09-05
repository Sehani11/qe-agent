"""Tests for hybrid-lite retrieval and document re-upload dedupe.

Hybrid-lite: pure cosine similarity misses exact identifiers ("PROJ-142",
"login_handler"), so retrieval over-fetches the vector query, re-ranks by
verbatim identifier hits, and pulls explicitly named tickets via a
metadata-filtered query.

Dedupe: a document's source id is derived from its filename, so re-uploading
replaces the previous version (vectors and knowledge_sources row) instead of
doubling its chunks in retrieval.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.knowledge_service import (
    _extract_identifiers,
    _query_matches_hybrid,
    ingest_document,
)


def _match(mid: str, text: str, score: float, source_id: str = "SRC-1"):
    m = MagicMock()
    m.id = mid
    m.score = score
    m.metadata = {
        "text": text,
        "source": "jira",
        "source_id": source_id,
        "title": "t",
        "url": "",
    }
    return m


def _index_returning(*result_sets):
    """Mock Pinecone index whose query returns each result set in turn."""
    index = MagicMock()
    index.query.side_effect = [MagicMock(matches=list(rs)) for rs in result_sets]
    return index


# ---------------------------------------------------------------------------
# Identifier extraction
# ---------------------------------------------------------------------------


class TestIdentifierExtraction:
    def test_ticket_keys_and_code_tokens(self):
        keys, tokens = _extract_identifiers(
            "Does PROJ-142 cover the login_handler and validateToken() path?"
        )
        assert keys == {"PROJ-142"}
        assert "login_handler" in tokens
        assert "validateToken" in tokens

    def test_plain_prose_yields_nothing(self):
        keys, tokens = _extract_identifiers("How is the password reset handled?")
        assert keys == set()
        assert tokens == set()


# ---------------------------------------------------------------------------
# Hybrid ranking
# ---------------------------------------------------------------------------


class TestHybridRanking:
    @pytest.mark.asyncio
    async def test_identifier_hit_outranks_higher_cosine_score(self):
        with_ident = _match("m1", "the login_handler validates the session", 0.4)
        without = _match("m2", "general prose about authentication flows", 0.9)
        index = _index_returning([without, with_ident])

        matches = await _query_matches_hybrid(
            index, "u:knowledge", [0.1], "check the login_handler behaviour", top_k=2
        )

        assert [m.id for m in matches] == ["m1", "m2"]

    @pytest.mark.asyncio
    async def test_prose_query_keeps_vector_order_and_single_call(self):
        a = _match("m1", "chunk a", 0.9)
        b = _match("m2", "chunk b", 0.8)
        index = _index_returning([a, b])

        matches = await _query_matches_hybrid(
            index, "u:knowledge", [0.1], "how is auth handled?", top_k=2
        )

        assert [m.id for m in matches] == ["m1", "m2"]
        assert index.query.call_count == 1  # no ticket keys → no extra query

    @pytest.mark.asyncio
    async def test_named_ticket_is_pulled_by_metadata_filter(self):
        """A ticket the user names is retrieved even when the vector query
        misses it entirely."""
        semantic = _match("m1", "unrelated prose", 0.9)
        exact = _match("m2", "AC for the named ticket", 0.1, source_id="PROJ-142")
        index = _index_returning([semantic], [exact])

        matches = await _query_matches_hybrid(
            index, "u:knowledge", [0.1], "Is PROJ-142 implemented?", top_k=2
        )

        # Exact-key hit first, despite its low cosine score
        assert [m.id for m in matches] == ["m2", "m1"]
        # Second query used the metadata filter on the named key
        filter_kwargs = index.query.call_args_list[1][1]
        assert filter_kwargs["filter"] == {"source_id": {"$in": ["PROJ-142"]}}

    @pytest.mark.asyncio
    async def test_exact_pull_deduplicates_against_vector_hits(self):
        shared = _match("m1", "PROJ-9 acceptance criteria", 0.9, source_id="PROJ-9")
        index = _index_returning([shared], [shared])

        matches = await _query_matches_hybrid(
            index, "u:knowledge", [0.1], "PROJ-9 status?", top_k=3
        )

        assert [m.id for m in matches] == ["m1"]


# ---------------------------------------------------------------------------
# Document re-upload dedupe
# ---------------------------------------------------------------------------

_DOC_TEXT = "The auth service signs JWTs with RS256 and rotates keys quarterly. " * 5


def _make_db(existing_source=None) -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_source
    db.execute = AsyncMock(return_value=result)
    return db


def _ingest_patches():
    return (
        patch(
            "app.services.knowledge_service._extract_pdf_text",
            return_value=_DOC_TEXT,
        ),
        patch(
            "app.services.knowledge_service._embed_chunks",
            new=AsyncMock(return_value=[[0.1] * 1536]),
        ),
        patch(
            "app.services.knowledge_service._upsert_to_pinecone",
            new=AsyncMock(),
        ),
    )


class TestDocumentDedupe:
    @pytest.mark.asyncio
    async def test_same_filename_produces_same_source_id(self):
        p1, p2, p3 = _ingest_patches()
        with p1, p2, p3 as mock_upsert:
            await ingest_document("user-a", "design.pdf", b"%PDF", _make_db())
            first_id = mock_upsert.call_args[0][0]
            await ingest_document("user-a", "design.pdf", b"%PDF", _make_db())
            second_id = mock_upsert.call_args[0][0]

        assert first_id == second_id
        assert first_id.startswith("doc-")

    @pytest.mark.asyncio
    async def test_different_filenames_produce_different_ids(self):
        p1, p2, p3 = _ingest_patches()
        with p1, p2, p3 as mock_upsert:
            await ingest_document("user-a", "design.pdf", b"%PDF", _make_db())
            first_id = mock_upsert.call_args[0][0]
            await ingest_document("user-a", "other.pdf", b"%PDF", _make_db())
            second_id = mock_upsert.call_args[0][0]

        assert first_id != second_id

    @pytest.mark.asyncio
    async def test_reupload_updates_existing_row_instead_of_inserting(self):
        existing = MagicMock()
        db = _make_db(existing_source=existing)

        p1, p2, p3 = _ingest_patches()
        with p1, p2, p3:
            result = await ingest_document("user-a", "design.pdf", b"%PDF", db)

        assert result["ingested_count"] == 1
        # The found row was updated in place — no duplicate completed row added
        assert existing.ingestion_status == "completed"
        assert existing.title == "design.pdf"
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_upload_inserts_a_row(self):
        db = _make_db(existing_source=None)

        p1, p2, p3 = _ingest_patches()
        with p1, p2, p3:
            await ingest_document("user-a", "design.pdf", b"%PDF", db)

        db.add.assert_called_once()
        row = db.add.call_args[0][0]
        assert row.source_type == "document"
        assert row.ingestion_status == "completed"
