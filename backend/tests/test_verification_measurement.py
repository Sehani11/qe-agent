"""Step 0 of the hybrid-discovery plan: measure the loop before optimising it.

Two mechanisms are covered here, and they exist for one reason — the claim that
semantic retrieval would cut verification cost by 40-60% was an estimate made
before the repository tree went into the cached prompt prefix, and nobody has
checked it since.

  - ``ToolLoopStats``: normalised per-scenario token and round counts, so two
    runs of the same BDD can be compared instead of argued about. The number
    that decides whether to build a code index is
    ``rounds_before_first_read`` — rounds the agent spent locating code before
    reading any.
  - Tool-result elision: the complementary lever the measurement exposes. The
    replay cost is dominated by large file reads re-sent every round, which
    retrieval does not touch.

Both are observational or default-off. The tests that matter most are the ones
asserting elision cannot be read as evidence of absence: a model that can no
longer see a file it read must not conclude the behaviour was missing.
"""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.llm.claude_provider import ClaudeProvider
from app.services.llm.openai_provider import OpenAIProvider
from app.services.llm.provider import ToolLoopStats

from tests.test_agentic_verification_service import (
    _SINGLE_BDD,
    _collect,
    _make_db_session,
    _make_verdict_json,
    _parse_events,
    _simulate_tool_read,
)
from app.services.agentic_verification_service import run_agentic_verification

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


# ---------------------------------------------------------------------------
# ToolLoopStats
# ---------------------------------------------------------------------------


class TestRoundsBefore:
    def test_reports_the_round_a_tool_was_first_requested(self) -> None:
        stats = ToolLoopStats(
            tool_calls=[["search_code"], ["list_directory"], ["get_file_contents"]]
        )
        assert stats.rounds_before("get_file_contents") == 2

    def test_zero_when_the_agent_went_straight_to_it(self) -> None:
        stats = ToolLoopStats(tool_calls=[["get_file_contents"]])
        assert stats.rounds_before("get_file_contents") == 0

    def test_none_rather_than_zero_when_never_requested(self) -> None:
        """A run that never read a file is not a run that read one immediately.

        Collapsing the two would report the best possible result for the worst
        possible run, in the exact metric being used to justify a decision.
        """
        stats = ToolLoopStats(tool_calls=[["search_code"]])
        assert stats.rounds_before("get_file_contents") is None


# ---------------------------------------------------------------------------
# Claude: usage normalisation and round accounting
# ---------------------------------------------------------------------------


def _text(text: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=text)


def _tool_use(id_: str, name: str, input_: dict) -> SimpleNamespace:
    return SimpleNamespace(type="tool_use", id=id_, name=name, input=input_)


def _claude_reply(
    *blocks: SimpleNamespace,
    uncached: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    output: int = 0,
) -> SimpleNamespace:
    return SimpleNamespace(
        content=list(blocks),
        usage=SimpleNamespace(
            input_tokens=uncached,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
            output_tokens=output,
        ),
    )


@pytest.fixture
def claude() -> ClaudeProvider:
    return ClaudeProvider(api_key="test-key", model="claude-sonnet-5")


