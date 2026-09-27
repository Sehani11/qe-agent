"""Tests for the shared training-data parser (Story 6.7).

The Gherkin parser and quality rules used to live in `training/build_dataset.py`.
They now live in the backend so the upload endpoint's accept/reject decision is
the SAME code as the builder's keep/drop decision. If these two ever diverge,
the app accepts feature files the dataset builder silently discards — which is
exactly how the captured corpus reached zero usable rows (Story 6.6 finding).

These tests pin the parser's behaviour so the extraction cannot quietly change
what is considered usable.
"""

import json

import pytest

from app.services.training_data_service import (
    MAX_FILE_CHARS,
    MAX_OUTLINE_EXPANSIONS,
    MAX_SCENARIOS,
    MAX_STEP_CHARS,
    MIN_STEP_CHARS,
    REASON_INCOMPLETE_STEPS,
    REASON_MESSAGES,
    REASON_NO_SCENARIOS,
    REASON_PLACEHOLDER_TEXT,
    REASON_TOO_LARGE,
    REASON_TOO_MANY_SCENARIOS,
    REASON_UNPARSABLE,
    TrainingDataError,
    feature_doc_from_json,
    pair_fingerprint,
    parse_csv_records,
    parse_csv_tickets,
    parse_feature,
    quality_reason,
    reject_unsafe_filename,
    ticket_warnings,
    validate_jsonl,
    validate_upload,
)

GOOD_FEATURE = """
Feature: Password reset

  Scenario: A user requests a reset link
    Given a registered user with a verified email address
    When they submit the password reset form
    Then a reset link is emailed to their address
"""


def _valid_pair() -> dict:
    """One line of the JSONL shape build_dataset.py emits."""
    target = {
        "scenarios": [
            {
                "source_ac_clause": "AC1",
                "feature": "Password reset",
                "scenario": "A user requests a reset link",
                "given": "a registered user with a verified email address",
                "when": "they submit the password reset form",
                "then": "a reset link is emailed to their address",
            }
        ]
    }
    return {
        "messages": [
            {"role": "system", "content": "You are a QA engineer."},
            {"role": "user", "content": "AC1: users can reset their password"},
            {"role": "assistant", "content": json.dumps(target)},
        ]
    }


# --- parse_feature ----------------------------------------------------------


def test_parses_a_well_formed_feature_file():
    doc, reason = parse_feature(GOOD_FEATURE, origin="corpus/reset.feature")

    assert reason is None
    assert doc is not None
    assert doc.feature == "Password reset"
    assert len(doc.scenarios) == 1
    assert doc.scenarios[0].title == "A user requests a reset link"
    assert doc.scenarios[0].given.startswith("a registered user")
    assert doc.scenarios[0].when.startswith("they submit")
    assert doc.scenarios[0].then.startswith("a reset link")
    assert quality_reason(doc) is None


def test_and_steps_keep_their_connective():
    """Dropping And/But produces run-on nonsense; the keyword must survive."""
    text = """
Feature: Checkout

  Scenario: Paying with a saved card
    Given a customer with a saved payment card
    And a basket containing two items
    When they confirm the order
    Then the order is placed successfully
"""
    doc, _ = parse_feature(text, origin="checkout.feature")

    assert doc is not None
    assert "And a basket containing two items" in doc.scenarios[0].given


def test_scenario_outlines_are_skipped_and_counted():
    """<placeholders> make poor training targets, so outlines are dropped."""
    text = """
Feature: Login attempts

  Scenario Outline: Signing in with <role>
    Given a user with role <role>
    When they sign in
    Then they see the <landing> page

  Scenario: Signing in as an administrator
    Given a registered administrator account
    When they sign in with valid credentials
    Then they are taken to the admin dashboard
"""
    doc, reason = parse_feature(text, origin="login.feature")

    assert reason is None
    assert doc is not None
    assert doc.outlines_skipped == 1
    assert len(doc.scenarios) == 1
    assert doc.scenarios[0].title == "Signing in as an administrator"


