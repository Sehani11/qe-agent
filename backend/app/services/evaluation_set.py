"""Evaluation-set assembly for Story 6.3.

Epic 6.3 opens *"Given a set of 20+ real Jira tickets with known acceptance
criteria"*. Measured 2026-08-13, that is unsatisfied: `bdd_files` holds **zero**
rows with `acceptance_criteria` (Story 6.4's capture has never fired) and only
five distinct Jira tickets have ever been ingested. So the set is assembled from
what actually exists, and this module's job is to keep the pieces honestly
labelled rather than to pretend they are equivalent.

Two groups, which must be reported SEPARATELY:

  * **off_domain** — Story 6.5's holdout (18 items at Run 1; it tracks the
    corpus, so a rebuild changes the count). Carries human-authored reference
    Gherkin, so reference-similarity scoring applies. But the ACs were
    LLM-back-generated, and the corpus is 84% testing-framework Gherkin about
    running CLI commands. Split by origin file, so the fine-tune never saw it.

  * **on_domain** — curated product-behaviour ACs of the kind this application
    converts from Jira tickets. No reference Gherkin, so only the structural
    metrics and the blinded judge apply.

Averaging the two produces a number that describes neither. The *gap* between
them is the more interesting measurement: it is the difference between testing
the fine-tune on its training distribution and testing it on the one the
product actually operates in.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
CURATED_PATH = REPO_ROOT / "evaluation" / "curated_ac_sets.json"
HOLDOUT_PATH = REPO_ROOT / "training" / "data" / "holdout.jsonl"

DOMAIN_GROUPS = frozenset({"on_domain", "off_domain"})

#: How the acceptance-criteria text came to exist. This is the single most
#: load-bearing label in the evaluation: `back_generated` ACs were reconstructed
#: from Gherkin by an LLM, so they describe the reference by construction and
#: flatter any model that reproduces it. `agent_authored` ACs were written for
#: this evaluation and are not real tickets. Only `human_jira` is the thing the
#: epic actually asked for, and there are none yet.
PROVENANCES = frozenset(
    {"back_generated", "agent_authored", "human_authored", "human_jira"}
)

# Real acceptance criteria are not uniformly formatted. Under-counting clauses
# would silently depress the coverage metric for BOTH providers and make the
# comparison noisier, so tolerate spacing and separator variance.
_AC_CLAUSE_RE = re.compile(r"\bAC\s*(\d+)\s*[:\-\.]", re.IGNORECASE)


@dataclass(frozen=True)
class EvalItem:
    """One acceptance-criteria set to generate BDD scenarios from."""

    id: str
    acceptance_criteria: str
    domain_group: str
    provenance: str
    reference_scenarios: list[dict[str, Any]] | None = None
    origin: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.domain_group not in DOMAIN_GROUPS:
            raise ValueError(
                f"domain_group must be one of {sorted(DOMAIN_GROUPS)}, "
                f"got {self.domain_group!r}"
            )
        if self.provenance not in PROVENANCES:
            raise ValueError(
                f"provenance must be one of {sorted(PROVENANCES)}, "
                f"got {self.provenance!r}"
            )

    @property
    def has_reference(self) -> bool:
        """Whether reference-similarity scoring can be applied to this item."""
        return bool(self.reference_scenarios)


def ac_clauses(acceptance_criteria: str) -> list[str]:
    """The distinct AC clause labels, in order of first appearance.

    This is the denominator of the coverage metric: the fraction of clauses
    that the model produced at least one scenario for. It needs no LLM and
    cannot be argued with, which is why AC5 makes it the backbone.
    """
    seen: list[str] = []
    for number in _AC_CLAUSE_RE.findall(acceptance_criteria):
        label = f"AC{int(number)}"
        if label not in seen:
            seen.append(label)
    return seen


def load_curated_items() -> list[EvalItem]:
    """Product-domain ACs for the on-domain group (no reference Gherkin).

    Each entry may declare its own `provenance`. It defaults to
    `agent_authored`, which is what the seed items are, so an entry that says
    nothing is never silently upgraded to something stronger. An item taken
    from a real ticket should set `"provenance": "human_jira"` — that is the
    label the epic actually asked for, and hardcoding `agent_authored` here
    used to make it unreachable, so real tickets would have been filed under a
    label that understated them.
    """
    payload = json.loads(CURATED_PATH.read_text(encoding="utf-8"))
    return [
        EvalItem(
            id=entry["id"],
            acceptance_criteria=entry["acceptance_criteria"],
            domain_group="on_domain",
            provenance=entry.get("provenance", "agent_authored"),
            meta={"domain": entry.get("domain", "")},
        )
        for entry in payload["items"]
    ]


def load_holdout_items() -> list[EvalItem]:
    """Story 6.5's holdout: back-generated ACs with human-authored references.

    Returns [] when the file is absent — it is git-ignored derived data, so a
    fresh checkout will not have it. Callers must treat an empty result as
    "rebuild the dataset", never as "the holdout scored nothing".
    """
    if not HOLDOUT_PATH.exists():
        return []

    items: list[EvalItem] = []
    for index, line in enumerate(
        HOLDOUT_PATH.read_text(encoding="utf-8").splitlines()
    ):
        if not line.strip():
            continue
        record = json.loads(line)
        messages = {m["role"]: m["content"] for m in record["messages"]}
        reference = json.loads(messages["assistant"])
        origin = record["meta"]["origin"]
        items.append(
            EvalItem(
                id=f"holdout-{index + 1:02d}-{Path(origin).stem}",
                acceptance_criteria=messages["user"],
                domain_group="off_domain",
                provenance="back_generated",
                reference_scenarios=reference.get("scenarios", []),
                origin=origin,
                meta={"source_repo": _corpus_repo(origin)},
            )
        )
    return items


def _corpus_repo(origin: str) -> str:
    """Which cloned repository a holdout item came from, for grouping."""
    normalised = origin.replace("\\", "/")
    marker = "/training/corpus/"
    if marker in normalised:
        return normalised.split(marker, 1)[1].split("/", 1)[0]
    return "unknown"


def load_evaluation_set() -> list[EvalItem]:
    """The full set: curated on-domain items plus the off-domain holdout.

    AC1 requires >= 20 items. The caller is responsible for enforcing that gate
    and reporting a shortfall rather than padding — this function reports what
    exists and does not manufacture anything.
    """
    return load_curated_items() + load_holdout_items()
