"""Side-by-side model comparison for a single input.

The same comparison `app.evaluate_models` runs over the whole evaluation set,
exposed for one ticket so it can be triggered from the UI.

Generation and scoring are reused wholesale from the evaluation services —
`generate_one` for the run, `evaluation_metrics` for the scores — so this route
and the batch CLI can never drift into measuring different things. This file
handles auth, input resolution and HTTP mapping only.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.evaluation_result import EvaluationResult
from app.schemas.evaluation import (
    BatchCompareRequest,
    BatchCompareResponse,
    BatchItemOutcome,
    CompareRequest,
    CompareResponse,
    GroupMetrics,
    ProviderMetrics,
    ProviderResult,
    RunReport,
    RunSummary,
)
from app.services import jira_service
from app.services.evaluation_metrics import coverage, duplicate_rate, summarise_group
from app.services.evaluation_report import score_row
from app.services.evaluation_service import (
    GenerationOutcome,
    generate_one,
    persist_outcome,
)
from app.services.evaluation_set import EvalItem, ac_clauses
from app.services.project_config_service import (
    JiraCredentials,
    ensure_project,
    jira_credentials_for,
)

logger = logging.getLogger(__name__)

router = APIRouter()

#: Order matters for the UI: the general LLM answers in a few seconds and the
#: fine-tuned model can take a minute, so running the baseline first means the
#: slow column is the only thing still pending.
PROVIDERS = ("general_llm", "fine_tuned")


def _score(outcome: GenerationOutcome, acceptance_criteria: str) -> ProviderResult:
    """Turn one generation into a scored row."""
    scenarios = outcome.scenarios or []
    return ProviderResult(
        configured_provider=outcome.configured_provider,
        effective_provider=outcome.effective_provider,
        fallback_reason=outcome.fallback_reason,
        model_identifier=outcome.model_identifier,
        succeeded=outcome.succeeded,
        error=outcome.error,
        scenarios=scenarios,
        latency_seconds=outcome.latency_seconds,
        # Only score what actually ran. Scoring a failed generation would put a
        # real-looking 0.0 next to a real number and invite the comparison to
        # be read as "this model covered nothing", which is a different claim
        # from "this model did not answer".
        coverage=(
            coverage(acceptance_criteria, scenarios) if outcome.succeeded else None
        ),
        duplicate_rate=duplicate_rate(scenarios) if outcome.succeeded else None,
        scenario_count=len(scenarios),
    )


async def _resolve_criteria(
    ticket_id: str | None, criteria: str, credentials: JiraCredentials | None = None
) -> str:
    """The criteria text to compare, fetching the ticket when needed.

    `credentials` is resolved by the caller from the request's project, the
    same way ingestion does it. Passing None falls back to the environment,
    which reports "Jira is not configured" for anyone whose credentials live in
    project settings — the ticket is then fetched from whichever Jira the
    server happens to name, or from none at all.
    """
    if criteria:
        return criteria
    try:
        ticket = await jira_service.fetch_ticket_content(ticket_id or "", credentials)
    except Exception as exc:
        logger.warning("compare: jira fetch failed for %s: %s", ticket_id, exc)
        raise HTTPException(
            status_code=502, detail=f"Could not fetch ticket: {exc}"
        ) from exc
    return (ticket.acceptance_criteria or "").strip()


async def _run_both(
    item: EvalItem,
) -> tuple[list[ProviderResult], list[GenerationOutcome]]:
    """Generate with both providers, sequentially, without fallback.

    Sequential, not concurrent: a fine-tuned generation saturates the CPU on
    this hardware, so running the two together would inflate the baseline's
    latency and make the timing column meaningless.

    No fallback: substituting the general LLM for a failed fine-tuned call
    would fill the fine-tuned column with the other model's output — two
    columns showing one model, with nothing to distinguish that from a real
    comparison.
    """
    results: list[ProviderResult] = []
    outcomes: list[GenerationOutcome] = []
    for provider_name in PROVIDERS:
        outcome = await generate_one(item, provider_name, allow_fallback=False)
        outcomes.append(outcome)
        results.append(_score(outcome, item.acceptance_criteria))
    return results, outcomes


@router.post("/compare", response_model=CompareResponse)
async def compare_models(
    payload: CompareRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> CompareResponse:
    """Run the same acceptance criteria through both models and score both.

    Ad-hoc and NOT saved. Use `POST /evaluation/runs` to record results.
    """
    criteria = (payload.acceptance_criteria or "").strip()
    ticket_id = (payload.jira_ticket_id or "").strip() or None

    # Only when a ticket is actually being fetched: pasted criteria need no
    # Jira at all, and resolving a project for them would make this endpoint
    # depend on project state it does not use.
    credentials = None
    if not criteria and ticket_id:
        project = await ensure_project(db, current_user, payload.project_id)
        credentials = jira_credentials_for(project)

    criteria = await _resolve_criteria(ticket_id, criteria, credentials)
    if not criteria:
        raise HTTPException(
            status_code=422,
            detail="No acceptance criteria found for this input.",
        )

    item = EvalItem(
        id=ticket_id or "ad-hoc",
        acceptance_criteria=criteria,
        domain_group="on_domain",
        provenance="human_jira" if ticket_id else "agent_authored",
    )

    results, _ = await _run_both(item)

    notes: list[str] = []
    for row in results:
        if not row.succeeded:
            notes.append(
                f"{row.configured_provider} did not answer: {row.error}. "
                "No result is shown for it rather than substituting the other "
                "model."
            )
        elif not row.is_trustworthy:
            notes.append(
                f"{row.configured_provider} was answered by "
                f"{row.effective_provider} "
                f"({row.fallback_reason or 'unknown reason'})."
            )

    clauses = ac_clauses(criteria)
    if not clauses:
        notes.append(
            "No numbered AC clauses were found, so coverage cannot be computed. "
            "Number the criteria as AC1, AC2, … to enable it."
        )

    logger.info(
        "compare user=%s ticket=%s clauses=%d results=%s",
        current_user,
        ticket_id,
        len(clauses),
        [
            (r.configured_provider, r.effective_provider, r.latency_seconds)
            for r in results
        ],
    )

    return CompareResponse(
        acceptance_criteria=criteria,
        jira_ticket_id=ticket_id,
        ac_clauses=clauses,
        results=results,
        comparable=all(r.is_trustworthy for r in results),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Saved runs
#
# A run groups several tickets so results accumulate over time and can be
# reported together. Rows live in `evaluation_results` — the same table the
# `app.evaluate_models` CLI writes — so a run started here can be scored by the
# CLI and vice versa.
# ---------------------------------------------------------------------------


@router.post("/runs", response_model=BatchCompareResponse)
async def run_batch_comparison(
    payload: BatchCompareRequest,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> BatchCompareResponse:
    """Compare several tickets and SAVE the results under one run id.

    One ticket failing never stops the batch. A fine-tuned generation can take
    minutes, so losing a whole run to a single unreachable ticket would be
    expensive; failures become per-item errors instead.
    """
    items: list[BatchItemOutcome] = []
    saved = 0

    # Resolved once for the whole batch, not per ticket: every ticket in a run
    # comes from the same project's Jira.
    project = await ensure_project(db, current_user, payload.project_id)
    credentials = jira_credentials_for(project)

    for ticket_id in payload.jira_ticket_ids:
        try:
            criteria = await _resolve_criteria(ticket_id, "", credentials)
            if not criteria:
                items.append(
                    BatchItemOutcome(
                        jira_ticket_id=ticket_id,
                        saved=False,
                        error="No acceptance criteria found on this ticket.",
                    )
                )
                continue

            item = EvalItem(
                id=ticket_id,
                acceptance_criteria=criteria,
                domain_group="on_domain",
                provenance="human_jira",
            )
            results, outcomes = await _run_both(item)

            # Persisted per row, not per batch: a run that dies at ticket 8
            # keeps the first seven rather than discarding minutes of work.
            for outcome in outcomes:
                await persist_outcome(
                    outcome, item, payload.run_id, db, user_id=current_user
                )

            saved += 1
            items.append(
                BatchItemOutcome(
                    jira_ticket_id=ticket_id,
                    saved=True,
                    comparable=all(r.is_trustworthy for r in results),
                    ac_clause_count=len(ac_clauses(criteria)),
                )
            )
        except HTTPException as exc:
            items.append(
                BatchItemOutcome(
                    jira_ticket_id=ticket_id, saved=False, error=str(exc.detail)
                )
            )
        except Exception as exc:
            # Deliberately broad: one bad ticket must not abort a batch that
            # may already have spent minutes on earlier ones.
            logger.warning("batch: %s failed: %s", ticket_id, exc)
            items.append(
                BatchItemOutcome(
                    jira_ticket_id=ticket_id,
                    saved=False,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    logger.info(
        "batch user=%s run=%s requested=%d saved=%d",
        current_user,
        payload.run_id,
        len(payload.jira_ticket_ids),
        saved,
    )
    return BatchCompareResponse(
        run_id=payload.run_id,
        requested=len(payload.jira_ticket_ids),
        saved=saved,
        items=items,
    )


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[RunSummary]:
    """Saved runs for the current user, newest first."""
    degraded = func.count(EvaluationResult.id).filter(
        EvaluationResult.configured_provider != EvaluationResult.effective_provider
    )
    result = await db.execute(
        select(
            EvaluationResult.run_id,
            func.count(func.distinct(EvaluationResult.item_id)),
            func.count(EvaluationResult.id),
            degraded,
            func.min(EvaluationResult.created_at),
            func.max(EvaluationResult.created_at),
        )
        .where(EvaluationResult.user_id == current_user)
        .group_by(EvaluationResult.run_id)
        .order_by(func.max(EvaluationResult.created_at).desc())
    )
    return [
        RunSummary(
            run_id=run_id,
            item_count=item_count,
            row_count=row_count,
            degraded_rows=degraded_rows or 0,
            first_created_at=first.isoformat() if first else None,
            last_created_at=last.isoformat() if last else None,
        )
        for run_id, item_count, row_count, degraded_rows, first, last in result.all()
    ]


@router.get("/report", response_model=RunReport)
async def run_report(
    run_id: str | None = None,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> RunReport:
    """Aggregated metrics for one run, or every saved run combined.

    Omit `run_id` to pool all of the user's runs — the "final report" across
    several sessions. Degraded rows are excluded from every average and counted
    separately, because averaging in a row the fine-tuned model did not produce
    is precisely how this comparison becomes wrong while looking healthy.
    """
    query = select(EvaluationResult).where(EvaluationResult.user_id == current_user)
    if run_id:
        query = query.where(EvaluationResult.run_id == run_id)

    rows = (await db.execute(query)).scalars().all()
    if not rows:
        detail = (
            f"No results found for run '{run_id}'."
            if run_id
            else "No saved evaluation results yet."
        )
        raise HTTPException(status_code=404, detail=detail)

    scored = [
        score_row(
            {
                "acceptance_criteria": r.acceptance_criteria,
                "scenarios": r.scenarios or [],
                "reference_scenarios": r.reference_scenarios,
                "domain_group": r.domain_group,
                "configured_provider": r.configured_provider,
                "effective_provider": r.effective_provider,
                "succeeded": r.succeeded,
                "latency_seconds": r.latency_seconds,
            }
        )
        for r in rows
    ]

    groups: list[GroupMetrics] = []
    for group in sorted({r["domain_group"] for r in scored}):
        providers: list[ProviderMetrics] = []
        for provider in PROVIDERS:
            cell = [
                r
                for r in scored
                if r["domain_group"] == group
                and r["configured_provider"] == provider
            ]
            if not cell:
                continue
            summary = summarise_group(cell)
            providers.append(
                ProviderMetrics(
                    provider=provider,
                    items_scored=summary["n_trustworthy"],
                    degraded_excluded=summary["n_degraded"],
                    success_rate=summary.get("success_rate"),
                    coverage=summary.get("coverage_mean"),
                    duplicate_rate=summary.get("duplicate_rate_mean"),
                    reference_alignment=summary.get("reference_alignment_mean"),
                    latency_seconds=summary.get("latency_mean_seconds"),
                )
            )
        if providers:
            groups.append(GroupMetrics(domain_group=group, providers=providers))

    notes: list[str] = []
    total_degraded = sum(p.degraded_excluded for g in groups for p in g.providers)
    if total_degraded:
        notes.append(
            f"{total_degraded} row(s) were answered by a model other than the "
            "one configured, and are excluded from these averages."
        )

    return RunReport(
        run_ids=sorted({r.run_id for r in rows}),
        item_count=len({r.item_id for r in rows}),
        row_count=len(rows),
        groups=groups,
        notes=notes,
    )


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> None:
    """Delete every saved row for one run.

    Scoped to the current user, so one user can never delete another's results
    by guessing a run id.
    """
    result = await db.execute(
        delete(EvaluationResult)
        .where(EvaluationResult.run_id == run_id)
        .where(EvaluationResult.user_id == current_user)
    )
    await db.commit()

    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail=f"No run named '{run_id}'.")

    logger.info(
        "deleted run=%s user=%s rows=%d", run_id, current_user, result.rowcount
    )
