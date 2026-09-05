"""A rate limit must not end a verification run.

The run-level stop rule exists for a real problem: a missing API key fails every
scenario identically, and letting it try twenty times buries the one fact the
user needs. Its original test for "is this about the run or about this scenario"
was whether the error message repeated word for word, on the reasoning that a
transient failure would not.

That reasoning was wrong. A provider renders a rate limit as one fixed sentence
regardless of input, so the most ordinary transient failure there is looked
exactly like a missing key — and a run that needed to wait a few seconds was
stopped instead, and blamed on the user's configuration.

Verification also grew from strictly sequential to several scenarios in flight,
each carrying evidence blocks raised eightfold to fix a truncation bug. Those
caps are an accuracy guarantee and are not the thing to trade away; the number
of requests in flight is, which is why concurrency is configurable and starts
low.
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


_BDD = """\
Feature: F

  Scenario: alpha
    Given a user
    When they do alpha
    Then it works

  Scenario: beta
    Given a user
    When they do beta
    Then it works
"""


def _verdict(scenario_id: str) -> str:
    return json.dumps(
        {
            "scenario_id": scenario_id,
            "scenario_title": "t",
            "status": "pass",
            "justification": "Handled in `login()`.",
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


async def _collect(gen) -> list[dict]:
    events = []
    async for chunk in gen:
        for line in chunk.strip().split("\n"):
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def _rate_limited() -> LLMProviderError:
    """The exact shape both providers now raise for a 429."""
    return LLMProviderError(
        "The AI service is busy right now (rate limit reached). "
        "Please try again in a few seconds.",
        retryable=True,
    )


async def _run(llm, **kwargs) -> list[dict]:
    # Backoff is patched out: the delays are real seconds, and what is under
    # test is the control flow rather than the clock.
    kwargs.setdefault("max_concurrency", 1)
    with patch(
        "app.services.agentic_verification_service.asyncio.sleep", new=AsyncMock()
    ):
        return await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=_db(), pat="pat", **kwargs,
            )
        )


class TestProvidersSayWhichKindOfFailure:
    def test_a_plain_provider_error_is_not_retryable(self) -> None:
        """The default has to be "no": retrying an unrecoverable failure just
        fails more slowly."""
        assert LLMProviderError("no API key configured").retryable is False

    def test_the_flag_survives_being_raised_and_caught(self) -> None:
        try:
            raise _rate_limited()
        except LLMProviderError as exc:
            assert exc.retryable is True

    def test_openai_marks_every_rate_limit_retryable(self) -> None:
        import inspect

        from app.services.llm import openai_provider

        source = inspect.getsource(openai_provider)
        # One handler that forgets the flag silently restores the stopped-run
        # bug, so the count has to match rather than merely be non-zero.
        assert source.count("except RateLimitError") >= 1
        assert source.count("except RateLimitError") == source.count("retryable=True")

    def test_claude_marks_every_rate_limit_retryable(self) -> None:
        import inspect

        from app.services.llm import claude_provider

        source = inspect.getsource(claude_provider)
        assert source.count("except RateLimitError") >= 1
        assert source.count("except RateLimitError") == source.count("retryable=True")

    def test_claude_catches_rate_limits_before_the_general_api_error(self) -> None:
        """`RateLimitError` subclasses `APIError`.

        Ordered the other way the general handler wins, the flag is never set,
        and the bug returns with no visible change at the call site.
        """
        import inspect

        from app.services.llm import claude_provider

        source = inspect.getsource(claude_provider)
        assert source.index("except RateLimitError") < source.index("except APIError")


class TestQuotaIsNotTheSameAs429:
    """OpenAI answers 429 for two unrelated things.

    `rate_limit_exceeded` recovers by waiting; `insufficient_quota` never does.
    Telling someone to "try again in a few seconds" when their key has no
    credit sends them to watch a request that cannot succeed.
    """

    @staticmethod
    def _error(code: str | None):
        from openai import RateLimitError

        exc = RateLimitError.__new__(RateLimitError)
        exc.code = code
        exc.body = {"code": code} if code else None
        return exc

    def test_an_exhausted_quota_says_waiting_will_not_help(self) -> None:
        from app.services.llm.openai_provider import _rate_limit_message

        message = _rate_limit_message(self._error("insufficient_quota"))
        assert "no remaining quota" in message
        assert "Waiting will not help" in message

    def test_an_ordinary_rate_limit_still_says_try_again(self) -> None:
        from app.services.llm.openai_provider import _rate_limit_message

        message = _rate_limit_message(self._error("rate_limit_exceeded"))
        assert "try again in a few seconds" in message.lower()

    def test_an_unlabelled_429_falls_back_to_the_generic_message(self) -> None:
        from app.services.llm.openai_provider import _rate_limit_message

        assert "busy right now" in _rate_limit_message(self._error(None))


class TestARateLimitDoesNotStopTheRun:
    @pytest.mark.asyncio
    async def test_a_rate_limited_scenario_is_retried_and_succeeds(self) -> None:
        """The whole point: a transient failure costs a pause, not the run."""
        calls = {"n": 0}

        async def _gen(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise _rate_limited()
            await _read_code(kwargs)
            task = kwargs["messages"][-1]["content"]
            return _verdict(task.split("Scenario ID: ")[1].split("\n")[0])

        llm = MagicMock()
        llm.generate_with_tools = _gen
        events = await _run(llm)

        verdicts = [e for e in events if e["type"] == "verdict"]
        assert len(verdicts) == 2
        assert all(v["status"] == "pass" for v in verdicts)
        assert not [e for e in events if e["type"] == "error"]

    @pytest.mark.asyncio
    async def test_a_persistent_rate_limit_skips_the_scenario_not_the_run(
        self,
    ) -> None:
        """Every scenario rate limited, identically — the exact case that used
        to be reported as a configuration problem and stop everything."""
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_rate_limited())
        events = await _run(llm)

        errors = [e for e in events if e["type"] == "error"]
        assert len(errors) == 2  # one per scenario; neither ended the run
        assert next(e for e in events if e["type"] == "complete")["total"] == 2

    @pytest.mark.asyncio
    async def test_the_message_says_transient_not_misconfigured(self) -> None:
        """Telling someone to check their configuration when the fix is to wait
        sends them to debug something that is not broken."""
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_rate_limited())
        events = await _run(llm)

        message = next(e for e in events if e["type"] == "error")["message"]
        assert "configuration problem" not in message
        assert "rate limit" in message.lower()
        assert "VERIFICATION_MAX_CONCURRENCY" in message


class TestUnrecoverableErrorsStillStopTheRun:
    @pytest.mark.asyncio
    async def test_an_identical_non_retryable_error_ends_the_run(self) -> None:
        """The original guard must survive the fix.

        A missing key fails the twentieth call exactly as it failed the first,
        and stacking twenty identical errors buries the one fact that matters.
        """
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            side_effect=LLMProviderError("No OpenAI API key is configured.")
        )
        events = await _run(llm)

        errors = [e for e in events if e["type"] == "error"]
        assert "the run was stopped" in errors[-1]["message"]
        assert "configuration problem" in errors[-1]["message"]
        assert not [e for e in events if e["type"] == "complete"]


class TestConcurrencyIsTheRateLimitDial:
    @pytest.mark.asyncio
    async def test_the_setting_supplies_the_default(self) -> None:
        """The throttle a rate-limited user is told to reach for has to work."""

        async def _gen(*args, **kwargs):
            await _read_code(kwargs)
            task = kwargs["messages"][-1]["content"]
            return _verdict(task.split("Scenario ID: ")[1].split("\n")[0])

        llm = MagicMock()
        llm.generate_with_tools = _gen

        # Only the one field is patched. Replacing the whole settings object
        # hands the service a MagicMock for every OTHER setting it reads, so
        # this test would break the next time an unrelated one is added.
        with patch(
            "app.services.agentic_verification_service.settings."
            "verification_max_concurrency",
            3,
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_BDD,
                    mode="full_repo", github_input="https://github.com/org/repo",
                    llm=llm, db=_db(), pat="pat",
                )
            )

        assert next(e for e in events if e["type"] == "plan")["max_concurrency"] == 3

    @pytest.mark.asyncio
    async def test_an_explicit_argument_overrides_the_setting(self) -> None:
        async def _gen(*args, **kwargs):
            await _read_code(kwargs)
            task = kwargs["messages"][-1]["content"]
            return _verdict(task.split("Scenario ID: ")[1].split("\n")[0])

        llm = MagicMock()
        llm.generate_with_tools = _gen
        events = await _run(llm)

        assert next(e for e in events if e["type"] == "plan")["max_concurrency"] == 1

    def test_the_shipped_default_is_low(self) -> None:
        """Verification used to be strictly sequential.

        Five in flight, each carrying the enlarged evidence blocks, put roughly
        an order of magnitude more tokens per minute through one account than
        that ever did.
        """
        from app.core.config import Settings

        assert Settings.model_fields["verification_max_concurrency"].default <= 2

    @pytest.mark.asyncio
    async def test_a_nonsense_value_cannot_disable_the_window(self) -> None:
        """0 or a negative would make the fill loop launch nothing at all."""

        async def _gen(*args, **kwargs):
            await _read_code(kwargs)
            task = kwargs["messages"][-1]["content"]
            return _verdict(task.split("Scenario ID: ")[1].split("\n")[0])

        llm = MagicMock()
        llm.generate_with_tools = _gen
        events = await _run(llm, max_concurrency=0)

        assert next(e for e in events if e["type"] == "plan")["max_concurrency"] == 1
        assert len([e for e in events if e["type"] == "verdict"]) == 2
