"""Embedding vendor selection — EMBEDDING_PROVIDER.

An index is built with ONE embedding model. The vectors a query is compared
against must come from the same model that produced them, or retrieval returns
nothing and says nothing. That is why the vendor is a deployment setting rather
than a consequence of the per-request chat picker: tying the two together means
a ticket ingested while OpenAI was selected is invisible to a question asked
while Claude is selected, with no error anywhere.

These tests hold that line — the chat provider must never move the embedding
provider — and cover the wire differences between the two vendors.
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.services.vector_service import (
    _EMBEDDING_DIMENSIONS,
    _EMBEDDING_MODELS,
    EmbeddingsUnavailableError,
    _embed_chunks,
    embedding_model,
    embedding_provider,
    embeddings_available,
)


def _embedding_response(count: int = 2) -> httpx.Response:
    """A response in the envelope both vendors share, deliberately out of order."""
    return httpx.Response(
        200,
        json={
            "data": [
                {"embedding": [float(i)], "index": i}
                for i in reversed(range(count))
            ]
        },
        request=httpx.Request("POST", "https://example.invalid/"),
    )


def _capture_post(response: httpx.Response | None = None) -> AsyncMock:
    return AsyncMock(return_value=response or _embedding_response())


class TestProviderSelection:
    def test_openai_is_the_default(self, monkeypatch) -> None:
        """Existing deployments must not change vendor by upgrading."""
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", ""
        )
        assert embedding_provider() == "openai"
        assert embedding_model() == "text-embedding-3-small"

    def test_voyage_is_selectable(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyage"
        )
        assert embedding_provider() == "voyage"
        assert embedding_model() == "voyage-4"

    @pytest.mark.parametrize("raw", ["  VOYAGE  ", "Voyage", "voyage"])
    def test_the_setting_is_case_and_space_insensitive(self, monkeypatch, raw) -> None:
        """A stray space in an env var is not a reason to change vendor."""
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", raw
        )
        assert embedding_provider() == "voyage"

    def test_an_unknown_provider_falls_back_rather_than_raising(
        self, monkeypatch
    ) -> None:
        """A typo in one setting should not take down every RAG surface."""
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyahe"
        )
        assert embedding_provider() == "openai"

    def test_every_provider_has_a_model_and_a_dimension(self) -> None:
        """A provider missing a dimension is one nobody can build an index for."""
        assert set(_EMBEDDING_MODELS) == set(_EMBEDDING_DIMENSIONS)

    def test_the_two_vendors_have_different_dimensions(self) -> None:
        """The whole reason switching needs a new index.

        Pinecone rejects a vector of the wrong width, so this difference is what
        turns a silent retrieval failure into a loud write failure.
        """
        assert _EMBEDDING_DIMENSIONS["openai"] != _EMBEDDING_DIMENSIONS["voyage"]


class TestTheChatProviderNeverMovesTheEmbeddingProvider:
    """The exact bug this design exists to prevent."""

    @pytest.mark.parametrize("chat_provider", ["claude", "openai", "ollama"])
    def test_llm_provider_does_not_affect_the_embedding_vendor(
        self, monkeypatch, chat_provider
    ) -> None:
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyage"
        )
        monkeypatch.setattr(
            "app.core.config.settings.llm_provider", chat_provider, raising=False
        )
        assert embedding_provider() == "voyage"

    @pytest.mark.asyncio
    async def test_selecting_claude_for_chat_does_not_disable_embeddings(
        self, monkeypatch
    ) -> None:
        """Choosing Claude used to empty every RAG surface. It must not again."""
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "openai"
        )
        monkeypatch.setattr(
            "app.core.config.settings.llm_provider", "claude", raising=False
        )
        monkeypatch.setattr(
            "app.services.vector_service.settings.openai_api_key", "sk-test"
        )
        assert embeddings_available() is True


class TestAvailabilityFollowsTheSelectedVendor:
    def test_voyage_selected_needs_a_voyage_key(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyage"
        )
        monkeypatch.setattr("app.services.vector_service.settings.voyage_api_key", "")
        monkeypatch.setattr(
            "app.services.vector_service.settings.openai_api_key", "sk-test"
        )
        # An OpenAI key is not a Voyage key — availability must say no.
        assert embeddings_available() is False

        monkeypatch.setattr(
            "app.services.vector_service.settings.voyage_api_key", "pa-test"
        )
        assert embeddings_available() is True

    @pytest.mark.asyncio
    async def test_the_error_names_the_variable_to_set(self, monkeypatch) -> None:
        """"Set OPENAI_API_KEY" is the wrong instruction on a Voyage deployment."""
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyage"
        )
        monkeypatch.setattr("app.services.vector_service.settings.voyage_api_key", "")

        with pytest.raises(EmbeddingsUnavailableError) as exc:
            await _embed_chunks(["hello"])

        assert "VOYAGE_API_KEY" in exc.value.message
        assert exc.value.code == "EMBEDDINGS_UNAVAILABLE"


class TestVoyageWireFormat:
    @pytest.fixture(autouse=True)
    def _voyage(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "voyage"
        )
        monkeypatch.setattr(
            "app.services.vector_service.settings.voyage_api_key", "pa-secret"
        )

    @pytest.mark.asyncio
    async def test_it_calls_voyage_with_its_key_and_model(self) -> None:
        post = _capture_post()
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["a", "b"])

        assert post.await_args.args[0] == "https://api.voyageai.com/v1/embeddings"
        assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer pa-secret"
        assert post.await_args.kwargs["json"]["model"] == "voyage-4"

    @pytest.mark.asyncio
    async def test_stored_text_is_embedded_as_a_document(self) -> None:
        post = _capture_post()
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["a", "b"])

        assert post.await_args.kwargs["json"]["input_type"] == "document"

    @pytest.mark.asyncio
    async def test_a_search_string_is_embedded_as_a_query(self) -> None:
        """Voyage prepends a different instruction per type; its docs are
        explicit that omitting or mixing this costs retrieval quality."""
        post = _capture_post(_embedding_response(1))
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["what is X?"], input_type="query")

        assert post.await_args.kwargs["json"]["input_type"] == "query"

    @pytest.mark.asyncio
    async def test_vectors_come_back_in_input_order(self) -> None:
        """The response is deliberately out of order in the fixture.

        Both vendors return an `index` per item precisely because array order is
        not guaranteed; trusting position misaligns every vector with the wrong
        text, which no test of a single chunk would ever catch.
        """
        post = _capture_post(_embedding_response(3))
        with patch("httpx.AsyncClient.post", new=post):
            result = await _embed_chunks(["a", "b", "c"])

        assert result == [[0.0], [1.0], [2.0]]


class TestOpenAIWireFormatIsUnchanged:
    @pytest.fixture(autouse=True)
    def _openai(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.vector_service.settings.embedding_provider", "openai"
        )
        monkeypatch.setattr(
            "app.services.vector_service.settings.openai_api_key", "sk-secret"
        )

    @pytest.mark.asyncio
    async def test_it_still_calls_openai_with_the_same_model(self) -> None:
        post = _capture_post()
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["a", "b"])

        assert post.await_args.args[0] == "https://api.openai.com/v1/embeddings"
        assert post.await_args.kwargs["headers"]["Authorization"] == "Bearer sk-secret"
        assert post.await_args.kwargs["json"]["model"] == "text-embedding-3-small"

    @pytest.mark.asyncio
    async def test_input_type_is_not_sent_to_openai(self) -> None:
        """OpenAI has no such parameter; sending an unknown key risks a 400."""
        post = _capture_post()
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["a", "b"], input_type="query")

        assert "input_type" not in post.await_args.kwargs["json"]
