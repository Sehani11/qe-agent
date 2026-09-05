"""Tests for the side-by-side comparison endpoint.

The endpoint's whole value is that the two columns describe two DIFFERENT
models. The fine-tuned provider falls back to the general LLM silently, so the
cases that matter most here are the ones where a comparison is not a comparison
at all — those must be visible in the response, not inferred from the logs.

Strategy: override get_current_user (same pattern as test_training_datasets.py)
and patch `generate_one`, so nothing calls a model.
"""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.main import app
from app.services.evaluation_service import GenerationOutcome

USER = "user-a-id"

AC = (
    "AC1: A user can request a reset link.\n"
    "AC2: A link older than one hour is rejected."
)

TWO_SCENARIOS = [
    {
        "source_ac_clause": "AC1",
        "feature": "Password reset",
        "scenario": "Request a link",
        "given": "a registered user",
        "when": "they request a reset link",
        "then": "a link is emailed",
    },
    {
        "source_ac_clause": "AC2",
        "feature": "Password reset",
        "scenario": "Expired link",
        "given": "a link issued two hours ago",
        "when": "they open it",
        "then": "it is rejected as expired",
    },
]


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: USER
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _project_without_jira():
    """Resolve to a project that has no Jira token of its own.

    Keeps these tests on the environment fallback they were written against.
    `test_the_ticket_is_fetched_from_the_projects_jira` covers the other side.
    """
    project = SimpleNamespace(
        id="proj-1",
        jira_base_url="",
        jira_user_email="",
        jira_api_token_encrypted="",
    )
    with patch(
        "app.api.v1.evaluation.ensure_project", new=AsyncMock(return_value=project)
    ):
        yield


def _outcome(provider, *, effective=None, scenarios=None, succeeded=True, **kw):
    return GenerationOutcome(
        item_id="ad-hoc",
        configured_provider=provider,
        effective_provider=effective if effective is not None else provider,
        model_identifier=f"model-{provider}",
        scenarios=scenarios if scenarios is not None else TWO_SCENARIOS,
        latency_seconds=1.5,
        succeeded=succeeded,
        **kw,
    )


def _patch(outcomes):
    """Return a patch whose generate_one yields `outcomes` in call order."""
    queue = list(outcomes)

    async def fake(item, provider, *, allow_fallback=True):
        return queue.pop(0)

    return patch("app.api.v1.evaluation.generate_one", new=fake)


def test_both_models_run_on_the_same_criteria(client):
    calls = []

    async def record(item, provider, *, allow_fallback=True):
        calls.append((item.acceptance_criteria, provider))
        return _outcome(provider)

    with patch("app.api.v1.evaluation.generate_one", new=record):
        r = client.post("/api/v1/evaluation/compare", json={"acceptance_criteria": AC})

    assert r.status_code == 200
    # Identical input to both — a paired comparison, not two different questions.
    assert [c[0] for c in calls] == [AC, AC]
    assert [c[1] for c in calls] == ["general_llm", "fine_tuned"]

    body = r.json()
    assert body["comparable"] is True
    assert body["ac_clauses"] == ["AC1", "AC2"]
    assert len(body["results"]) == 2


def test_the_fallback_is_disabled_for_every_comparison_call(client):
    """A comparison must never substitute one model for the other.

    With fallback on, a failed fine-tuned call returns general-LLM scenarios
    under the fine_tuned label — two columns showing one model.
    """
    seen = []

    async def record(item, provider, *, allow_fallback=True):
        seen.append((provider, allow_fallback))
        return _outcome(provider)

    with patch("app.api.v1.evaluation.generate_one", new=record):
        client.post("/api/v1/evaluation/compare", json={"acceptance_criteria": AC})

    assert seen == [("general_llm", False), ("fine_tuned", False)]


def test_a_fine_tuned_failure_shows_no_scenarios_at_all(client):
    """The column stays empty rather than borrowing the other model's output."""
    outcomes = [
        _outcome("general_llm"),
        _outcome(
            "fine_tuned",
            effective=None,
            scenarios=[],
            succeeded=False,
            error="BDDModelProviderError: fallback is disabled for this call.",
        ),
    ]

    with _patch(outcomes):
        r = client.post("/api/v1/evaluation/compare", json={"acceptance_criteria": AC})

    body = r.json()
    assert body["comparable"] is False

    fine = next(x for x in body["results"] if x["configured_provider"] == "fine_tuned")
    assert fine["succeeded"] is False
    assert fine["scenarios"] == []
    assert fine["coverage"] is None

    # The general LLM's own result is still returned and still scored.
    general = next(
        x for x in body["results"] if x["configured_provider"] == "general_llm"
    )
    assert general["succeeded"] is True
    assert general["coverage"] == 1.0

    assert any("did not answer" in n for n in body["notes"])


def test_metrics_are_computed_for_each_model(client):
    duplicate = [TWO_SCENARIOS[0], dict(TWO_SCENARIOS[0], source_ac_clause="AC2")]
    outcomes = [
        _outcome("general_llm"),
        _outcome("fine_tuned", scenarios=duplicate),
    ]

    with _patch(outcomes):
        r = client.post("/api/v1/evaluation/compare", json={"acceptance_criteria": AC})

    by = {x["configured_provider"]: x for x in r.json()["results"]}
    assert by["general_llm"]["coverage"] == 1.0
    assert by["general_llm"]["duplicate_rate"] == 0.0
    # Same steps twice under different clause citations: full coverage, but the
    # duplicate rate is what stops that reading as a win.
    assert by["fine_tuned"]["coverage"] == 1.0
    assert by["fine_tuned"]["duplicate_rate"] > 0.0


