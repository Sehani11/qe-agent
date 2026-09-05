"""ClaudeProvider.generate_with_tools — Anthropic's tool-use protocol.

Callers hand this provider OpenAI-shaped messages and tools (that is what
`agentic_verification_service` builds), so most of what is covered here is the
translation into Anthropic's shape and back.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.claude_provider import (
    ClaudeProvider,
    _cacheable_system,
    _roll_conversation_breakpoint,
    _to_claude_messages,
)
from app.services.llm.provider import LLMProviderError

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_file_contents",
            "description": "Fetch a file from GitHub.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]

_MESSAGES = [
    {"role": "system", "content": "You are a verifier."},
    {"role": "user", "content": "Evaluate this scenario."},
]


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(id_: str, name: str, input_: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _reply(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks))


@pytest.fixture
def provider() -> ClaudeProvider:
    return ClaudeProvider(api_key="test-key", model="claude-sonnet-5")


@pytest.mark.asyncio
async def test_answers_without_calling_tools(provider: ClaudeProvider) -> None:
    """A reply carrying no tool_use block is the final answer."""
    executor = AsyncMock()

    with patch.object(
        provider._client.messages,
        "create",
        new=AsyncMock(return_value=_reply(_text('{"status": "pass"}'))),
    ):
        result = await provider.generate_with_tools(
            _MESSAGES, _TOOLS, executor, max_tool_rounds=5
        )

    assert json.loads(result) == {"status": "pass"}
    executor.assert_not_awaited()


@pytest.mark.asyncio
async def test_translates_messages_and_tools_to_anthropic_shape(
    provider: ClaudeProvider,
) -> None:
    """System is hoisted out of messages; tools gain `input_schema`.

    Anthropic rejects a role="system" entry in the messages array and does not
    read OpenAI's nested `function`/`parameters` — sending either unchanged is
    a 400, so this is the whole reason the provider translates.
    """
    create = AsyncMock(return_value=_reply(_text("done")))

    with patch.object(provider._client.messages, "create", new=create):
        await provider.generate_with_tools(
            _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5
        )

    kwargs = create.await_args.kwargs
    # System is sent as a block rather than a bare string so it can carry a
    # cache breakpoint; the text itself is unchanged.
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "You are a verifier.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert all(m["role"] != "system" for m in kwargs["messages"])
    assert kwargs["model"] == "claude-sonnet-5"
    assert kwargs["tools"] == [
        {
            "name": "get_file_contents",
            "description": "Fetch a file from GitHub.",
            "input_schema": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        }
    ]


@pytest.mark.asyncio
async def test_executes_a_tool_and_feeds_the_result_back(
    provider: ClaudeProvider,
) -> None:
    """One round trip: tool_use out, tool_result in, then the answer."""
    create = AsyncMock(
        side_effect=[
            _reply(_tool_use("tu_1", "get_file_contents", {"path": "app.py"})),
            _reply(_text("verdict")),
        ]
    )
    executor = AsyncMock(return_value="file contents here")

    with patch.object(provider._client.messages, "create", new=create):
        result = await provider.generate_with_tools(
            _MESSAGES, _TOOLS, executor, max_tool_rounds=5
        )

    assert result == "verdict"
    executor.assert_awaited_once_with("get_file_contents", {"path": "app.py"})

    sent = create.await_args_list[1].kwargs["messages"]
    # The assistant turn must be echoed back — the tool_result below refers to
    # the tool_use block it carries by id.
    assert sent[-2]["role"] == "assistant"
    # The newest tool_result additionally carries the rolling cache breakpoint,
    # so the next round reads the conversation so far from cache.
    assert sent[-1] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": "file contents here",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


@pytest.mark.asyncio
async def test_parallel_calls_return_in_one_user_message(
    provider: ClaudeProvider,
) -> None:
    """Splitting results across messages trains the model out of parallel calls."""
    create = AsyncMock(
        side_effect=[
            _reply(
                _tool_use("tu_1", "get_file_contents", {"path": "a.py"}),
                _tool_use("tu_2", "get_file_contents", {"path": "b.py"}),
            ),
            _reply(_text("verdict")),
        ]
    )
    executor = AsyncMock(side_effect=["contents a", "contents b"])

    with patch.object(provider._client.messages, "create", new=create):
        await provider.generate_with_tools(
            _MESSAGES, _TOOLS, executor, max_tool_rounds=5
        )

    results = create.await_args_list[1].kwargs["messages"][-1]
    assert results["role"] == "user"
    assert [block["tool_use_id"] for block in results["content"]] == ["tu_1", "tu_2"]


@pytest.mark.asyncio
async def test_final_round_forces_an_answer_within_the_call_budget(
    provider: ClaudeProvider,
) -> None:
    """A model that keeps calling tools is cut off, not left to loop.

    Total API calls stay within max_tool_rounds — the same budget
    OpenAIProvider honours — with the last one barred from using tools.
    """
    async def always_tools(**kwargs):
        if kwargs.get("tool_choice") == {"type": "none"}:
            return _reply(_text("forced verdict"))
        return _reply(_tool_use("tu_x", "get_file_contents", {"path": "a.py"}))

    with patch.object(
        provider._client.messages, "create", new=AsyncMock(side_effect=always_tools)
    ) as mocked:
        result = await provider.generate_with_tools(
            _MESSAGES, _TOOLS, AsyncMock(return_value="x"), max_tool_rounds=4
        )

    assert result == "forced verdict"
    assert mocked.await_count == 4
    assert mocked.await_args_list[-1].kwargs["tool_choice"] == {"type": "none"}


@pytest.mark.asyncio
async def test_api_errors_become_provider_errors(provider: ClaudeProvider) -> None:
    """SDK failures surface as LLMProviderError like every other provider call."""
    from anthropic import APIError
    from httpx import Request

    create = AsyncMock(
        side_effect=APIError(message="boom", request=Request("POST", ""), body={})
    )

    with (
        patch.object(provider._client.messages, "create", new=create),
        pytest.raises(LLMProviderError, match="Claude API error"),
    ):
        await provider.generate_with_tools(
            _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=3
        )


# ---------------------------------------------------------------------------
# Streaming — the picker makes Claude a chat provider, so it must stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_yields_incremental_text(provider: ClaudeProvider) -> None:
    """Tokens arrive as they are produced, not as one chunk at the end.

    The base class's fallback (`yield await self.generate(...)`) is a valid
    stream that produces exactly one chunk. Without this override, choosing
    Claude in the model picker silently turned chat from token-by-token into a
    long pause followed by the whole answer.
    """

    class _FakeStream:
        """Mirrors AsyncMessageStream: `text_stream` is an INSTANCE attribute
        holding an async iterator, assigned in __init__ — not a property. A
        property-shaped fake still satisfies `async for` and would keep passing
        against an SDK this code could no longer drive."""

        def __init__(self, tokens):
            self.text_stream = self._emit(tokens)

        @staticmethod
        async def _emit(tokens):
            for token in tokens:
                yield token

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    stream = MagicMock(return_value=_FakeStream(("Hel", "lo ", "there")))
    with patch.object(provider._client.messages, "stream", stream):
        chunks = [c async for c in provider.generate_stream("hi", "sys")]

    assert chunks == ["Hel", "lo ", "there"]
    assert "".join(chunks) == "Hello there"


@pytest.mark.asyncio
async def test_stream_passes_system_and_omits_temperature_on_a_5_model(
    provider: ClaudeProvider,
) -> None:
    """System prompt is hoisted to its own parameter, as the non-stream path does.

    Temperature is NOT sent: the fixture is a Sonnet 5, and Anthropic removed
    the sampling parameters from 4.7 onwards — sending one there is a 400 that
    fails the whole request.
    """

    class _FakeStream:
        def __init__(self):
            self.text_stream = self._emit()

        @staticmethod
        async def _emit():
            yield "ok"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    stream = MagicMock(return_value=_FakeStream())
    with patch.object(provider._client.messages, "stream", stream):
        [c async for c in provider.generate_stream("q", "be terse", temperature=0.2)]

    kwargs = stream.call_args.kwargs
    assert kwargs["system"] == "be terse"
    assert "temperature" not in kwargs
    assert kwargs["messages"] == [{"role": "user", "content": "q"}]


@pytest.mark.asyncio
async def test_stream_still_passes_temperature_on_an_older_model() -> None:
    """The parameter follows the model rather than being dropped outright —
    4.6 and earlier still take it, and the picker still offers Haiku 4.5."""
    older = ClaudeProvider(api_key="sk-ant-test", model="claude-haiku-4-5")

    class _FakeStream:
        def __init__(self):
            self.text_stream = self._emit()

        @staticmethod
        async def _emit():
            yield "ok"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    stream = MagicMock(return_value=_FakeStream())
    with patch.object(older._client.messages, "stream", stream):
        [c async for c in older.generate_stream("q", "be terse", temperature=0.2)]

    assert stream.call_args.kwargs["temperature"] == 0.2


@pytest.mark.asyncio
async def test_stream_errors_become_provider_errors(provider: ClaudeProvider) -> None:
    """A mid-stream SDK failure surfaces like every other provider call."""
    from anthropic import APIError
    from httpx import Request

    def _boom(**kwargs):
        raise APIError(message="boom", request=Request("POST", ""), body={})

    with (
        patch.object(provider._client.messages, "stream", _boom),
        pytest.raises(LLMProviderError, match="Claude API error"),
    ):
        [c async for c in provider.generate_stream("q")]


@pytest.mark.asyncio
async def test_a_failing_tool_is_not_blamed_on_the_api(
    provider: ClaudeProvider,
) -> None:
    """The executor is the CALLER's code, running inside this method's try.

    A crash in a GitHub tool was surfacing as "Unexpected error calling
    Claude", which sent anyone debugging it to the wrong module entirely — the
    reported `'list' object has no attribute 'get'` came from github_tools, not
    from the SDK.
    """
    create = AsyncMock(
        return_value=_reply(_tool_use("tu_1", "get_file_contents", {"path": "src"}))
    )
    executor = AsyncMock(
        side_effect=AttributeError("'list' object has no attribute 'get'")
    )

    with (
        patch.object(provider._client.messages, "create", new=create),
        pytest.raises(LLMProviderError) as caught,
    ):
        await provider.generate_with_tools(
            _MESSAGES, _TOOLS, executor, max_tool_rounds=5
        )

    assert "Tool 'get_file_contents' failed" in str(caught.value)
    assert "calling Claude" not in str(caught.value)


# ---------------------------------------------------------------------------
# Message translation — prompt caching and Anthropic's role alternation
# ---------------------------------------------------------------------------


class TestToClaudeMessages:
    def test_a_plain_message_becomes_a_text_block(self) -> None:
        result = _to_claude_messages([{"role": "user", "content": "hello"}])
        assert result == [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ]

    def test_a_flagged_message_carries_an_ephemeral_cache_breakpoint(self) -> None:
        result = _to_claude_messages(
            [{"role": "user", "content": "shared", "cache_control": True}]
        )
        assert result[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_the_flag_itself_never_reaches_the_api(self) -> None:
        # It is this codebase's convention, not an Anthropic field.
        result = _to_claude_messages(
            [{"role": "user", "content": "shared", "cache_control": True}]
        )
        assert "cache_control" not in result[0]

    def test_consecutive_user_messages_merge_into_one_turn(self) -> None:
        # Anthropic expects roles to alternate; the caller writes OpenAI-shaped
        # messages where two user turns in a row are ordinary.
        result = _to_claude_messages(
            [
                {"role": "user", "content": "shared", "cache_control": True},
                {"role": "user", "content": "scenario"},
            ]
        )
        assert len(result) == 1
        blocks = result[0]["content"]
        assert [b["text"] for b in blocks] == ["shared", "scenario"]
        # The breakpoint falls between them — that split is what makes the
        # shared half cacheable while the scenario half varies.
        assert blocks[0]["cache_control"] == {"type": "ephemeral"}
        assert "cache_control" not in blocks[1]

    def test_alternating_roles_stay_separate(self) -> None:
        result = _to_claude_messages(
            [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ]
        )
        assert [m["role"] for m in result] == ["user", "assistant", "user"]

    def test_empty_content_is_dropped(self) -> None:
        # An empty text block is rejected by the API and carries nothing.
        result = _to_claude_messages(
            [{"role": "user", "content": ""}, {"role": "user", "content": "real"}]
        )
        assert len(result) == 1
        assert result[0]["content"][0]["text"] == "real"

    def test_non_string_content_passes_through_untouched(self) -> None:
        # Tool results and echoed assistant turns are already in wire shape.
        tool_results = [{"type": "tool_result", "tool_use_id": "x", "content": "ok"}]
        result = _to_claude_messages([{"role": "user", "content": tool_results}])
        assert result == [{"role": "user", "content": tool_results}]


class TestPromptCaching:
    """Cache breakpoints — the agent loop replays every earlier round in full.

    A run verifies many scenarios against one repository, so the same system
    prompt, the same tool schemas and (within a scenario) the same tool output
    are sent over and over. These tests pin down where the breakpoints land,
    because a breakpoint in the wrong place is not a bug that shows up in
    behaviour — it just quietly stops saving anything.
    """

    def test_the_system_prompt_carries_a_breakpoint(self) -> None:
        """One breakpoint here covers the tool schemas too.

        Anthropic builds the cache prefix as tools -> system -> messages, so
        marking the end of system caches both.
        """
        assert _cacheable_system("instructions") == [
            {
                "type": "text",
                "text": "instructions",
                "cache_control": {"type": "ephemeral"},
            }
        ]

    def test_an_empty_system_prompt_stays_a_plain_string(self) -> None:
        """Nothing to cache, and no empty block for the API to reject."""
        assert _cacheable_system("") == ""

    def test_the_breakpoint_moves_to_the_newest_tool_result(self) -> None:
        conversation = [
            {"role": "user", "content": [{"type": "tool_result", "content": "old"}]},
            {"role": "user", "content": [{"type": "tool_result", "content": "new"}]},
        ]
        _roll_conversation_breakpoint(conversation)

        assert "cache_control" not in conversation[0]["content"][0]
        assert conversation[1]["content"][0]["cache_control"] == {"type": "ephemeral"}

    def test_the_shared_prefix_breakpoint_survives_the_roll(self) -> None:
        """The text-block breakpoint is what OTHER scenarios read from.

        Stripping it to make room would trade a cross-scenario saving for a
        within-scenario one — strictly worse, and silent.
        """
        conversation = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "shared evidence",
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
            },
            {"role": "user", "content": [{"type": "tool_result", "content": "r"}]},
        ]
        _roll_conversation_breakpoint(conversation)

        assert conversation[0]["content"][0]["cache_control"] == {"type": "ephemeral"}

    @pytest.mark.asyncio
    async def test_breakpoints_stay_within_the_api_cap(
        self, provider: ClaudeProvider
    ) -> None:
        """Anthropic rejects a request carrying more than four breakpoints.

        One per round would pass in testing and fail on the fifth round of a
        real run, which is exactly the kind of bug worth a test.
        """
        create = AsyncMock(
            side_effect=[
                _reply(_tool_use(f"tu_{i}", "get_file_contents", {"path": f"{i}.py"}))
                for i in range(6)
            ]
            + [_reply(_text("verdict"))]
        )

        with patch.object(provider._client.messages, "create", new=create):
            await provider.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(return_value="contents"), max_tool_rounds=8
            )

        for call in create.await_args_list:
            breakpoints = sum(
                1
                for message in call.kwargs["messages"]
                if isinstance(message.get("content"), list)
                for block in message["content"]
                if isinstance(block, dict) and "cache_control" in block
            )
            system = call.kwargs["system"]
            breakpoints += sum(
                1 for block in system if "cache_control" in block
            ) if isinstance(system, list) else 0
            assert breakpoints <= 4

    @pytest.mark.asyncio
    async def test_exactly_one_tool_result_breakpoint_per_request(
        self, provider: ClaudeProvider
    ) -> None:
        """The roll must move the marker, not accumulate markers."""
        create = AsyncMock(
            side_effect=[
                _reply(_tool_use("tu_1", "get_file_contents", {"path": "a.py"})),
                _reply(_tool_use("tu_2", "get_file_contents", {"path": "b.py"})),
                _reply(_text("verdict")),
            ]
        )

        with patch.object(provider._client.messages, "create", new=create):
            await provider.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(return_value="contents"), max_tool_rounds=5
            )

        final = create.await_args_list[-1].kwargs["messages"]
        marked = [
            block
            for message in final
            if isinstance(message.get("content"), list)
            for block in message["content"]
            if isinstance(block, dict)
            and block.get("type") == "tool_result"
            and "cache_control" in block
        ]
        assert len(marked) == 1
        assert marked[0]["tool_use_id"] == "tu_2"