class TestClaudeUsage:
    @pytest.mark.asyncio
    async def test_sums_the_three_prompt_buckets_into_one_total(
        self, claude: ClaudeProvider
    ) -> None:
        """Anthropic's own ``input_tokens`` excludes anything served from cache.

        Recording it raw would make a well-cached Claude run look an order of
        magnitude cheaper than an identical OpenAI one, purely because the two
        vendors count differently — and the whole point of these numbers is
        comparing runs.
        """
        stats = ToolLoopStats()
        reply = _claude_reply(
            _text('{"status": "pass"}'),
            uncached=100,
            cache_read=900,
            cache_write=50,
            output=25,
        )

        with patch.object(
            claude._client.messages, "create", new=AsyncMock(return_value=reply)
        ):
            await claude.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5, stats=stats
            )

        assert stats.input_tokens == 1050
        assert stats.cached_input_tokens == 900
        assert stats.output_tokens == 25
        assert stats.rounds == 1

    @pytest.mark.asyncio
    async def test_records_tool_names_per_round(self, claude: ClaudeProvider) -> None:
        replies = [
            _claude_reply(_tool_use("t1", "list_directory", {"path": ""})),
            _claude_reply(_tool_use("t2", "get_file_contents", {"path": "a.py"})),
            _claude_reply(_text('{"status": "pass"}')),
        ]
        stats = ToolLoopStats()

        with patch.object(
            claude._client.messages, "create", new=AsyncMock(side_effect=replies)
        ):
            await claude.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="contents"),
                max_tool_rounds=5,
                stats=stats,
            )

        assert stats.tool_calls == [["list_directory"], ["get_file_contents"]]
        assert stats.rounds_before("get_file_contents") == 1
        # Three API calls were made, including the one that answered.
        assert stats.rounds == 3

    @pytest.mark.asyncio
    async def test_a_run_without_stats_is_unaffected(
        self, claude: ClaudeProvider
    ) -> None:
        """Measurement is optional; omitting it must not change any behaviour."""
        with patch.object(
            claude._client.messages,
            "create",
            new=AsyncMock(return_value=_claude_reply(_text("done"))),
        ):
            result = await claude.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5
            )

        assert result == "done"

    @pytest.mark.asyncio
    async def test_missing_usage_records_zero_rather_than_raising(
        self, claude: ClaudeProvider
    ) -> None:
        """Usage fields come and go between SDK versions.

        A cost number is never worth failing a verification run over.
        """
        stats = ToolLoopStats()
        with patch.object(
            claude._client.messages,
            "create",
            new=AsyncMock(return_value=SimpleNamespace(content=[_text("done")])),
        ):
            await claude.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5, stats=stats
            )

        assert stats.input_tokens == 0
        assert stats.rounds == 1


# ---------------------------------------------------------------------------
# OpenAI: usage and round accounting
# ---------------------------------------------------------------------------


def _openai_call(id_: str, name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(args))
    )


def _openai_reply(
    *,
    content: str | None = None,
    tool_calls: list | None = None,
    finish_reason: str = "tool_calls",
    prompt: int = 0,
    cached: int = 0,
    output: int = 0,
) -> SimpleNamespace:
    message = MagicMock()
    message.content = content
    message.tool_calls = tool_calls
    message.model_dump.return_value = {"role": "assistant", "tool_calls": []}
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish_reason, message=message)],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=output,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )


@pytest.fixture
def openai() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key", model="gpt-4o")


class TestOpenAIUsage:
    @pytest.mark.asyncio
    async def test_records_the_total_and_the_cached_portion(
        self, openai: OpenAIProvider
    ) -> None:
        """``prompt_tokens`` already includes cache hits, so it is the total.

        The cached portion is still broken out, because on OpenAI it counts
        against the per-minute token limit — the constraint that actually
        stops these runs — even though it is discounted on the bill.
        """
        stats = ToolLoopStats()
        reply = _openai_reply(
            content='{"status": "pass"}',
            finish_reason="stop",
            prompt=1000,
            cached=800,
            output=30,
        )

        with patch.object(
            openai._client.chat.completions, "create", new=AsyncMock(return_value=reply)
        ):
            await openai.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5, stats=stats
            )

        assert stats.input_tokens == 1000
        assert stats.cached_input_tokens == 800
        assert stats.output_tokens == 30
        assert stats.rounds == 1

    @pytest.mark.asyncio
    async def test_counts_rounds_before_the_first_file_read(
        self, openai: OpenAIProvider
    ) -> None:
        replies = [
            _openai_reply(tool_calls=[_openai_call("c1", "search_code", {"query": "x"})]),
            _openai_reply(
                tool_calls=[_openai_call("c2", "list_directory", {"path": ""})]
            ),
            _openai_reply(
                tool_calls=[_openai_call("c3", "get_file_contents", {"path": "a.py"})]
            ),
            _openai_reply(content='{"status": "pass"}', finish_reason="stop"),
        ]
        stats = ToolLoopStats()

        with patch.object(
            openai._client.chat.completions,
            "create",
            new=AsyncMock(side_effect=replies),
        ):
            await openai.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="contents"),
                max_tool_rounds=8,
                stats=stats,
            )

        assert stats.rounds_before("get_file_contents") == 2
        assert stats.rounds == 4


