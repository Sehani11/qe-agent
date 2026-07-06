"""Tests for Jira ticket extraction helpers."""

from app.services.jira_service import (
    _extract_acceptance_criteria,
    _extract_section_by_heading,
    _extract_ticket_id,
)


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