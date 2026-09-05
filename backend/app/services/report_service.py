"""Traceability report assembly (Story 5.3).

Joins bdd_files (AC clauses) with verification_results (verdicts) into a flat,
export-friendly report. No LLM, no Pinecone — pure DB read + assembly.

The join: verification_results.scenario_title == BDDScenario.scenario, which
recovers the scenario's source_ac_clause (only present for generated BDD).
"""

import csv
import io
import json
import logging
from datetime import UTC, datetime
from xml.sax.saxutils import escape as _xml_escape

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.bdd_file import BddFile
from app.models.session import Session
from app.models.verification_result import VerificationResult
from app.schemas.report import (
    TraceabilityReport,
    TraceabilityRow,
    TraceabilitySummary,
)

logger = logging.getLogger(__name__)

# Exact CSV header order (Story 5.4, AC4 / FR30, FR45, FR46).
CSV_COLUMNS = [
    "AC Clause",
    "Scenario",
    "Status",
    "Justification",
    "Code Reference",
    "Implementation Suggestion",
    "RAG Context",
]


def _build_ac_map(bdd_file: BddFile | None) -> dict[str, str | None]:
    """Map {scenario_title: source_ac_clause} from a generated BDD file.

    Returns an empty map for uploaded BDD (no structured AC) or malformed JSON.
    """
    if bdd_file is None or bdd_file.source != "generated":
        return {}
    try:
        scenarios = json.loads(bdd_file.content).get("scenarios", [])
    except (json.JSONDecodeError, TypeError, AttributeError):
        logger.warning(
            "Malformed BDD content for session=%s — no AC clauses mapped",
            bdd_file.session_id,
        )
        return {}
    # Key on the STRIPPED title: verification stores scenario_title via
    # parse_bdd_scenarios' `.strip()`, so the map key must match that or the
    # join silently misses (returns a null ac_clause).
    ac_map: dict[str, str | None] = {}
    for s in scenarios:
        if not isinstance(s, dict):
            continue
        title = s.get("scenario")
        if isinstance(title, str) and title.strip():
            ac_map[title.strip()] = s.get("source_ac_clause")
    return ac_map


async def build_traceability_report(
    session: Session, db: AsyncSession
) -> TraceabilityReport:
    """Assemble the AC → scenario → verdict report for an owned session (no LLM)."""
    session_id = str(session.id)

    # Latest BDD file for the session → AC-clause lookup map.
    # Limitation: verification_results has no FK to a specific bdd_file, so this
    # uses the newest BDD. If a user uploads a .feature AFTER generating+verifying,
    # the newest row is the uploaded one (no source_ac_clause) → null ac_clauses.
    bdd_result = await db.execute(
        select(BddFile)
        .where(BddFile.session_id == session_id)
        .order_by(BddFile.created_at.desc())
        .limit(1)
    )
    ac_map = _build_ac_map(bdd_result.scalar_one_or_none())

    # All verification results, oldest first
    vr_result = await db.execute(
        select(VerificationResult)
        .where(VerificationResult.session_id == session_id)
        .order_by(VerificationResult.created_at.asc())
    )
    results = vr_result.scalars().all()

    rows: list[TraceabilityRow] = []
    # One counter per verdict. The previous `if pass else failed` reported
    # every inconclusive verdict as a failure, so a run that declined to judge
    # a scenario was indistinguishable in the summary from one that read the
    # code and found the behaviour missing.
    counts = {"pass": 0, "partial": 0, "fail": 0, "inconclusive": 0}
    for r in results:
        # An unrecognised status counts as a failure rather than vanishing from
        # the roll-up: total must always equal the number of rows.
        counts[r.status if r.status in counts else "fail"] += 1
        rows.append(
            TraceabilityRow(
                ac_clause=ac_map.get(r.scenario_title),
                scenario_title=r.scenario_title,
                scenario_status=r.status,
                justification=r.justification,
                code_reference=r.code_reference,
                github_links=r.github_links,
                implementation_suggestion=r.implementation_suggestion,
                rag_context=r.rag_context,
            )
        )

    return TraceabilityReport(
        session_id=session_id,
        jira_ticket_id=session.jira_ticket_id,
        generated_at=datetime.now(UTC),
        summary=TraceabilitySummary(
            total=len(rows),
            passed=counts["pass"],
            failed=counts["fail"],
            partial=counts["partial"],
            inconclusive=counts["inconclusive"],
        ),
        rows=rows,
    )