def test_background_steps_are_inherited_by_every_scenario():
    """Gherkin's Background declares shared steps once, for all scenarios.

    Without this, a scenario whose Given lives in the Background looks
    incomplete and the whole document is discarded — which silently rejected
    38% of real-world feature files.
    """
    text = """
Feature: Append snippets option

  Background:
    Given I initialise the working directory from the fixtures folder

  Scenario: Appending snippets to the main context
    When I run behat with the append-snippets option
    Then the context file should contain the generated snippets

  Scenario: Appending snippets to a secondary context
    When I run behat naming a secondary context
    Then the secondary context file should contain the snippets
"""
    doc, reason = parse_feature(text, origin="append.feature")

    assert reason is None
    assert doc is not None
    assert len(doc.scenarios) == 2
    for s in doc.scenarios:
        assert s.given == "I initialise the working directory from the fixtures folder"
    assert quality_reason(doc) is None  # usable, not "incomplete steps"


def test_a_scenarios_own_given_extends_the_background():
    text = """
Feature: Layered setup

  Background:
    Given a registered user with a verified email address

  Scenario: The scenario adds its own precondition
    Given an active subscription on the account
    When they open the billing page
    Then the next invoice date is displayed
"""
    doc, _ = parse_feature(text, origin="layered.feature")

    assert doc is not None
    given = doc.scenarios[0].given
    assert given.startswith("a registered user with a verified email address")
    assert "an active subscription on the account" in given


def test_background_is_not_emitted_as_a_scenario_of_its_own():
    text = """
Feature: Only one real scenario

  Background:
    Given a registered user with a verified email address

  Scenario: The only scenario in this file
    When they submit the password reset form
    Then a reset link is emailed to their address
"""
    doc, _ = parse_feature(text, origin="single.feature")

    assert doc is not None
    assert len(doc.scenarios) == 1
    assert doc.scenarios[0].title == "The only scenario in this file"


def test_a_background_with_no_scenarios_yields_nothing():
    text = """
Feature: Setup only

  Background:
    Given a registered user with a verified email address
"""
    doc, reason = parse_feature(text, origin="setuponly.feature")

    assert reason is None
    assert doc is not None
    assert quality_reason(doc) == REASON_NO_SCENARIOS


def test_oversize_file_is_rejected_before_parsing():
    doc, reason = parse_feature("x" * (MAX_FILE_CHARS + 1), origin="huge.feature")

    assert doc is None
    assert reason == REASON_TOO_LARGE


def test_text_with_no_feature_and_no_scenarios_is_unparsable():
    doc, reason = parse_feature("just some prose\nand more prose", origin="a.txt")

    assert doc is None
    assert reason == REASON_UNPARSABLE


def test_feature_name_falls_back_to_the_origin_filename():
    text = """
  Scenario: Deleting an archived report
    Given an archived report owned by the current user
    When they choose to delete it
    Then the report no longer appears in their list
"""
    doc, reason = parse_feature(text, origin="/corpus/report_deletion.feature")

    assert reason is None
    assert doc is not None
    assert doc.feature == "Report Deletion"


# --- feature_doc_from_json --------------------------------------------------


def test_a_generated_row_is_parsed_from_its_json_content():
    """`generated` rows hold a serialized BDDGenerateResponse, not Gherkin.

    parse_feature only understands Gherkin, so before this they were all
    counted as unparsable — 8 of the 10 rows in the live database.
    """
    content = json.dumps(
        {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "Password reset",
                    "scenario": "A user requests a reset link",
                    "given": "a registered user with a verified email address",
                    "when": "they submit the password reset form",
                    "then": "a reset link is emailed to their address",
                },
                {
                    "source_ac_clause": "AC2",
                    "feature": "Password reset",
                    "scenario": "The reset link has expired",
                    "given": "a reset link issued more than an hour ago",
                    "when": "the user opens that expired link",
                    "then": "they are told the link is no longer valid",
                },
            ]
        }
    )

    doc, reason = feature_doc_from_json(content, origin="db:generated:1")

    assert reason is None
    assert doc is not None
    assert doc.feature == "Password reset"
    assert [s.title for s in doc.scenarios] == [
        "A user requests a reset link",
        "The reset link has expired",
    ]
    assert doc.scenarios[0].given.startswith("a registered user")
    assert quality_reason(doc) is None


