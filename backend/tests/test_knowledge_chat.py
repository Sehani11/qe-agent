"""Tests for the project knowledge-base RAG chat (POST /api/v1/chat/knowledge).

Covers:
- stream_knowledge_answer retrieves from the user's knowledge base, streams
  token events, then a sources event, then a terminal complete
- empty retrieval still produces a grounded answer + complete
- an LLM failure emits an error event and no complete
- the endpoint requires auth and streams tokens for the authed user

Strategy: mock query_knowledge_base and the LLM's generate_stream.
"""

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.knowledge_chat_service import stream_knowledge_answer

USER_A = "user-a"

_CHUNKS = [
    {
        "source": "confluence",
        "source_id": "123",
        "snippet": "The auth service uses JWT validated against Supabase JWKS.",
        "title": "Auth Architecture",
        "url": "https://confluence/x",
    },
    {
        "source": "jira",
        "source_id": "PROJ-9",
        "snippet": "Sessions persist to Postgres with RLS per user_id.",
        "title": "Session persistence",
        "url": "https://jira/PROJ-9",
    },
]


class _FakeLLM:
    def __init__(self, tokens: list[str]):
        self._tokens = tokens

    async def generate_stream(
        self, prompt: str, system_prompt: str = "", temperature: float = 0.7
    ):
        for token in self._tokens:
            yield token


async def _collect(gen) -> list[dict]:
    events = []
    async for chunk in gen:
        if chunk.startswith("data: "):
            events.append(json.loads(chunk[len("data: "):]))
    return events


# ---------------------------------------------------------------------------
# stream_knowledge_answer
# ---------------------------------------------------------------------------


class TestStreamKnowledgeAnswer:
    @pytest.mark.asyncio
    async def test_streams_tokens_then_sources_then_complete(self):
        llm = _FakeLLM(["Auth uses ", "JWT."])
        with patch(
            "app.services.knowledge_chat_service.query_knowledge_base",
            new=AsyncMock(return_value=_CHUNKS),
        ) as qkb:
            events = await _collect(
                stream_knowledge_answer(USER_A, "how does auth work?", llm)
            )

        # Retrieval used the user-scoped knowledge base
        assert qkb.await_args.args[0] == USER_A

        tokens = [e["content"] for e in events if e["type"] == "token"]
        assert "".join(tokens) == "Auth uses JWT."

        sources_events = [e for e in events if e["type"] == "sources"]
        assert len(sources_events) == 1
        assert sources_events[0]["sources"] == _CHUNKS

        assert events[-1]["type"] == "complete"
        # sources must arrive before complete
        assert events.index(sources_events[0]) < len(events) - 1

    @pytest.mark.asyncio
    async def test_empty_retrieval_still_answers(self):
        llm = _FakeLLM(["Not covered by the knowledge base."])
        with patch(
            "app.services.knowledge_chat_service.query_knowledge_base",
            new=AsyncMock(return_value=[]),
        ):
            events = await _collect(
                stream_knowledge_answer(USER_A, "unrelated?", llm)
            )

        tokens = [e["content"] for e in events if e["type"] == "token"]
        assert "".join(tokens) == "Not covered by the knowledge base."
        assert any(e["type"] == "complete" for e in events)
        # sources event still emitted (empty list)
        src = [e for e in events if e["type"] == "sources"]
        assert src and src[0]["sources"] == []

    @pytest.mark.asyncio
    async def test_llm_error_emits_error_and_no_complete(self):
        from app.services.llm.provider import LLMProviderError

        class _FailingLLM:
            async def generate_stream(self, prompt, system_prompt="", temperature=0.7):
                raise LLMProviderError("boom")
                yield  # pragma: no cover

        with patch(
            "app.services.knowledge_chat_service.query_knowledge_base",
            new=AsyncMock(return_value=_CHUNKS),
        ):
            events = await _collect(
                stream_knowledge_answer(USER_A, "q?", _FailingLLM())
            )

        assert any(e["type"] == "error" for e in events)
        assert not any(e["type"] == "complete" for e in events)


# ---------------------------------------------------------------------------
# POST /api/v1/chat/knowledge
# ---------------------------------------------------------------------------


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)


def test_post_knowledge_streams_tokens_for_authed_user(client_as_user_a):
    with (
        patch(
            "app.api.v1.chat.llm_for",
            return_value=_FakeLLM(["Hi", " there"]),
        ),
        patch(
            "app.services.knowledge_chat_service.query_knowledge_base",
            new=AsyncMock(return_value=_CHUNKS),
        ),
    ):
        resp = client_as_user_a.post(
            "/api/v1/chat/knowledge",
            json={"question": "what is the auth design?"},
        )

    assert resp.status_code == 200
    body = resp.text
    assert '"type": "token"' in body
    assert "Hi" in body and "there" in body
    assert '"type": "sources"' in body
    assert '"type": "complete"' in body


def test_post_knowledge_rejects_empty_question(client_as_user_a):
    resp = client_as_user_a.post("/api/v1/chat/knowledge", json={"question": ""})
    assert resp.status_code == 422
