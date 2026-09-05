"""Tests for the RAG chat API (Story 5.1).

Covers:
- stream_chat_answer queries ONLY the {user_id}:{session_id} namespace (NFR-S4)
- persists exactly a user then an assistant ChatMessage with correct ids
- SSE stream yields token events then a terminal complete
- empty retrieval still produces a grounded answer + complete
- endpoint enforces session ownership (403 non-owner, 404 missing)

Strategy: mock _embed_chunks, pinecone.Pinecone, and the LLM's generate_stream.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.services.chat_service import stream_chat_answer

USER_A = "user-a"
USER_B = "user-b"
SESSION_ID = str(uuid.uuid4())


def _make_db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    return db


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


def _mock_pinecone(texts: list[str]):
    matches = []
    for t in texts:
        m = MagicMock()
        m.metadata = {"text": t}
        matches.append(m)
    mock_index = MagicMock()
    mock_index.query.return_value = MagicMock(matches=matches)
    mock_pc = MagicMock()
    mock_pc.Index.return_value = mock_index
    return mock_pc, mock_index


# ---------------------------------------------------------------------------
# stream_chat_answer
# ---------------------------------------------------------------------------


class TestStreamChatAnswer:
    @pytest.mark.asyncio
    async def test_queries_own_namespace_and_streams_tokens(self):
        db = _make_db()
        llm = _FakeLLM(["Hello", " world"])
        mock_pc, mock_index = _mock_pinecone(["Ticket detail about login"])

        with (
            patch(
                "app.services.chat_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.chat_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch("app.services.chat_service.settings.pinecone_api_key", new="k"),
        ):
            events = await _collect(
                stream_chat_answer(SESSION_ID, USER_A, "how does login work?", llm, db)
            )

        # NFR-S4: queried exactly the caller's own namespace
        expected_ns = f"{USER_A}:{SESSION_ID}"
        assert mock_index.query.call_args.kwargs["namespace"] == expected_ns
        # token events reconstruct the answer, terminated by complete
        tokens = [e["content"] for e in events if e["type"] == "token"]
        assert "".join(tokens) == "Hello world"
        assert events[-1]["type"] == "complete"

    @pytest.mark.asyncio
    async def test_persists_user_then_assistant(self):
        db = _make_db()
        llm = _FakeLLM(["Answer text"])
        mock_pc, _ = _mock_pinecone(["ctx"])

        with (
            patch(
                "app.services.chat_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.chat_service.pinecone.Pinecone",
                return_value=mock_pc,
            ),
            patch("app.services.chat_service.settings.pinecone_api_key", new="k"),
        ):
            await _collect(
                stream_chat_answer(SESSION_ID, USER_A, "my question?", llm, db)
            )

        added = [call.args[0] for call in db.add.call_args_list]
        assert len(added) == 2
        assert added[0].role == "user"
        assert added[0].content == "my question?"
        assert added[1].role == "assistant"
        assert added[1].content == "Answer text"
        assert all(a.user_id == USER_A and a.session_id == SESSION_ID for a in added)
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_empty_retrieval_still_answers(self):
        """No Pinecone key → no context, but the LLM still answers + completes."""
        db = _make_db()
        llm = _FakeLLM(["Not in this ticket."])

        with patch("app.services.chat_service.settings.pinecone_api_key", new=""):
            events = await _collect(
                stream_chat_answer(SESSION_ID, USER_A, "unrelated?", llm, db)
            )

        tokens = [e["content"] for e in events if e["type"] == "token"]
        assert "".join(tokens) == "Not in this ticket."
        assert any(e["type"] == "complete" for e in events)

    @pytest.mark.asyncio
    async def test_llm_error_emits_error_event(self):
        from app.services.llm.provider import LLMProviderError

        db = _make_db()

        class _FailingLLM:
            async def generate_stream(self, prompt, system_prompt="", temperature=0.7):
                raise LLMProviderError("boom")
                yield  # pragma: no cover

        with patch("app.services.chat_service.settings.pinecone_api_key", new=""):
            events = await _collect(
                stream_chat_answer(SESSION_ID, USER_A, "q?", _FailingLLM(), db)
            )

        assert any(e["type"] == "error" for e in events)
        assert not any(e["type"] == "complete" for e in events)


# ---------------------------------------------------------------------------
# POST /api/v1/chat/message — ownership
# ---------------------------------------------------------------------------


def _make_session(user_id: str) -> MagicMock:
    s = MagicMock()
    s.id = SESSION_ID
    s.user_id = user_id
    return s


def _scalar_result(items):
    r = MagicMock()
    r.scalar_one_or_none.return_value = items[0] if items else None
    sc = MagicMock()
    sc.all.return_value = items
    r.scalars.return_value = sc
    return r


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_post_message_403_for_non_owner(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([_make_session(USER_B)]))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/chat/message",
        json={"session_id": SESSION_ID, "question": "q"},
    )
    assert resp.status_code == 403


def test_post_message_404_for_missing_session(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_result([]))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/chat/message",
        json={"session_id": SESSION_ID, "question": "q"},
    )
    assert resp.status_code == 404


def test_post_message_streams_tokens_for_owner(client_as_user_a):
    """Happy path: owner POST streams token events then complete (L1)."""
    db = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result([_make_session(USER_A)]))
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch(
            "app.api.v1.chat.llm_for",
            return_value=_FakeLLM(["Hi", " there"]),
        ),
        patch("app.services.chat_service.settings.pinecone_api_key", new=""),
    ):
        resp = client_as_user_a.post(
            "/api/v1/chat/message",
            json={"session_id": SESSION_ID, "question": "hello?"},
        )

    assert resp.status_code == 200
    body = resp.text
    assert '"type": "token"' in body
    assert "Hi" in body and "there" in body
    assert '"type": "complete"' in body


def test_list_messages_returns_history_for_owner(client_as_user_a):
    msg = MagicMock()
    msg.id = uuid.uuid4()
    msg.session_id = SESSION_ID
    msg.role = "user"
    msg.content = "hi"
    from datetime import UTC, datetime
    msg.created_at = datetime(2026, 7, 4, tzinfo=UTC)

    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[_scalar_result([_make_session(USER_A)]), _scalar_result([msg])]
    )
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.get(f"/api/v1/chat/{SESSION_ID}/messages")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["role"] == "user"
    assert data[0]["content"] == "hi"