def test_malformed_json_content_is_unparsable_not_silently_dropped():
    doc, reason = feature_doc_from_json("{not json", origin="db:generated:2")

    assert doc is None
    assert reason == REASON_UNPARSABLE


def test_json_content_failing_schema_validation_is_unparsable():
    content = json.dumps({"scenarios": [{"scenario": "missing most fields"}]})

    doc, reason = feature_doc_from_json(content, origin="db:generated:3")

    assert doc is None
    assert reason == REASON_UNPARSABLE


def test_json_content_with_no_scenarios_is_rejected():
    doc, reason = feature_doc_from_json('{"scenarios": []}', origin="db:generated:4")

    assert doc is None
    assert reason == REASON_NO_SCENARIOS


def test_json_feature_name_falls_back_to_the_origin_when_absent():
    content = json.dumps(
        {
            "scenarios": [
                {
                    "source_ac_clause": "AC1",
                    "feature": "",
                    "scenario": "A scenario with no feature grouping given",
                    "given": "a registered user with a verified email address",
                    "when": "they submit the password reset form",
                    "then": "a reset link is emailed to their address",
                }
            ]
        }
    )

    doc, reason = feature_doc_from_json(content, origin="db:generated:5")

    assert reason is None
    assert doc is not None
    assert doc.feature  # something usable, never empty


# --- quality_reason ---------------------------------------------------------


def test_scenario_missing_a_then_is_not_usable():
    """The exact failure mode that made the live corpus unusable."""
    text = """
Feature: Partial coverage

  Scenario: Something happens but nothing is asserted
    Given a registered user with a verified email address
    When they submit the password reset form
"""
    doc, reason = parse_feature(text, origin="partial.feature")

    assert reason is None  # it parses...
    assert doc is not None
    assert quality_reason(doc) == REASON_INCOMPLETE_STEPS  # ...but is unusable


def test_document_with_no_scenarios_is_rejected():
    doc, reason = parse_feature("Feature: Empty\n", origin="empty.feature")

    assert reason is None
    assert doc is not None
    assert quality_reason(doc) == REASON_NO_SCENARIOS


def test_too_many_scenarios_is_rejected():
    scenario = """
  Scenario: Scenario number {n}
    Given a registered user with a verified email address
    When they submit the password reset form number {n}
    Then a reset link is emailed to their address
"""
    text = "Feature: Mega file\n" + "".join(
        scenario.format(n=i) for i in range(25)
    )
    doc, _ = parse_feature(text, origin="mega.feature")

    assert doc is not None
    assert quality_reason(doc) == REASON_TOO_MANY_SCENARIOS


def test_placeholder_text_is_rejected():
    text = """
Feature: Work in progress

  Scenario: TODO write this properly later on
    Given a registered user with a verified email address
    When they submit the password reset form
    Then a reset link is emailed to their address
"""
    doc, _ = parse_feature(text, origin="todo.feature")

    assert doc is not None
    assert quality_reason(doc) == REASON_PLACEHOLDER_TEXT


def test_very_short_steps_are_rejected():
    text = """
Feature: Terse

  Scenario: A scenario with a step that is far too short
    Given x
    When they submit the password reset form
    Then a reset link is emailed to their address
"""
    doc, _ = parse_feature(text, origin="terse.feature")

    assert doc is not None
    assert quality_reason(doc) == REASON_INCOMPLETE_STEPS


def test_every_reason_has_a_human_message():
    """The API surfaces these verbatim, so none may be missing."""
    for reason in (
        REASON_TOO_LARGE,
        REASON_UNPARSABLE,
        REASON_NO_SCENARIOS,
        REASON_TOO_MANY_SCENARIOS,
        REASON_INCOMPLETE_STEPS,
        REASON_PLACEHOLDER_TEXT,
    ):
        assert REASON_MESSAGES[reason]
        assert not REASON_MESSAGES[reason].endswith("_")  # a message, not a slug


