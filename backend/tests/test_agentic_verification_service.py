"""Tests for the Agentic Verification Service (Story 2.5).

Tests agentic_verification_service functions:
  - run_agentic_verification (async generator, mocked LLMProvider + github_tools)

Tests POST /run-agentic route:
  - Returns text/event-stream content type
  - Request body validation (missing session_id → 422)
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.schemas.verification import FetchedFile
from app.services.llm.provider import LLMProviderError
from app.services.agentic_verification_service import run_agentic_verification

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _mock_auth() -> str:
    return "test-user-id"


@pytest.fixture
def client() -> TestClient:
    app.dependency_overrides[get_current_user] = _mock_auth
    yield TestClient(app)
    app.dependency_overrides.clear()


class _AsyncSavepoint:
    """Stand-in for AsyncSession.begin_nested() as an async context manager."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


def _make_db_session() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.begin_nested = MagicMock(side_effect=lambda: _AsyncSavepoint())
    return db


def _make_verdict_json(
    scenario_id: str,
    title: str,
    status: str = "pass",
    implementation_suggestion: str | None = None,
    justification: str = "The code handles this in `auth.login()`.",
) -> str:
    verdict = {
        "scenario_id": scenario_id,
        "scenario_title": title,
        "status": status,
        "justification": justification,
        "code_reference": {"file": "src/auth.py", "function": "login", "line": 42},
        "github_links": ["https://github.com/org/repo/blob/main/src/auth.py"],
        "implementation_suggestion": implementation_suggestion,
    }
    return json.dumps(verdict)


async def _simulate_tool_read(kwargs: dict) -> None:
    """Perform one successful tool read through the run's executor.

    A "pass" verdict now requires evidence — a successful tool read or code
    injected into the prompt — so mocks that verdict "pass" in full_repo mode
    must read code first, exactly as the real model is instructed to.
    """
    with patch(
        "app.services.github_tools.get_file_contents",
        new=AsyncMock(return_value="def login(): ..."),
    ):
        await kwargs["tool_executor"]("get_file_contents", {"path": "src/auth.py"})


_SIMPLE_BDD = """\
Feature: Authentication

  Scenario: User can log in
    Given a registered user
    When they submit valid credentials
    Then they are authenticated

  Scenario: User sees error on bad password
    Given a registered user
    When they submit an invalid password
    Then they see an error message
"""

_SINGLE_BDD = """\
Feature: Authentication

  Scenario: User can log in
    Given a registered user
    When they submit valid credentials
    Then they are authenticated
"""


def _prompt_text(messages: list[dict]) -> str:
    """All prompt text the run sent, regardless of how it is split into messages.

    The prompt is deliberately split so its shared, cacheable half comes first
    and the per-scenario half last. Asserting on a fixed message index would
    make these tests fail whenever that split is retuned, which is a packaging
    detail rather than a behaviour.
    """
    return "\n\n".join(
        str(m.get("content", "")) for m in messages if isinstance(m.get("content"), str)
    )


