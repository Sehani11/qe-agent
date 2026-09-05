"""Tests for Story 6.3's objective metrics.

These are the backbone of the comparison precisely because no LLM is involved:
they are arithmetic over stored rows and cannot be argued with. Every function
here is pure, so scoring can be revised and re-run without regenerating —
which matters when one fine-tuned generation costs ~45s.
"""

import pytest

from app.services.evaluation_metrics import (
    coverage,
    duplicate_rate,
    reference_alignment,
    summarise_group,
)


def _s(
    clause,
    given="a user is logged in",
    when="they submit the form",
    then="it saves",
):
    return {
        "source_ac_clause": clause,
        "feature": "F",
        "scenario": f"scenario for {clause}",
        "given": given,
        "when": when,
        "then": then,
    }


# --- AC-clause coverage ----------------------------------------------------


def test_full_coverage_when_every_clause_has_a_scenario():
    ac = "AC1: first\nAC2: second"

    assert coverage(ac, [_s("AC1"), _s("AC2")]) == 1.0


def test_partial_coverage_is_the_fraction_of_clauses_hit():
    ac = "AC1: a\nAC2: b\nAC3: c\nAC4: d"

    assert coverage(ac, [_s("AC1"), _s("AC3")]) == 0.5


def test_extra_scenarios_for_one_clause_do_not_inflate_coverage():
    """Three scenarios for AC1 still leave AC2 uncovered. Coverage measures
    breadth; rewarding volume would let a model game it by repeating itself."""
    ac = "AC1: a\nAC2: b"

    assert coverage(ac, [_s("AC1"), _s("AC1"), _s("AC1")]) == 0.5


def test_a_citation_to_a_clause_that_does_not_exist_does_not_count():
    ac = "AC1: a\nAC2: b"

    assert coverage(ac, [_s("AC1"), _s("AC7")]) == 0.5


def test_clause_citations_are_matched_tolerantly():
    """Models write 'AC 1', 'ac1', 'AC1:' — penalising formatting would
    measure obedience to a citation style, not coverage."""
    ac = "AC1: a\nAC2: b"

    assert coverage(ac, [_s("AC 1"), _s("ac2")]) == 1.0


def test_coverage_of_an_empty_generation_is_zero():
    assert coverage("AC1: a", []) == 0.0


def test_coverage_is_undefined_rather_than_zero_when_no_clauses_parse():
    """Dividing by zero clauses would silently score every provider 0 on a
    malformed item and drag both averages down equally but meaninglessly."""
    assert coverage("free text with no clause labels", [_s("AC1")]) is None


# --- duplicate detection ---------------------------------------------------


def test_distinct_scenarios_have_no_duplicates():
    scenarios = [
        _s("AC1", given="a", when="b", then="c"),
        _s("AC2", given="x", when="y", then="z"),
    ]

    assert duplicate_rate(scenarios) == 0.0


def test_identical_scenarios_are_detected():
    scenarios = [_s("AC1"), _s("AC2")]  # same given/when/then text

    assert duplicate_rate(scenarios) == 0.5


def test_near_duplicates_differing_only_in_punctuation_are_detected():
    """RUN_LOG records the fine-tune emitting a scenario whose `when` copied
    the previous one. Near-duplicate detection is what makes that visible."""
    scenarios = [
        _s("AC1", given="A user is logged in.", when="They submit!", then="Saved"),
        _s("AC2", given="a user is logged in", when="they submit", then="saved"),
    ]

    assert duplicate_rate(scenarios) > 0


def test_duplicate_rate_of_a_single_scenario_is_zero():
    assert duplicate_rate([_s("AC1")]) == 0.0


def test_duplicate_rate_of_nothing_is_zero_not_an_error():
    assert duplicate_rate([]) == 0.0


# --- reference alignment (holdout only) ------------------------------------


def test_alignment_is_one_for_an_exact_reproduction():
    reference = [_s("AC1", given="a user exists", when="they log in", then="ok")]

    assert reference_alignment(reference, reference) == pytest.approx(1.0)


def test_alignment_is_low_for_unrelated_content():
    reference = [_s("AC1", given="a user exists", when="they log in", then="ok")]
    generated = [_s("AC1", given="zebras roam", when="rain falls", then="tea brews")]

    assert reference_alignment(generated, reference) < 0.2


def test_alignment_is_none_without_a_reference():
    """On-domain curated items have no reference. Scoring them 0 would make the
    on-domain group look catastrophically worse than the holdout for a reason
    that has nothing to do with the models."""
    assert reference_alignment([_s("AC1")], None) is None
    assert reference_alignment([_s("AC1")], []) is None


# --- aggregation -----------------------------------------------------------


def test_summarise_group_excludes_untrustworthy_rows():
    """A row served by the fallback is data about a degradation, not about the
    fine-tune. Averaging it in is how a comparison quietly becomes wrong."""
    rows = [
        {"succeeded": True, "configured_provider": "fine_tuned",
         "effective_provider": "fine_tuned", "coverage": 1.0, "latency_seconds": 40.0},
        {"succeeded": True, "configured_provider": "fine_tuned",
         "effective_provider": "general_llm", "coverage": 0.0, "latency_seconds": 5.0},
    ]

    summary = summarise_group(rows)

    assert summary["n_trustworthy"] == 1
    assert summary["n_degraded"] == 1
    assert summary["coverage_mean"] == 1.0


def test_success_rate_excludes_degraded_rows_like_every_other_metric():
    """Review finding: a fallback-served row was counted as a fine-tuned
    SUCCESS, because success_rate ran over all rows while every other stat
    used trustworthy-only. The one metric added to stop a model hiding behind
    its good rows was the one counting rows it never produced.
    """
    rows = [
        {"succeeded": True, "configured_provider": "fine_tuned",
         "effective_provider": "fine_tuned", "coverage": 1.0},
        {"succeeded": True, "configured_provider": "fine_tuned",
         "effective_provider": "general_llm", "coverage": 1.0},  # degraded
    ]

    summary = summarise_group(rows)

    # The degraded row is excluded from the denominator entirely, so the one
    # genuine generation is 1/1 — not 2/2, which would credit the fine-tune
    # for output the general LLM produced.
    assert summary["success_rate"] == 1.0
    assert summary["n_degraded"] == 1


def test_success_rate_still_counts_genuine_failures():
    """Excluding degraded rows must not also excuse real failures."""
    rows = [
        {"succeeded": True, "configured_provider": "fine_tuned",
         "effective_provider": "fine_tuned"},
        {"succeeded": False, "configured_provider": "fine_tuned",
         "effective_provider": "fine_tuned"},
    ]

    assert summarise_group(rows)["success_rate"] == 0.5


def test_summarise_group_reports_generation_success_rate():
    """A model that often returns nothing must not hide behind good scores on
    the occasions it does answer."""
    rows = [
        {"succeeded": True, "configured_provider": "general_llm",
         "effective_provider": "general_llm", "coverage": 1.0},
        {"succeeded": False, "configured_provider": "general_llm",
         "effective_provider": "general_llm", "coverage": None},
    ]

    summary = summarise_group(rows)

    assert summary["success_rate"] == 0.5


def test_summarise_group_handles_no_rows():
    summary = summarise_group([])

    assert summary["n_trustworthy"] == 0
    assert summary["coverage_mean"] is None
