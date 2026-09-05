"""Per-project embedding vendor and Pinecone index.

An index holds vectors from exactly one embedding model, and a query is only
comparable to vectors that model produced. So the vendor and the index have to
agree — and they can only be made to agree if the same source decides both, on
both the write path and the read path.

That source is the project. It cannot be the chat model picker: a ticket
ingested while one vendor was selected would be invisible to a question asked
while another was, with no error anywhere. These tests hold that boundary and
cover the fallbacks.
"""

from unittest.mock import AsyncMock, patch

import pytest

from app.services.project_config_service import VectorConfig, vector_config_for
from app.services.vector_service import _EMBEDDING_DIMENSIONS, _embed_chunks


class _Project:
    """The two fields the resolver reads. Not a DB row — nothing here needs one."""

    def __init__(self, embedding_provider: str = "", pinecone_index_name: str = ""):
        self.embedding_provider = embedding_provider
        self.pinecone_index_name = pinecone_index_name


@pytest.fixture(autouse=True)
def _deployment_defaults(monkeypatch):
    """Pin the environment so a developer's .env cannot decide these outcomes."""
    monkeypatch.setattr(
        "app.core.config.settings.embedding_provider", "openai", raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.pinecone_index_name", "env-index", raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.openai_api_key", "sk-env", raising=False
    )
    monkeypatch.setattr(
        "app.core.config.settings.voyage_api_key", "pa-env", raising=False
    )


class TestResolution:
    def test_no_project_uses_the_deployment_settings(self) -> None:
        config = vector_config_for(None)
        assert config.provider == "openai"
        assert config.index_name == "env-index"
        assert config.source == "environment"

    def test_a_project_without_preferences_uses_them_too(self) -> None:
        config = vector_config_for(_Project())
        assert config.provider == "openai"
        assert config.index_name == "env-index"
        assert config.source == "environment"

    def test_a_project_can_set_the_vendor(self) -> None:
        config = vector_config_for(_Project(embedding_provider="voyage"))
        assert config.provider == "voyage"
        assert config.model == "voyage-4"
        assert config.source == "project"

    def test_a_project_can_set_the_index(self) -> None:
        config = vector_config_for(_Project(pinecone_index_name="proj-index"))
        assert config.index_name == "proj-index"
        assert config.source == "project"

    def test_the_halves_resolve_independently(self) -> None:
        """Naming an index without changing vendor is an ordinary setup.

        Unlike a credential set, there is no secret here to cross-contaminate,
        so the all-or-nothing rule the Jira/GitHub resolvers follow would only
        get in the way.
        """
        config = vector_config_for(_Project(pinecone_index_name="proj-index"))
        assert config.provider == "openai"  # from the environment
        assert config.index_name == "proj-index"  # from the project

    def test_surrounding_whitespace_is_not_a_preference(self) -> None:
        config = vector_config_for(_Project(embedding_provider="  voyage  "))
        assert config.provider == "voyage"

    def test_an_unknown_vendor_falls_back_rather_than_raising(self) -> None:
        """A typo in one project must not take down its whole knowledge base."""
        config = vector_config_for(_Project(embedding_provider="voyahe"))
        assert config.provider == "openai"

    def test_the_key_follows_the_vendor(self) -> None:
        voyage = vector_config_for(_Project(embedding_provider="voyage"))
        openai = vector_config_for(_Project(embedding_provider="openai"))
        assert voyage.api_key == "pa-env"
        assert openai.api_key == "sk-env"

    def test_the_key_is_never_read_from_the_project(self) -> None:
        """API keys are billing credentials for the deployment, by design.

        A project carrying its own key would let each project spend against a
        different account with no operator visibility.
        """
        project = _Project(embedding_provider="voyage")
        project.voyage_api_key = "pa-project-secret"  # type: ignore[attr-defined]
        assert vector_config_for(project).api_key == "pa-env"


class TestTheKeyIsNeverPrinted:
    def test_repr_masks_the_credential(self) -> None:
        """This object is passed through services that log and raise.

        A dataclass's generated repr prints every field, so one debug log or an
        unhandled traceback would put a live key in a log file.
        """
        text = repr(vector_config_for(_Project(embedding_provider="voyage")))
        assert "pa-env" not in text
        assert "api_key=<set>" in text

    def test_repr_still_says_when_a_key_is_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.core.config.settings.voyage_api_key", "", raising=False
        )
        text = repr(vector_config_for(_Project(embedding_provider="voyage")))
        assert "api_key=<missing>" in text

    def test_the_other_fields_are_still_visible(self) -> None:
        """Masking must not make the object useless for debugging."""
        text = repr(vector_config_for(_Project(pinecone_index_name="proj-index")))
        assert "proj-index" in text
        assert "openai" in text


class TestTheConfigActuallyReachesTheCall:
    @pytest.mark.asyncio
    async def test_a_projects_vendor_decides_the_endpoint(self) -> None:
        """The whole point of threading it: two projects, two vendors, one run."""
        config = vector_config_for(_Project(embedding_provider="voyage"))
        post = AsyncMock(
            return_value=_ok_response(),
        )
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["hello"], config=config)

        assert post.await_args.args[0] == "https://api.voyageai.com/v1/embeddings"
        assert post.await_args.kwargs["json"]["model"] == "voyage-4"

    @pytest.mark.asyncio
    async def test_a_different_project_gets_the_other_vendor(self) -> None:
        config = vector_config_for(_Project(embedding_provider="openai"))
        post = AsyncMock(return_value=_ok_response())
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["hello"], config=config)

        assert post.await_args.args[0] == "https://api.openai.com/v1/embeddings"

    @pytest.mark.asyncio
    async def test_no_config_still_uses_the_deployment_vendor(self) -> None:
        """Every pre-existing caller passes nothing and must be unaffected."""
        post = AsyncMock(return_value=_ok_response())
        with patch("httpx.AsyncClient.post", new=post):
            await _embed_chunks(["hello"])

        assert post.await_args.args[0] == "https://api.openai.com/v1/embeddings"


class TestDimensionsAreDocumented:
    def test_each_vendor_has_a_dimension(self) -> None:
        """The number someone needs when creating the index."""
        assert _EMBEDDING_DIMENSIONS["openai"] == 1536
        assert _EMBEDDING_DIMENSIONS["voyage"] == 1024

    def test_the_two_differ(self) -> None:
        """Why a vendor switch fails loudly at Pinecone instead of silently."""
        assert _EMBEDDING_DIMENSIONS["openai"] != _EMBEDDING_DIMENSIONS["voyage"]


def _ok_response():
    import httpx

    return httpx.Response(
        200,
        json={"data": [{"embedding": [0.0], "index": 0}]},
        request=httpx.Request("POST", "https://example.invalid/"),
    )


def test_vector_config_is_frozen() -> None:
    """Resolved once per request and passed down — nothing downstream may edit it."""
    config = vector_config_for(None)
    with pytest.raises(AttributeError):
        config.index_name = "somewhere-else"  # type: ignore[misc]
    assert isinstance(config, VectorConfig)