def test_reason_constants_match_builder_stats_field_names():
    """build_dataset.py maps reasons onto Stats fields by name — keep them aligned."""
    from tests.test_build_dataset import bd

    fields = vars(bd.Stats())
    for reason in REASON_MESSAGES:
        assert reason in fields, f"Stats has no counter named {reason!r}"


# --- validate_jsonl ---------------------------------------------------------


def test_valid_jsonl_returns_the_pair_count():
    text = "\n".join(json.dumps(_valid_pair()) for _ in range(3))

    assert validate_jsonl(text) == 3


def test_blank_lines_are_skipped_not_counted():
    text = f"\n{json.dumps(_valid_pair())}\n\n{json.dumps(_valid_pair())}\n\n"

    assert validate_jsonl(text) == 2


def test_malformed_json_reports_the_line_number():
    text = "\n".join(
        [json.dumps(_valid_pair()), json.dumps(_valid_pair()), "{not json"]
    )

    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl(text)

    assert exc.value.line_number == 3
    assert "line 3" in exc.value.message.lower()


def test_missing_messages_array_reports_the_line_number():
    text = "\n".join([json.dumps(_valid_pair()), json.dumps({"prompt": "hi"})])

    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl(text)

    assert exc.value.line_number == 2
    assert "messages" in exc.value.message


def test_missing_assistant_message_is_rejected():
    pair = _valid_pair()
    pair["messages"] = [m for m in pair["messages"] if m["role"] != "assistant"]

    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl(json.dumps(pair))

    assert exc.value.line_number == 1
    assert "assistant" in exc.value.message


def test_assistant_content_must_validate_against_the_response_schema():
    """A pair the application cannot parse trains a model it cannot serve."""
    pair = _valid_pair()
    pair["messages"][-1]["content"] = "Feature: plain gherkin, not JSON"

    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl(json.dumps(pair))

    assert exc.value.line_number == 1
    assert "BDDGenerateResponse" in exc.value.message


def test_empty_scenarios_array_is_rejected():
    """It validates against the model (defaults to []) and teaches nothing."""
    pair = _valid_pair()
    pair["messages"][-1]["content"] = json.dumps({"scenarios": []})

    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl(json.dumps(pair))

    assert exc.value.line_number == 1
    assert "scenario" in exc.value.message.lower()


def test_a_file_with_no_pairs_at_all_is_rejected():
    with pytest.raises(TrainingDataError) as exc:
        validate_jsonl("\n\n   \n")

    assert "no training pairs" in exc.value.message.lower()


# --- Filename safety --------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        "../../../victim/owned.feature",
        "../owned.feature",
        "sub/dir/owned.feature",
        "..\\..\\victim\\owned.feature",
        "/absolute/owned.feature",
        "..",
        ".",
        "",
    ],
)
def test_filenames_with_a_path_component_are_rejected(evil):
    """The filename becomes a storage object key — it must be a plain name."""
    with pytest.raises(TrainingDataError):
        reject_unsafe_filename(evil)


@pytest.mark.parametrize("ok", ["reset.feature", "pairs.jsonl", "my-corpus.v2.jsonl"])
def test_plain_filenames_are_accepted(ok):
    reject_unsafe_filename(ok)  # must not raise


def test_validate_upload_refuses_a_traversing_name_before_anything_else():
    feature = (
        b"Feature: x\n\n"
        b"  Scenario: A scenario that passes every quality rule\n"
        b"    Given a registered user with a verified email address\n"
        b"    When they submit the password reset form\n"
        b"    Then a reset link is emailed to their address\n"
    )

    with pytest.raises(TrainingDataError) as exc:
        validate_upload("../../victim/owned.feature", feature)

    assert "path separators" in exc.value.message


# --- Pair fingerprinting ----------------------------------------------------


