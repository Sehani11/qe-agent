"""Clause coverage for the BDD generation path.

`evaluation_metrics.coverage` already measures this, but only inside the Story
6.3 evaluation harness and only for clauses labelled ``AC1``/``AC 2``/``ac3``.
Two things follow from that, and this module exists to fix both:

1. Nothing checked coverage on the path users actually run. A generation that
   silently skipped half a ticket's criteria produced a BDD file that looked
   complete, and every downstream verdict inherited the gap — the scenarios
   that were never written cannot fail, so the report reads as though the
   ticket were smaller than it is.

2. Plenty of real tickets number their criteria without the letters "AC" —
   a "Business Rules" list, or a bare numbered list under a heading. Those
   parse to zero clauses, so the existing metric returns None (undefined) and
   measures nothing at all. Falling back to ordinal clauses gives those tickets
   a denominator.

`evaluation_metrics.ac_clauses` is deliberately left alone: it is the
denominator of scores already recorded for model comparison, and changing what
it counts would silently re-score historical runs.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

#: "AC1", "AC 2:", "ac3 -", "**AC4** -". Requiring digits directly after the
#: letters is what keeps this from matching the "ac" inside ordinary words;
#: demanding trailing punctuation as well would miss every clause written with
#: Markdown emphasis, which is how Jira tickets routinely arrive.
_AC_LABEL_RE = re.compile(r"\bAC\s*(\d+)\b", re.IGNORECASE)

#: A numbered clause at the start of a line: "1.", "2)", "3 -". Used only when
#: a ticket carries no AC labels at all.
_ORDINAL_LABEL_RE = re.compile(r"(?m)^\s*(?:[-*]\s*)?(\d{1,2})\s*[.):\-]\s+\S")

#: How a scenario cites an ordinal clause. Models write "1.", "Rule 1",
#: "Business Rule 1" — all of which should count as clause 1.
_ORDINAL_CITATION_RE = re.compile(r"(?m)^\s*(?:[-*]\s*)?(\d{1,2})\s*[.):\-]?\s")


def clause_labels(acceptance_criteria: str) -> list[str]:
    """The distinct clause labels in a ticket, in order of first appearance.

    Prefers explicit ``AC<n>`` labels. Only when a ticket has none does it fall
    back to ordinal list numbering, so a ticket that labels *some* criteria is
    never measured against a mixture of the two schemes.

    Returns an empty list when nothing parses, which callers must treat as
    "coverage is undefined" rather than as zero coverage.
    """
    ac_numbers = _AC_LABEL_RE.findall(acceptance_criteria)
    if ac_numbers:
        return _dedupe(f"AC{int(n)}" for n in ac_numbers)

    ordinals = _ORDINAL_LABEL_RE.findall(acceptance_criteria)
    return _dedupe(f"C{int(n)}" for n in ordinals)


def cited_label(scenario: dict[str, Any], scheme: str) -> str | None:
    """The clause a scenario claims to cover, normalised to `scheme`.

    `scheme` is "AC" or "C", taken from whatever `clause_labels` found — a
    scenario citing "1." means clause 1 either way, and penalising the model
    for the citation style it was given would measure obedience rather than
    coverage.
    """
    raw = str(scenario.get("source_ac_clause", ""))
    if not raw.strip():
        return None

    match = _AC_LABEL_RE.search(raw)
    if match:
        return f"{scheme}{int(match.group(1))}"

    if scheme == "C":
        ordinal = _ORDINAL_CITATION_RE.search(raw)
        if ordinal:
            return f"C{int(ordinal.group(1))}"
    return None


def coverage_report(
    acceptance_criteria: str, scenarios: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Which clauses of the ticket got a scenario, and which did not.

    Computed against the RAW criteria text submitted for generation, so a
    clause that never reached the model still counts against coverage. Scoring
    only what the model was shown would report perfect coverage for a ticket
    that arrived truncated — the exact failure this is meant to surface.
    """
    labels = clause_labels(acceptance_criteria)
    if not labels:
        return {
            "total_clauses": 0,
            "covered_clauses": 0,
            "uncovered": [],
            "ratio": None,
        }

    scheme = "AC" if labels[0].startswith("AC") else "C"
    cited = {
        label
        for label in (cited_label(s, scheme) for s in scenarios)
        if label is not None
    }
    covered = [label for label in labels if label in cited]
    uncovered = [label for label in labels if label not in cited]

    return {
        "total_clauses": len(labels),
        "covered_clauses": len(covered),
        "uncovered": uncovered,
        "ratio": len(covered) / len(labels),
    }


def _dedupe(labels) -> list[str]:
    """Preserve first-appearance order while dropping repeats."""
    seen: list[str] = []
    for label in labels:
        if label not in seen:
            seen.append(label)
    return seen
