"""Tests for the shared verification helpers.

The legacy direct runner (and its POST /run route) was removed with
the two-step flow; parse_bdd_scenarios remains as the shared Gherkin parser
used by the agentic verification service.
"""

import uuid

from app.schemas.verification import CodeReference, VerificationVerdict
from app.services.verification_service import (
    _FILE_CONTENT_TRUNCATE,
    build_verification_result,
    deduplicate_scenarios,
    parse_bdd_scenarios,
    truncate_with_notice,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
# Verification source persistence
# ---------------------------------------------------------------------------


def test_build_verification_result_records_the_source_it_verified():
    """The row carries the mode and the exact input the run was scoped to.

    Without this a revisited session shows verdicts with no way to tell what
    they were checked against, and the workspace cannot restore the source
    field. `github_links` cannot stand in: those are links the LLM cited for a
    single scenario, not the source the run was scoped to.
    """
    verdict = VerificationVerdict(
        scenario_id=str(uuid.uuid4()),
        scenario_title="User can log in",
        status="pass",
        justification="Handled in routes.py.",
        code_reference=CodeReference(
            file="src/auth/routes.py", function="login", line=42
        ),
        github_links=[],
        implementation_suggestion=None,
    )

    row = build_verification_result(
        "session-1",
        "user-1",
        verdict,
        None,
        mode="pull_request",
        github_input="https://github.com/org/repo/pull/42",
    )

    assert row.verification_mode == "pull_request"
    assert row.github_input == "https://github.com/org/repo/pull/42"


def test_build_verification_result_source_defaults_to_none():
    """Callers predating the columns still build a valid row."""
    verdict = VerificationVerdict(
        scenario_id=str(uuid.uuid4()),
        scenario_title="User can log in",
        status="pass",
        justification="Handled in routes.py.",
        code_reference=CodeReference(
            file="src/auth/routes.py", function="login", line=42
        ),
        github_links=[],
        implementation_suggestion=None,
    )

    row = build_verification_result("session-1", "user-1", verdict, None)

    assert row.verification_mode is None
    assert row.github_input is None


# ---------------------------------------------------------------------------
# truncate_with_notice — silent truncation is the bug this guards against
# ---------------------------------------------------------------------------


class TestTruncateWithNotice:
    def test_content_under_the_cap_is_returned_unchanged(self) -> None:
        content = "def login():\n    return True\n"
        assert truncate_with_notice(content, "src/auth.py") == content

    def test_content_at_exactly_the_cap_is_not_annotated(self) -> None:
        content = "x" * _FILE_CONTENT_TRUNCATE
        assert truncate_with_notice(content, "src/big.py") == content

    def test_oversized_content_keeps_the_cap_and_announces_the_rest(self) -> None:
        content = "y" * (_FILE_CONTENT_TRUNCATE + 500)
        result = truncate_with_notice(content, "src/big.py")

        assert result.startswith("y" * _FILE_CONTENT_TRUNCATE)
        assert "TRUNCATED" in result
        # The notice must carry the three facts a reader needs to recover:
        # how much was withheld, that it is not the whole file, and where to
        # resume from.
        assert str(len(content)) in result
        assert "NOT the whole file" in result
        assert f"offset={_FILE_CONTENT_TRUNCATE}" in result
        assert "src/big.py" in result


# ---------------------------------------------------------------------------
# deduplicate_scenarios — deciding the same question twice costs twice
# ---------------------------------------------------------------------------


class TestDeduplicateScenarios:
    def test_distinct_scenarios_are_all_kept(self) -> None:
        scenarios = parse_bdd_scenarios(
            "Scenario: User logs in\n  Given a user\n  When they log in\n"
            "  Then they see the dashboard\n"
            "Scenario: User resets a password\n  Given a user\n"
            "  When they request a reset\n  Then they receive an email\n"
        )
        kept, dropped = deduplicate_scenarios(scenarios)
        assert len(kept) == 2
        assert dropped == 0

    def test_an_exact_repeat_is_dropped(self) -> None:
        one = "Scenario: User logs in\n  Given a user\n  When they log in\n  Then ok\n"
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(one + one))
        assert len(kept) == 1
        assert dropped == 1

    def test_a_reworded_repeat_is_dropped(self) -> None:
        # The near-duplicate case that actually happens: same scenario, minor
        # punctuation and casing differences from a second generation pass.
        bdd = (
            "Scenario: User logs in\n  Given a registered user\n"
            "  When they submit valid credentials\n  Then they are authenticated\n"
            "Scenario: User logs in!\n  Given a Registered user,\n"
            "  When they submit valid credentials.\n  Then they are authenticated!\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 1
        assert dropped == 1

    def test_order_is_preserved_and_the_first_copy_wins(self) -> None:
        bdd = (
            "Scenario: Alpha\n  Given a\n  When b\n  Then c\n"
            "Scenario: Beta\n  Given d\n  When e\n  Then f\n"
            "Scenario: Alpha\n  Given a\n  When b\n  Then c\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert [s["title"] for s in kept] == ["Alpha", "Beta"]
        assert dropped == 1

    def test_a_shared_title_with_different_checks_is_kept(self) -> None:
        # Dropping these would silently delete coverage, which is far worse than
        # paying for one extra scenario.
        bdd = (
            "Scenario: Filtering\n  Given interviewers exist\n"
            "  When the candidate filters by domain\n  Then only that domain remains\n"
            "Scenario: Filtering\n  Given interviewers exist\n"
            "  When the candidate sets a minimum rating\n"
            "  Then unrated interviewers are excluded\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 2
        assert dropped == 0

    def test_an_empty_list_is_handled(self) -> None:
        assert deduplicate_scenarios([]) == ([], 0)

    def test_short_scenarios_differing_by_one_word_are_both_kept(self) -> None:
        # Similarity is unreliable over a few words: these score as near
        # identical because nearly every character is shared boilerplate.
        # Dropping one would delete coverage the reader still believes they have.
        bdd = (
            "Scenario: A\n  Given a user\n  When they do A\n  Then A happens\n"
            "Scenario: B\n  Given a user\n  When they do B\n  Then B happens\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 2
        assert dropped == 0

    def test_a_short_scenario_repeated_exactly_is_still_dropped(self) -> None:
        one = "Scenario: A\n  Given a user\n  When they do A\n  Then A happens\n"
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(one + one))
        assert len(kept) == 1
        assert dropped == 1


# ---------------------------------------------------------------------------
# Source AC attribution — each scenario owns its own clause, not its neighbour's
# ---------------------------------------------------------------------------


_AC_BDD = """\
Feature: Discovery

  # Source AC: clause ALPHA
  Scenario: Alpha
    Given a user
    When they browse
    Then they see interviewers

  # Source AC: clause BETA
  Scenario: Beta
    Given a user
    When they filter
    Then results narrow
"""


class TestSourceAcAttribution:
    def test_each_scenario_gets_its_own_clause(self) -> None:
        alpha, beta = parse_bdd_scenarios(_AC_BDD)
        assert alpha["ac_clause"] == "clause ALPHA"
        assert beta["ac_clause"] == "clause BETA"

    def test_a_scenario_does_not_carry_its_neighbours_clause(self) -> None:
        # The bug this replaces: the block ran to the next scenario's header, so
        # it swallowed the comment introducing that scenario. Every prompt then
        # showed the model an acceptance criterion it was not judging.
        alpha, beta = parse_bdd_scenarios(_AC_BDD)
        assert "BETA" not in alpha["text"]
        assert "ALPHA" not in beta["text"]

    def test_the_first_scenarios_clause_is_not_lost(self) -> None:
        # It sits above the first header, so an offset-based slice never saw it.
        assert parse_bdd_scenarios(_AC_BDD)[0]["ac_clause"] == "clause ALPHA"

    def test_steps_survive_the_trimming(self) -> None:
        alpha = parse_bdd_scenarios(_AC_BDD)[0]
        assert "Given a user" in alpha["text"]
        assert "Then they see interviewers" in alpha["text"]

    def test_a_file_without_traceability_comments_has_empty_clauses(self) -> None:
        scenarios = parse_bdd_scenarios("Scenario: Plain\n  Given a\n  Then b\n")
        assert scenarios[0]["ac_clause"] == ""

    def test_an_unrelated_comment_is_not_mistaken_for_a_clause(self) -> None:
        bdd = "  # written by hand\n  Scenario: Plain\n    Given a\n    Then b\n"
        assert parse_bdd_scenarios(bdd)[0]["ac_clause"] == ""


# ---------------------------------------------------------------------------
# Deduplication by shared intent (title + acceptance clause)
# ---------------------------------------------------------------------------


_SHARED_AC = (
    "  # Source AC: Availability is matched as a case-insensitive substring "
    "against the declared availability slots."
)


class TestDeduplicateBySharedIntent:
    def test_a_reworded_duplicate_under_one_ac_is_dropped(self) -> None:
        # Same title, same clause, steps rewritten — the shape that survived
        # pure text matching and had the same scenario verified twice.
        bdd = (
            f"Feature: D\n{_SHARED_AC}\n"
            "  Scenario: Availability filter functionality\n"
            "    Given interviewers with availability slots\n"
            "    When the candidate enters weekends\n"
            "    Then only interviewers available at weekends remain\n"
            f"{_SHARED_AC}\n"
            "  Scenario: Availability filter functionality\n"
            "    Given interviewers declaring availability\n"
            "    When the candidate types weekends into the availability box\n"
            "    Then the list shows only those free at weekends\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 1
        assert dropped == 1

    def test_different_checks_under_one_ac_are_both_kept(self) -> None:
        # Same title and clause, but one tests the match and the other tests
        # case-insensitivity. Dropping either would delete real coverage.
        bdd = (
            f"Feature: D\n{_SHARED_AC}\n"
            "  Scenario: Availability filter functionality\n"
            "    Given interviewers with availability slots\n"
            "    When the candidate enters weekends\n"
            "    Then only interviewers available at weekends remain\n"
            f"{_SHARED_AC}\n"
            "  Scenario: Availability filter functionality\n"
            "    Given an interviewer whose slot is stored as Weekday Evenings\n"
            "    When the candidate types EVENINGS in upper case\n"
            "    Then that interviewer is still matched because search ignores case\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 2
        assert dropped == 0

    def test_different_titles_under_one_ac_are_kept(self) -> None:
        # One acceptance criterion routinely needs several scenarios; differing
        # titles are how they are told apart.
        bdd = (
            f"Feature: D\n{_SHARED_AC}\n"
            "  Scenario: Filter by domain\n"
            "    Given interviewers in several domains\n"
            "    When the candidate selects Backend\n"
            "    Then only Backend interviewers remain\n"
            f"{_SHARED_AC}\n"
            "  Scenario: Filter by minimum rating\n"
            "    Given interviewers with and without ratings\n"
            "    When the candidate selects a four star minimum\n"
            "    Then unrated interviewers are excluded from the results\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 2
        assert dropped == 0

    def test_a_shared_title_without_a_clause_never_merges(self) -> None:
        # No traceability comment means no identity, so the weak signal of a
        # shared title alone can never drop anything.
        bdd = (
            "Scenario: Filtering\n  Given interviewers exist\n"
            "  When the candidate filters by domain\n  Then that domain remains\n"
            "Scenario: Filtering\n  Given interviewers exist\n"
            "  When the candidate sets a rating\n  Then unrated are excluded\n"
        )
        kept, dropped = deduplicate_scenarios(parse_bdd_scenarios(bdd))
        assert len(kept) == 2
        assert dropped == 0
