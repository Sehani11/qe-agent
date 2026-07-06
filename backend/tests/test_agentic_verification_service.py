"""Tests for the Agentic Verification Service (Story 2.5).

Tests agentic_verification_service functions:
  - run_agentic_verification (async generator, mocked LLMProvider + github_tools)

Tests POST /run-agentic route:
  - Returns text/event-stream content type
  - Request body validation (missing session_id → 422)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
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


def _make_db_session() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


def _make_verdict_json(
    scenario_id: str,
    title: str,
    status: str = "pass",
    implementation_suggestion: str | None = None,
) -> str:
    verdict = {
        "scenario_id": scenario_id,
        "scenario_title": title,
        "status": status,
        "justification": "The code handles this in `auth.login()`.",
        "code_reference": {"file": "src/auth.py", "function": "login", "line": 42},
        "github_links": ["https://github.com/org/repo/blob/main/src/auth.py"],
        "implementation_suggestion": implementation_suggestion,
    }
    return json.dumps(verdict)


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
# Status: fail → implementation_suggestion is non-null
# ---------------------------------------------------------------------------


class TestFailVerdictHasImplementationSuggestion:
    @pytest.mark.asyncio
    async def test_fail_verdict_has_non_null_suggestion(self) -> None:
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="fail",
                implementation_suggestion="Add login endpoint",
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
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value=_make_verdict_json(
                "00000000-0000-0000-0000-000000000001",
                "User can log in",
                status="pass",
                implementation_suggestion=None,
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
        # generate_with_tools returns unparseable prose for the single scenario.
        llm = MagicMock()
        llm.generate_with_tools = AsyncMock(
            return_value="Sorry, here is my analysis but not JSON."
        )
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

        with patch("app.api.v1.verification.get_llm_provider", return_value=mock_llm), \
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