def test_identical_pairs_share_a_fingerprint():
    """Re-uploading the same dataset must not double the corpus."""
    a = {"messages": _valid_pair()["messages"], "meta": {"origin": "upload:1:a.jsonl"}}
    b = {"messages": _valid_pair()["messages"], "meta": {"origin": "upload:2:b.jsonl"}}

    # Same content, different origin — still the same pair.
    assert pair_fingerprint(a) == pair_fingerprint(b)


def test_different_pairs_have_different_fingerprints():
    a = {"messages": _valid_pair()["messages"]}
    changed = _valid_pair()["messages"]
    changed[1] = {"role": "user", "content": "AC9: something else entirely"}

    assert pair_fingerprint(a) != pair_fingerprint({"messages": changed})


# ---------------------------------------------------------------------------
# Scenario Outline expansion
# ---------------------------------------------------------------------------
#
# RUN_LOG.md named these "the largest untapped source in the current corpus":
# 150 of 708 corpus files were discarded whole because their scenarios used
# <placeholders>. They are real human-authored Gherkin — the placeholders just
# need substituting from the Examples table that always accompanies them.


def test_an_outline_is_expanded_against_its_examples_table():
    text = """Feature: Cucumber counting
  Scenario Outline: eating cucumbers
    Given there are <start> cucumbers in the basket
    When I eat <eat> cucumbers from the basket
    Then I should have <left> cucumbers remaining

    Examples:
      | start | eat | left |
      |    12 |   5 |    7 |
      |    20 |   5 |   15 |
"""
    doc, reason = parse_feature(text, "outline.feature")

    assert reason is None
    assert len(doc.scenarios) == 2
    assert "12 cucumbers" in doc.scenarios[0].given
    assert "7 cucumbers" in doc.scenarios[0].then
    assert "20 cucumbers" in doc.scenarios[1].given


def test_no_placeholder_survives_expansion():
    """A leftover <token> would be caught by quality_reason as placeholder
    text — but it should never get that far."""
    text = """Feature: F
  Scenario Outline: substitution
    Given a user named <name> exists in the system
    When they request the <resource> resource today
    Then the response status code should be <status>

    Examples:
      | name  | resource | status |
      | alice | reports  | 200    |
"""
    doc, _ = parse_feature(text, "f.feature")

    rendered = f"{doc.scenarios[0].given}{doc.scenarios[0].when}{doc.scenarios[0].then}"
    assert "<" not in rendered
    assert "alice" in rendered and "reports" in rendered and "200" in rendered


def test_each_expanded_row_gets_a_distinct_title():
    text = """Feature: F
  Scenario Outline: login attempt
    Given a user with role <role> is registered
    When they open the administration dashboard
    Then access should be <outcome> for that user

    Examples:
      | role   | outcome |
      | admin  | granted |
      | viewer | denied  |
"""
    doc, _ = parse_feature(text, "f.feature")

    titles = [s.title for s in doc.scenarios]
    assert len(set(titles)) == 2, f"expanded rows share a title: {titles}"


def test_expansion_is_capped_so_one_outline_cannot_flood_the_dataset():
    """A 40-row Examples table would otherwise emit 40 near-identical
    scenarios — teaching exactly the repetition the fine-tune already
    over-produces."""
    rows = "\n".join(f"      | value{n} | result{n} |" for n in range(40))
    text = f"""Feature: F
  Scenario Outline: repeated behaviour
    Given the system holds <input> as its input
    When the operation is executed by the user
    Then the output should equal <output> exactly

    Examples:
      | input | output |
{rows}
"""
    doc, _ = parse_feature(text, "f.feature")

    assert len(doc.scenarios) <= MAX_OUTLINE_EXPANSIONS


def test_an_outline_with_no_examples_table_is_still_skipped():
    """Without a table there is nothing to substitute, so the placeholders
    would reach the model verbatim."""
    text = """Feature: F
  Scenario Outline: no table
    Given a value <x> is provided to the system
    When it is processed by the pipeline
    Then <y> should be returned to the caller
"""
    doc, _ = parse_feature(text, "f.feature")

    assert doc is None or not doc.scenarios


