"""Tests for the Story 6.3 evaluation runner.

The runner's correctness is not "does it produce numbers" — it is "are the
numbers about the thing they claim to be about". Two properties carry that:

  * every row records which provider ACTUALLY served it, and
  * a run that silently degraded is refused rather than summarised.

Both are tested here with the providers stubbed, so none of this needs a model.
"""

from unittest.mock import patch

import pytest

from app.services.evaluation_service import (
    DegradedRunError,
    GenerationOutcome,
    assert_run_is_trustworthy,
    generate_one,
    pending_work,
    provider_selected,
)
from app.services.evaluation_set import EvalItem

_MOD = "app.services.evaluation_service"

_ITEM = EvalItem(
    id="curated-01",
    acceptance_criteria="AC1: a user can log in\nAC2: a bad password is rejected",
    domain_group="on_domain",
    provenance="agent_authored",
)

_RESULT = {
    "scenarios": [
        {
            "source_ac_clause": "AC1",
            "feature": "Login",
            "scenario": "Valid login",
            "given": "a registered user",
            "when": "they submit valid credentials",
            "then": "the dashboard loads",
        }
    ]
}


class _StubProvider:
    """Stands in for a BDDModelProvider, including its attribution state."""

    def __init__(self, name, effective=None, reason=None, result=None, boom=None):
        self.name = name
        self._effective = effective if effective is not None else name
        self._reason = reason
        self._result = result if result is not None else _RESULT
        self._boom = boom
        self.effective_provider = None
        self.fallback_reason = None

    async def generate_bdd(
        self, acceptance_criteria, system_prompt="", response_format=None
    ):
        if self._boom:
            raise self._boom
        self.effective_provider = self._effective
        self.fallback_reason = self._reason
        return self._result


# --- provider selection stays with the factory (NFR-R6) --------------------


def test_provider_selection_goes_through_the_factory():
    """NFR-R6: the factory is the only place selection logic may live, so the
    runner must not keep its own registry of provider classes."""
    with provider_selected("general_llm") as provider:
        assert provider.name == "general_llm"


def test_the_configured_provider_setting_is_restored_afterwards():
    """The runner mutates a global to reuse the factory; it must put it back,
    or a later call in the same process silently evaluates the wrong model."""
    from app.core.config import settings

    before = settings.bdd_model_provider
    with provider_selected("fine_tuned"):
        pass

    assert settings.bdd_model_provider == before


def test_the_setting_is_restored_even_when_the_body_raises():
    from app.core.config import settings

    before = settings.bdd_model_provider
    with pytest.raises(RuntimeError), provider_selected("fine_tuned"):
        raise RuntimeError("boom")

    assert settings.bdd_model_provider == before


# --- attribution is captured per generation (AC3) --------------------------


async def test_a_genuine_generation_records_the_effective_provider():
    stub = _StubProvider("fine_tuned")

    with patch(f"{_MOD}.get_bdd_model_provider", return_value=stub):
        outcome = await generate_one(_ITEM, "fine_tuned")

    assert outcome.succeeded is True
    assert outcome.configured_provider == "fine_tuned"
    assert outcome.effective_provider == "fine_tuned"
    assert outcome.latency_seconds is not None


async def test_a_silent_fallback_is_recorded_not_hidden():
    """The failure this story is most likely to die of: the 12s bound fires,
    the general LLM answers, and the row looks like a fine-tuned result."""
    stub = _StubProvider("fine_tuned", effective="general_llm", reason="endpoint_error")

    with patch(f"{_MOD}.get_bdd_model_provider", return_value=stub):
        outcome = await generate_one(_ITEM, "fine_tuned")

    assert outcome.effective_provider == "general_llm"
    assert outcome.fallback_reason == "endpoint_error"
    assert outcome.succeeded is True, "it produced output; it is just not the fine-tune"


