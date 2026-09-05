"""Tests for RAG retrieval (Story 4.3).

Covers knowledge_service.query_knowledge_base / query_knowledge_base_batch:
  - Returns structured chunks when Pinecone query succeeds
  - Returns [] when pinecone_api_key is absent (graceful degradation)
  - Returns [] on any Pinecone or embed exception (graceful degradation)

The verification-prompt and direct-runner RAG coverage moved out with the
legacy two-step flow; the agentic equivalents live in
test_agentic_verification_service.py.

Strategy:
- Mock Pinecone at SDK level for query_knowledge_base tests
- Mock _embed_chunks to avoid real OpenAI calls
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_RAG_CHUNKS = [
    {
        "source": "confluence",
        "source_id": "12345",
        "snippet": "Architecture decision: the auth service uses JWT with RS256.",
    },
    {
        "source": "jira",
        "source_id": "PROJ-1",
        "snippet": "AC: Given valid credentials, When login, Then redirect.",
    },
]

# ---------------------------------------------------------------------------
# knowledge_service.query_knowledge_base
# ---------------------------------------------------------------------------


class TestQueryKnowledgeBase:
    @pytest.mark.asyncio
    async def test_returns_chunks_on_successful_query(self):
        """query_knowledge_base returns structured dicts from Pinecone matches."""
        from app.services.knowledge_service import query_knowledge_base

        mock_match = MagicMock()
        mock_match.metadata = {
            "source": "confluence",
            "source_id": "99",
            "text": "Some architecture detail here" * 20,
        }
        mock_match.score = 0.87

        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[mock_match])

        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base("user-a", "login flow")

        assert len(result) == 1
        assert result[0]["source"] == "confluence"
        assert result[0]["source_id"] == "99"
        assert len(result[0]["snippet"]) <= 300

    @pytest.mark.asyncio
    async def test_returns_empty_when_pinecone_api_key_absent(self):
        """query_knowledge_base returns [] without error when pinecone_api_key is ''."""
        from app.services.knowledge_service import query_knowledge_base

        with patch(
            "app.services.knowledge_service.settings.pinecone_api_key",
            new="",
        ):
            result = await query_knowledge_base("user-a", "login flow")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_pinecone_exception(self):
        """query_knowledge_base returns [] when Pinecone raises any exception."""
        from app.services.knowledge_service import query_knowledge_base

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(side_effect=RuntimeError("Network error")),
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base("user-a", "login flow")

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_for_blank_query_text(self):
        """query_knowledge_base returns [] immediately for blank query text."""
        from app.services.knowledge_service import query_knowledge_base

        with patch(
            "app.services.knowledge_service.settings.pinecone_api_key",
            new="test-key",
        ):
            result = await query_knowledge_base("user-a", "   ")

        assert result == []

    @pytest.mark.asyncio
    async def test_namespace_scoped_to_user(self):
        """query_knowledge_base queries Pinecone with {user_id}:knowledge namespace."""
        from app.services.knowledge_service import query_knowledge_base

        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base("user-xyz", "some query")

        assert result == []
        call_kwargs = mock_index.query.call_args[1]
        assert call_kwargs["namespace"] == "user-xyz:knowledge"
        # Hybrid-lite over-fetches the vector query so the identifier re-rank
        # has candidates beyond the returned top_k (5 * _QUERY_OVERFETCH).
        assert call_kwargs["top_k"] == 15
        assert call_kwargs["include_metadata"] is True

    @pytest.mark.asyncio
    async def test_filters_matches_below_relevance_threshold(self):
        """Matches scoring below settings.rag_min_score are dropped as irrelevant."""
        from app.services.knowledge_service import query_knowledge_base

        relevant = MagicMock()
        relevant.metadata = {"source": "confluence", "source_id": "1", "text": "keep"}
        relevant.score = 0.8
        irrelevant = MagicMock()
        irrelevant.metadata = {"source": "confluence", "source_id": "2", "text": "drop"}
        irrelevant.score = 0.05

        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[relevant, irrelevant])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key", new="k"
            ),
            patch(
                "app.services.knowledge_service.settings.rag_min_score", new=0.3
            ),
        ):
            result = await query_knowledge_base("user-a", "q")

        # Only the above-threshold chunk survives
        assert len(result) == 1
        assert result[0]["source_id"] == "1"


# ---------------------------------------------------------------------------
# Story 4.4 — title/url enrichment on retrieved chunks
# ---------------------------------------------------------------------------


class TestQueryKnowledgeBaseTitleUrl:
    @pytest.mark.asyncio
    async def test_returns_title_and_url_from_metadata(self):
        """Chunks ingested with title/url metadata expose those fields."""
        from app.services.knowledge_service import query_knowledge_base

        mock_match = MagicMock()
        mock_match.metadata = {
            "source": "confluence",
            "source_id": "777",
            "text": "Some content",
            "title": "Deployment Guide",
            "url": "https://wiki.example.com/pages/777",
        }
        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[mock_match])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base("user-a", "deploy")

        assert result[0]["title"] == "Deployment Guide"
        assert result[0]["url"] == "https://wiki.example.com/pages/777"

    @pytest.mark.asyncio
    async def test_jira_url_derived_when_metadata_missing_url(self):
        """Legacy Jira chunk (no url in metadata) derives a browse URL."""
        from app.services.knowledge_service import query_knowledge_base

        mock_match = MagicMock()
        mock_match.metadata = {
            "source": "jira",
            "source_id": "PROJ-42",
            "text": "Ticket body",
        }
        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[mock_match])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
            patch(
                "app.services.knowledge_service.settings.jira_base_url",
                new="https://jira.example.com/",
            ),
        ):
            result = await query_knowledge_base("user-a", "ticket")

        assert result[0]["url"] == "https://jira.example.com/browse/PROJ-42"
        # title falls back to empty; frontend uses source_id
        assert result[0]["title"] == ""

    @pytest.mark.asyncio
    async def test_confluence_url_empty_when_metadata_missing_url(self):
        """Legacy Confluence chunk with no url stays unlinkable (empty url)."""
        from app.services.knowledge_service import query_knowledge_base

        mock_match = MagicMock()
        mock_match.metadata = {
            "source": "confluence",
            "source_id": "555",
            "text": "Body",
        }
        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[mock_match])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
            patch(
                "app.services.knowledge_service.settings.jira_base_url",
                new="https://jira.example.com",
            ),
        ):
            result = await query_knowledge_base("user-a", "doc")

        assert result[0]["url"] == ""


# ---------------------------------------------------------------------------
# Story 4.8 — knowledge_service.query_knowledge_base_batch
# ---------------------------------------------------------------------------


class TestQueryKnowledgeBaseBatch:
    @pytest.mark.asyncio
    async def test_embeds_once_and_queries_per_text(self):
        """One embeddings request for all texts; one Pinecone query per text."""
        from app.services.knowledge_service import query_knowledge_base_batch

        mock_match = MagicMock()
        mock_match.metadata = {"source": "confluence", "source_id": "1", "text": "ctx"}
        mock_index = MagicMock()
        mock_index.query.return_value = MagicMock(matches=[mock_match])
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        embed = AsyncMock(return_value=[[0.1] * 1536, [0.2] * 1536])

        with (
            patch("app.services.knowledge_service._embed_chunks", new=embed),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base_batch("user-a", ["t1", "t2"])

        # Exactly ONE embeddings request, batching both texts. These are search
        # strings, so they must be embedded as queries — Voyage embeds queries
        # and stored documents asymmetrically, and getting it wrong here costs
        # retrieval quality silently.
        embed.assert_awaited_once_with(["t1", "t2"], input_type="query", config=None)
        # One Pinecone query per text; result aligned with inputs
        assert mock_index.query.call_count == 2
        assert len(result) == 2
        assert result[0][0]["source"] == "confluence"

    @pytest.mark.asyncio
    async def test_returns_empty_per_text_without_pinecone(self):
        """No Pinecone key → [[]... ] aligned with inputs, no embedding call."""
        from app.services.knowledge_service import query_knowledge_base_batch

        embed = AsyncMock()
        with (
            patch("app.services.knowledge_service._embed_chunks", new=embed),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key", new=""
            ),
        ):
            result = await query_knowledge_base_batch("user-a", ["t1", "t2", "t3"])

        assert result == [[], [], []]
        embed.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_isolates_a_single_failed_query(self):
        """One query raising (e.g. 429) yields [] for that text only, not all."""
        from app.services.knowledge_service import query_knowledge_base_batch

        mock_match = MagicMock()
        mock_match.metadata = {"source": "jira", "source_id": "P-1", "text": "ctx"}

        def _query_side_effect(*args, **kwargs):
            # Fail only the second text's vector; succeed for the first.
            if kwargs.get("vector", [None])[0] == 0.2:
                raise RuntimeError("pinecone 429")
            return MagicMock(matches=[mock_match])

        mock_index = MagicMock()
        mock_index.query.side_effect = _query_side_effect
        mock_pc = MagicMock()
        mock_pc.Index.return_value = mock_index

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536, [0.2] * 1536]),
            ),
            patch(
                "app.services.knowledge_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch(
                "app.services.knowledge_service.settings.pinecone_api_key",
                new="test-key",
            ),
        ):
            result = await query_knowledge_base_batch("user-a", ["ok", "throttled"])

        assert len(result) == 2
        assert result[0] and result[0][0]["source"] == "jira"  # first survived
        assert result[1] == []  # failed query isolated to its own slot

    @pytest.mark.asyncio
    async def test_empty_input_returns_empty_list(self):
        """No texts → [] (no embedding / Pinecone work)."""
        from app.services.knowledge_service import query_knowledge_base_batch

        with patch(
            "app.services.knowledge_service.settings.pinecone_api_key", new="test-key"
        ):
            assert await query_knowledge_base_batch("user-a", []) == []