# ---------------------------------------------------------------------------
# Tool-result elision
# ---------------------------------------------------------------------------


def _file_read_replies(count: int, vendor: str) -> list:
    """`count` rounds that each read one file, then a final answer."""
    if vendor == "claude":
        return [
            _claude_reply(_tool_use(f"t{i}", "get_file_contents", {"path": f"f{i}.py"}))
            for i in range(count)
        ] + [_claude_reply(_text('{"status": "pass"}'))]
    return [
        _openai_reply(
            tool_calls=[_openai_call(f"c{i}", "get_file_contents", {"path": f"f{i}.py"})]
        )
        for i in range(count)
    ] + [_openai_reply(content='{"status": "pass"}', finish_reason="stop")]


class TestDeterminism:
    """A verdict is a judgement, so the same input must reach the same answer.

    This loop ran at the provider default of 1.0 — the most random setting
    there is — while `generate_structured` next to it used 0.2. It showed up as
    identical runs reading wildly different amounts of code and as verdicts
    moving between "pass" and "inconclusive" with the code under test
    unchanged. A report that contradicts itself on a rerun is worse than a
    slow one.
    """

    @pytest.mark.asyncio
    async def test_openai_pins_the_temperature(
        self, openai: OpenAIProvider
    ) -> None:
        from app.services.llm.provider import TOOL_LOOP_TEMPERATURE

        create = AsyncMock(
            return_value=_openai_reply(content="{}", finish_reason="stop")
        )
        with patch.object(openai._client.chat.completions, "create", new=create):
            await openai.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5
            )

        assert create.await_args.kwargs["temperature"] == TOOL_LOOP_TEMPERATURE

    @pytest.mark.asyncio
    async def test_claude_pins_the_temperature_on_models_that_take_one(
        self,
    ) -> None:
        from app.services.llm.provider import TOOL_LOOP_TEMPERATURE

        provider = ClaudeProvider(api_key="k", model="claude-3-5-sonnet-20240620")
        create = AsyncMock(return_value=_claude_reply(_text("done")))
        with patch.object(provider._client.messages, "create", new=create):
            await provider.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5
            )

        assert create.await_args.kwargs["temperature"] == TOOL_LOOP_TEMPERATURE

    @pytest.mark.asyncio
    async def test_claude_omits_it_where_the_parameter_was_removed(self) -> None:
        """Claude 4.7 onwards rejects `temperature` with a 400.

        Sending it would fail the whole request, so the newer models simply go
        without — they are low-variance by default.
        """
        provider = ClaudeProvider(api_key="k", model="claude-sonnet-5")
        create = AsyncMock(return_value=_claude_reply(_text("done")))
        with patch.object(provider._client.messages, "create", new=create):
            await provider.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(), max_tool_rounds=5
            )

        assert "temperature" not in create.await_args.kwargs


