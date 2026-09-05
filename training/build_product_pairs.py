"""Turn authored product-domain tickets into complete fine-tuning pairs.

RUN_LOG.md has recorded the same conclusion since Run 1: the model learned the
SHAPE of the task and not the reasoning, because the corpus is Gherkin from
testing frameworks describing CLI invocations rather than product behaviour.
Run 2 established that more data of the same kind changes nothing. The fix is
data from a different domain, and this script is how that data gets in.

It is deliberately NOT build_dataset.py:

    build_dataset.py   real .feature file -> LLM invents the AC -> pair
    this script        authored ticket AND scenarios -> pair, no LLM at all

Nothing here calls a language model. Both sides of every pair are written by a
human in `corpus-product/*.yaml` and are reviewable as prose before they ever
become JSON. That matters more than it sounds: if an LLM wrote the Gherkin, the
fine-tune would be learning to imitate the general model it is supposed to beat,
which caps it at the general model's quality by construction.

    uv run --project backend python training/build_product_pairs.py --check
    uv run --project backend python training/build_product_pairs.py

The output is `<name>.jsonl` beside each source YAML, in exactly the shape
build_dataset.py's `--pairs-dir` consumes: complete pairs that skip
back-generation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ORIGINAL_CWD = Path.cwd()
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# Same reason as build_dataset.py: app.core.config resolves env_file=".env"
# against the working directory, and the repo-root .env holds keys Settings
# rejects as extra fields — printing their values in the ValidationError.
os.chdir(REPO_ROOT / "backend")

import yaml  # noqa: E402

from app.schemas.bdd import BDDGenerateResponse  # noqa: E402
from app.services.bdd_service import BDD_SYSTEM_PROMPT  # noqa: E402

CORPUS_DIR = REPO_ROOT / "training" / "corpus-product"


class AuthoringError(RuntimeError):
    """A mistake in the YAML that would produce a bad training pair."""


def render_ticket(ticket: dict) -> str:
    """The user-side message: the ticket as the application receives it.

    Prose first, then the numbered criteria. The prose is what makes this
    realistic — real tickets carry a story and a description, and the existing
    corpus's input side is a bare "AC1: ... AC2: ..." block that no Jira ticket
    has ever looked like.

    The numbered list is not decoration either. `ac_clauses()` in
    app/services/evaluation_metrics.py finds clauses by matching "AC<n>" with a
    separator; a ticket written as pure prose yields ZERO clauses, which makes
    coverage() return None for every item and silently disables the metric that
    currently carries the best result on record.
    """
    criteria = ticket["acceptance_criteria"]
    lines = [
        ticket["title"],
        "",
        f"As a {ticket['as_a']}",
        f"I want {ticket['i_want']}",
        f"So that {ticket['so_that']}",
        "",
        " ".join(str(ticket["description"]).split()),
        "",
        "Acceptance Criteria:",
    ]
    lines += [f"AC{n}: {text}" for n, text in enumerate(criteria, start=1)]
    return "\n".join(lines)


def build_scenarios(ticket: dict) -> list[dict]:
    """The assistant-side scenarios, with traceability carrying both halves.

    `source_ac_clause` is "AC3: <the criterion>" — label and sentence together.
    The label is what the coverage metric matches on; the sentence is what makes
    the rendered Gherkin comment readable. Emitting only the sentence (as the
    original sample did) parses as no clause at all.
    """
    criteria = ticket["acceptance_criteria"]
    scenarios = []
    for entry in ticket["scenarios"]:
        index = entry["clause"]
        if not 1 <= index <= len(criteria):
            raise AuthoringError(
                f"{ticket['id']}: scenario '{entry['scenario']}' cites AC{index}, "
                f"but the ticket declares {len(criteria)} criteria"
            )
        scenarios.append(
            {
                "source_ac_clause": f"AC{index}: {criteria[index - 1]}",
                "feature": ticket["feature"],
                "scenario": entry["scenario"],
                "given": entry["given"],
                "when": entry["when"],
                "then": entry["then"],
            }
        )
    return scenarios


def check_ticket(ticket: dict) -> list[str]:
    """Authoring problems that make a pair a worse example than no pair.

    Returns warnings rather than raising: none of these produce an invalid
    record, they produce a MISLEADING one, and the author is the right person
    to judge which.
    """
    warnings: list[str] = []
    criteria = ticket["acceptance_criteria"]
    cited = {entry["clause"] for entry in ticket["scenarios"]}

    # An uncited criterion teaches the model that leaving a clause uncovered is
    # acceptable, and it caps the reference's own coverage below 1.0 — which is
    # exactly how the human references ended up scoring 0.974 rather than 1.000.
    uncovered = [n for n in range(1, len(criteria) + 1) if n not in cited]
    if uncovered:
        warnings.append(
            f"{ticket['id']}: AC{', AC'.join(str(n) for n in uncovered)} "
            "has no scenario — the reference cannot score 1.0 coverage"
        )

    # A `then` that restates its criterion is the "fluent form, thin substance"
    # defect RUN_LOG names. This catches only the blatant case (the outcome is
    # literally the criterion), which is still worth catching.
    for entry in ticket["scenarios"]:
        outcome = " ".join(str(entry["then"]).lower().split())
        criterion = " ".join(str(criteria[entry["clause"] - 1]).lower().split())
        if outcome.rstrip(".") in criterion.rstrip("."):
            warnings.append(
                f"{ticket['id']}: '{entry['scenario']}' has a `then` that "
                "restates its criterion instead of stating an outcome"
            )

    if len(ticket["scenarios"]) < 2:
        warnings.append(f"{ticket['id']}: only one scenario")

    return warnings


def build_records(path: Path) -> tuple[list[dict], list[str]]:
    """Every pair from one YAML file, plus the authoring warnings it raised."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    records: list[dict] = []
    warnings: list[str] = []

    for ticket in document["tickets"]:
        warnings += check_ticket(ticket)
        target = {"scenarios": build_scenarios(ticket)}

        # The application must be able to parse anything we train it to emit.
        validated = BDDGenerateResponse.model_validate(target)
        if not validated.scenarios:
            raise AuthoringError(f"{ticket['id']}: no scenarios")

        records.append(
            {
                "messages": [
                    {"role": "system", "content": BDD_SYSTEM_PROMPT},
                    {"role": "user", "content": render_ticket(ticket)},
                    {"role": "assistant", "content": validated.model_dump_json()},
                ],
                # origin drives the train/holdout split, which is by SOURCE FILE
                # and never per row. One ticket is one origin: its scenarios are
                # related, and splitting them across the boundary would leak.
                "meta": {
                    "origin": f"{path.stem}/{ticket['id']}",
                    "scenario_count": len(validated.scenarios),
                    "provenance": ticket.get("source", "authored"),
                },
            }
        )

    return records, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus-dir",
        default=str(CORPUS_DIR),
        help="Directory of authored ticket YAML files",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate and report without writing any .jsonl",
    )
    args = parser.parse_args()

    corpus = Path(args.corpus_dir)
    if not corpus.is_absolute():
        corpus = (ORIGINAL_CWD / corpus).resolve()

    sources = sorted(corpus.glob("*.yaml"))
    if not sources:
        print(f"No ticket YAML found in {corpus}", file=sys.stderr)
        return 1

    total = 0
    all_warnings: list[str] = []
    for path in sources:
        try:
            records, warnings = build_records(path)
        except (AuthoringError, KeyError) as exc:
            print(f"\n{path.name}: {exc}", file=sys.stderr)
            return 2

        all_warnings += warnings
        scenarios = sum(r["meta"]["scenario_count"] for r in records)
        total += len(records)
        print(f"{path.name}: {len(records)} tickets, {scenarios} scenarios")

        if not args.check:
            out = path.with_suffix(".jsonl")
            out.write_text(
                "".join(
                    json.dumps(r, ensure_ascii=False) + "\n" for r in records
                ),
                encoding="utf-8",
            )
            print(f"  -> {out.name}")

    if all_warnings:
        print(f"\n{len(all_warnings)} authoring warning(s):")
        for warning in all_warnings:
            print(f"  ! {warning}")

    print(f"\n{total} pairs total")
    if args.check:
        print("--check: nothing written")
    else:
        print(
            "Merge them into the training set with:\n"
            "  uv run --project backend python training/build_dataset.py \\\n"
            "      --pairs-dir training/corpus-product --out training/data"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
