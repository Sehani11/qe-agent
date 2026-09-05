"""Tests for clause coverage on the BDD generation path.

The failure these exist to prevent: a generation that cites some of a ticket's
clauses and silently skips the rest. The skipped clauses produce no scenarios,
the missing scenarios produce no verdicts, and the traceability report reads as
though the ticket were smaller than it is.
"""

from app.services.bdd_coverage import clause_labels, coverage_report


def _s(clause: str) -> dict[str, str]:
    return {"source_ac_clause": clause}


# ---------------------------------------------------------------------------
# clause_labels
# ---------------------------------------------------------------------------


def test_ac_labels_are_parsed_in_order() -> None:
    text = "- AC1: first\n- AC2: second\n- AC3: third"
    assert clause_labels(text) == ["AC1", "AC2", "AC3"]


def test_ac_labels_survive_markdown_emphasis() -> None:
    """`- **AC1** — Given ...` is how a Jira ticket routinely arrives.

    Requiring punctuation directly after the label made every emphasised
    clause invisible, which silently switched the whole ticket to ordinal
    numbering.
    """
    text = "- **AC1** — Given a candidate\n- **AC12** — Given a third party"
    assert clause_labels(text) == ["AC1", "AC12"]


def test_ac_labels_are_deduped_keeping_first_appearance() -> None:
    text = "AC2: second\nAC1: first\nAC2: restated"
    assert clause_labels(text) == ["AC2", "AC1"]


def test_ordinal_clauses_are_used_when_no_ac_labels_exist() -> None:
    """A "Business Rules"-style ticket still gets a denominator."""
    text = "1. cancelled is terminal.\n2. Only participants may cancel.\n3. Refunds."
    assert clause_labels(text) == ["C1", "C2", "C3"]


def test_ac_labels_win_over_ordinals_when_both_are_present() -> None:
    """Mixing the two schemes would measure against a denominator that is
    neither list."""
    text = "1. a business rule\n2. another\n\n- AC1: the real criterion"
    assert clause_labels(text) == ["AC1"]


def test_prose_with_no_clauses_parses_to_nothing() -> None:
    assert clause_labels("Allow the user to update their profile.") == []


def test_ac_label_does_not_match_ac_inside_a_word() -> None:
    assert clause_labels("access control and each account") == []


# ---------------------------------------------------------------------------
# coverage_report
# ---------------------------------------------------------------------------


def test_full_coverage_reports_no_gaps() -> None:
    text = "AC1: a\nAC2: b"
    report = coverage_report(text, [_s("AC1: a"), _s("AC2: b")])
    assert report["ratio"] == 1.0
    assert report["uncovered"] == []


def test_uncovered_clauses_are_named_in_ticket_order() -> None:
    """The whole point of the metric: say WHICH clauses were skipped."""
    text = "AC1: a\nAC2: b\nAC3: c\nAC4: d"
    report = coverage_report(text, [_s("AC3: c"), _s("AC1: a")])
    assert report["covered_clauses"] == 2
    assert report["total_clauses"] == 4
    assert report["uncovered"] == ["AC2", "AC4"]
    assert report["ratio"] == 0.5


def test_citing_one_clause_repeatedly_does_not_cover_the_others() -> None:
    """Breadth, not volume - three scenarios for one clause is still 1 of 2."""
    text = "AC1: a\nAC2: b"
    report = coverage_report(text, [_s("AC1"), _s("AC1"), _s("AC1")])
    assert report["ratio"] == 0.5
    assert report["uncovered"] == ["AC2"]


def test_a_ticket_covered_only_by_its_rules_reports_every_ac_uncovered() -> None:
    """The exact HS-187 failure: scenarios derived from the Business Rules
    while the ticket's Acceptance Criteria list went untouched."""
    text = (
        "## Business Rules\n"
        "1. cancelled is terminal.\n"
        "2. Only participants may cancel.\n"
        "\n"
        "## Acceptance Criteria\n"
        "- **AC1** — candidate cancels a pending booking\n"
        "- **AC2** — interviewer cancels an accepted booking\n"
        "- **AC3** — a third party gets 403\n"
    )
    scenarios = [
        _s("1. cancelled is terminal."),
        _s("2. Only participants may cancel."),
    ]
    report = coverage_report(text, scenarios)
    assert report["total_clauses"] == 3
    assert report["covered_clauses"] == 0
    assert report["uncovered"] == ["AC1", "AC2", "AC3"]


def test_ordinal_citations_match_ordinal_clauses() -> None:
    text = "1. first rule\n2. second rule"
    report = coverage_report(text, [_s("1. first rule")])
    assert report["uncovered"] == ["C2"]


def test_citation_style_is_not_scored() -> None:
    """'AC 1', 'ac1' and 'AC1:' all mean clause 1."""
    text = "AC1: a\nAC2: b"
    report = coverage_report(text, [_s("AC 1"), _s("ac2 - b")])
    assert report["ratio"] == 1.0


def test_no_parsable_clauses_gives_undefined_coverage_not_zero() -> None:
    """None and 0.0 mean different things: 'we cannot measure this ticket'
    versus 'the model covered nothing'."""
    report = coverage_report("free prose with no clause numbering", [_s("AC1")])
    assert report["ratio"] is None
    assert report["total_clauses"] == 0


def test_no_scenarios_is_zero_coverage_not_undefined() -> None:
    report = coverage_report("AC1: a\nAC2: b", [])
    assert report["ratio"] == 0.0
    assert report["uncovered"] == ["AC1", "AC2"]


def test_uncited_scenarios_cover_nothing() -> None:
    report = coverage_report("AC1: a", [{"source_ac_clause": ""}])
    assert report["ratio"] == 0.0