async def _collect(gen) -> list[str]:
    """Collect all SSE events from an async generator."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def _parse_events(events: list[str]) -> list[dict]:
    """Parse SSE data lines into dicts."""
    results = []
    for ev in events:
        if ev.startswith("data: "):
            results.append(json.loads(ev[6:]))
    return results


# ---------------------------------------------------------------------------
# Happy path — 2 scenarios
# ---------------------------------------------------------------------------


class TestRunAgenticVerificationHappyPath:
    @pytest.mark.asyncio
    async def test_two_scenarios_yield_two_verdicts_and_complete(self) -> None:
        llm = MagicMock()
        call_count = 0

        async def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            await _simulate_tool_read(kwargs)
            verdicts = [
                _make_verdict_json("00000000-0000-0000-0000-000000000001", "User can log in"),
                _make_verdict_json(
                    "00000000-0000-0000-0000-000000000002",
                    "User sees error on bad password",
                    status="fail",
                    implementation_suggestion="Add proper error handling",
                ),
            ]
            result = verdicts[call_count]
            call_count += 1
            return result

        llm.generate_with_tools = side_effect_fn

        db = _make_db_session()

        events = await _collect(
            run_agentic_verification(
                session_id="sess-1",
                user_id="user-1",
                bdd_content=_SIMPLE_BDD,
                mode="full_repo",
                github_input="https://github.com/org/repo",
                llm=llm,
                db=db,
                pat="test-pat",
            )
        )

        parsed = _parse_events(events)
        verdict_events = [e for e in parsed if e["type"] == "verdict"]
        complete_events = [e for e in parsed if e["type"] == "complete"]

        assert len(verdict_events) == 2
        assert len(complete_events) == 1
        complete = complete_events[0]
        assert complete["total"] == 2
        assert complete["passed"] == 1
        assert complete["failed"] == 1


# ---------------------------------------------------------------------------
# Story 4.9 — knowledge-base enrichment (opt-in per run)
# ---------------------------------------------------------------------------


class TestAgenticKnowledgeBase:
    @pytest.mark.asyncio
    async def test_use_knowledge_base_enriches_prompt_and_sets_rag_context(self):
        """use_knowledge_base=True → PROJECT CONTEXT in the prompt + rag_context set."""
        chunk = {
            "source": "confluence",
            "source_id": "42",
            "snippet": "auth uses JWT via Supabase",
            "title": "Auth Design",
            "url": "",
        }
        captured: dict = {}

        async def _gen_with_tools(*args, **kwargs):
            captured["messages"] = kwargs["messages"]
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen_with_tools
        db = _make_db_session()

        with patch(
            "app.services.agentic_verification_service.retrieve_rag_per_scenario",
            new=AsyncMock(return_value=[[chunk]]),
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s",
                    user_id="u",
                    bdd_content=_SINGLE_BDD,
                    mode="full_repo",
                    github_input="https://github.com/org/repo",
                    llm=llm,
                    db=db,
                    pat="pat",
                    use_knowledge_base=True,
                )
            )

        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["rag_context"] and verdict["rag_context"][0]["source_id"] == "42"
        # The knowledge-base context reached the LLM prompt
        user_msg = _prompt_text(captured["messages"])
        assert "PROJECT CONTEXT" in user_msg
        assert "auth uses JWT via Supabase" in user_msg
        # Persisted on the verification result
        assert db.add.call_args[0][0].rag_context[0]["source_id"] == "42"

    @pytest.mark.asyncio
    async def test_default_off_skips_query_and_null_rag_context(self):
        """Default (use_knowledge_base omitted) → KB not queried, rag_context null."""
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )
        )
        db = _make_db_session()
        batch = AsyncMock(return_value=[[{"source": "x", "source_id": "1", "snippet": "s"}]])

        with patch(
            "app.services.verification_service._ks.query_knowledge_base_batch",
            new=batch,
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s",
                    user_id="u",
                    bdd_content=_SINGLE_BDD,
                    mode="full_repo",
                    github_input="https://github.com/org/repo",
                    llm=llm,
                    db=db,
                    pat="pat",
                )
            )

        batch.assert_not_called()
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["rag_context"] is None
        assert db.add.call_args[0][0].rag_context is None


# ---------------------------------------------------------------------------
# Status: fail → implementation_suggestion is non-null
# ---------------------------------------------------------------------------


class TestFailVerdictHasImplementationSuggestion:
    @pytest.mark.asyncio
    async def test_fail_verdict_has_non_null_suggestion(self) -> None:
        # The verdict reads code first: an unevidenced "fail" is now downgraded
        # to "inconclusive" (nothing was read, so absence was never shown), and
        # this test is about the suggestion a real failure carries.
        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="fail",
                implementation_suggestion="Add login endpoint",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        verdict = next(e for e in parsed if e["type"] == "verdict")
        assert verdict["status"] == "fail"
        assert verdict["implementation_suggestion"] is not None


# ---------------------------------------------------------------------------
# Status: pass → implementation_suggestion is null
# ---------------------------------------------------------------------------


class TestPassVerdictHasNullSuggestion:
    @pytest.mark.asyncio
    async def test_pass_verdict_has_null_suggestion(self) -> None:
        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="pass",
                implementation_suggestion=None,
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        verdict = next(e for e in parsed if e["type"] == "verdict")
        assert verdict["status"] == "pass"
        assert verdict["implementation_suggestion"] is None


# ---------------------------------------------------------------------------
# GitHubServiceError in tool call → error SSE, other scenarios continue
# ---------------------------------------------------------------------------


class TestGitHubServiceErrorContinues:
    @pytest.mark.asyncio
    async def test_github_error_in_tool_yields_error_event_and_continues(self) -> None:
        call_count = 0

        async def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Simulate the tool executor raising GitHubServiceError by
                # having the LLM provider raise LLMProviderError
                raise LLMProviderError("GitHub API error: rate limit")
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000002",
                "User sees error on bad password",
            )

        llm = MagicMock()
        llm.generate_with_tools = side_effect_fn

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        error_events = [e for e in parsed if e["type"] == "error"]
        verdict_events = [e for e in parsed if e["type"] == "verdict"]
        complete_events = [e for e in parsed if e["type"] == "complete"]

        assert len(error_events) >= 1
        assert len(verdict_events) == 1
        assert len(complete_events) == 1


# ---------------------------------------------------------------------------
# LLMProviderError on one scenario → error SSE, other scenarios continue
# ---------------------------------------------------------------------------


class TestAnUnusableProviderStopsTheRun:
    """One root cause should produce one error, not one per scenario.

    The provider is built once for the whole run, so a misconfiguration — an
    unset key, a malformed one — fails every scenario identically. Continuing
    spent a doomed API call per scenario and stacked a toast per scenario on
    the user, burying the single fact they needed under copies of itself.
    """

    @pytest.mark.asyncio
    async def test_an_identically_repeating_error_ends_the_run(self) -> None:
        calls = 0

        async def always_fails(*args, **kwargs):
            nonlocal calls
            calls += 1
            raise LLMProviderError(
                "Unexpected error calling OpenAI: Illegal header value b'Bearer '"
            )

        llm = MagicMock()
        llm.generate_with_tools = always_fails

        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=_make_db_session(), pat="pat",
            )
        )
        parsed = _parse_events(events)
        errors = [e for e in parsed if e["type"] == "error"]

        # Two scenarios, two attempts — the second is what identifies the
        # failure as not scenario-specific. A third would be waste.
        assert calls == 2
        assert len(errors) == 2
        assert "stopped" in errors[-1]["message"]
        # Stopped, so no misleading "complete" summary claiming a finished run.
        assert not [e for e in parsed if e["type"] == "complete"]

    @pytest.mark.asyncio
    async def test_different_errors_do_not_stop_the_run(self) -> None:
        """Genuinely per-scenario failures do not reproduce word for word, and
        must not be mistaken for a broken provider."""
        messages = iter(
            ["Context length exceeded for this scenario", "Rate limit reached"]
        )

        async def varying(*args, **kwargs):
            raise LLMProviderError(next(messages))

        llm = MagicMock()
        llm.generate_with_tools = varying

        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=_make_db_session(), pat="pat",
            )
        )
        parsed = _parse_events(events)

        assert len([e for e in parsed if e["type"] == "error"]) == 2
        assert [e for e in parsed if e["type"] == "complete"]


class TestLLMProviderErrorContinues:
    @pytest.mark.asyncio
    async def test_llm_error_yields_error_event_and_continues(self) -> None:
        call_count = 0

        async def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMProviderError("OpenAI API error: timeout")
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000002",
                "User sees error on bad password",
            )

        llm = MagicMock()
        llm.generate_with_tools = side_effect_fn

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        assert any(e["type"] == "error" for e in parsed)
        assert any(e["type"] == "verdict" for e in parsed)
        assert any(e["type"] == "complete" for e in parsed)


# ---------------------------------------------------------------------------
# No BDD scenarios found → error SSE, no DB writes
# ---------------------------------------------------------------------------


class TestNoBDDScenarios:
    @pytest.mark.asyncio
    async def test_no_scenarios_yields_error_event(self) -> None:
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock()
        db = _make_db_session()

        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content="Feature: empty",
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        assert any(e["type"] == "error" for e in parsed)
        llm.generate_with_tools.assert_not_called()
        db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Malformed JSON verdict from LLM → error SSE, continues
# ---------------------------------------------------------------------------


class TestMalformedVerdictContinues:
    @pytest.mark.asyncio
    async def test_malformed_json_yields_error_and_continues(self) -> None:
        call_count = 0

        async def side_effect_fn(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "This is not valid JSON at all, just plain text response"
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000002",
                "User sees error on bad password",
            )

        llm = MagicMock()
        llm.generate_with_tools = side_effect_fn
        # The corrective re-ask also fails to produce a valid verdict, so the
        # first scenario should still surface an error event.
        llm.generate_structured = AsyncMock(return_value={"type": "message"})

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        assert any(e["type"] == "error" for e in parsed)
        assert any(e["type"] == "verdict" for e in parsed)
        assert any(e["type"] == "complete" for e in parsed)
        # The re-ask was attempted for the malformed first scenario.
        llm.generate_structured.assert_awaited()

    @pytest.mark.asyncio
    async def test_malformed_json_recovered_by_reask(self) -> None:
        """A malformed first response is recovered by the one-shot re-ask."""
        # generate_with_tools reads code, then returns unparseable prose.
        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return "Sorry, here is my analysis but not JSON."

        llm = MagicMock()
        llm.generate_with_tools = _gen
        # The re-ask reformats it into a valid verdict dict.
        llm.generate_structured = AsyncMock(return_value={
            "scenario_id": "00000000-0000-0000-0000-000000000001",
            "scenario_title": "User can log in",
            "status": "pass",
            "justification": "Handled in auth.login().",
            "code_reference": {"file": "src/auth.py", "function": "login", "line": 42},
            "github_links": ["https://github.com/org/repo/blob/main/src/auth.py"],
            "implementation_suggestion": None,
        })

        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        # No error event — the re-ask recovered a valid verdict.
        assert not any(e["type"] == "error" for e in parsed)
        verdicts = [e for e in parsed if e["type"] == "verdict"]
        assert len(verdicts) == 1
        assert verdicts[0]["status"] == "pass"
        llm.generate_structured.assert_awaited_once()


# ---------------------------------------------------------------------------
# Mode scopes — exact_files and pull_request must verify the chosen code
# ---------------------------------------------------------------------------


class TestModeScopes:
    @pytest.mark.asyncio
    async def test_exact_files_injects_listed_files_and_pins_ref(self) -> None:
        """Exact-files mode prefetches the listed files (at their URL's ref),
        injects them as evidence, and pins follow-up tool reads to that ref."""
        captured: dict = {}
        captured_refs: list[str] = []

        async def _gen(*args, **kwargs):
            captured["messages"] = kwargs["messages"]
            # A follow-up read must default to the URL's ref, not HEAD.
            async def fake_get(repo, path, pat, ref="HEAD", offset=0):
                captured_refs.append(ref)
                return "def login(): ..."

            with patch("app.services.github_tools.get_file_contents", new=fake_get):
                await kwargs["tool_executor"]("get_file_contents", {"path": "src/auth.py"})
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()
        files = [
            FetchedFile(
                path="src/auth.py",
                content="def login():\n    return True",
                github_url="https://github.com/org/repo/blob/feature-x/src/auth.py",
            )
        ]

        with patch(
            "app.services.agentic_verification_service.fetch_exact_files_resolved",
            new=AsyncMock(return_value=(files, "feature-x")),
        ) as fetch_mock:
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="exact_files",
                    github_input="https://github.com/org/repo/blob/feature-x/src/auth.py",
                    llm=llm, db=db, pat="pat",
                )
            )

        fetch_mock.assert_awaited_once()
        parsed = _parse_events(events)
        verdict = next(e for e in parsed if e["type"] == "verdict")
        assert verdict["status"] == "pass"
        user_msg = _prompt_text(captured["messages"])
        assert "Exact File Paths mode" in user_msg
        assert "[F:src/auth.py]" in user_msg
        assert "def login():" in user_msg
        assert captured_refs == ["feature-x"]

    @pytest.mark.asyncio
    async def test_exact_files_evidence_alone_supports_a_pass(self) -> None:
        """With injected evidence, a verdict needs no tool calls to pass."""
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )
        )
        db = _make_db_session()
        files = [FetchedFile(path="src/auth.py", content="def login(): ...")]

        with patch(
            "app.services.agentic_verification_service.fetch_exact_files_resolved",
            new=AsyncMock(return_value=(files, "main")),
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="exact_files",
                    github_input="https://github.com/org/repo/blob/main/src/auth.py",
                    llm=llm, db=db, pat="pat",
                )
            )

        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "pass"

    @pytest.mark.asyncio
    async def test_exact_files_across_repos_is_refused(self) -> None:
        """URLs spanning two repositories error out instead of silently using the first."""
        llm = MagicMock()
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="exact_files",
                github_input=(
                    "https://github.com/org/repo-a/blob/main/src/auth.py\n"
                    "https://github.com/org/repo-b/blob/main/src/auth.py"
                ),
                llm=llm, db=db, pat="pat",
            )
        )
        parsed = _parse_events(events)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "error"
        assert "same repository" in parsed[0]["message"]

    @pytest.mark.asyncio
    async def test_pull_request_injects_diff_and_pins_head_sha(self) -> None:
        """PR mode injects the PR's diffs as evidence and pins tools to the
        PR head commit — not the default branch."""
        captured: dict = {}
        captured_refs: list[str] = []

        async def _gen(*args, **kwargs):
            captured["messages"] = kwargs["messages"]

            async def fake_get(repo, path, pat, ref="HEAD", offset=0):
                captured_refs.append(ref)
                return "full file body"

            with patch("app.services.github_tools.get_file_contents", new=fake_get):
                await kwargs["tool_executor"]("get_file_contents", {"path": "src/auth.py"})
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()
        pr_files = [
            FetchedFile(
                path="src/auth.py",
                content="@@ -1 +1 @@\n+def login(): return True",
                github_url="https://github.com/org/repo/blob/abc123/src/auth.py",
            )
        ]

        with patch(
            "app.services.agentic_verification_service.get_pr_head_sha",
            new=AsyncMock(return_value="abc123"),
        ), patch(
            "app.services.agentic_verification_service.fetch_pull_request",
            new=AsyncMock(return_value=pr_files),
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="pull_request",
                    github_input="https://github.com/org/repo/pull/42",
                    llm=llm, db=db, pat="pat",
                )
            )

        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "pass"
        user_msg = _prompt_text(captured["messages"])
        assert "Pull Request mode" in user_msg
        assert "[F:src/auth.py]" in user_msg
        assert "@@ -1 +1 @@" in user_msg
        assert captured_refs == ["abc123"]

    @pytest.mark.asyncio
    async def test_pull_request_prefetch_failure_yields_error(self) -> None:
        """A missing PR errors out before any LLM call."""
        from app.services.github_service import GitHubServiceError

        llm = MagicMock()
        db = _make_db_session()
        with patch(
            "app.services.agentic_verification_service.get_pr_head_sha",
            new=AsyncMock(side_effect=GitHubServiceError("Pull request #42 not found in org/repo")),
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="pull_request",
                    github_input="https://github.com/org/repo/pull/42",
                    llm=llm, db=db, pat="pat",
                )
            )
        parsed = _parse_events(events)
        assert len(parsed) == 1
        assert parsed[0]["type"] == "error"
        assert "not found" in parsed[0]["message"]


# ---------------------------------------------------------------------------
# Evidence guard + verdict identity
# ---------------------------------------------------------------------------


class TestVerdictGrounding:
    @pytest.mark.asyncio
    async def test_pass_with_no_tool_calls_and_no_evidence_is_inconclusive(self) -> None:
        """full_repo mode: a "pass" the model produced without reading any code
        cannot stand — it is evidence-free.

        It lands on "inconclusive" rather than "fail": with nothing read, absence
        was never established, and a fabricated failure sends someone to rewrite
        working code. The guard's safety property is unchanged — an unevidenced
        run still cannot report a pass.
        """
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )
        )
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "inconclusive"
        assert "no code evidence" in verdict["implementation_suggestion"]

    @pytest.mark.asyncio
    async def test_scenario_identity_is_pinned_not_echoed(self) -> None:
        """A garbled scenario_id from the model is overwritten with the real
        one, so the result stays attributable and persistable."""

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json("not-a-uuid-at-all", "Wrong Title")

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["scenario_id"] != "not-a-uuid-at-all"
        uuid.UUID(verdict["scenario_id"])  # valid UUID → persistable
        assert verdict["scenario_title"] == "User can log in"
        # The persisted row carries the same pinned identity
        assert str(db.add.call_args[0][0].scenario_id) == verdict["scenario_id"]


# ---------------------------------------------------------------------------
# POST /run-agentic route tests
# ---------------------------------------------------------------------------


class TestRunAgenticEndpoint:
    def test_run_agentic_endpoint_returns_event_stream(self, client: TestClient) -> None:
        """POST /run-agentic returns text/event-stream content type."""
        mock_stream_events = [
            _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
            ),
        ]
        call_count = 0

        async def mock_llm_generate(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return mock_stream_events[0]

        mock_llm = MagicMock()
        mock_llm.generate_with_tools = mock_llm_generate

        with patch("app.api.v1.verification.llm_with_tools_for", return_value=mock_llm), \
             patch("app.api.v1.verification.agentic_verification_service.run_agentic_verification") as mock_svc:

            async def fake_stream(*args, **kwargs):
                yield f"data: {json.dumps({'type': 'verdict', 'status': 'pass'})}\n\n"
                yield f"data: {json.dumps({'type': 'complete', 'total': 1, 'passed': 1, 'failed': 0})}\n\n"

            mock_svc.return_value = fake_stream()

            response = client.post(
                "/api/v1/verification/run-agentic",
                json={
                    "session_id": "sess-1",
                    "bdd_content": _SINGLE_BDD,
                    "mode": "full_repo",
                    "github_input": "https://github.com/org/repo",
                },
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_run_agentic_missing_session_id_returns_422(self, client: TestClient) -> None:
        """Missing session_id in request → 422 validation error."""
        response = client.post(
            "/api/v1/verification/run-agentic",
            json={
                "bdd_content": _SINGLE_BDD,
                "mode": "full_repo",
                "github_input": "https://github.com/org/repo",
            },
        )
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# Inconclusive — the third verdict, for a run that established neither answer
# ---------------------------------------------------------------------------


class TestInconclusiveVerdict:
    @pytest.mark.asyncio
    async def test_a_hedged_fail_is_downgraded_to_inconclusive(self) -> None:
        """A justification that admits it never checked cannot assert absence.

        This is the exact shape of the false positive the guard exists for: the
        model reads one file, does not find the behaviour there, and reports it
        missing while saying in the same breath that it could not verify.
        """

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="fail",
                implementation_suggestion="Add the login handler",
                justification=(
                    "The matching is likely handled in the backend, which I "
                    "cannot verify with the current scope."
                ),
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "inconclusive"
        assert verdict["implementation_suggestion"]

    @pytest.mark.asyncio
    async def test_a_grounded_fail_still_fails(self) -> None:
        """The downgrade must not swallow real findings — a failure stated from
        code that was read stands as a failure."""

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="fail",
                implementation_suggestion="Add the login handler",
                justification="auth.py defines only logout(); no login handler exists.",
            )

        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(side_effect=_gen)
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "fail"

    @pytest.mark.asyncio
    async def test_complete_event_counts_inconclusive_separately(self) -> None:
        """total = passed + failed + inconclusive, with the undecided count
        never folded into failures."""
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )
        )
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        complete = next(e for e in _parse_events(events) if e["type"] == "complete")
        # Compared as a subset rather than for equality: the counts are what
        # this test is about, and the event also carries additive fields (run
        # token usage) that have nothing to do with how a verdict was tallied.
        assert {
            "type": "complete",
            "total": 1,
            "passed": 0,
            "failed": 0,
            "inconclusive": 1,
        }.items() <= complete.items()


class TestPassRequiresCodeToHaveBeenRead:
    """A directory listing is not code.

    `tool_successes` counts every tool equally, so before this guard a run that
    called `list_directory` once — and never opened a file — could still report
    "pass": the model recognises a filename, infers what must be inside it, and
    is believed. Reading a file is what the system prompt calls CRITICAL, so
    this enforces the existing instruction rather than adding a rule.
    """

    @staticmethod
    async def _run(llm) -> dict:
        db = _make_db_session()
        events = await _collect(
            run_agentic_verification(
                session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                mode="full_repo", github_input="https://github.com/org/repo",
                llm=llm, db=db, pat="pat",
            )
        )
        return next(e for e in _parse_events(events) if e["type"] == "verdict")

    @pytest.mark.asyncio
    async def test_a_pass_backed_only_by_a_listing_is_inconclusive(self) -> None:
        async def _gen(*args, **kwargs):
            with patch(
                "app.services.github_tools.list_directory",
                new=AsyncMock(return_value='["auth.py"]'),
            ):
                await kwargs["tool_executor"]("list_directory", {"path": "src"})
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        verdict = await self._run(llm)

        assert verdict["status"] == "inconclusive"
        assert "without any file being read" in verdict["implementation_suggestion"]

    @pytest.mark.asyncio
    async def test_a_pass_backed_by_a_real_read_still_passes(self) -> None:
        """The guard must not cost a legitimate pass."""

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        verdict = await self._run(llm)

        assert verdict["status"] == "pass"

    @pytest.mark.asyncio
    async def test_a_fail_backed_only_by_a_listing_is_left_alone(self) -> None:
        """The guard is about unearned confidence in a pass.

        A "fail" reached without reading a file is caught by the hedging and
        no-evidence rules, not by this one — widening it here would start
        rewriting verdicts on a second, unrelated basis.
        """

        async def _gen(*args, **kwargs):
            with patch(
                "app.services.github_tools.list_directory",
                new=AsyncMock(return_value='["auth.py"]'),
            ):
                await kwargs["tool_executor"]("list_directory", {"path": "src"})
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="fail",
                implementation_suggestion="Add a login handler.",
                justification="There is no login handler anywhere in src/.",
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        verdict = await self._run(llm)

        assert verdict["status"] == "fail"


class TestRepoTreeReachesThePrompt:
    @pytest.mark.asyncio
    async def test_the_tree_is_fetched_once_for_the_whole_run(self) -> None:
        """Per-run, not per-scenario — that is the entire saving."""
        tree = AsyncMock(return_value=["src/auth.py", "src/api/routes.py"])

        async def _gen(*args, **kwargs):
            await _simulate_tool_read(kwargs)
            task = kwargs["messages"][-1]["content"]
            scenario_id = task.split("Scenario ID: ")[1].split("\n")[0]
            return _make_verdict_json(scenario_id, "t")

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()

        with patch("app.services.github_tools.get_repo_tree", new=tree):
            await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SIMPLE_BDD,
                    mode="full_repo", github_input="https://github.com/org/repo",
                    llm=llm, db=db, pat="pat",
                )
            )

        assert tree.await_count == 1

    @pytest.mark.asyncio
    async def test_the_tree_rides_in_the_cacheable_shared_message(self) -> None:
        """In the prefix, so the run pays for it once rather than per scenario."""
        captured: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            captured.append(kwargs["messages"])
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()

        with patch(
            "app.services.github_tools.get_repo_tree",
            new=AsyncMock(return_value=["src/auth.py"]),
        ):
            await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="full_repo", github_input="https://github.com/org/repo",
                    llm=llm, db=db, pat="pat",
                )
            )

        shared = next(m for m in captured[0] if m.get("cache_control"))
        assert "REPOSITORY FILE TREE" in shared["content"]
        assert "src/auth.py" in shared["content"]

    @pytest.mark.asyncio
    async def test_a_run_without_a_tree_is_unaffected(self) -> None:
        """The fallback is the behaviour that existed before the tree did."""
        captured: list[list[dict]] = []

        async def _gen(*args, **kwargs):
            captured.append(kwargs["messages"])
            await _simulate_tool_read(kwargs)
            return _make_verdict_json(
                "00000000-0000-0000-0000-000000000001", "User can log in"
            )

        llm = MagicMock()
        llm.generate_with_tools = _gen
        db = _make_db_session()

        with patch(
            "app.services.github_tools.get_repo_tree", new=AsyncMock(return_value=[])
        ):
            events = await _collect(
                run_agentic_verification(
                    session_id="s", user_id="u", bdd_content=_SINGLE_BDD,
                    mode="full_repo", github_input="https://github.com/org/repo",
                    llm=llm, db=db, pat="pat",
                )
            )

        shared = next(m for m in captured[0] if m.get("cache_control"))
        assert "REPOSITORY FILE TREE" not in shared["content"]
        verdict = next(e for e in _parse_events(events) if e["type"] == "verdict")
        assert verdict["status"] == "pass"