def test_a_plain_scenario_alongside_an_outline_still_parses():
    text = """Feature: F
  Scenario: an ordinary scenario
    Given a registered user exists in the system
    When they submit the login form correctly
    Then the dashboard should be displayed

  Scenario Outline: a templated one
    Given the counter starts at <start> for this run
    When it is incremented by the background job
    Then it should read <end> afterwards

    Examples:
      | start | end |
      |     1 |   2 |
"""
    doc, _ = parse_feature(text, "f.feature")

    titles = [s.title for s in doc.scenarios]
    assert "an ordinary scenario" in titles
    assert len(doc.scenarios) == 2


# ---------------------------------------------------------------------------
# CSV authoring format
# ---------------------------------------------------------------------------
#
# CSV exists so a ticket can be authored in a spreadsheet instead of by hand in
# JSONL. It is converted to pairs at upload and never stored as CSV, so these
# tests pin two things: that a spreadsheet's habits (fill-down, blank rows, a
# BOM) are read the way an author expects, and that every rejection names the
# row number the author can actually see.

CSV_HEADER = (
    "ticket_id,feature,title,as_a,i_want,so_that,description,"
    "ac_number,ac_text,scenario,given,when,then"
)


def _csv_row(
    ticket_id="T-1",
    feature="Sign in",
    title="Sign in with email and password",
    as_a="registered user",
    i_want="to sign in with my email and password",
    so_that="I can reach my dashboard",
    description="The sign-in form takes an email and a password.",
    ac_number="1",
    ac_text="A correct email and password signs the user in.",
    scenario="Correct credentials sign the user in",
    given="a registered user is on the sign-in page",
    when="they submit their correct email and password",
    then="they are signed in and land on their dashboard",
):
    """One CSV row, quoted as csv.writer would. Defaults are a valid scenario."""
    cells = [
        ticket_id,
        feature,
        title,
        as_a,
        i_want,
        so_that,
        description,
        ac_number,
        ac_text,
        scenario,
        given,
        when,
        then,
    ]
    return ",".join(f'"{c}"' for c in cells)


def _csv(*rows):
    return CSV_HEADER + "\n" + "\n".join(rows) + "\n"


GOOD_CSV = _csv(
    _csv_row(),
    _csv_row(
        ticket_id="T-1",
        feature="",
        title="",
        as_a="",
        i_want="",
        so_that="",
        description="",
        ac_number="2",
        ac_text="A wrong password is refused without saying whether the email exists.",
        scenario="A wrong password is rejected",
        given="a registered user is on the sign-in page",
        when="they submit their email with the wrong password",
        then="they stay on the page and are told the credentials did not match",
    ),
)


def test_a_valid_csv_becomes_one_pair_per_ticket():
    records = parse_csv_records(GOOD_CSV)
    assert len(records) == 1

    record = records[0]
    roles = [m["role"] for m in record["messages"]]
    assert roles == ["system", "user", "assistant"]

    # The assistant side must be a JSON STRING the served provider can parse.
    target = json.loads(record["messages"][2]["content"])
    assert len(target["scenarios"]) == 2


def test_ticket_columns_are_inherited_by_later_rows():
    """Blank ticket cells on a continuation row reuse the group's first row.

    This is the rule an author is least likely to guess, and the one a
    spreadsheet's fill-down makes natural.
    """
    ticket = parse_csv_tickets(GOOD_CSV)[0]
    assert ticket["title"] == "Sign in with email and password"
    assert [s["clause"] for s in ticket["scenarios"]] == [1, 2]
    assert len(ticket["acceptance_criteria"]) == 2


def test_the_user_side_numbers_every_criterion():
    """The rendered ticket must carry "AC<n>:" labels.

    `ac_clauses()` in evaluation_metrics.py matches clauses by that label, so a
    ticket rendered without them yields zero clauses and silently disables the
    coverage metric.
    """
    prompt = parse_csv_records(GOOD_CSV)[0]["messages"][1]["content"]
    assert "Acceptance Criteria:" in prompt
    assert "AC1: A correct email and password signs the user in." in prompt
    assert "AC2: A wrong password is refused" in prompt


