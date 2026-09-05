"""Tests for Story 6.3's blinded judge (AC6).

Blinding is testable without any model call, which is the point: the property
that makes a subjective score admissible is a property of the payload, not of
the judge's answer.
"""

import random

import pytest

from app.services.evaluation_judge import (
    aggregate_judge_scores,
    build_blinded_comparison,
    judge_conflict,
    parse_judge_response,
    unblind_scores,
)

_FT = [{"source_ac_clause": "AC1", "feature": "F", "scenario": "s",
        "given": "g", "when": "w", "then": "t", "provider": "fine_tuned"}]
_GL = [{"source_ac_clause": "AC1", "feature": "F", "scenario": "s2",
        "given": "g2", "when": "w2", "then": "t2", "model": "gpt-4o"}]


def _comparison(seed: int = 0):
    return build_blinded_comparison(
        "item-1", "AC1: x", {"fine_tuned": _FT, "general_llm": _GL},
        rng=random.Random(seed),
    )


def test_the_payload_carries_no_provenance_markers():
    """A judge that can tell which system is which is not blinded."""
    payload = repr(_comparison().payload)

    assert "fine_tuned" not in payload
    assert "general_llm" not in payload
    assert "gpt-4o" not in payload


def test_provenance_keys_are_stripped_from_every_scenario():
    comparison = _comparison()

    for label in ("A", "B"):
        for scenario in comparison.payload[label]["scenarios"]:
            assert "provider" not in scenario
            assert "model" not in scenario


def test_the_scenario_content_itself_survives_blinding():
    """Stripping must not gut the thing being judged."""
    comparison = _comparison()
    all_steps = {
        s["given"]
        for label in ("A", "B")
        for s in comparison.payload[label]["scenarios"]
    }

    assert all_steps == {"g", "g2"}


def test_the_key_maps_labels_back_to_providers():
    comparison = _comparison()

    assert set(comparison.key) == {"A", "B"}
    assert set(comparison.key.values()) == {"fine_tuned", "general_llm"}


def test_the_key_is_not_present_in_the_payload():
    assert "key" not in _comparison().payload


def test_presentation_order_is_randomised_across_items():
    """A judge with a positional bias must not systematically favour one system.

    With a fixed order, position and provider would be perfectly confounded.
    """
    orders = {_comparison(seed).key["A"] for seed in range(30)}

    assert orders == {"fine_tuned", "general_llm"}, "order never varied"


def test_unblinding_restores_provider_names():
    comparison = _comparison()
    scores = {"A": {"correctness": 4}, "B": {"correctness": 2}}

    result = unblind_scores(scores, comparison.key)

    assert set(result) == {"fine_tuned", "general_llm"}
    assert result[comparison.key["A"]]["correctness"] == 4


def test_comparing_other_than_two_systems_is_rejected():
    with pytest.raises(ValueError):
        build_blinded_comparison("i", "AC1: x", {"only_one": _FT})


# --- conflict of interest ---------------------------------------------------


def test_a_judge_that_is_also_a_contestant_is_flagged():
    conflict = judge_conflict("gpt-4o", ["gpt-4o", "bdd-lora"])

    assert conflict is not None
    assert "contestant" in conflict


def test_a_judge_sharing_a_family_with_a_contestant_is_flagged():
    conflict = judge_conflict("gpt-4o-mini", ["gpt-4o", "bdd-lora"])

    assert conflict is not None
    assert "family" in conflict


def test_an_independent_judge_is_not_flagged():
    assert judge_conflict("claude-opus-5", ["gpt-4o", "bdd-lora"]) is None


# --- response parsing -------------------------------------------------------


def test_a_fenced_json_verdict_parses():
    raw = '```json\n{"A": {"correctness": 4}, "B": {"correctness": 3}}\n```'

    assert parse_judge_response(raw)["A"]["correctness"] == 4


@pytest.mark.parametrize(
    "raw", ["no json here", '{"A": {"correctness": 4}}', "{not json"]
)
def test_an_unusable_verdict_is_rejected_rather_than_half_scored(raw):
    with pytest.raises(ValueError):
        parse_judge_response(raw)


# --- aggregation ------------------------------------------------------------


def test_judge_scores_are_averaged_per_provider():
    per_item = [
        {"fine_tuned": {"ac_relevance": 4, "correctness": 4, "specificity": 2},
         "general_llm": {"ac_relevance": 5, "correctness": 5, "specificity": 4}},
        {"fine_tuned": {"ac_relevance": 2, "correctness": 2, "specificity": 2},
         "general_llm": {"ac_relevance": 5, "correctness": 3, "specificity": 4}},
    ]

    agg = aggregate_judge_scores(per_item)

    assert agg["fine_tuned"]["ac_relevance"] == 3.0
    assert agg["general_llm"]["correctness"] == 4.0


def test_non_numeric_judge_output_is_skipped_not_coerced():
    """A judge that returns "good" instead of 4 must not become 0."""
    per_item = [
        {"fine_tuned": {"ac_relevance": "good", "correctness": 4, "specificity": 3}},
    ]

    agg = aggregate_judge_scores(per_item)

    assert "ac_relevance" not in agg["fine_tuned"]
    assert agg["fine_tuned"]["correctness"] == 4.0


def test_aggregating_nothing_yields_nothing():
    assert aggregate_judge_scores([]) == {}
