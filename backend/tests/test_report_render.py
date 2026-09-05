"""Tests for traceability export rendering.

`render_report_pdf` had no test at all, which mattered once the summary line
and the per-row status colour started branching on the verdict: a wrong key or
a missing colour would only have surfaced as a 500 when a user clicked Export.
"""

from datetime import UTC, datetime

import pytest

from app.schemas.report import (
    TraceabilityReport,
    TraceabilityRow,
    TraceabilitySummary,
)
from app.services.report_service import render_report_csv, render_report_pdf


def _row(title: str, status: str) -> TraceabilityRow:
    return TraceabilityRow(
        ac_clause=f"AC1: {title}",
        scenario_title=title,
        scenario_status=status,
        justification="because the code says so",
        code_reference={"file": "payments.py", "function": "refund", "line": 221},
        github_links=["https://github.com/acme/widgets/blob/main/payments.py"],
        implementation_suggestion=None if status == "pass" else "wire it up",
        rag_context=None,
    )


def _report(summary: TraceabilitySummary, rows: list[TraceabilityRow]):
    return TraceabilityReport(
        session_id="s-1",
        jira_ticket_id="PROJ-1",
        generated_at=datetime.now(UTC),
        summary=summary,
        rows=rows,
    )


ALL_VERDICTS = _report(
    TraceabilitySummary(total=4, passed=1, failed=1, partial=1, inconclusive=1),
    [
        _row("A passes", "pass"),
        _row("B is partial", "partial"),
        _row("C fails", "fail"),
        _row("D is undecided", "inconclusive"),
    ],
)


def test_pdf_renders_every_verdict() -> None:
    """Each status must resolve to a colour; an unmapped one used to be
    impossible and is now a lookup that could miss."""
    pytest.importorskip("reportlab")

    out = render_report_pdf(ALL_VERDICTS)

    assert out.startswith(b"%PDF")
    assert len(out) > 1000


def test_pdf_renders_an_unknown_status_without_crashing() -> None:
    """A verdict written by a newer build must not break an export."""
    pytest.importorskip("reportlab")

    report = _report(
        TraceabilitySummary(total=1, passed=0, failed=1),
        [_row("E is novel", "something-new")],
    )

    assert render_report_pdf(report).startswith(b"%PDF")


def test_pdf_renders_an_empty_report() -> None:
    pytest.importorskip("reportlab")

    report = _report(TraceabilitySummary(total=0, passed=0, failed=0), [])

    assert render_report_pdf(report).startswith(b"%PDF")


def test_csv_carries_each_verdict_verbatim() -> None:
    text = render_report_csv(ALL_VERDICTS).decode("utf-8")

    assert "partial" in text
    assert "inconclusive" in text
    # The header plus one line per row; justifications hold no newlines here.
    assert text.count("payments.py:refund:221") == 4
