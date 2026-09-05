"""Tests for Jira ticket extraction helpers."""

from app.services.jira_service import (
    _extract_acceptance_criteria,
    _extract_section_by_heading,
    _extract_text_from_jira_adf,
    _extract_ticket_id,
)


def _adf_text(text: str) -> dict:
    return {"type": "text", "text": text}


def _adf_block(node_type: str, text: str) -> dict:
    return {"type": node_type, "content": [_adf_text(text)]}


def test_extract_ticket_id_from_browse_url() -> None:
    ticket_id = _extract_ticket_id("https://example.atlassian.net/browse/QE-123?focusedCommentId=1")
    assert ticket_id == "QE-123"


def test_extract_acceptance_criteria_prefers_named_custom_field() -> None:
    fields = {
        "summary": "User login",
        "description": {"type": "doc", "content": []},
        "customfield_10001": "Given valid credentials\nWhen the user signs in\nThen the dashboard is shown",
    }
    names = {"customfield_10001": "Acceptance Criteria"}

    ac_text = _extract_acceptance_criteria(fields, names, "", "User login")

    assert "Given valid credentials" in ac_text
    assert "Then the dashboard is shown" in ac_text


def test_extract_acceptance_criteria_from_description_heading() -> None:
    description = "Summary\n\nAcceptance Criteria:\nUser can reset password\nSystem sends email\n\nNotes:\nKeep audit trail"

    ac_text = _extract_acceptance_criteria({}, {}, description, "Password reset")

    assert ac_text == "User can reset password\nSystem sends email"


def test_extract_acceptance_criteria_falls_back_to_description() -> None:
    description = "Allow the user to update profile details and save them successfully."

    ac_text = _extract_acceptance_criteria({}, {}, description, "Profile update")

    assert ac_text == description


def test_extract_section_by_heading_inline_heading() -> None:
    text = "Acceptance Criteria: user can export the report as PDF"

    section = _extract_section_by_heading(text, ["acceptance criteria"])

    assert section == "user can export the report as PDF"


# ---------------------------------------------------------------------------
# ADF extraction - the only format the real Jira API returns
# ---------------------------------------------------------------------------


def test_adf_blocks_are_separated_by_newlines() -> None:
    """Joining every text node with a space collapsed a whole ticket onto one
    line, which left the heading extractor - and the model - with no structure
    to read."""
    doc = {
        "type": "doc",
        "content": [
            _adf_block("heading", "Acceptance Criteria"),
            _adf_block("paragraph", "The user can cancel."),
            _adf_block("paragraph", "The refund is issued."),
        ],
    }

    assert _extract_text_from_jira_adf(doc) == (
        "Acceptance Criteria\nThe user can cancel.\nThe refund is issued."
    )


def test_adf_inline_nodes_within_a_block_stay_on_one_line() -> None:
    """Emphasis and links split a sentence into several text nodes; they must
    not each become their own line."""
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    _adf_text("A booking is "),
                    _adf_text("cancelled"),
                    _adf_text(" by either party."),
                ],
            }
        ],
    }

    assert _extract_text_from_jira_adf(doc) == "A booking is cancelled by either party."


def test_adf_list_items_are_marked() -> None:
    """An ordered list is how criteria are usually written; without a marker
    the clauses are indistinguishable from prose once ADF markup is gone."""
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "orderedList",
                "content": [
                    {
                        "type": "listItem",
                        "content": [_adf_block("paragraph", "First rule.")],
                    },
                    {
                        "type": "listItem",
                        "content": [_adf_block("paragraph", "Second rule.")],
                    },
                ],
            }
        ],
    }

    assert _extract_text_from_jira_adf(doc) == "- First rule.\n- Second rule."


def test_adf_hard_break_starts_a_new_line() -> None:
    doc = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "content": [
                    _adf_text("Line one"),
                    {"type": "hardBreak"},
                    _adf_text("Line two"),
                ],
            }
        ],
    }

    assert _extract_text_from_jira_adf(doc) == "Line one\nLine two"


def test_adf_empty_document_is_empty_string() -> None:
    assert _extract_text_from_jira_adf({"type": "doc", "content": []}) == ""
    assert _extract_text_from_jira_adf(None) == ""


def test_adf_section_extraction_works_end_to_end() -> None:
    """The whole point of preserving newlines: the heading extractor can find
    a section in a real ADF description."""
    doc = {
        "type": "doc",
        "content": [
            _adf_block("heading", "Business Rules"),
            _adf_block("paragraph", "Cancelling is terminal."),
            _adf_block("heading", "Acceptance Criteria"),
            _adf_block("paragraph", "AC1 - the candidate can cancel."),
        ],
    }

    text = _extract_text_from_jira_adf(doc)

    assert _extract_section_by_heading(
        text, ["acceptance criteria", "business rules"]
    ) == "AC1 - the candidate can cancel."


# ---------------------------------------------------------------------------
# Heading selection - priority, decoration, termination
# ---------------------------------------------------------------------------


def test_heading_list_is_a_priority_order_not_document_order() -> None:
    """A ticket writing Business Rules first must still yield its Acceptance
    Criteria. Matching whichever heading appeared first silently dropped the
    other section."""
    text = (
        "Business Rules\n"
        "1. cancelled is terminal.\n"
        "\n"
        "Acceptance Criteria\n"
        "AC1 - the candidate can cancel.\n"
    )

    assert _extract_section_by_heading(
        text, ["acceptance criteria", "business rules"]
    ) == "AC1 - the candidate can cancel."


def test_markdown_heading_decoration_is_ignored() -> None:
    text = "## Acceptance Criteria\nAC1 - the candidate can cancel."

    assert _extract_section_by_heading(
        text, ["acceptance criteria"]
    ) == "AC1 - the candidate can cancel."


def test_section_survives_a_blank_line_between_paragraphs() -> None:
    """A blank line separates paragraphs; treating it as the end of the section
    truncated multi-paragraph criteria at the first break."""
    text = (
        "Acceptance Criteria\n"
        "AC1 - the candidate can cancel.\n"
        "\n"
        "AC2 - the interviewer can cancel.\n"
    )

    section = _extract_section_by_heading(text, ["acceptance criteria"])

    assert "AC1 - the candidate can cancel." in section
    assert "AC2 - the interviewer can cancel." in section


def test_section_stops_at_the_next_heading() -> None:
    text = (
        "Acceptance Criteria\n"
        "AC1 - the candidate can cancel.\n"
        "\n"
        "Out of scope:\n"
        "Bulk cancellation.\n"
    )

    section = _extract_section_by_heading(text, ["acceptance criteria"])

    assert section == "AC1 - the candidate can cancel."
    assert "Bulk cancellation" not in section


def test_falls_back_to_the_whole_description_when_no_heading_matches() -> None:
    description = "Let the candidate cancel a booking and refund what they paid."

    assert _extract_acceptance_criteria({}, {}, description, "Cancel") == description
