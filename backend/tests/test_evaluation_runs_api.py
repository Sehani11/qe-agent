"""Tests for saved comparison runs: batch, list, report and delete.

These endpoints turn one-off comparisons into data that accumulates, so the
cases that matter are the ones where accumulation could quietly corrupt a
conclusion — a degraded row averaged in, or one user reading or deleting
another user's rows.

Strategy: override get_current_user + get_db (same pattern as
test_training_datasets.py) and patch generate_one, so nothing calls a model.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.services.evaluation_service import GenerationOutcome

USER = "user-a-id"

AC = "AC1: A user can pay with a saved card.\nAC2: An expired card is rejected."

SCENARIOS = [
    {
        "source_ac_clause": "AC1",
        "feature": "Checkout",
        "scenario": "Pay with a saved card",
        "given": "a saved card",
        "when": "they pay",
        "then": "the payment succeeds",
    },
    {
        "source_ac_clause": "AC2",
        "feature": "Checkout",
        "scenario": "Expired card",
        "given": "an expired card",
        "when": "they pay",
        "then": "the payment is rejected",
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


def _outcome(provider, *, effective=None, succeeded=True, scenarios=None):
    return GenerationOutcome(
        item_id="PROJ-1",
        configured_provider=provider,
        effective_provider=effective if effective is not None else provider,
        model_identifier=f"model-{provider}",
        scenarios=scenarios if scenarios is not None else SCENARIOS,
        latency_seconds=2.0,
        succeeded=succeeded,
    )


def _row(**kw):
    """A persisted EvaluationResult, as the report endpoint reads it."""
    base = dict(
        run_id="run-a",
        item_id="PROJ-1",
        user_id=USER,
        domain_group="on_domain",
        provenance="human_jira",
        acceptance_criteria=AC,
        configured_provider="general_llm",
        effective_provider="general_llm",
        scenarios=SCENARIOS,
        reference_scenarios=None,
        latency_seconds=2.0,
        succeeded=True,
        created_at=datetime.now(UTC),
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _db_returning(rows):
    db = AsyncMock()
    scalars = MagicMock()
    scalars.all.return_value = rows
    result = MagicMock()
    result.scalars.return_value = scalars
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    return db


# --- batch ----------------------------------------------------------------


def test_a_batch_saves_one_row_per_provider_per_ticket(client):
    saved = []

    async def fake_persist(outcome, item, run_id, db, user_id=None):
        saved.append((run_id, item.id, outcome.configured_provider, user_id))

    ticket = SimpleNamespace(acceptance_criteria=AC)

    with (
        patch(
            "app.services.jira_service.fetch_ticket_content",
            new=AsyncMock(return_value=ticket),
        ),
        patch(
            "app.api.v1.evaluation.generate_one",
            new=AsyncMock(side_effect=lambda i, p, **k: _outcome(p)),
        ),
        patch("app.api.v1.evaluation.persist_outcome", new=fake_persist),
    ):
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        r = client.post(
            "/api/v1/evaluation/runs",
            json={"jira_ticket_ids": ["PROJ-1", "PROJ-2"], "run_id": "run-a"},
        )

    assert r.status_code == 200
    body = r.json()
    assert body["requested"] == 2 and body["saved"] == 2
    # Two tickets x two providers, all under one run id and the caller's user.
    assert len(saved) == 4
    assert {s[0] for s in saved} == {"run-a"}
    assert {s[3] for s in saved} == {USER}
    assert {s[2] for s in saved} == {"general_llm", "fine_tuned"}


def test_one_bad_ticket_does_not_abort_the_batch(client):
    """A batch can already have spent minutes; one failure must not discard it."""

    # Mirrors fetch_ticket_content(ticket_id_or_url, credentials): the caller
    # now passes the project's Jira credentials as the second argument.
    async def fetch(ticket_id, credentials=None):
        if ticket_id == "BAD-1":
            raise RuntimeError("jira down")
        return SimpleNamespace(acceptance_criteria=AC)

    with (
        patch("app.services.jira_service.fetch_ticket_content", new=fetch),
        patch(
            "app.api.v1.evaluation.generate_one",
            new=AsyncMock(side_effect=lambda i, p, **k: _outcome(p)),
        ),
        patch("app.api.v1.evaluation.persist_outcome", new=AsyncMock()),
    ):
        app.dependency_overrides[get_db] = lambda: AsyncMock()
        r = client.post(
            "/api/v1/evaluation/runs",
            json={"jira_ticket_ids": ["PROJ-1", "BAD-1", "PROJ-2"], "run_id": "run-a"},
        )

    body = r.json()
    assert body["requested"] == 3
    assert body["saved"] == 2
    bad = next(i for i in body["items"] if i["jira_ticket_id"] == "BAD-1")
    assert bad["saved"] is False
    assert "Could not fetch ticket" in bad["error"]


def test_an_empty_ticket_list_is_rejected(client):
    app.dependency_overrides[get_db] = lambda: AsyncMock()
    r = client.post(
        "/api/v1/evaluation/runs", json={"jira_ticket_ids": [], "run_id": "run-a"}
    )
    assert r.status_code == 422


# --- report ---------------------------------------------------------------


def test_report_aggregates_both_providers(client):
    rows = [
        _row(configured_provider="general_llm", effective_provider="general_llm"),
        _row(configured_provider="fine_tuned", effective_provider="fine_tuned"),
    ]
    app.dependency_overrides[get_db] = lambda: _db_returning(rows)

    r = client.get("/api/v1/evaluation/report?run_id=run-a")

    assert r.status_code == 200
    body = r.json()
    assert body["run_ids"] == ["run-a"]
    assert body["row_count"] == 2
    group = body["groups"][0]
    assert group["domain_group"] == "on_domain"
    providers = {p["provider"]: p for p in group["providers"]}
    assert providers["general_llm"]["coverage"] == 1.0
    assert providers["fine_tuned"]["coverage"] == 1.0


def test_a_degraded_row_is_excluded_from_the_averages_and_reported(client):
    """THE correctness case: a fine-tuned row the general LLM actually answered
    must not be averaged in as if the fine-tune produced it."""
    rows = [
        _row(configured_provider="general_llm", effective_provider="general_llm"),
        _row(configured_provider="fine_tuned", effective_provider="general_llm"),
    ]
    app.dependency_overrides[get_db] = lambda: _db_returning(rows)

    body = client.get("/api/v1/evaluation/report?run_id=run-a").json()

    providers = {p["provider"]: p for p in body["groups"][0]["providers"]}
    fine = providers["fine_tuned"]
    assert fine["degraded_excluded"] == 1
    assert fine["items_scored"] == 0
    assert fine["coverage"] is None
    assert any("excluded" in n for n in body["notes"])


def test_report_with_no_run_id_pools_every_run(client):
    rows = [
        _row(run_id="run-a", item_id="PROJ-1"),
        _row(run_id="run-b", item_id="PROJ-2"),
    ]
    app.dependency_overrides[get_db] = lambda: _db_returning(rows)

    body = client.get("/api/v1/evaluation/report").json()

    assert body["run_ids"] == ["run-a", "run-b"]
    assert body["item_count"] == 2


def test_report_for_an_unknown_run_is_404(client):
    app.dependency_overrides[get_db] = lambda: _db_returning([])
    r = client.get("/api/v1/evaluation/report?run_id=nope")
    assert r.status_code == 404


# --- delete ---------------------------------------------------------------


def test_delete_removes_the_run_and_is_scoped_to_the_user(client):
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 4
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    r = client.delete("/api/v1/evaluation/runs/run-a")

    assert r.status_code == 204
    db.commit.assert_awaited_once()
    # The WHERE clause must carry the user id, or one user could delete
    # another's results by guessing a run id.
    statement = str(db.execute.await_args.args[0])
    assert "user_id" in statement and "run_id" in statement


def test_deleting_a_run_that_does_not_exist_is_404(client):
    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    assert client.delete("/api/v1/evaluation/runs/nope").status_code == 404