def test_traceability_carries_the_label_and_the_sentence():
    target = json.loads(parse_csv_records(GOOD_CSV)[0]["messages"][2]["content"])
    clause = target["scenarios"][0]["source_ac_clause"]
    assert clause.startswith("AC1: ")
    assert "signs the user in" in clause


def test_origin_is_the_ticket_id_so_the_split_groups_by_ticket():
    """One ticket is one origin.

    The train/holdout split is by origin, and `process_upload_rows` reads this
    value back off the stored line. Scenarios of one ticket are related, so
    splitting them across the boundary would leak.
    """
    assert parse_csv_records(GOOD_CSV)[0]["meta"]["origin"] == "T-1"


def test_two_tickets_become_two_pairs():
    text = _csv(_csv_row(), _csv_row(ticket_id="T-2", feature="Sign out"))
    records = parse_csv_records(text)
    assert [r["meta"]["origin"] for r in records] == ["T-1", "T-2"]


def test_a_conflicting_ticket_cell_is_refused_naming_the_row():
    """A filled cell that disagrees with the group means two rows describe one
    ticket two ways; keeping the first would train on whichever sorted first."""
    text = _csv(_csv_row(), _csv_row(title="A different title", ac_number="2"))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert exc.value.line_number == 3
    assert "'title'" in exc.value.message


def test_a_row_with_no_scenario_declares_an_uncovered_criterion():
    """Legal, and warned about rather than refused — an uncovered clause makes a
    worse example, not an invalid one."""
    text = _csv(
        _csv_row(),
        _csv_row(
            feature="",
            title="",
            as_a="",
            i_want="",
            so_that="",
            description="",
            ac_number="2",
            ac_text="Sessions expire after thirty days.",
            scenario="",
            given="",
            when="",
            then="",
        ),
    )
    ticket = parse_csv_tickets(text)[0]
    assert len(ticket["acceptance_criteria"]) == 2
    assert len(ticket["scenarios"]) == 1
    assert any("AC2" in w for w in ticket_warnings(ticket))
    # It still reaches the user side of the pair: a real ticket lists the
    # criterion whether or not anyone wrote a scenario for it.
    assert "AC2: Sessions expire after thirty days." in (
        parse_csv_records(text)[0]["messages"][1]["content"]
    )


def test_a_half_filled_scenario_is_refused():
    text = _csv(_csv_row(then=""))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert exc.value.line_number == 2
    assert "then" in exc.value.message


def test_gaps_in_the_criterion_numbering_are_refused():
    """A gap would print AC1/AC2 for criteria the scenarios cite as AC1/AC3,
    misattributing every clause after the gap."""
    text = _csv(_csv_row(), _csv_row(ac_number="3", title="", feature=""))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert "no gaps" in exc.value.message


def test_one_number_cannot_carry_two_criteria():
    text = _csv(_csv_row(), _csv_row(ac_text="Something else entirely.", title=""))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert exc.value.line_number == 3


def test_a_non_numeric_ac_number_is_refused():
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(_csv(_csv_row(ac_number="one")))
    assert exc.value.line_number == 2


def test_a_missing_ticket_level_cell_on_the_first_row_is_refused():
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(_csv(_csv_row(as_a="")))
    assert "'as_a'" in exc.value.message
    assert exc.value.line_number == 2


def test_row_numbers_count_the_header_so_they_match_the_spreadsheet():
    """The first data row is row 2, which is what the author sees in the gutter.
    An index into the data rows would send them to the wrong line."""
    text = _csv(_csv_row(), _csv_row(ac_number="2", ac_text="", title=""))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert exc.value.line_number == 3
    assert "Row 3" in exc.value.message


def test_blank_rows_between_groups_are_skipped():
    """Spreadsheets leave trailing blank lines on export, and a blank line
    between tickets is a reasonable thing for an author to add."""
    text = CSV_HEADER + "\n" + _csv_row() + "\n,,,,,,,,,,,,\n\n"
    assert len(parse_csv_records(text)) == 1


