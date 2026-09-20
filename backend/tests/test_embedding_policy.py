"""Tests for the embedding policy and RAG grounding fixes.

Covers:
- _embed_chunks refuses to run without a real backend (no random-vector
  fallback: garbage vectors index fine and then retrieval serves arbitrary
  chunks as "project context")
- embed_and_index_ticket degrades (skips indexing) instead of 502ing when
  embeddings are unconfigured
- _chunk_from_match carries the full chunk text for LLM grounding alongside
  the 300-char display snippet
- format_rag_block / knowledge-chat prompts ground in the full text
- _upsert_to_pinecone clears a source's existing vectors before re-upserting
- persisted rag_context still carries only the snippet (no payload bloat)
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.knowledge_chat_service import _build_prompt as _kb_prompt
from app.services.knowledge_service import _chunk_from_match, _upsert_to_pinecone
from app.services.vector_service import (
    EmbeddingsUnavailableError,
    _embed_chunks,
    embed_and_index_ticket,
    embeddings_available,
)
from app.services.verification_service import format_rag_block, rag_payload_from_chunks

# ---------------------------------------------------------------------------
# Embedding availability
# ---------------------------------------------------------------------------


class TestEmbeddingAvailability:
    """Embeddings depend on an OpenAI credential — and on nothing else.

    These patch `api_key_for`, the seam vector_service actually reads. Patching
    `vector_service.settings` no longer covers this path, and a stale patch
    here does not fail loudly: `_embed_chunks` would reach the real API with
    whatever key the developer's .env holds.
    """

    @pytest.fixture(autouse=True)
    def _openai_is_the_embedding_provider(self, monkeypatch):
        """Pin the vendor, rather than inheriting the developer's .env.

        `api_key_for` is the OpenAI seam; it is not consulted at all when
        EMBEDDING_PROVIDER names another vendor. Without this pin these tests
        pass or fail according to a local setting that has nothing to do with
        what they are asserting.
        """
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "openai"
        )

    @pytest.mark.asyncio
    async def test_embed_chunks_raises_without_an_openai_key(self):
        with (
            patch("app.services.vector_service.api_key_for", return_value=""),
            pytest.raises(EmbeddingsUnavailableError),
        ):
            await _embed_chunks(["some text"])

    def test_availability_follows_the_openai_credential(self):
        with patch("app.services.vector_service.api_key_for") as key_for:
            key_for.return_value = "sk-openai"
            assert embeddings_available() is True
            key_for.return_value = ""
            assert embeddings_available() is False

    def test_availability_ignores_the_selected_chat_provider(self):
        """Choosing Claude for chat must not empty every RAG surface.

        The index is built with one embedding model and has to stay
        consistent, so embeddings always go to OpenAI regardless of which
        provider answers the question. Gating them on `llm_provider` meant
        retrieval silently returned no context and answers came back
        ungrounded, with nothing on screen to say why.
        """
        with (
            patch("app.services.vector_service.settings") as mock_settings,
            patch(
                "app.services.vector_service.api_key_for", return_value="sk-openai"
            ),
        ):
            mock_settings.llm_provider = "claude"
            assert embeddings_available() is True

    @pytest.mark.asyncio
    async def test_ticket_indexing_skips_when_embeddings_unavailable(self):
        """Ticket ingestion must not 502 just because embeddings are off —
        the session works, only chat retrieval degrades."""
        with (
            patch("app.services.vector_service.settings") as mock_settings,
            patch("app.services.vector_service.api_key_for", return_value=""),
            patch("app.services.vector_service.pinecone.Pinecone") as mock_pc,
        ):
            mock_settings.pinecone_api_key = "pk"
            mock_settings.pinecone_index_name = "idx"

            # No exception, and Pinecone is never touched
            await embed_and_index_ticket(
                session_id="s1",
                ticket_id="PROJ-1",
                summary="Sum",
                description="Desc",
                acceptance_criteria="Given x Then y",
                user_id="user-a",
            )
            mock_pc.assert_not_called()


# ---------------------------------------------------------------------------
# Full-text grounding vs display snippet
# ---------------------------------------------------------------------------

_LONG_TEXT = "The auth service signs JWTs with RS256. " * 20  # ~800 chars


def _make_match(text: str = _LONG_TEXT):
    m = MagicMock()
    m.metadata = {
        "text": text,
        "source": "confluence",
        "source_id": "42",
        "title": "Auth Design",
        "url": "https://wiki/42",
    }
    m.score = 0.9
    return m


class TestFullTextGrounding:
    def test_chunk_from_match_carries_text_and_snippet(self):
        chunk = _chunk_from_match(_make_match())
        assert chunk["text"] == _LONG_TEXT
        # The excerpt stays within the budget (the ellipsis counts against it)
        # and ends on a whole word — a mid-word cut reads in the UI as text
        # that failed to load rather than as an excerpt.
        assert len(chunk["snippet"]) <= 300
        assert chunk["snippet"].endswith("…")
        assert _LONG_TEXT.startswith(chunk["snippet"].rstrip("…"))

    def test_snippet_is_left_whole_when_it_fits(self):
        chunk = _chunk_from_match(_make_match("Short enough to keep."))
        assert chunk["snippet"] == "Short enough to keep."

    def test_snippet_hard_cuts_a_chunk_with_no_word_boundary(self):
        """One long token (a URL, a minified blob) offers nothing to back up to."""
        chunk = _chunk_from_match(_make_match("x" * 500))
        assert len(chunk["snippet"]) <= 300
        assert chunk["snippet"].endswith("…")

    def test_format_rag_block_uses_full_text(self):
        chunk = _chunk_from_match(_make_match())
        block = format_rag_block([chunk])
        # The tail of the chunk (beyond 300 chars) must reach the prompt
        assert _LONG_TEXT.strip() in block

    def test_format_rag_block_falls_back_to_snippet(self):
        """Persisted chunks (no 'text' key) still render."""
        block = format_rag_block(
            [{"source": "jira", "source_id": "P-1", "snippet": "AC: Given x"}]
        )
        assert "AC: Given x" in block

    def test_knowledge_chat_prompt_uses_full_text(self):
        chunk = _chunk_from_match(_make_match())
        prompt = _kb_prompt("How is auth signed?", [chunk])
        assert _LONG_TEXT.strip() in prompt

    def test_persisted_rag_payload_keeps_snippet_only(self):
        """rag_context rows must not balloon with full chunk text.

        Carrying the full chunk was tried, to let the context panel show the
        whole retrieved passage, and reverted: an arbitrary ~500-word window
        opens mid-table and ends mid-thought, and the panel exists to say which
        source backed a verdict, not to stand in for reading it.
        """
        chunk = _chunk_from_match(_make_match())
        payload = rag_payload_from_chunks([chunk])
        assert payload is not None
        assert len(payload[0]["snippet"]) <= 300
        assert "text" not in payload[0]


# ---------------------------------------------------------------------------
# Stale-chunk cleanup on re-ingest
# ---------------------------------------------------------------------------


class TestStaleChunkCleanup:
    @pytest.mark.asyncio
    async def test_upsert_deletes_existing_source_vectors_first(self):
        with (
            patch("app.services.knowledge_service.settings") as mock_settings,
            patch("app.services.knowledge_service.pinecone.Pinecone") as mock_pc,
            patch(
                "app.services.knowledge_service._delete_vectors_sync"
            ) as mock_delete,
        ):
            mock_settings.pinecone_api_key = "pk"
            mock_settings.pinecone_index_name = "idx"
            mock_index = MagicMock()
            mock_pc.return_value.Index.return_value = mock_index

            await _upsert_to_pinecone(
                "page-9",
                ["chunk one"],
                [[0.1] * 1536],
                "user-a:knowledge",
                source="confluence",
                title="T",
                url="u",
            )

            # The trailing config is the caller's VectorConfig — None here means
            # the deployment default, and it must reach the delete so cleanup
            # targets the same index the upsert is about to write to.
            mock_delete.assert_called_once_with(
                "user-a:knowledge", "confluence", "page-9", None
            )
            mock_index.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_failure_does_not_block_indexing(self):
        with (
            patch("app.services.knowledge_service.settings") as mock_settings,
            patch("app.services.knowledge_service.pinecone.Pinecone") as mock_pc,
            patch(
                "app.services.knowledge_service._delete_vectors_sync",
                side_effect=RuntimeError("pinecone hiccup"),
            ),
        ):
            mock_settings.pinecone_api_key = "pk"
            mock_settings.pinecone_index_name = "idx"
            mock_index = MagicMock()
            mock_pc.return_value.Index.return_value = mock_index

            await _upsert_to_pinecone(
                "page-9",
                ["chunk one"],
                [[0.1] * 1536],
                "user-a:knowledge",
                source="confluence",
            )

            mock_index.upsert.assert_called_once()


# ---------------------------------------------------------------------------
# Ingestion aborts clearly when embeddings are unconfigured
# ---------------------------------------------------------------------------


class TestIngestionAbort:
    @pytest.mark.asyncio
    async def test_confluence_ingest_aborts_with_clear_error(self):
        import json

        from app.schemas.knowledge import ConfluenceIngestRequest
        from app.services.knowledge_service import ingest_confluence

        page = MagicMock()
        page.id = "p1"
        page.title = "Page"
        page.body = "Some body text long enough to chunk."
        page.url = "https://wiki/p1"

        db = MagicMock()
        db.add = MagicMock()
        db.commit = AsyncMock()

        with (
            patch(
                "app.services.knowledge_service.confluence_service.fetch_page_by_id",
                new=AsyncMock(return_value=page),
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(side_effect=EmbeddingsUnavailableError()),
            ),
        ):
            events = []
            async for ev in ingest_confluence(
                "user-a", ConfluenceIngestRequest(page_id="p1"), db
            ):
                if ev.startswith("data: "):
                    events.append(json.loads(ev[6:]))

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 1
        assert errors[0]["error"] == "EMBEDDINGS_UNAVAILABLE"
        # One clear abort, not a per-page failure spam followed by "complete"
        assert not any(e["type"] == "complete" for e in events)
