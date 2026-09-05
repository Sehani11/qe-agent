"""Story 6.3 — compare the fine-tuned model against the general LLM.

    python -m app.evaluate_models --run-id run-1
    python -m app.evaluate_models --run-id run-1 --resume        # continue
    python -m app.evaluate_models --limit 4 --providers general_llm

This is the repo's first management command, so it is kept deliberately thin:
argument parsing and reporting only. Everything testable lives in
`app/services/evaluation_service.py` and `app/services/evaluation_metrics.py`,
which is why the pipeline has tests and this file needs almost none.

BEFORE A REAL RUN — the fine-tuned side will otherwise be silently fake:

  1. Start the shim (see training/serve/README.md) and confirm
     `curl -s localhost:9000/health` reports chat_template.status == "match".
  2. Export, for THIS RUN ONLY — never in a committed file:

         BDD_MODEL_PROVIDER stays irrelevant (this tool sets it per call)
         FINE_TUNED_MODEL_ENDPOINT=http://localhost:9000/
         FINE_TUNED_MODEL_TIMEOUT_SECONDS=300

     12s is production's value so the general-LLM fallback still fits inside
     NFR-P3's 30s. A fine-tuned generation takes ~45s on non-GPU hardware, so
     at 12s EVERY call falls back and the run compares the general LLM with
     itself. `--check-config` and the trust gate both exist to catch that.

Expect ~45s per fine-tuned generation. 28 items x 2 providers is roughly half
an hour; the run commits per row and `--resume` picks up where it stopped.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.core.config import settings
from app.core.database import async_session_factory
from app.services.evaluation_service import (
    DegradedRunError,
    GenerationOutcome,
    assert_run_is_trustworthy,
    general_llm_model_name,
    run_evaluation,
)
from app.services.evaluation_set import load_evaluation_set

logger = logging.getLogger("evaluate_models")

MIN_ITEMS = 20  # AC1's gate
REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "evaluation-report.md"


def _check_config(providers: list[str]) -> list[str]:
    """Warnings a user needs BEFORE spending half an hour on a fake run."""
    problems: list[str] = []
    if "fine_tuned" in providers:
        if not settings.fine_tuned_model_endpoint:
            problems.append(
                "FINE_TUNED_MODEL_ENDPOINT is unset - every fine-tuned "
                "generation will fall back to the general LLM."
            )
        if settings.fine_tuned_model_timeout_seconds < 60:
            problems.append(
                f"FINE_TUNED_MODEL_TIMEOUT_SECONDS is "
                f"{settings.fine_tuned_model_timeout_seconds}s, but a fine-tuned "
                "generation takes ~45s on non-GPU hardware. Every call will time "
                "out and fall back. Raise it via the env var FOR THIS RUN ONLY."
            )
    return problems


async def _run_judge(
    rows: list[dict],
) -> tuple[dict | None, str | None, str | None, int]:
    """Blinded subjective scoring over items where BOTH providers succeeded.

    Only complete, trustworthy pairs are judged: comparing a real generation
    against a failed or fallback-served one would score the degradation rather
    than the model.
    """
    from app.services.evaluation_judge import (
        aggregate_judge_scores,
        build_blinded_comparison,
        judge_comparison,
        judge_conflict,
    )

    by_item: dict[str, dict[str, list]] = {}
    for row in rows:
        served_itself = row["effective_provider"] == row["configured_provider"]
        if not row["succeeded"] or not served_itself:
            continue
        by_item.setdefault(row["item_id"], {})[row["configured_provider"]] = row

    pairs = {k: v for k, v in by_item.items() if len(v) == 2}
    logger.info("Judging %d complete pairs (of %d items)", len(pairs), len(by_item))

    per_item = []
    for item_id, providers in pairs.items():
        comparison = build_blinded_comparison(
            item_id=item_id,
            acceptance_criteria=next(iter(providers.values()))["acceptance_criteria"],
            outputs={p: r["scenarios"] or [] for p, r in providers.items()},
        )
        scores = await judge_comparison(comparison)
        if scores:
            per_item.append(scores)
        else:
            logger.warning("judge returned nothing usable for %s", item_id)

    logger.info("Judged %d of %d pairs successfully", len(per_item), len(pairs))

    # The judge is whatever LLM_PROVIDER actually resolves to, not the
    # OpenAI-specific llm_model field (see general_llm_model_name).
    judge_model = general_llm_model_name()
    contestants = [judge_model, "bdd-lora"]
    conflict = judge_conflict(judge_model, contestants)
    if conflict:
        logger.warning("JUDGE CONFLICT: %s", conflict)

    return (
        aggregate_judge_scores(per_item) or None,
        conflict,
        judge_model,
        len(per_item),
    )


async def _score(args: argparse.Namespace) -> int:
    """Compute metrics over already-persisted rows and write the report.

    Separate from generation on purpose: scoring is cheap and generation is
    not, so a scoring change must never cost another half-hour of inference.
    """
    from sqlalchemy import select, text

    from app.models.evaluation_result import EvaluationResult
    from app.services.evaluation_report import build_summary, render_report

    async with async_session_factory() as db:
        result = await db.execute(
            select(EvaluationResult).where(EvaluationResult.run_id == args.run_id)
        )
        rows = [
            {
                "item_id": r.item_id,
                "domain_group": r.domain_group,
                "configured_provider": r.configured_provider,
                "effective_provider": r.effective_provider,
                "acceptance_criteria": r.acceptance_criteria,
                "scenarios": r.scenarios,
                "reference_scenarios": r.reference_scenarios,
                "latency_seconds": r.latency_seconds,
                "succeeded": r.succeeded,
            }
            for r in result.scalars().all()
        ]
        # AC7's denominator, measured rather than assumed.
        edited = await db.execute(
            text("SELECT count(*) FROM bdd_files WHERE source = 'edited'")
        )
        edited_count = edited.scalar() or 0

    if not rows:
        logger.error("No rows for run_id=%s. Generate first.", args.run_id)
        return 2

    # The trust gate must guard the ARTIFACT, not just one invocation. Review
    # finding: it ran only in the generation path, over that call's outcomes —
    # so a resumed run scored rows it never saw and laundered a systematically
    # degraded earlier run into a clean-looking report. Re-derive it here from
    # what is actually about to be summarised.
    persisted = [
        GenerationOutcome(
            item_id=r["item_id"],
            configured_provider=r["configured_provider"],
            effective_provider=r["effective_provider"],
            succeeded=r["succeeded"],
        )
        for r in rows
    ]
    try:
        assert_run_is_trustworthy(persisted)
    except DegradedRunError as exc:
        logger.error("REFUSING TO WRITE REPORT: %s", exc)
        return 4

    logger.info("Scoring %d rows for run %s", len(rows), args.run_id)
    summary = build_summary(rows)
    for cell, values in summary.items():
        logger.info("  %-28s %s", cell, values)

    judge_scores = None
    conflict_note = None
    judge_model = None
    judged_n = None
    if args.judge:
        judge_scores, conflict_note, judge_model, judged_n = await _run_judge(rows)

    report = render_report(
        summary,
        run_id=args.run_id,
        edited_row_count=edited_count,
        judge_model=judge_model,
        judge_conflict_note=conflict_note,
        judge_scores=judge_scores,
        judged_item_count=judged_n,
        conclusion=args.conclusion or "",
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    logger.info("Wrote %s", REPORT_PATH)
    return 0


async def _main(args: argparse.Namespace) -> int:
    if args.score:
        return await _score(args)

    items = load_evaluation_set()

    if len(items) < MIN_ITEMS and not args.limit:
        logger.error(
            "Evaluation set has %d items; AC1 requires >= %d. "
            "Is training/data/holdout.jsonl on disk? Rebuild it rather than "
            "running on a partial set.",
            len(items),
            MIN_ITEMS,
        )
        return 2

    if args.limit:
        items = items[: args.limit]

    problems = _check_config(args.providers)
    for problem in problems:
        logger.warning("CONFIG: %s", problem)
    if problems and args.check_config:
        return 3
    if args.check_config:
        logger.info("Config looks usable for providers=%s", args.providers)
        return 0

    logger.info(
        "run_id=%s items=%d providers=%s", args.run_id, len(items), args.providers
    )

    outcomes = await run_evaluation(
        items=items,
        providers=args.providers,
        run_id=args.run_id,
        session_factory=async_session_factory,
        resume=args.resume,
    )

    if not outcomes:
        logger.info("Nothing to do — run %s is already complete.", args.run_id)
        return 0

    succeeded = sum(1 for o in outcomes if o.succeeded)
    logger.info(
        "Generated %d rows (%d succeeded, %d failed)",
        len(outcomes),
        succeeded,
        len(outcomes) - succeeded,
    )

    try:
        assert_run_is_trustworthy(outcomes)
    except DegradedRunError as exc:
        logger.error("REFUSING TO SUMMARISE: %s", exc)
        return 4

    logger.info("Run %s complete. Score it with the metrics module.", args.run_id)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="run-1", help="Groups one execution")
    parser.add_argument("--limit", type=int, default=None, help="Cap item count")
    parser.add_argument(
        "--providers",
        nargs="+",
        default=["fine_tuned", "general_llm"],
        choices=["fine_tuned", "general_llm"],
    )
    parser.add_argument(
        "--resume",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip (item, provider) pairs already recorded for this run-id",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="Report configuration problems and exit without generating",
    )
    parser.add_argument(
        "--score",
        action="store_true",
        help="Score already-persisted rows and write the report (no generation)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        help="Also run blinded subjective scoring (costs LLM calls)",
    )
    parser.add_argument(
        "--conclusion",
        default="",
        help="Conclusion text for the report's final section",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    return asyncio.run(_main(args))


if __name__ == "__main__":
    sys.exit(main())
