"""Tests for the run-level cost and latency work.

Verifying many scenarios against one repository re-sends the same system prompt
and the same code evidence every time. These cover the three things that stop
that being paid for over and over: duplicates never reaching an LLM call, the
shared half of the prompt coming first and byte-identical so it can be cached,
and scenarios running concurrently without giving up ordering or the rule that
stops a run failing the same way twenty times.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.agentic_verification_service import run_agentic_verification
from app.services.llm.provider import LLMProviderError


class _AsyncSavepoint:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _db() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(side_effect=lambda: _AsyncSavepoint())
    return db


def _verdict(scenario_id: str, title: str) -> str:
    return json.dumps(
        {
            "scenario_id": scenario_id,
            "scenario_title": title,
            "status": "pass",
            "justification": "Handled in `auth.login()`.",
            "code_reference": {"file": "src/auth.py", "function": "login", "line": 1},
            "github_links": [],
            "implementation_suggestion": None,
        }
    )


async def _read_code(kwargs: dict) -> None:
    with patch(
        "app.services.github_tools.get_file_contents",
        new=AsyncMock(return_value="def login(): ..."),
    ):
        await kwargs["tool_executor"]("get_file_contents", {"path": "src/auth.py"})


def _bdd(*titles: str) -> str:
    return "Feature: F\n\n" + "\n".join(
        f"  Scenario: {t}\n    Given a user\n    When they do {t}\n"
        f"    Then {t} happens\n"
        for t in titles
    )


async def _collect(gen) -> list[dict]:
    events = []
    async for raw in gen:
        if raw.startswith("data: "):
            events.append(json.loads(raw[6:]))
    return events


async def _run(llm, bdd: str, **kwargs) -> list[dict]:
    return await _collect(
        run_agentic_verification(
            session_id="s",
            user_id="u",
            bdd_content=bdd,
            mode="full_repo",
            github_input="https://github.com/org/repo",
            llm=llm,
            db=_db(),
            pat="pat",
            **kwargs,
        )
    )


class TestDuplicatesNeverReachTheModel:
    @pytest.mark.asyncio
    async def test_a_repeated_scenario_is_not_verified_twice(self) -> None:
        calls = 0

        async def _gen(*args, **kwargs):
            nonlocal calls
            calls += 1
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "Alpha",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)

        events = await _run(llm, _bdd("Alpha", "Alpha", "Beta"))

        # Three scenarios in, two verified: the duplicate cost nothing.
        assert calls == 2
        plan = next(e for e in events if e["type"] == "plan")
        assert plan["total"] == 2
        assert plan["duplicates_skipped"] == 1
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["total"] == 2

    @pytest.mark.asyncio
    async def test_the_plan_is_announced_before_any_verdict(self) -> None:
        async def _gen(*args, **kwargs):
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "Alpha",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        events = await _run(llm, _bdd("Alpha"))

        # A client sizes its progress bar from this, so it cannot arrive late.
        assert events[0]["type"] == "plan"


class TestCacheablePromptPrefix:
    @pytest.mark.asyncio
    async def test_the_shared_prefix_is_identical_across_scenarios(self) -> None:
        seen: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            seen.append(kwargs["messages"])
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "Alpha",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        await _run(llm, _bdd("Alpha", "Beta"), max_concurrency=1)

        assert len(seen) == 2
        first, second = seen
        # System prompt and the shared user message must match byte for byte —
        # a prefix that varies is never a cache hit, however it is flagged.
        assert first[0] == second[0]
        assert first[1]["content"] == second[1]["content"]
        # ...and the scenario-specific half, which cannot be shared, comes last.
        assert first[-1]["content"] != second[-1]["content"]

    @pytest.mark.asyncio
    async def test_the_shared_message_carries_the_cache_flag(self) -> None:
        seen: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            seen.append(kwargs["messages"])
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "Alpha",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        await _run(llm, _bdd("Alpha"))

        messages = seen[0]
        assert messages[1].get("cache_control") is True
        # The per-scenario message must never be flagged: caching it would
        # store a prefix that changes every scenario.
        assert "cache_control" not in messages[-1]

    @pytest.mark.asyncio
    async def test_the_scenario_text_is_still_in_the_prompt(self) -> None:
        seen: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            seen.append(kwargs["messages"])
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "Alpha",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        await _run(llm, _bdd("Alpha"))

        prompt = "\n\n".join(str(m["content"]) for m in seen[0])
        assert "Alpha" in prompt
        assert "org/repo" in prompt


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_verdicts_are_streamed_in_bdd_order(self) -> None:
        """Concurrency must not scramble the report.

        Slowest first: if results were emitted as they completed, this ordering
        would invert, and a reader comparing the report to their feature file
        would have to hunt for each scenario.
        """
        import asyncio

        delays = {"Alpha": 0.05, "Beta": 0.02, "Gamma": 0.0}

        async def _gen(*args, **kwargs):
            content = kwargs["messages"][-1]["content"]
            title = next(t for t in delays if t in content)
            await asyncio.sleep(delays[title])
            await _read_code(kwargs)
            return _verdict(content.split("Scenario ID: ")[1].strip(), title)

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        events = await _run(llm, _bdd("Alpha", "Beta", "Gamma"), max_concurrency=3)

        titles = [e["scenario_title"] for e in events if e["type"] == "verdict"]
        assert titles == ["Alpha", "Beta", "Gamma"]

    @pytest.mark.asyncio
    async def test_scenarios_actually_overlap(self) -> None:
        import asyncio

        in_flight = 0
        peak = 0

        async def _gen(*args, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "x",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        await _run(llm, _bdd("A", "B", "C", "D"), max_concurrency=3)

        assert peak > 1, "scenarios ran one at a time"

    @pytest.mark.asyncio
    async def test_concurrency_is_bounded_by_the_window(self) -> None:
        import asyncio

        in_flight = 0
        peak = 0

        async def _gen(*args, **kwargs):
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            await _read_code(kwargs)
            return _verdict(
                kwargs["messages"][-1]["content"].split("Scenario ID: ")[1].strip(),
                "x",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        await _run(llm, _bdd("A", "B", "C", "D", "E", "F"), max_concurrency=2)

        assert peak <= 2

    @pytest.mark.asyncio
    async def test_a_repeating_failure_still_stops_the_run_early(self) -> None:
        """The stop rule must survive concurrency.

        Launching every scenario up front would run the whole batch before the
        first result was read, so a bad API key would cost a full run's calls
        before anyone noticed. The sliding window bounds that to what is in
        flight.
        """
        calls = 0

        async def _gen(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise LLMProviderError("no api key")

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        events = await _run(
            llm, _bdd("A", "B", "C", "D", "E", "F", "G", "H"), max_concurrency=2
        )

        errors = [e for e in events if e["type"] == "error"]
        assert any("the run was stopped" in e["message"] for e in errors)
        # Two consumed to detect the repeat, plus at most the window still in
        # flight — nowhere near all eight.
        assert calls <= 4, f"stopped too late: {calls} calls"
        assert not any(e["type"] == "complete" for e in events)


class TestWideEvidenceStillCaches:
    """More files than one prompt should carry, so each scenario picks its own.

    That per-scenario subset used to be folded into the message carrying the
    cache flag, which made the "shared" prefix different for every scenario —
    never a cache hit for anyone. The evidence still varies per scenario; what
    changed is that it now sits after the breakpoint instead of inside it.
    """

    @staticmethod
    def _many_files() -> list:
        from app.schemas.verification import FetchedFile

        # Seven, one past _MAX_FILES_PER_SCENARIO, so the shared block is
        # declined and the per-scenario path is the one under test.
        return [
            FetchedFile(
                path=f"src/mod_{i}.py",
                content=f"def feature_{i}():\n    return {i}\n",
            )
            for i in range(7)
        ]

    @pytest.mark.asyncio
    async def test_the_cached_prefix_stays_identical_and_evidence_still_arrives(
        self,
    ) -> None:
        captured: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            captured.append(kwargs["messages"])
            task = kwargs["messages"][-1]["content"]
            scenario_id = task.split("Scenario ID: ")[1].split("\n")[0]
            return _verdict(scenario_id, "t")

        llm = MagicMock()
        llm.generate_with_tools = _gen

        with patch(
            "app.services.agentic_verification_service.fetch_exact_files_resolved",
            new=AsyncMock(return_value=(self._many_files(), "main")),
        ):
            await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u",
                    bdd_content=_bdd("alpha", "beta"),
                    mode="exact_files",
                    github_input="https://github.com/org/repo/blob/main/src/mod_0.py",
                    llm=llm, db=_db(), pat="pat", max_concurrency=1,
                )
            )

        assert len(captured) == 2
        prefixes = [
            next(m for m in messages if m.get("cache_control"))["content"]
            for messages in captured
        ]
        # The whole point: byte-identical, so the second scenario is a cache hit.
        assert prefixes[0] == prefixes[1]
        assert "CODE:" not in prefixes[0]

        # And the evidence a verdict rests on is still in the prompt, just after
        # the breakpoint rather than inside it.
        for messages in captured:
            assert "CODE:" in messages[-1]["content"]
            assert "def feature_" in messages[-1]["content"]
