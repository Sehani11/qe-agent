"""Tests for the EvaluationResult model (Story 6.3).

`is_trustworthy` is the guard that decides whether a row may enter the metrics.
It exists because `FineTunedModelProvider` degrades to the general LLM silently
and returns an identically-shaped payload — so a run can be entirely
general-versus-general and look perfectly healthy in the database.
"""

import pytest

from app.models.evaluation_result import EvaluationResult


def _row(**overrides) -> EvaluationResult:
    defaults = dict(
        run_id="run-1",
        user_id="user-1",
        item_id="curated-01",
        domain_group="on_domain",
        provenance="agent_authored",
        acceptance_criteria="AC1: x",
        configured_provider="fine_tuned",
        effective_provider="fine_tuned",
        model_identifier="bdd-lora",
        succeeded=True,
    )
    return EvaluationResult(**{**defaults, **overrides})


def test_a_genuinely_fine_tuned_row_is_trustworthy():
    assert _row().is_trustworthy is True


def test_a_row_served_by_the_fallback_is_not_trustworthy():
    """The whole reason this column exists.

    Configured fine_tuned, served by general_llm: real data about a
    degradation, useless data about the fine-tune. It must be excluded from
    the comparison, not averaged into it.
    """
    row = _row(effective_provider="general_llm", fallback_reason="endpoint_error")

    assert row.is_trustworthy is False


def test_a_failed_row_is_not_trustworthy():
    assert _row(succeeded=False, error="boom").is_trustworthy is False


def test_a_row_with_no_attribution_is_not_trustworthy():
    """`effective_provider` is None when a generation never reached a model.

    Absence of evidence must not read as evidence of a genuine run.
    """
    assert _row(effective_provider=None).is_trustworthy is False


def test_a_general_llm_row_is_trustworthy_when_it_served_itself():
    """The baseline has the same standard applied to it."""
    row = _row(configured_provider="general_llm", effective_provider="general_llm")

    assert row.is_trustworthy is True


@pytest.mark.parametrize("group", ["on_domain", "off_domain"])
def test_both_domain_groups_round_trip(group):
    assert _row(domain_group=group).domain_group == group


def test_the_model_is_registered_for_alembic_autogenerate():
    """A model missing from app.models is invisible to migrations."""
    from app import models

    assert models.EvaluationResult is EvaluationResult
    assert "EvaluationResult" in models.__all__
