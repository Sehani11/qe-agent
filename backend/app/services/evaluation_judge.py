"""Blinded subjective scoring for Story 6.3 (AC6).

The obvious way to score "AC relevance" and "correctness" is LLM-as-judge. The
problem is that the general LLM is a **contestant**. Asking it to grade its own
output against a competitor's is not a measurement, and it is the kind of flaw
that invalidates a research contribution rather than merely weakening it.

This module does what can be done about that:

  * **Blinding.** Outputs are stripped of every provenance marker and presented
    as "System A" / "System B".
  * **Randomised order.** The mapping from label to provider is drawn per item,
    so a judge with a positional bias cannot systematically favour one system.
  * **Recorded judge.** The judging model identifier is stored with the score,
    so the result can be re-run against a different judge later.

What it does NOT do is make a contestant an impartial judge. If the judge model
belongs to a contestant, `judge_conflict` says so and the report must carry it.
The strongest available fix is a judge from an unrelated provider; that is a
configuration choice, not something this module can enforce.
"""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import mean
from typing import Any

JUDGE_SYSTEM_PROMPT = """You are grading two sets of BDD test scenarios that \
were generated from the same acceptance criteria.

Score each system from 1 to 5 on:
- ac_relevance: do the scenarios actually test what the acceptance criteria say?
- correctness: are Given/When/Then steps coherent, specific, and in the right order?
- specificity: do the steps reference concrete conditions rather than restating \
the criteria in vague terms?

Return ONLY JSON:
{"A": {"ac_relevance": n, "correctness": n, "specificity": n, "note": "..."},
 "B": {"ac_relevance": n, "correctness": n, "specificity": n, "note": "..."}}

Judge only what is in front of you. The two systems are anonymous and their \
order is random; nothing about which is which is inferable or relevant."""

_PROVENANCE_KEYS = {"provider", "model", "model_identifier", "configured_provider",
                    "effective_provider", "run_id", "item_id"}


@dataclass(frozen=True)
class BlindedComparison:
    """One anonymised A/B pair, plus the key needed to un-blind it later."""

    item_id: str
    acceptance_criteria: str
    payload: dict[str, Any]
    #: label -> provider. Kept OUT of `payload`; the judge never sees it.
    key: dict[str, str]


def _strip_provenance(scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove anything that could reveal which system produced this."""
    return [
        {k: v for k, v in scenario.items() if k not in _PROVENANCE_KEYS}
        for scenario in scenarios
    ]


def build_blinded_comparison(
    item_id: str,
    acceptance_criteria: str,
    outputs: dict[str, list[dict[str, Any]]],
    rng: random.Random | None = None,
) -> BlindedComparison:
    """Anonymise two providers' outputs into a randomised A/B payload.

    `outputs` maps provider name -> scenarios. Exactly two are expected.
    """
    if len(outputs) != 2:
        raise ValueError(f"expected exactly 2 systems to compare, got {len(outputs)}")

    rng = rng or random.Random()
    providers = list(outputs)
    rng.shuffle(providers)
    labels = ["A", "B"]

    return BlindedComparison(
        item_id=item_id,
        acceptance_criteria=acceptance_criteria,
        payload={
            "acceptance_criteria": acceptance_criteria,
            **{
                label: {"scenarios": _strip_provenance(outputs[provider])}
                for label, provider in zip(labels, providers, strict=True)
            },
        },
        key=dict(zip(labels, providers, strict=True)),
    )


def unblind_scores(
    scores: dict[str, Any], key: dict[str, str]
) -> dict[str, dict[str, Any]]:
    """Map A/B scores back onto provider names after judging."""
    return {
        provider: scores[label] for label, provider in key.items() if label in scores
    }


def judge_conflict(judge_model: str, contestant_models: list[str]) -> str | None:
    """Describe the conflict of interest, or None if the judge is independent.

    Returned rather than raised: an evaluation run with a compromised judge is
    still worth having, provided the compromise is stated. Silently proceeding
    is what would not be.
    """
    for contestant in contestant_models:
        if not contestant:
            continue
        if judge_model == contestant:
            return (
                f"The judge ({judge_model}) is ALSO a contestant. It is grading its "
                "own output against a competitor's. Treat the subjective scores as "
                "indicative only; the objective metrics carry the comparison."
            )
        # Same family, e.g. gpt-4o judging gpt-4o-mini, or a fine-tune of the judge.
        head = contestant.split("-")[0].split(":")[0]
        if head and (judge_model.startswith(head) or head in judge_model):
            return (
                f"The judge ({judge_model}) shares a model family with a contestant "
                f"({contestant}). Family-level preference bias cannot be ruled out."
            )
    return None


async def judge_comparison(comparison: BlindedComparison) -> dict[str, Any] | None:
    """Score one blinded A/B pair, returning provider-keyed scores.

    Returns None on any judge failure. A judging pass that silently drops items
    is preferable to one that fabricates scores, and the report states how many
    items were judged so a thin sample cannot masquerade as a full one.
    """
    from app.services.llm.factory import get_llm_provider

    provider = get_llm_provider()
    try:
        raw = await provider.generate(
            prompt=json.dumps(comparison.payload, indent=1),
            system_prompt=JUDGE_SYSTEM_PROMPT,
        )
        return unblind_scores(parse_judge_response(raw), comparison.key)
    except Exception:
        return None


def aggregate_judge_scores(
    per_item: Sequence[dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Mean of each criterion per provider across judged items."""
    criteria = ("ac_relevance", "correctness", "specificity")
    totals: dict[str, dict[str, list[float]]] = {}
    for scores in per_item:
        for provider, values in scores.items():
            bucket = totals.setdefault(provider, {c: [] for c in criteria})
            for criterion in criteria:
                value = values.get(criterion)
                if isinstance(value, int | float):
                    bucket[criterion].append(float(value))
    return {
        provider: {
            criterion: round(mean(values), 3)
            for criterion, values in buckets.items()
            if values
        }
        for provider, buckets in totals.items()
    }


def parse_judge_response(raw: str) -> dict[str, Any]:
    """Extract the JSON verdict, tolerating a code fence or preamble."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("judge returned no JSON object")
    parsed = json.loads(raw[start : end + 1])
    if not {"A", "B"} <= set(parsed):
        raise ValueError("judge response missing an A or B verdict")
    return parsed
