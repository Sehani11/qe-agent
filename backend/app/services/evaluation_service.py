"""Evaluation runner for Story 6.3 — fine-tuned vs general LLM.

Generates BDD scenarios for every evaluation item with every configured
provider, records what happened, and refuses to summarise a run that silently
degraded.

**The failure this module exists to prevent.** `FineTunedModelProvider` bounds
its call at `FINE_TUNED_MODEL_TIMEOUT_SECONDS` (12s) and, on expiry, returns
general-LLM output that is byte-identical in shape. Story 6.2 measured a
fine-tuned generation at **45s** on non-GPU hardware. So the default
configuration turns this entire evaluation into general-versus-general while
every row looks healthy. Two defences:

  * every generation records `effective_provider`, read off the provider
    instance that Story 6.1 built for exactly this purpose; and
  * `assert_run_is_trustworthy` refuses a run where a large fraction of
    fine-tuned rows were served by the fallback.

Raise the budget with the `FINE_TUNED_MODEL_TIMEOUT_SECONDS` env var **for the
evaluation run only** — never in a committed file. It is 12s in production so
the general-LLM fallback still fits inside NFR-P3's 30s, and this batch job is
not bound by NFR-P3 at all.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.evaluation_result import EvaluationResult
from app.schemas.bdd import BDDGenerateResponse
from app.services.bdd_model.factory import get_bdd_model_provider
from app.services.bdd_service import BDD_SYSTEM_PROMPT
from app.services.evaluation_set import EvalItem
from app.services.llm.claude_provider import DEFAULT_CLAUDE_MODEL

logger = logging.getLogger(__name__)

#: Above this share of degraded fine-tuned rows the run is refused outright.
#: Below it, the rows are excluded from metrics and the exclusion is reported.
#: One flake should not discard a 30-minute batch; a systematically degraded
#: run must never be presented as a comparison.
MAX_DEGRADED_FRACTION = 0.2


class DegradedRunError(RuntimeError):
    """Raised when too many generations were served by the fallback.

    This is deliberately fatal. A summary produced from these rows would be a
    confident, well-formatted answer to a different question than the one asked.
    """


@dataclass
class GenerationOutcome:
    """What happened for one (item, provider) pair."""

    item_id: str
    configured_provider: str
    effective_provider: str | None = None
    fallback_reason: str | None = None
    model_identifier: str = ""
    scenarios: list[dict[str, Any]] | None = None
    latency_seconds: float | None = None
    succeeded: bool = True
    error: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_trustworthy(self) -> bool:
        """Whether this outcome measures the provider it claims to."""
        return (
            self.succeeded
            and self.effective_provider is not None
            and self.effective_provider == self.configured_provider
        )


@contextmanager
def provider_selected(
    provider_name: str, *, allow_fallback: bool | None = None
) -> Iterator[Any]:
    """Yield the provider for `provider_name`, via the factory.

    NFR-R6 makes `bdd_model/factory.py` the ONLY place provider selection may
    live, so this evaluation — which deliberately runs both — reuses the factory
    rather than keeping its own registry that could drift from it.

    This used to swap `settings.bdd_model_provider` around the call and restore
    it in a `finally`. The factory now takes the provider as an argument, so
    nothing global moves: a leaked value could have made every later call in the
    process silently evaluate the wrong model, and concurrent callers could not
    have used this at all.
    """
    yield get_bdd_model_provider(
        provider=provider_name, allow_fallback=allow_fallback
    )


def general_llm_model_name() -> str:
    """The model the general-LLM baseline actually uses.

    NOT simply `settings.llm_model`: that field is documented as "OpenAI model
    name", so under any non-OpenAI provider reading it unconditionally
    mislabels both the baseline and the judge — recording a comparison against
    a model that never ran.

    This reports the DEFAULTS, which is correct for the evaluation runner: it
    has no request behind it, so it gets exactly what the factory would build
    from settings. It does not know about a per-request model override.
    """
    provider = (settings.llm_provider or "").lower()
    if provider == "claude":
        return DEFAULT_CLAUDE_MODEL
    if provider in {"local", "ollama"}:
        return settings.ollama_model
    return settings.llm_model


def _model_identifier(provider_name: str) -> str:
    """What was actually being measured, recorded so a run stays interpretable."""
    if provider_name == "fine_tuned":
        import os

        return os.environ.get("FINE_TUNED_OLLAMA_MODEL", "bdd-lora")
    return general_llm_model_name()


async def generate_one(
    item: EvalItem, provider_name: str, *, allow_fallback: bool | None = None
) -> GenerationOutcome:
    """Generate scenarios for one item with one provider.

    Never raises for a model-side problem: a 30-minute batch must not lose
    completed work to a single bad item. Failures become failed rows.

    `allow_fallback=False` makes a fine-tuned failure a FAILED row instead of a
    silently-substituted general-LLM row. A single interactive comparison wants
    the failure immediately, and without the cost of a fallback call it
    discards. `None` defers to `FINE_TUNED_ALLOW_FALLBACK`; the batch runner
    leaves it there and filters degraded rows afterwards, which still works
    whichever way the setting is configured.
    """
    outcome = GenerationOutcome(
        item_id=item.id,
        configured_provider=provider_name,
        model_identifier=_model_identifier(provider_name),
    )

    started = time.monotonic()
    provider = None
    try:
        with provider_selected(
            provider_name, allow_fallback=allow_fallback
        ) as provider:
            raw = await provider.generate_bdd(
                acceptance_criteria=item.acceptance_criteria,
                system_prompt=BDD_SYSTEM_PROMPT,
                response_format=BDDGenerateResponse.model_json_schema(),
            )

        validated = BDDGenerateResponse.model_validate(raw)

        # An empty array VALIDATES — `scenarios` defaults to [] — so a body like
        # {"wrong_key": []} arrives here looking like a successful generation of
        # nothing. Scoring that as 0% coverage would average a MALFORMED
        # response together with a model that genuinely declined; counting it as
        # a failed generation keeps the two apart. The report surfaces it as a
        # per-provider generation success rate, so a model that often returns
        # nothing cannot hide behind good scores on the times it does answer.
        if not validated.scenarios:
            outcome.succeeded = False
            outcome.error = "returned zero scenarios"
        else:
            outcome.scenarios = [s.model_dump() for s in validated.scenarios]
            outcome.succeeded = True

    except ValidationError as exc:
        outcome.succeeded = False
        outcome.error = (
            f"response did not match BDDGenerateResponse: "
            f"{exc.error_count()} errors"
        )
    except Exception as exc:
        outcome.succeeded = False
        outcome.error = f"{type(exc).__name__}: {exc}"
    finally:
        outcome.latency_seconds = round(time.monotonic() - started, 3)
        # Capture attribution in the `finally`, not after the await. Review
        # finding: reading it inline meant a provider that RAISED persisted
        # effective_provider=NULL — losing the record of whether the fine-tune
        # or its fallback was the thing that broke, which is precisely what a
        # failed row most needs to say.
        if provider is not None:
            outcome.effective_provider = provider.effective_provider
            outcome.fallback_reason = provider.fallback_reason

    return outcome


def pending_work(
    items: Sequence[EvalItem],
    providers: Sequence[str],
    completed: set[tuple[str, str]],
) -> list[tuple[str, str]]:
    """The (item_id, provider) pairs still to run.

    Resumability is an acceptance criterion rather than a nicety: at ~45s per
    fine-tuned generation, a run that loses everything to a transient error
    will be run once and then trusted less than it deserves.
    """
    return [
        (item.id, provider)
        for item in items
        for provider in providers
        if (item.id, provider) not in completed
    ]


async def completed_keys(run_id: str, db: AsyncSession) -> set[tuple[str, str]]:
    """(item_id, provider) pairs already persisted AND worth keeping.

    Only rows that succeeded *and* were served by the provider they claim are
    treated as done. Review finding: filtering on presence alone meant a failed
    or fallback-served pair was skipped forever, so `--resume` permanently
    locked in exactly the rows a rerun exists to replace. Re-running them is
    safe because `persist_outcome` upserts.
    """
    rows = await db.execute(
        select(
            EvaluationResult.item_id, EvaluationResult.configured_provider
        ).where(
            EvaluationResult.run_id == run_id,
            EvaluationResult.succeeded.is_(True),
            EvaluationResult.effective_provider == EvaluationResult.configured_provider,
        )
    )
    return {(item_id, provider) for item_id, provider in rows.all()}


async def persist_outcome(
    outcome: GenerationOutcome,
    item: EvalItem,
    run_id: str,
    db: AsyncSession,
    user_id: str | None = None,
) -> None:
    """UPSERT one result row and commit it immediately.

    Committed per row on purpose: this is a long batch, and a crash at item 25
    must not discard the first 24. That is what makes `--resume` meaningful
    rather than decorative.

    An upsert rather than an insert, because the unique index on
    (run_id, item_id, configured_provider) otherwise makes every rerun of a
    pair a crash — which broke `--no-resume` outright and made retrying a
    failed pair impossible. `attempt` increments so a row records how many
    tries it took, instead of being written as 1 forever.
    """
    values = {
        "run_id": run_id,
        "user_id": user_id or settings.dev_user_id,
        "item_id": item.id,
        "domain_group": item.domain_group,
        "provenance": item.provenance,
        "acceptance_criteria": item.acceptance_criteria,
        "configured_provider": outcome.configured_provider,
        "effective_provider": outcome.effective_provider,
        "fallback_reason": outcome.fallback_reason,
        "model_identifier": outcome.model_identifier,
        "scenarios": outcome.scenarios,
        "reference_scenarios": item.reference_scenarios,
        "latency_seconds": outcome.latency_seconds,
        "succeeded": outcome.succeeded,
        "error": outcome.error,
    }
    statement = pg_insert(EvaluationResult).values(**values)
    statement = statement.on_conflict_do_update(
        index_elements=["run_id", "item_id", "configured_provider"],
        set_={
            **{k: statement.excluded[k] for k in values if k != "run_id"},
            "attempt": EvaluationResult.__table__.c.attempt + 1,
        },
    )
    await db.execute(statement)
    await db.commit()


async def run_evaluation(
    items: Sequence[EvalItem],
    providers: Sequence[str],
    run_id: str,
    session_factory: Callable[[], AsyncSession],
    resume: bool = True,
) -> list[GenerationOutcome]:
    """Generate for every pending (item, provider) pair, persisting as it goes.

    Sequential by design: a fine-tuned generation saturates the CPU on this
    hardware, so concurrency would slow the whole batch down rather than speed
    it up, and would make the latency measurements meaningless.

    Takes a session FACTORY, not a session. A single session held across the
    whole batch is dropped by the pooler while a generation runs — measured on
    2026-08-13, when one holdout item took 40 minutes and the row was lost to
    `ConnectionDoesNotExistError` mid-INSERT. Each write therefore gets its own
    short-lived session, which is what makes `--resume` actually able to resume.
    """
    async with session_factory() as db:
        done = await completed_keys(run_id, db) if resume else set()

    pending = pending_work(items, providers, done)
    by_id = {item.id: item for item in items}

    if done:
        logger.info("evaluation: resuming run %s, %d already done", run_id, len(done))
    logger.info("evaluation: %d generations to run", len(pending))

    outcomes: list[GenerationOutcome] = []
    for index, (item_id, provider_name) in enumerate(pending, start=1):
        item = by_id[item_id]
        logger.info(
            "evaluation [%d/%d] %s via %s", index, len(pending), item_id, provider_name
        )
        outcome = await generate_one(item, provider_name)
        # Fresh session per write: the generation above may have taken tens of
        # minutes, by which point any session opened before it is dead.
        async with session_factory() as db:
            await persist_outcome(outcome, item, run_id, db)
        outcomes.append(outcome)

        if not outcome.is_trustworthy:
            logger.warning(
                "evaluation: %s via %s -> effective=%s reason=%s error=%s",
                item_id,
                provider_name,
                outcome.effective_provider,
                outcome.fallback_reason,
                outcome.error,
            )

    return outcomes


def assert_run_is_trustworthy(outcomes: Sequence[GenerationOutcome]) -> None:
    """Refuse a run that was mostly served by the fallback.

    Raises DegradedRunError above MAX_DEGRADED_FRACTION. Below it, logs the
    exclusions — the metrics drop those rows and the report states it.
    """
    configured_ft = [o for o in outcomes if o.configured_provider == "fine_tuned"]
    if not configured_ft:
        return

    degraded = [
        o for o in configured_ft if o.succeeded and o.effective_provider != "fine_tuned"
    ]
    if not degraded:
        return

    fraction = len(degraded) / len(configured_ft)
    if fraction > MAX_DEGRADED_FRACTION:
        reasons = sorted({o.fallback_reason or "unknown" for o in degraded})
        raise DegradedRunError(
            f"{len(degraded)} of {len(configured_ft)} fine-tuned generations were "
            f"served by the FALLBACK ({fraction:.0%}), reasons={reasons}. "
            "This run compares the general LLM with itself. Most likely cause: "
            "FINE_TUNED_MODEL_TIMEOUT_SECONDS is 12s and a fine-tuned generation "
            "takes ~45s on non-GPU hardware — raise it via the env var for this "
            "run, and confirm the shim is up."
        )

    logger.warning(
        "evaluation: %d of %d fine-tuned rows degraded (%.0f%%); excluded from metrics",
        len(degraded),
        len(configured_ft),
        fraction * 100,
    )
