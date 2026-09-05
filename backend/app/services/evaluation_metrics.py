"""Objective metrics for Story 6.3 — no LLM judgement anywhere in this module.

AC5 makes these the backbone of the comparison for one reason: they are
arithmetic over stored rows, so they cannot be disputed, cannot be biased
toward a contestant, and can be recomputed after a scoring change without
re-running a half-hour of generation.

Every function is pure and returns `None` rather than 0.0 when a metric is
genuinely undefined. That distinction is load-bearing: a curated item has no
reference Gherkin, and scoring it 0 for similarity would make the on-domain
group look catastrophically worse than the holdout for a reason that has
nothing to do with either model.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from difflib import SequenceMatcher
from statistics import mean
from typing import Any

from app.services.evaluation_set import ac_clauses

#: Above this similarity two scenarios are treated as the same scenario.
#: Tuned to catch the failure RUN_LOG documented for the fine-tune — a second
#: scenario whose steps copy the first with a negated `then` — without flagging
#: two genuinely different scenarios that share a Given.
NEAR_DUPLICATE_RATIO = 0.90

_NORMALISE_RE = re.compile(r"[^a-z0-9\s]+")
_CLAUSE_RE = re.compile(r"\bAC\s*(\d+)", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return " ".join(_NORMALISE_RE.sub(" ", (text or "").lower()).split())


def _steps(scenario: dict[str, Any]) -> str:
    return _normalise(
        f"{scenario.get('given', '')} {scenario.get('when', '')} "
        f"{scenario.get('then', '')}"
    )


def _cited_clause(scenario: dict[str, Any]) -> str | None:
    """The AC label a scenario claims to cover, normalised.

    Models write 'AC 1', 'ac1', 'AC1:'. Penalising formatting would measure
    obedience to a citation style rather than coverage.
    """
    match = _CLAUSE_RE.search(str(scenario.get("source_ac_clause", "")))
    return f"AC{int(match.group(1))}" if match else None


def coverage(
    acceptance_criteria: str, scenarios: Sequence[dict[str, Any]]
) -> float | None:
    """Fraction of AC clauses with at least one scenario citing them.

    Returns None when no clauses parse — dividing by zero clauses would score
    every provider 0 on a malformed item, dragging both averages down equally
    but meaninglessly. Breadth, not volume: three scenarios for one clause do
    not compensate for a clause with none.
    """
    clauses = ac_clauses(acceptance_criteria)
    if not clauses:
        return None
    if not scenarios:
        return 0.0

    cited = {c for c in (_cited_clause(s) for s in scenarios) if c}
    return len(cited & set(clauses)) / len(clauses)


def duplicate_rate(scenarios: Sequence[dict[str, Any]]) -> float:
    """Fraction of scenarios that near-duplicate an earlier one.

    This is the metric that catches the specific weakness RUN_LOG recorded:
    the fine-tune producing a second scenario whose steps restate the first.
    A model can otherwise score well on coverage by emitting the same scenario
    under different clause citations.
    """
    if len(scenarios) < 2:
        return 0.0

    texts = [_steps(s) for s in scenarios]
    duplicates = 0
    for index, text in enumerate(texts):
        if any(
            SequenceMatcher(None, text, earlier).ratio() >= NEAR_DUPLICATE_RATIO
            for earlier in texts[:index]
        ):
            duplicates += 1
    return duplicates / len(texts)


def reference_alignment(
    generated: Sequence[dict[str, Any]],
    reference: Sequence[dict[str, Any]] | None,
) -> float | None:
    """Mean best-match token overlap against human-authored reference Gherkin.

    Applies ONLY to the holdout group, which is the only source carrying a
    reference. Returns None when there is none — see the module docstring on
    why that must not be 0.0.

    Deliberately a crude lexical measure, not a semantic one: it is here to be
    unarguable and reproducible, not to be clever. A high score means the model
    reproduced the reference's language; it does not by itself mean the output
    is good, which is what the blinded judge is for.
    """
    if not reference or not generated:
        return None

    reference_tokens = [set(_steps(r).split()) for r in reference]
    scores: list[float] = []
    for scenario in generated:
        tokens = set(_steps(scenario).split())
        if not tokens:
            scores.append(0.0)
            continue
        best = max(
            (
                len(tokens & ref) / len(tokens | ref) if (tokens | ref) else 0.0
                for ref in reference_tokens
            ),
            default=0.0,
        )
        scores.append(best)
    return mean(scores) if scores else None


def _is_trustworthy(row: dict[str, Any]) -> bool:
    return (
        bool(row.get("succeeded"))
        and row.get("effective_provider") is not None
        and row.get("effective_provider") == row.get("configured_provider")
    )


def summarise_group(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate scored rows for one (provider, domain_group) cell.

    Degraded rows — configured `fine_tuned`, served by `general_llm` — are
    EXCLUDED from every average and counted separately. Averaging them in is
    the precise mechanism by which this comparison would become wrong while
    looking healthy.
    """
    trustworthy = [r for r in rows if _is_trustworthy(r)]
    degraded = [
        r
        for r in rows
        if r.get("succeeded") and not _is_trustworthy(r)
    ]

    def _mean_of(key: str) -> float | None:
        values = [r[key] for r in trustworthy if r.get(key) is not None]
        return round(mean(values), 4) if values else None

    # A degraded row is excluded from the success rate's DENOMINATOR too, not
    # just its numerator. Review finding: computing it over all rows credited
    # the fine-tune for output the general LLM produced — the one metric added
    # to stop a model hiding behind its good rows was itself counting rows the
    # model never generated. Genuine failures stay in: they are attributable.
    attributable = [r for r in rows if r not in degraded]

    return {
        "n_rows": len(rows),
        "n_trustworthy": len(trustworthy),
        "n_degraded": len(degraded),
        # Of the generations actually attributable to this provider, how many
        # returned usable scenarios. A model that often returns nothing must
        # not hide behind the occasions it answers.
        "success_rate": (
            round(
                sum(1 for r in attributable if r.get("succeeded")) / len(attributable),
                4,
            )
            if attributable
            else None
        ),
        "coverage_mean": _mean_of("coverage"),
        "duplicate_rate_mean": _mean_of("duplicate_rate"),
        "reference_alignment_mean": _mean_of("reference_alignment"),
        "scenario_count_mean": _mean_of("scenario_count"),
        "latency_mean_seconds": _mean_of("latency_seconds"),
    }