class TestOpenAIElision:
    @pytest.mark.asyncio
    async def test_keeps_the_window_and_replaces_older_results(
        self, openai: OpenAIProvider
    ) -> None:
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "openai")

        with patch.object(
            openai._client.chat.completions, "create", new=AsyncMock(side_effect=_create)
        ):
            await openai.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="THE REAL FILE BODY"),
                max_tool_rounds=8,
                tool_result_window=2,
            )

        results = [m for m in sent[-1] if m.get("role") == "tool"]
        assert len(results) == 3
        # Oldest elided, two newest intact.
        assert "removed from this transcript" in results[0]["content"]
        assert results[1]["content"] == "THE REAL FILE BODY"
        assert results[2]["content"] == "THE REAL FILE BODY"

    @pytest.mark.asyncio
    async def test_the_placeholder_names_the_call_and_denies_being_evidence(
        self, openai: OpenAIProvider
    ) -> None:
        """The wording is the accuracy guard, so it is asserted on directly.

        Without it, a model whose earlier read has vanished can decide the
        behaviour it was looking for is not implemented — a false "fail",
        which is the failure mode the verification prompt's evidence rules
        exist to prevent.
        """
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "openai")

        with patch.object(
            openai._client.chat.completions, "create", new=AsyncMock(side_effect=_create)
        ):
            await openai.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="body"),
                max_tool_rounds=8,
                tool_result_window=1,
            )

        elided = [m for m in sent[-1] if m.get("role") == "tool"][0]["content"]
        # Which call was dropped, so the model can re-issue exactly that one.
        assert 'get_file_contents(path="f0.py")' in elided
        assert "SUCCEEDED" in elided
        assert "NOT evidence" in elided

    @pytest.mark.asyncio
    async def test_an_elided_result_keeps_its_tool_call_id(
        self, openai: OpenAIProvider
    ) -> None:
        """The assistant turn requesting it is still there.

        OpenAI rejects a conversation where a tool call has no matching
        result, so eliding the body must never drop the linkage.
        """
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "openai")

        with patch.object(
            openai._client.chat.completions, "create", new=AsyncMock(side_effect=_create)
        ):
            await openai.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="body"),
                max_tool_rounds=8,
                tool_result_window=1,
            )

        for message in [m for m in sent[-1] if m.get("role") == "tool"]:
            assert message["tool_call_id"]

    @pytest.mark.asyncio
    async def test_off_by_default_nothing_is_replaced(
        self, openai: OpenAIProvider
    ) -> None:
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "openai")

        with patch.object(
            openai._client.chat.completions, "create", new=AsyncMock(side_effect=_create)
        ):
            await openai.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(return_value="body"), max_tool_rounds=8
            )

        results = [m for m in sent[-1] if m.get("role") == "tool"]
        assert [m["content"] for m in results] == ["body", "body", "body"]


class TestClaudeElision:
    @pytest.mark.asyncio
    async def test_keeps_the_window_and_replaces_older_results(
        self, claude: ClaudeProvider
    ) -> None:
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "claude")

        with patch.object(
            claude._client.messages, "create", new=AsyncMock(side_effect=_create)
        ):
            await claude.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="THE REAL FILE BODY"),
                max_tool_rounds=8,
                tool_result_window=2,
            )

        blocks = _tool_result_blocks(sent[-1])
        assert len(blocks) == 3
        assert "removed from this transcript" in blocks[0]["content"]
        assert blocks[1]["content"] == "THE REAL FILE BODY"
        assert blocks[2]["content"] == "THE REAL FILE BODY"

    @pytest.mark.asyncio
    async def test_an_elided_block_keeps_its_tool_use_id_and_sends_no_extra_keys(
        self, claude: ClaudeProvider
    ) -> None:
        """Anthropic rejects an unrecognised key inside a tool_result.

        The label naming the elided call is therefore held beside the block
        rather than on it.
        """
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "claude")

        with patch.object(
            claude._client.messages, "create", new=AsyncMock(side_effect=_create)
        ):
            await claude.generate_with_tools(
                _MESSAGES,
                _TOOLS,
                AsyncMock(return_value="body"),
                max_tool_rounds=8,
                tool_result_window=1,
            )

        for block in _tool_result_blocks(sent[-1]):
            assert block["tool_use_id"]
            assert set(block) <= {"type", "tool_use_id", "content", "cache_control"}

    @pytest.mark.asyncio
    async def test_off_by_default_nothing_is_replaced(
        self, claude: ClaudeProvider
    ) -> None:
        sent: list[list[dict]] = []

        async def _create(**kwargs):
            sent.append(copy.deepcopy(kwargs["messages"]))
            return replies.pop(0)

        replies = _file_read_replies(3, "claude")

        with patch.object(
            claude._client.messages, "create", new=AsyncMock(side_effect=_create)
        ):
            await claude.generate_with_tools(
                _MESSAGES, _TOOLS, AsyncMock(return_value="body"), max_tool_rounds=8
            )

        blocks = _tool_result_blocks(sent[-1])
        assert [b["content"] for b in blocks] == ["body", "body", "body"]