def test_a_failed_generation_is_not_scored_as_zero(client):
    """0.0 reads as 'covered nothing'; the model did not answer at all."""
    outcomes = [
        _outcome("general_llm"),
        _outcome("fine_tuned", scenarios=[], succeeded=False, error="boom"),
    ]

    with _patch(outcomes):
        r = client.post("/api/v1/evaluation/compare", json={"acceptance_criteria": AC})

    fine = next(
        x for x in r.json()["results"] if x["configured_provider"] == "fine_tuned"
    )
    assert fine["succeeded"] is False
    assert fine["coverage"] is None
    assert fine["duplicate_rate"] is None
    assert any("did not answer" in n for n in r.json()["notes"])


def test_criteria_without_numbered_clauses_say_so(client):
    with _patch([_outcome("general_llm"), _outcome("fine_tuned")]):
        r = client.post(
            "/api/v1/evaluation/compare",
            json={"acceptance_criteria": "the user can log in somehow"},
        )

    body = r.json()
    assert body["ac_clauses"] == []
    assert any("No numbered AC clauses" in n for n in body["notes"])


def test_a_ticket_id_is_fetched_and_labelled_as_a_real_ticket(client):
    ticket = type("T", (), {"acceptance_criteria": AC})()
    seen = {}

    async def record(item, provider, *, allow_fallback=True):
        seen["provenance"] = item.provenance
        seen["id"] = item.id
        return _outcome(provider)

    with patch(
        "app.services.jira_service.fetch_ticket_content",
        new=AsyncMock(return_value=ticket),
    ), patch("app.api.v1.evaluation.generate_one", new=record):
        r = client.post(
            "/api/v1/evaluation/compare", json={"jira_ticket_id": "PROJ-123"}
        )

    assert r.status_code == 200
    assert r.json()["jira_ticket_id"] == "PROJ-123"
    assert seen["provenance"] == "human_jira"
    assert seen["id"] == "PROJ-123"


def test_an_unreachable_ticket_is_a_502_not_a_crash(client):
    with patch(
        "app.services.jira_service.fetch_ticket_content",
        new=AsyncMock(side_effect=RuntimeError("jira down")),
    ):
        r = client.post(
            "/api/v1/evaluation/compare", json={"jira_ticket_id": "PROJ-123"}
        )

    # main.py reshapes HTTPException into {error, message, code} — not {detail}.
    assert r.status_code == 502
    assert "Could not fetch ticket" in r.json()["message"]


def test_a_ticket_with_no_criteria_is_rejected(client):
    ticket = type("T", (), {"acceptance_criteria": "   "})()
    with patch(
        "app.services.jira_service.fetch_ticket_content",
        new=AsyncMock(return_value=ticket),
    ):
        r = client.post(
            "/api/v1/evaluation/compare", json={"jira_ticket_id": "PROJ-123"}
        )

    assert r.status_code == 422


def test_an_empty_request_is_rejected(client):
    assert client.post("/api/v1/evaluation/compare", json={}).status_code == 422


def test_the_ticket_is_fetched_from_the_projects_jira(client):
    """Regression: compare reported "Jira is not configured" for a configured project.

    It called fetch_ticket_content with no credentials, so the fetch fell back
    to the server environment — empty for anyone whose Jira lives in project
    settings, which is where the UI puts it.
    """
    project = SimpleNamespace(
        id="proj-1",
        jira_base_url="https://acme.atlassian.net",
        jira_user_email="qa@acme.test",
        jira_api_token_encrypted="ciphertext",
    )
    seen: dict = {}

    async def fetch(ticket_id, credentials=None):
        seen["credentials"] = credentials
        return SimpleNamespace(acceptance_criteria=AC)

    with (
        patch(
            "app.api.v1.evaluation.ensure_project", new=AsyncMock(return_value=project)
        ),
        patch("app.services.project_config_service._project_token", return_value="tok"),
        patch("app.services.jira_service.fetch_ticket_content", new=fetch),
        patch(
            "app.api.v1.evaluation.generate_one",
            new=AsyncMock(side_effect=lambda i, p, **k: _outcome(p)),
        ),
    ):
        r = client.post(
            "/api/v1/evaluation/compare",
            json={"jira_ticket_id": "PROJ-123", "project_id": str(uuid.uuid4())},
        )

    assert r.status_code == 200
    creds = seen["credentials"]
    assert creds is not None, "the fetch was given no credentials at all"
    # The project's Jira, not whichever one the environment names.
    assert creds.source == "project"
    assert creds.base_url == "https://acme.atlassian.net"


def test_pasted_criteria_never_touch_jira_or_a_project(client):
    """No ticket is being fetched, so resolving a project would be pointless
    work — and would make this path fail when the database is unavailable."""
    with (
        patch(
            "app.api.v1.evaluation.ensure_project",
            new=AsyncMock(side_effect=AssertionError("should not resolve a project")),
        ),
        patch(
            "app.api.v1.evaluation.generate_one",
            new=AsyncMock(side_effect=lambda i, p, **k: _outcome(p)),
        ),
    ):
        r = client.post(
            "/api/v1/evaluation/compare", json={"acceptance_criteria": AC}
        )

    assert r.status_code == 200