# ---------------------------------------------------------------------------
# Story 5.4 — export rendering (PDF / CSV). No LLM, no Pinecone: these render
# the already-assembled TraceabilityReport to bytes.
# ---------------------------------------------------------------------------


def _sanitize_csv_cell(value: str) -> str:
    """Neutralize CSV formula injection (CWE-1236).

    A cell beginning with '=', '+', '-', '@' (or a leading control char that
    spreadsheets treat as a formula lead-in) is prefixed with a single quote so
    Excel/Google Sheets render it as text rather than executing it.
    """
    if value and value[0] in ("=", "+", "-", "@", "\t", "\r", "\n"):
        return "'" + value
    return value


def _fmt_code_reference(code_reference: dict | None) -> str:
    """Flatten a CodeReference dict ({file, function, line}) to 'file:function:line'.

    Skips missing/None parts so a partial reference still reads cleanly.
    """
    if not isinstance(code_reference, dict):
        return ""
    parts = [
        code_reference.get("file"),
        code_reference.get("function"),
        code_reference.get("line"),
    ]
    return ":".join(str(p) for p in parts if p not in (None, ""))


def _fmt_rag_context(rag_context: list | None) -> str:
    """Render the list of RagContextItem dicts to a readable single-cell string.

    Each item → '{source}: {title or source_id} — {snippet}', joined by ' | '.
    Returns '' for no context (null/empty).
    """
    if not rag_context:
        return ""
    segments: list[str] = []
    for item in rag_context:
        if not isinstance(item, dict):
            continue
        source = item.get("source", "")
        label = item.get("title") or item.get("source_id") or ""
        snippet = item.get("snippet", "")
        head = f"{source}: {label}".strip(": ").strip()
        segments.append(f"{head} — {snippet}".strip(" —") if snippet else head)
    return " | ".join(s for s in segments if s)