def test_a_missing_column_names_what_is_missing():
    text = "ticket_id,feature\nT-1,Sign in\n"
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert "ac_number" in exc.value.message


def test_a_semicolon_export_says_so():
    """The message an Excel user in a semicolon locale needs, rather than
    "column 'ticket_id' is missing", which is true and useless."""
    text = CSV_HEADER.replace(",", ";") + "\n" + _csv_row().replace(",", ";") + "\n"
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(text)
    assert "semicolon" in exc.value.message


def test_newlines_inside_a_cell_are_collapsed():
    """Every one of these fields renders onto a single line. A step carrying a
    newline (Alt+Enter in a spreadsheet) would produce broken Gherkin."""
    text = _csv(_csv_row(then="they are signed in\nand land on their dashboard"))
    ticket = parse_csv_tickets(text)[0]
    assert ticket["scenarios"][0]["then"] == (
        "they are signed in and land on their dashboard"
    )


def test_steps_outside_the_length_bounds_are_refused():
    short = _csv(_csv_row(when="tap"))
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(short)
    assert str(MIN_STEP_CHARS) in exc.value.message

    long = _csv(_csv_row(then="x" * (MAX_STEP_CHARS + 1)))
    with pytest.raises(TrainingDataError):
        parse_csv_tickets(long)


def test_too_many_scenarios_in_one_ticket_is_refused():
    rows = [_csv_row()]
    rows += [
        _csv_row(
            title="",
            feature="",
            as_a="",
            i_want="",
            so_that="",
            description="",
            ac_number=str(n),
            ac_text=f"Criterion number {n} of this ticket.",
            scenario=f"Scenario number {n}",
        )
        for n in range(2, MAX_SCENARIOS + 3)
    ]
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(_csv(*rows))
    assert str(MAX_SCENARIOS) in exc.value.message


def test_placeholder_text_is_refused():
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(_csv(_csv_row(then="TODO decide what happens")))
    assert exc.value.message == REASON_MESSAGES[REASON_PLACEHOLDER_TEXT]


def test_an_empty_csv_is_refused():
    with pytest.raises(TrainingDataError):
        parse_csv_tickets("")
    with pytest.raises(TrainingDataError) as exc:
        parse_csv_tickets(CSV_HEADER + "\n")
    assert "no data rows" in exc.value.message


# --- The upload path --------------------------------------------------------


def test_a_csv_upload_is_stored_as_jsonl():
    """CSV is an authoring format: it is converted here so that nothing
    downstream — the builder, --pairs-dir, the trainer — knows CSV exists."""
    result = validate_upload("tickets.csv", GOOD_CSV.encode())
    assert result.kind == "jsonl"
    assert result.item_count == 1
    assert result.stored_filename == "tickets.jsonl"

    # What lands in Storage must itself pass the JSONL validator, which is the
    # check that keeps the conversion honest.
    assert validate_jsonl(result.data.decode()) == 1


def test_a_utf8_bom_is_tolerated():
    """Excel's "CSV UTF-8" writes a BOM. Without utf-8-sig it lands inside the
    first header name and makes `ticket_id` unfindable."""
    result = validate_upload("tickets.csv", b"\xef\xbb\xbf" + GOOD_CSV.encode())
    assert result.item_count == 1


def test_a_non_utf8_csv_says_how_to_re_save_it():
    with pytest.raises(TrainingDataError) as exc:
        validate_upload("tickets.csv", GOOD_CSV.encode("utf-16"))
    assert "CSV UTF-8" in exc.value.message


def test_feature_and_jsonl_uploads_are_stored_untouched():
    """The conversion must not have changed the other two formats: their bytes
    are the bytes that arrived, under the name they arrived under."""
    data = GOOD_FEATURE.encode()
    result = validate_upload("corpus.feature", data)
    assert (result.kind, result.data, result.stored_filename) == (
        "feature",
        data,
        "corpus.feature",
    )


def test_an_unsupported_extension_names_all_three():
    with pytest.raises(TrainingDataError) as exc:
        validate_upload("tickets.txt", b"anything")
    assert ".csv" in exc.value.message