def _tool_result_blocks(conversation: list[dict]) -> list[dict]:
    """Every tool_result block in an Anthropic conversation, oldest first."""
    blocks = []
    for message in conversation:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        blocks.extend(
            b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"
        )
    return blocks


# ---------------------------------------------------------------------------
# The verification service reports what it measured
# ---------------------------------------------------------------------------


async def _run_with(llm, window: int = 0) -> list[dict]:
    with patch(
        "app.services.agentic_verification_service.settings."
        "verification_tool_result_window",
        window,
    ):
        events = await _collect(
            run_agentic_verification(
                session_id="s",
                user_id="u",
                bdd_content=_SINGLE_BDD,
                mode="full_repo",
                github_input="https://github.com/org/repo",
                llm=llm,
                db=_make_db_session(),
                pat="pat",
            )
        )
    return _parse_events(events)


class TestServiceReportsUsage:
    @pytest.mark.asyncio
    async def test_verdict_and_complete_events_carry_the_numbers(self) -> None:
        """Streaming them is what lets one run be compared against another."""

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            stats = kwargs["stats"]
            stats.rounds = 4
            stats.input_tokens = 30_000
            stats.cached_input_tokens = 12_000
            stats.output_tokens = 500
            stats.tool_calls = [["search_code"], ["get_file_contents"]]
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        parsed = await _run_with(llm)

        verdict = next(e for e in parsed if e["type"] == "verdict")
        assert verdict["usage"] == {
            "rounds": 4,
            "input_tokens": 30_000,
            "cached_input_tokens": 12_000,
            "output_tokens": 500,
            "rounds_before_first_read": 1,
        }

        complete = next(e for e in parsed if e["type"] == "complete")
        assert complete["usage"]["input_tokens"] == 30_000
        assert complete["usage"]["rounds"] == 4
        assert complete["usage"]["tool_result_window"] == 0

    @pytest.mark.asyncio
    async def test_a_scenario_whose_call_failed_reports_no_invented_cost(self) -> None:
        """Stats are kept only from the attempt that produced a verdict.

        A retried scenario starts its conversation over, so accumulating
        across attempts would report rounds that never ran together.
        """
        calls = {"n": 0}

        async def _gen(*args, **kwargs):
            calls["n"] += 1
            kwargs["stats"].rounds = 9
            if calls["n"] == 1:
                from app.services.llm.provider import LLMProviderError

                raise LLMProviderError("busy", retryable=True)
            await _simulate_tool_read(kwargs)
            kwargs["stats"].rounds = 2
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen

        with patch(
            "app.services.agentic_verification_service._RATE_LIMIT_BACKOFF_SECONDS", 0
        ):
            parsed = await _run_with(llm)

        verdict = next(e for e in parsed if e["type"] == "verdict")
        assert verdict["usage"]["rounds"] == 2


class TestElisionRuleReachesThePrompt:
    @pytest.mark.asyncio
    async def test_the_rule_is_added_only_when_the_window_is_on(self) -> None:
        """The system prompt is the cached prefix.

        Adding the rule unconditionally would change it for every run,
        including those that never elide anything.
        """
        captured: dict = {}

        async def _gen(*args, **kwargs):
            captured["system"] = kwargs["messages"][0]["content"]
            captured["window"] = kwargs["tool_result_window"]
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen

        await _run_with(llm, window=0)
        assert "removed from the transcript" not in captured["system"]
        assert captured["window"] == 0

        await _run_with(llm, window=2)
        assert "removed from the transcript" in captured["system"]
        assert "never evidence that anything is missing" in captured["system"]
        assert captured["window"] == 2