def render_report_csv(report: TraceabilityReport) -> bytes:
    """Render a TraceabilityReport to UTF-8 CSV bytes (Story 5.4, AC4).

    Columns: AC Clause, Scenario, Status, Justification, Code Reference,
    Implementation Suggestion, RAG Context. A report with no rows yields a
    header-only CSV. Quoting/escaping is handled by ``csv.writer``.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(CSV_COLUMNS)
    for row in report.rows:
        cells = [
            row.ac_clause or "",
            row.scenario_title,
            row.scenario_status,
            row.justification,
            _fmt_code_reference(row.code_reference),
            row.implementation_suggestion or "",
            _fmt_rag_context(row.rag_context),
        ]
        writer.writerow([_sanitize_csv_cell(c) for c in cells])
    return buffer.getvalue().encode("utf-8")


def render_report_pdf(report: TraceabilityReport) -> bytes:
    """Render a TraceabilityReport to a formatted PDF (Story 5.4, AC1/AC2).

    Includes a header (ticket, generated-at, summary) and one block per
    scenario carrying the verdict plus implementation suggestion (FR46) and
    RAG context (FR45). Rendered in-memory — no temp files, no LLM.
    """
    # Imported lazily so the rest of report_service (the 5.3 data assembly)
    # has no hard dependency on reportlab being importable.
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    # Amber for partial, slate for inconclusive: neither is the green of a
    # pass nor the red of an established absence, and a reader scanning the
    # PDF should be able to tell them apart without reading the justification.
    status_colors = {
        "pass": colors.HexColor("#059669"),
        "partial": colors.HexColor("#d97706"),
        "fail": colors.HexColor("#e11d48"),
        "inconclusive": colors.HexColor("#64748b"),
    }

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        title=f"{report.jira_ticket_id} Traceability Report",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    cell = ParagraphStyle(
        "cell", parent=styles["BodyText"], fontSize=8, leading=10, alignment=TA_LEFT
    )
    label = ParagraphStyle(
        "label", parent=cell, fontName="Helvetica-Bold", textColor=colors.HexColor(
            "#0369a1"
        )
    )

    story: list = []
    generated = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")
    story.append(Paragraph("Traceability Report", styles["Title"]))
    story.append(
        Paragraph(
            f"Jira Ticket: <b>{_xml_escape(report.jira_ticket_id)}</b> "
            f"&nbsp;·&nbsp; Generated: {generated}",
            styles["Normal"],
        )
    )
    s = report.summary
    # Only non-zero buckets are printed, so a run with no partial or
    # inconclusive verdicts reads exactly as it did before they existed.
    segments = [f"<b>{s.total}</b> total", f"<b>{s.passed}</b> passed"]
    if s.partial:
        segments.append(f"<b>{s.partial}</b> partial")
    segments.append(f"<b>{s.failed}</b> failed")
    if s.inconclusive:
        segments.append(f"<b>{s.inconclusive}</b> inconclusive")
    story.append(
        Paragraph(
            "Summary: " + " &nbsp;·&nbsp; ".join(segments),
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 8 * mm))

    if not report.rows:
        story.append(
            Paragraph("No verification results for this session yet.", styles["Italic"])
        )
    else:
        for i, row in enumerate(report.rows, start=1):
            status_color = status_colors.get(
                row.scenario_status, colors.HexColor("#e11d48")
            )
            # Escape every dynamic value: reportlab parses Paragraph text as
            # mini-XML, so unescaped '<'/'&' in LLM/user content would either
            # crash the build (unbalanced tags) or silently drop '<...>' spans.
            data = [
                [
                    Paragraph("Scenario", label),
                    Paragraph(f"{i}. {_xml_escape(row.scenario_title)}", cell),
                ],
                [
                    Paragraph("Status", label),
                    Paragraph(
                        f'<font color="{status_color.hexval()}">'
                        f"<b>{_xml_escape(row.scenario_status.upper())}</b></font>",
                        cell,
                    ),
                ],
                [
                    Paragraph("AC Clause", label),
                    Paragraph(
                        _xml_escape(row.ac_clause) if row.ac_clause else "—", cell
                    ),
                ],
                [
                    Paragraph("Justification", label),
                    Paragraph(
                        _xml_escape(row.justification) if row.justification else "—",
                        cell,
                    ),
                ],
                [
                    Paragraph("Code Reference", label),
                    Paragraph(
                        _xml_escape(_fmt_code_reference(row.code_reference)) or "—",
                        cell,
                    ),
                ],
            ]
            if row.implementation_suggestion:
                data.append(
                    [
                        Paragraph("Suggestion", label),
                        Paragraph(_xml_escape(row.implementation_suggestion), cell),
                    ]
                )
            rag_text = _fmt_rag_context(row.rag_context)
            if rag_text:
                data.append(
                    [
                        Paragraph("RAG Context", label),
                        Paragraph(_xml_escape(rag_text), cell),
                    ]
                )

            table = Table(data, colWidths=[28 * mm, 134 * mm])
            table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor(
                            "#eef2f7"
                        )),
                        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f8fafc")),
                        ("LEFTPADDING", (0, 0), (-1, -1), 5),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                    ]
                )
            )
            story.append(table)
            story.append(Spacer(1, 4 * mm))

    doc.build(story)
    return buffer.getvalue()
