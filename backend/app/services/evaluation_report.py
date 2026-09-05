"""Scoring and report generation for Story 6.3.

Reads persisted `evaluation_results` rows, computes the objective metrics, and
renders the side-by-side report. Kept separate from the runner so scoring can
be revised and re-run without regenerating — at ~45s per fine-tuned generation
that distinction is worth real time.

Two rules this module enforces because AC8 depends on them:

  * on-domain and off-domain groups are reported SEPARATELY, never averaged;
  * every table is followed by the caveats that determine how it may be read.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.evaluation_metrics import (
    coverage,
    duplicate_rate,
    reference_alignment,
    summarise_group,
)

PROVIDERS = ("fine_tuned", "general_llm")
GROUPS = ("off_domain", "on_domain")

GROUP_LABEL = {
    "off_domain": "off-domain (holdout, back-generated ACs, has reference)",
    "on_domain": "on-domain (curated product ACs, no reference)",
}


def score_row(row: dict[str, Any]) -> dict[str, Any]:
    """Attach the objective metrics to one persisted result row."""
    scenarios = row.get("scenarios") or []
    return {
        **row,
        "coverage": coverage(row["acceptance_criteria"], scenarios),
        "duplicate_rate": duplicate_rate(scenarios),
        "reference_alignment": reference_alignment(
            scenarios, row.get("reference_scenarios")
        ),
        "scenario_count": len(scenarios),
    }


def build_summary(rows: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Summarise per (domain_group, provider) cell."""
    scored = [score_row(r) for r in rows]
    summary: dict[str, dict[str, Any]] = {}
    for group in GROUPS:
        for provider in PROVIDERS:
            cell = [
                r
                for r in scored
                if r["domain_group"] == group and r["configured_provider"] == provider
            ]
            summary[f"{group}/{provider}"] = summarise_group(cell)
    return summary


