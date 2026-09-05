"""Tests for the Story 6.3 evaluation-set loader.

The set is deliberately heterogeneous — two groups with different provenance
and different scoring capability — and the loader's whole job is to keep that
distinction intact. Averaging an off-domain group that has reference Gherkin
with an on-domain group that does not produces a number describing neither.
"""

import json

import pytest

from app.services.evaluation_set import (
    EvalItem,
    ac_clauses,
    load_curated_items,
    load_evaluation_set,
    load_holdout_items,
)


@pytest.fixture
def holdout_available():
    """holdout.jsonl is git-ignored derived data — skip rather than fail on a
    fresh checkout, but never let an empty holdout masquerade as a result."""
    from app.services.evaluation_set import HOLDOUT_PATH

    if not HOLDOUT_PATH.exists():
        pytest.skip("training/data/holdout.jsonl not on disk — rebuild dataset")


def test_curated_items_are_on_domain_and_carry_no_reference():
    items = load_curated_items()

    assert len(items) >= 10
    for item in items:
        assert item.domain_group == "on_domain"
        assert item.provenance == "agent_authored"
        assert item.reference_scenarios is None, "curated items have no reference"
        assert item.acceptance_criteria.strip()


def test_curated_provenance_is_not_overstated_as_human():
    """These were written by the dev agent. Calling them human-authored would
    misrepresent the evaluation's validity."""
    assert all(i.provenance != "human_authored" for i in load_curated_items())


def test_a_curated_entry_can_declare_its_own_provenance(tmp_path, monkeypatch):
    """Real Jira tickets added to the curated file must be labelled as such.

    The loader hardcoded `agent_authored`, so an item from a real ticket was
    filed under a label that understated it and `human_jira` was unreachable.
    """
    from app.services import evaluation_set as module

    path = tmp_path / "curated.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {"id": "real-1", "acceptance_criteria": "AC1: x",
                     "provenance": "human_jira"},
                    {"id": "seed-1", "acceptance_criteria": "AC1: y"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "CURATED_PATH", path)

    by_id = {i.id: i for i in module.load_curated_items()}

    assert by_id["real-1"].provenance == "human_jira"
    # An entry that says nothing must NOT be upgraded to something stronger.
    assert by_id["seed-1"].provenance == "agent_authored"


def test_holdout_items_are_off_domain_and_carry_a_reference(holdout_available):
    items = load_holdout_items()

    # A LOWER BOUND, not an exact count. holdout.jsonl is git-ignored derived
    # data rebuilt by build_dataset.py, so its size tracks the corpus: Run 1
    # produced 18, and the Scenario Outline expansion added 2026-08-13 took it
    # to 19. Pinning the exact number asserts a property of one machine's last
    # dataset build, which fails for anyone who rebuilds. What must hold is that
    # the holdout is substantial enough to score against — the AC1 gate in
    # test_the_assembled_set_meets_the_ac1_threshold covers the total.
    assert len(items) >= 15
    for item in items:
        assert item.domain_group == "off_domain"
        assert item.provenance == "back_generated"
        assert item.reference_scenarios, "holdout items must carry reference Gherkin"


def test_the_assembled_set_meets_the_ac1_threshold(holdout_available):
    """AC1 gate: at least 20 items, or the story stops and reports."""
    items = load_evaluation_set()

    assert len(items) >= 20
    assert {i.domain_group for i in items} == {"on_domain", "off_domain"}


def test_every_item_id_is_unique(holdout_available):
    items = load_evaluation_set()

    assert len({i.id for i in items}) == len(items)


def test_no_evaluation_item_comes_from_the_training_split(holdout_available):
    """The fine-tune memorised train.jsonl. Scoring on it is meaningless.

    The holdout was split by ORIGIN FILE, so this checks the property that
    actually matters rather than trusting the filename.
    """
    from pathlib import Path

    train = Path(__file__).resolve().parents[2] / "training" / "data" / "train.jsonl"
    if not train.exists():
        pytest.skip("train.jsonl not on disk")

    train_origins = {
        json.loads(line)["meta"]["origin"]
        for line in train.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    holdout_origins = {i.origin for i in load_holdout_items()}

    assert not (train_origins & holdout_origins), "holdout leaked into training"


# --- AC-clause parsing: the backbone of the coverage metric ----------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("AC1: first\nAC2: second", ["AC1", "AC2"]),
        ("AC1: only one", ["AC1"]),
        ("AC1: a\nAC2: b\nAC3: c\nAC4: d", ["AC1", "AC2", "AC3", "AC4"]),
        ("no numbered clauses here", []),
    ],
)
def test_ac_clauses_are_extracted_for_the_coverage_metric(text, expected):
    assert ac_clauses(text) == expected


def test_ac_clause_extraction_tolerates_formatting_variance():
    """Real ACs are not uniformly formatted; the metric must not silently
    under-count and make coverage look worse than it is."""
    assert ac_clauses("AC 1: spaced\nac2 - lowercase dash") == ["AC1", "AC2"]


def test_eval_item_rejects_an_unknown_domain_group():
    with pytest.raises(ValueError):
        EvalItem(
            id="x",
            acceptance_criteria="AC1: x",
            domain_group="somewhere_else",
            provenance="agent_authored",
        )