async def test_a_raising_provider_becomes_a_failed_row_not_a_crash():
    """A 30-minute batch must not lose 29 minutes of work to one bad item."""
    stub = _StubProvider("fine_tuned", boom=ValueError("endpoint exploded"))

    with patch(f"{_MOD}.get_bdd_model_provider", return_value=stub):
        outcome = await generate_one(_ITEM, "fine_tuned")

    assert outcome.succeeded is False
    assert "endpoint exploded" in (outcome.error or "")
    assert outcome.scenarios is None


async def test_output_that_fails_schema_validation_is_a_failed_row():
    """`{"wrong_key": []}` VALIDATES — `scenarios` defaults to [] — so it
    arrives looking like a successful generation of nothing.

    Counting it as a success would average a malformed response in as 0%
    coverage, indistinguishable from a model that genuinely declined.
    """
    stub = _StubProvider("general_llm", result={"wrong_key": []})

    with patch(f"{_MOD}.get_bdd_model_provider", return_value=stub):
        outcome = await generate_one(_ITEM, "general_llm")

    assert outcome.succeeded is False
    assert "zero scenarios" in (outcome.error or "")


async def test_an_explicitly_empty_scenarios_array_is_also_a_failed_row():
    stub = _StubProvider("general_llm", result={"scenarios": []})

    with patch(f"{_MOD}.get_bdd_model_provider", return_value=stub):
        outcome = await generate_one(_ITEM, "general_llm")

    assert outcome.succeeded is False


# --- resumability (AC2) -----------------------------------------------------


def test_pending_work_excludes_already_completed_combinations():
    done = {("curated-01", "fine_tuned"), ("curated-01", "general_llm")}
    items = [_ITEM, EvalItem(id="curated-02", acceptance_criteria="AC1: x",
                             domain_group="on_domain", provenance="agent_authored")]

    pending = pending_work(items, ["fine_tuned", "general_llm"], done)

    assert ("curated-01", "fine_tuned") not in pending
    assert ("curated-02", "fine_tuned") in pending
    assert len(pending) == 2


def test_pending_work_is_everything_on_a_fresh_run():
    items = [_ITEM]

    pending = pending_work(items, ["fine_tuned", "general_llm"], set())

    assert len(pending) == 2


# --- a degraded run is refused, not summarised (AC3) ------------------------


def test_a_run_where_every_fine_tuned_row_fell_back_is_refused():
    """Summarising this would report a general-vs-general comparison as a
    fine-tuned evaluation — a confident, completely wrong answer."""
    outcomes = [
        GenerationOutcome(
            item_id=f"i{n}",
            configured_provider="fine_tuned",
            effective_provider="general_llm",
            fallback_reason="endpoint_error",
            succeeded=True,
        )
        for n in range(5)
    ]

    with pytest.raises(DegradedRunError) as excinfo:
        assert_run_is_trustworthy(outcomes)

    assert "5" in str(excinfo.value)


def test_a_clean_run_passes_the_trust_check():
    outcomes = [
        GenerationOutcome(
            item_id="i1", configured_provider="fine_tuned",
            effective_provider="fine_tuned", succeeded=True,
        ),
        GenerationOutcome(
            item_id="i1", configured_provider="general_llm",
            effective_provider="general_llm", succeeded=True,
        ),
    ]

    assert_run_is_trustworthy(outcomes)  # does not raise


def test_a_single_degraded_row_is_reported_but_does_not_fail_the_run():
    """One flake should not discard a 30-minute run; the metrics exclude the
    row, and the report states the exclusion."""
    outcomes = [
        GenerationOutcome(item_id=f"i{n}", configured_provider="fine_tuned",
                          effective_provider="fine_tuned", succeeded=True)
        for n in range(9)
    ] + [
        GenerationOutcome(item_id="i9", configured_provider="fine_tuned",
                          effective_provider="general_llm",
                          fallback_reason="endpoint_error", succeeded=True)
    ]

    assert_run_is_trustworthy(outcomes)  # 10% degraded is under the threshold