def _fmt(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def _metric_table(summary: dict[str, dict[str, Any]], group: str) -> str:
    ft = summary[f"{group}/fine_tuned"]
    gl = summary[f"{group}/general_llm"]
    metrics = [
        ("Items scored (trustworthy)", "n_trustworthy"),
        ("Rows excluded as degraded", "n_degraded"),
        ("Generation success rate", "success_rate"),
        ("AC-clause coverage", "coverage_mean"),
        ("Duplicate-scenario rate", "duplicate_rate_mean"),
        ("Reference alignment", "reference_alignment_mean"),
        ("Scenarios per item", "scenario_count_mean"),
        ("Latency (s)", "latency_mean_seconds"),
    ]
    lines = [
        "| Metric | Fine-tuned | General LLM |",
        "|---|---|---|",
    ]
    for label, key in metrics:
        lines.append(f"| {label} | {_fmt(ft.get(key))} | {_fmt(gl.get(key))} |")
    return "\n".join(lines)


def render_report(
    summary: dict[str, dict[str, Any]],
    *,
    run_id: str,
    edited_row_count: int,
    judge_model: str | None = None,
    judge_conflict_note: str | None = None,
    judge_scores: dict[str, Any] | None = None,
    judged_item_count: int | None = None,
    conclusion: str = "",
) -> str:
    """Render the full markdown report (AC7, AC8, AC9)."""
    parts: list[str] = [
        "# Model evaluation report — Story 6.3",
        "",
        f"**Run:** `{run_id}` · fine-tuned (`bdd-lora`) vs general LLM baseline.",
        "",
        "> **Read the caveats before the numbers.** Three known confounds are "
        "listed below, any one of which changes what these tables mean.",
        "",
        "## Results by group",
        "",
        "The two groups measure different things and are **never averaged**. "
        "The gap between them is the more informative comparison: it separates "
        "*learned the shape of Gherkin* from *learned this domain*.",
        "",
    ]

    for group in GROUPS:
        parts += [
            f"### {GROUP_LABEL[group]}",
            "",
            _metric_table(summary, group),
            "",
        ]

    parts += [
        "**Metric definitions.** *AC-clause coverage*: fraction of AC clauses "
        "with at least one scenario citing them — breadth, not volume. "
        "*Duplicate rate*: scenarios that near-duplicate an earlier one, which "
        "catches a model padding coverage by restating itself. *Reference "
        "alignment*: lexical overlap with human-authored Gherkin; **holdout "
        "only**, and deliberately crude — it measures reproduction of the "
        "reference's language, not quality. *Success rate*: generations that "
        "returned usable scenarios at all.",
        "",
    ]

    # --- AC7: the metric that cannot be computed ---------------------------
    parts += [
        "## Human-edit percentage — NOT MEASURABLE",
        "",
        f"The epic asks for this metric. It requires rows where a user edited "
        f"generated Gherkin and saved: `bdd_files` with `source='edited'`. "
        f"There are **{edited_row_count}**.",
        "",
        "It is reported as not measurable rather than computed from an empty "
        "denominator. Story 6.4's capture mechanism is correct and tested; it "
        "has simply never been exercised, which is the same finding that has "
        "now blocked three Epic 6 stories.",
        "",
    ]

    # --- Judge ------------------------------------------------------------
    parts += ["## Subjective scoring", ""]
    judged_n_label = (
        str(judged_item_count) if judged_item_count is not None else "unrecorded"
    )
    if judge_scores:
        parts += [
            f"Judged by `{judge_model}`, blinded: outputs anonymised as System "
            "A/B with the label→provider mapping drawn per item, so neither "
            "provenance nor position is inferable.",
            "",
            # Sample size is not optional here: unusable verdicts are dropped
            # silently, so without a count a thin judging pass would be
            # indistinguishable from a complete one.
            f"**Items judged: {judged_n_label}.** "
            "Only pairs where BOTH providers produced trustworthy output are "
            "judged; verdicts that failed to parse are dropped, so this may be "
            "smaller than the number of scored items above.",
            "",
            "| Criterion | Fine-tuned | General LLM |",
            "|---|---|---|",
        ]
        for criterion in ("ac_relevance", "correctness", "specificity"):
            ft = judge_scores.get("fine_tuned", {}).get(criterion)
            gl = judge_scores.get("general_llm", {}).get(criterion)
            parts.append(f"| {criterion} | {_fmt(ft)} | {_fmt(gl)} |")
        parts.append("")
    else:
        parts += ["Not run for this evaluation.", ""]

    if judge_conflict_note:
        parts += [
            f"> ⚠️ **Judge conflict of interest.** {judge_conflict_note}",
            "",
        ]

    # --- AC8: caveats ------------------------------------------------------
    parts += [
        "## Caveats that determine how these numbers may be read",
        "",
        "**1. Quantization drift (serving path).** The adapter trained under "
        "bitsandbytes NF4 and is served on a q4_K_M base. The shim's "
        "chat-template check cannot detect this — it compares prompt formats, "
        "not weights. **A poor fine-tuned score has two candidate causes**: the "
        "fine-tune's own weakness, and drift between training and serving. "
        "Ruling the second out requires running a subset through the adapter in "
        "`transformers`; see `training/serve/README.md`.",
        "",
        "**2. Corpus domain (training).** 84% of the fine-tune's training corpus "
        "was testing-framework Gherkin about running CLI commands, not product "
        "behaviour. The off-domain group is close to that distribution; the "
        "on-domain group is not. Expect the fine-tune to look better on the "
        "former for reasons that do not generalise to real tickets.",
        "",
        "**3. Evaluation-set provenance.** No item is a real Jira ticket. The "
        "holdout's ACs were LLM-back-generated *from their own reference*, which "
        "flatters any model reproducing it; the curated items were written by "
        "the dev agent. See `docs/evaluation-set.md`.",
        "",
        "**Sample size is small.** These numbers will not separate models that "
        "are close together.",
        "",
    ]

    parts += ["## Conclusion", "", conclusion or "_Pending._", ""]
    return "\n".join(parts)
