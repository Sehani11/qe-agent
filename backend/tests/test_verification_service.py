"""Tests for the LLM Verification Service (Story 2.3).

Tests verification_service functions:
  - parse_bdd_scenarios
  - run_verification (async generator, mocked LLMProvider)

Tests POST /run route:
  - Returns text/event-stream content type
  - Request body validation (missing session_id → 422)
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.schemas.verification import FetchedFile
from app.services.llm.provider import LLMProviderError
from app.services.verification_service import parse_bdd_scenarios, run_verification

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


def _make_verdict_dict(
    scenario_id: str,
    title: str,
    status: str = "pass",
    implementation_suggestion: str | None = None,
) -> dict:
    return {
        "scenario_id": scenario_id,
        "scenario_title": title,
        "status": status,
        "justification": "The code handles this fully in `auth.login()`.",
        "code_reference": {"file": "src/auth.py", "function": "login", "line": 42},
        "github_links": ["src/auth.py"],
        "implementation_suggestion": implementation_suggestion,
    }


def _make_db_session() -> MagicMock:
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# parse_bdd_scenarios
# ---------------------------------------------------------------------------


class TestParseBddScenarios:
    def test_empty_string_returns_empty_list(self) -> None:
        result = parse_bdd_scenarios("")
        assert result == []

    def test_no_scenarios_returns_empty_list(self) -> None:
        bdd = "Feature: Some feature\n  Background:\n    Given a setup step"
        result = parse_bdd_scenarios(bdd)
        assert result == []

    def test_single_scenario_parsed(self) -> None:
        bdd = (
            "Scenario: User logs in\n  Given a user"
            "\n  When they log in\n  Then they see dashboard"
        )
        result = parse_bdd_scenarios(bdd)
        assert len(result) == 1
        assert result[0]["title"] == "User logs in"
        assert "User logs in" in result[0]["text"]
        assert "id" in result[0]

    def test_multiple_scenarios_parsed(self) -> None:
        bdd = (
            "Scenario: Login\n  Given a user\n"
            "Scenario: Logout\n  Given a logged in user\n"
        )
        result = parse_bdd_scenarios(bdd)
        assert len(result) == 2
        assert result[0]["title"] == "Login"
        assert result[1]["title"] == "Logout"

    def test_scenario_outline_parsed(self) -> None:
        bdd = "Scenario Outline: Login with <role>\n  Given a <role>"
        result = parse_bdd_scenarios(bdd)
        assert len(result) == 1
        assert result[0]["title"] == "Login with <role>"

    def test_each_scenario_has_unique_id(self) -> None:
        bdd = "Scenario: A\n  Given a\nScenario: B\n  Given b\n"
        result = parse_bdd_scenarios(bdd)
        ids = [r["id"] for r in result]
        assert len(set(ids)) == 2

    def test_scenario_text_includes_steps(self) -> None:
        bdd = "Scenario: Check\n  Given x\n  When y\n  Then z"
        result = parse_bdd_scenarios(bdd)
        assert "Given x" in result[0]["text"]
        assert "Then z" in result[0]["text"]


# ---------------------------------------------------------------------------
# run_verification
# ---------------------------------------------------------------------------


class TestRunVerification:
    @pytest.mark.asyncio
    async def test_happy_path_two_scenarios_yields_verdicts_and_complete(self) -> None:
        bdd = "Scenario: Login\n  Given a user\nScenario: Logout\n  Given user\n"
        files = [FetchedFile(path="src/auth.py", content="def login(): pass")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        call_count = 0

        async def _generate_structured(prompt, system_prompt="", response_format=None):
            nonlocal call_count
            call_count += 1
            scenarios = parse_bdd_scenarios(bdd)
            idx = call_count - 1
            return _make_verdict_dict(scenarios[idx]["id"], scenarios[idx]["title"])

        mock_llm.generate_structured = _generate_structured

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        verdict_events = [e for e in events if e["type"] == "verdict"]
        complete_events = [e for e in events if e["type"] == "complete"]

        assert len(verdict_events) == 2
        assert len(complete_events) == 1
        assert complete_events[0]["total"] == 2
        assert complete_events[0]["passed"] == 2
        assert complete_events[0]["failed"] == 0

    @pytest.mark.asyncio
    async def test_failed_scenario_has_non_null_implementation_suggestion(self) -> None:
        bdd = "Scenario: Missing feature\n  Given it does not exist\n"
        files = [FetchedFile(path="src/main.py", content="# empty")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        scenarios = parse_bdd_scenarios(bdd)
        mock_llm.generate_structured = AsyncMock(
            return_value=_make_verdict_dict(
                scenarios[0]["id"],
                scenarios[0]["title"],
                status="fail",
                implementation_suggestion=(
                    "Implement the missing feature in src/main.py"
                ),
            )
        )

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        verdict = next(e for e in events if e["type"] == "verdict")
        assert verdict["status"] == "fail"
        assert verdict["implementation_suggestion"] is not None

    @pytest.mark.asyncio
    async def test_passed_scenario_has_null_implementation_suggestion(self) -> None:
        bdd = "Scenario: Working feature\n  Given it works\n"
        files = [FetchedFile(path="src/main.py", content="def feature(): pass")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        scenarios = parse_bdd_scenarios(bdd)
        mock_llm.generate_structured = AsyncMock(
            return_value=_make_verdict_dict(
                scenarios[0]["id"],
                scenarios[0]["title"],
                status="pass",
                implementation_suggestion=None,
            )
        )

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        verdict = next(e for e in events if e["type"] == "verdict")
        assert verdict["status"] == "pass"
        assert verdict["implementation_suggestion"] is None

    @pytest.mark.asyncio
    async def test_llm_provider_error_emits_error_and_continues(self) -> None:
        bdd = "Scenario: A\n  Given a\nScenario: B\n  Given b\n"
        files = [FetchedFile(path="src/a.py", content="pass")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        call_count = 0
        scenarios_parsed = parse_bdd_scenarios(bdd)

        async def _generate_structured(prompt, system_prompt="", response_format=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise LLMProviderError("rate limited")
            return _make_verdict_dict(
                scenarios_parsed[1]["id"], scenarios_parsed[1]["title"]
            )

        mock_llm.generate_structured = _generate_structured

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        error_events = [e for e in events if e["type"] == "error"]
        verdict_events = [e for e in events if e["type"] == "verdict"]
        complete_events = [e for e in events if e["type"] == "complete"]

        assert len(error_events) == 1
        assert "rate limited" in error_events[0]["message"]
        assert len(verdict_events) == 1
        assert len(complete_events) == 1

    @pytest.mark.asyncio
    async def test_warning_paths_filtered_before_llm_call(self) -> None:
        bdd = "Scenario: Check\n  Given x\n"
        files = [
            FetchedFile(
                path="[WARNING] Tree truncated — only 100 of 500 files shown",
                content="truncated",
            ),
            FetchedFile(path="src/real.py", content="def real(): pass"),
        ]

        mock_llm = AsyncMock()
        db = _make_db_session()

        captured_prompts: list[str] = []
        scenarios = parse_bdd_scenarios(bdd)

        async def _generate_structured(prompt, system_prompt="", response_format=None):
            captured_prompts.append(prompt)
            return _make_verdict_dict(scenarios[0]["id"], scenarios[0]["title"])

        mock_llm.generate_structured = _generate_structured

        async for _ in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            pass

        assert len(captured_prompts) == 1
        assert "[WARNING]" not in captured_prompts[0]
        assert "src/real.py" in captured_prompts[0]

    @pytest.mark.asyncio
    async def test_empty_fetched_files_still_processes_scenarios(self) -> None:
        bdd = "Scenario: Check\n  Given x\n"
        files: list[FetchedFile] = []

        mock_llm = AsyncMock()
        db = _make_db_session()

        scenarios = parse_bdd_scenarios(bdd)
        mock_llm.generate_structured = AsyncMock(
            return_value=_make_verdict_dict(scenarios[0]["id"], scenarios[0]["title"])
        )

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        verdict_events = [e for e in events if e["type"] == "verdict"]
        assert len(verdict_events) == 1

    @pytest.mark.asyncio
    async def test_no_scenarios_in_bdd_yields_error_and_no_db_writes(self) -> None:
        bdd = "Feature: Nothing\n  Background:\n    Given setup\n"
        files = [FetchedFile(path="src/x.py", content="pass")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        events = []
        async for chunk in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            events.append(json.loads(chunk[len("data: "):]))

        assert len(events) == 1
        assert events[0]["type"] == "error"
        assert "No BDD scenarios" in events[0]["message"]
        db.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_verdicts_persisted_to_db(self) -> None:
        bdd = "Scenario: Persist me\n  Given x\n"
        files = [FetchedFile(path="src/x.py", content="pass")]

        mock_llm = AsyncMock()
        db = _make_db_session()

        scenarios = parse_bdd_scenarios(bdd)
        mock_llm.generate_structured = AsyncMock(
            return_value=_make_verdict_dict(scenarios[0]["id"], scenarios[0]["title"])
        )

        async for _ in run_verification(
            "sess-1", "user-1", bdd, files, mock_llm, db
        ):
            pass

        db.add.assert_called_once()
        db.flush.assert_called_once()


# ---------------------------------------------------------------------------
# POST /run route tests
# ---------------------------------------------------------------------------


class TestRunVerificationEndpoint:
    def test_returns_text_event_stream_content_type(self, client: TestClient) -> None:
        bdd = "Scenario: A\n  Given x\n"
        files = [{"path": "src/a.py", "content": "pass"}]

        mock_verdict = {
            "scenario_id": "00000000-0000-0000-0000-000000000001",
            "scenario_title": "A",
            "status": "pass",
            "justification": "ok",
            "code_reference": {"file": "src/a.py", "function": "main", "line": 1},
            "github_links": [],
            "implementation_suggestion": None,
        }

        async def _fake_run(**_kwargs):
            yield f"data: {json.dumps({'type': 'verdict', **mock_verdict})}\n\n"
            complete = {'type': 'complete', 'total': 1, 'passed': 1, 'failed': 0}
            yield f"data: {json.dumps(complete)}\n\n"

        _run_path = (
            "app.api.v1.verification.verification_service.run_verification"
        )
        with (
            patch(_run_path, side_effect=_fake_run),
            patch("app.api.v1.verification.get_llm_provider", return_value=MagicMock()),
        ):
            response = client.post(
                "/api/v1/verification/run",
                json={
                    "session_id": "sess-abc",
                    "bdd_content": bdd,
                    "fetched_files": files,
                },
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]

    def test_missing_session_id_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/verification/run",
            json={
                "bdd_content": "Scenario: A\n  Given x\n",
                "fetched_files": [],
            },
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"] == "VALIDATION_ERROR"
        assert body["code"] == 422
