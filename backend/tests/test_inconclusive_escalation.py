"""Second pass at a scenario the model declined to decide.

An "inconclusive" verdict is not an answer, it is a declined question, and the
model reaches for it early — measured runs returned it after two or three tool
calls out of a budget of twenty, with the same scenarios coming back
inconclusive run after run. Between a quarter and a third of one 16-scenario
suite was arriving undecided.

The prompt already makes the model say what evidence would settle it, so the
escalation hands that back and tells it to go look.

The tests that matter most here are the ones guarding the wording. Pushing a
model off "inconclusive" is only safe if it is pushed towards LOOKING, never
towards DECIDING: a guessed "fail" sends someone to rewrite working code, which
is worse than any number of abstentions.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agentic_verification_service import run_agentic_verification
from app.services.llm.provider import LLMProviderError, ToolLoopStats

from tests.test_agentic_verification_service import (
    _SINGLE_BDD,
    _collect,
    _make_db_session,
    _make_verdict_json,
    _parse_events,
    _simulate_tool_read,
)

_SCENARIO_ID = "00000000-0000-0000-0000-000000000001"
_TITLE = "User can log in"

_GAP = "The filter code could not be located; read src/api/interviewers.py."


def _inconclusive() -> str:
    return _make_verdict_json(
        _SCENARIO_ID,
        _TITLE,
        status="inconclusive",
        justification=(
            "Could not find where the filters are applied. "
            + _GAP
        ),
        implementation_suggestion=_GAP,
    )


def _decided(status: str = "pass") -> str:
    return _make_verdict_json(
        _SCENARIO_ID,
        _TITLE,
        status=status,
        justification="`applyFilters()` handles every filter in the route.",
        implementation_suggestion=(
            None if status == "pass" else "Add the missing filter branch."
        ),
    )


async def _run(llm, *, escalate: bool = True) -> list[dict]:
    with patch(
        "app.services.agentic_verification_service.settings."
        "verification_escalate_inconclusive",
        escalate,
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


def _scripted(*responses: str) -> tuple[MagicMock, list]:
    """An LLM that returns each response in turn, recording the messages sent."""
    sent: list[list[dict]] = []
    queue = list(responses)

    async def _gen(*args, **kwargs):
        sent.append(kwargs["messages"])
        await _simulate_tool_read(kwargs)
        return queue.pop(0) if queue else queue_last[0]

    queue_last = [responses[-1]]
    llm = MagicMock()
    llm.generate_with_tools = _gen
    return llm, sent


class TestEscalationFires:
    @pytest.mark.asyncio
    async def test_an_inconclusive_verdict_gets_a_second_pass(self) -> None:
        llm, sent = _scripted(_inconclusive(), _decided("pass"))
        events = await _run(llm)

        assert len(sent) == 2
        verdict = next(e for e in events if e["type"] == "verdict")
        assert verdict["status"] == "pass"

    @pytest.mark.asyncio
    async def test_a_decided_verdict_costs_no_second_pass(self) -> None:
        """Only the scenarios that were going to be useless anyway pay."""
        llm, sent = _scripted(_decided("pass"))
        events = await _run(llm)

        assert len(sent) == 1
        assert next(e for e in events if e["type"] == "verdict")["status"] == "pass"

    @pytest.mark.asyncio
    async def test_the_setting_turns_it_off(self) -> None:
        llm, sent = _scripted(_inconclusive())
        events = await _run(llm, escalate=False)

        assert len(sent) == 1
        assert (
            next(e for e in events if e["type"] == "verdict")["status"]
            == "inconclusive"
        )


class TestEscalationWording:
    """The directive is the safety boundary, so it is asserted on directly."""

    @pytest.mark.asyncio
    async def test_it_quotes_back_the_evidence_the_model_named(self) -> None:
        """Asking it to "look harder" with no direction just buys the same
        answer at twice the price. It already said what was missing."""
        llm, sent = _scripted(_inconclusive(), _decided("pass"))
        await _run(llm)

        directive = str(sent[1][-1]["content"])
        assert _GAP in directive

    @pytest.mark.asyncio
    async def test_it_says_inconclusive_is_still_allowed(self) -> None:
        """Without this the retry reads as "give me a real answer", and the
        model supplies one whether or not the evidence does — trading
        abstentions for guesses, which is a strictly worse trade."""
        llm, sent = _scripted(_inconclusive(), _decided("pass"))
        await _run(llm)

        directive = str(sent[1][-1]["content"]).lower()
        assert "still the correct answer" in directive
        assert "do not" in directive and "upgrade" in directive

    @pytest.mark.asyncio
    async def test_it_keeps_the_original_conversation(self) -> None:
        """The second pass continues the first rather than starting over, so
        the model is not re-reading the scenario cold."""
        llm, sent = _scripted(_inconclusive(), _decided("pass"))
        await _run(llm)

        assert sent[1][: len(sent[0])] == sent[0]


class TestEscalationNeverCostsAVerdict:
    @pytest.mark.asyncio
    async def test_a_failed_escalation_keeps_the_inconclusive_verdict(self) -> None:
        calls = {"n": 0}

        async def _gen(*args, **kwargs):
            calls["n"] += 1
            await _simulate_tool_read(kwargs)
            if calls["n"] == 1:
                return _inconclusive()
            raise LLMProviderError("provider exploded")

        llm = MagicMock()
        llm.generate_with_tools = _gen
        events = await _run(llm)

        verdict = next(e for e in events if e["type"] == "verdict")
        assert verdict["status"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_an_unparseable_escalation_keeps_the_original(self) -> None:
        llm, _ = _scripted(_inconclusive(), "I could not decide, sorry.")
        events = await _run(llm)

        verdict = next(e for e in events if e["type"] == "verdict")
        assert verdict["status"] == "inconclusive"

    @pytest.mark.asyncio
    async def test_a_verdict_with_nothing_to_chase_is_left_alone(self) -> None:
        """No stated gap means no direction to send it in.

        `_build_verdict` supplies a default suggestion when the model omits
        one, so this is rare — but paying for a second pass with nothing to
        point at is buying the same answer twice.
        """
        from app.services.agentic_verification_service import (
            _escalate_inconclusive,
        )

        empty = SimpleNamespace(
            status="inconclusive", justification="", implementation_suggestion=None
        )
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock()

        result = await _escalate_inconclusive(
            llm=llm,
            messages=[],
            verdict=empty,
            tool_executor=AsyncMock(),
            max_tool_rounds=5,
            tool_result_window=0,
            stats=ToolLoopStats(),
            scenario={"title": "x"},
            build=lambda payload: None,
        )

        assert result is None
        llm.generate_with_tools.assert_not_awaited()


class TestEscalationCost:
    @pytest.mark.asyncio
    async def test_both_passes_are_counted(self) -> None:
        """A cost line that hides a retry is how a run looks cheaper than it was."""
        calls = {"n": 0}

        async def _sequenced(*args, **kwargs):
            calls["n"] += 1
            await _simulate_tool_read(kwargs)
            stats = kwargs["stats"]
            stats.rounds = 3
            stats.input_tokens = 10_000
            stats.tool_calls = [["get_file_contents"]]
            return _inconclusive() if calls["n"] == 1 else _decided("pass")

        llm = MagicMock()
        llm.generate_with_tools = _sequenced
        events = await _run(llm)

        usage = next(e for e in events if e["type"] == "verdict")["usage"]
        assert usage["rounds"] == 6
        assert usage["input_tokens"] == 20_000
        # rounds_before_first_read still describes the FIRST pass, which is the
        # one a code index would have shortcut.
        assert usage["rounds_before_first_read"] == 0
