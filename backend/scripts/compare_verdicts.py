"""Compare a session's verdicts across repeated runs.

Verification results accumulate per session rather than replacing each other,
so a session verified several times already holds several sets of verdicts for
the same scenarios. That makes the accuracy check cheap: no re-run needed, just
read what is already stored.

This is the check that matters when changing anything about how verification
finds or reads code — the repository tree format, the code index, tool-result
elision, the model. Token counts are easy to measure and easy to be misled by;
what must not change is the verdict. A scenario that moves from "pass" to
"inconclusive" has lost a usable answer, and one that moves to "fail" may be
sending someone to rewrite working code.

Read-only.

    python -m scripts.compare_verdicts <session-id>
    python -m scripts.compare_verdicts <session-id> --gap 300
"""

import argparse
import asyncio
import sys
from collections import defaultdict

from sqlalchemy import select

from app.core.database import async_session_factory
from app.models.verification_result import VerificationResult

def _group_into_runs(rows: list) -> list[list]:
    """Split time-ordered rows into runs, on the repeat of a scenario title.

    A run verifies each scenario exactly once — duplicates are dropped before
    any LLM call — so seeing a title a second time means a new run began. That
    is a fact about the data rather than a guess about it.

    A time gap was the obvious first idea and it is not reliable: two runs
    started a minute apart merge into one, and one slow run splits into two.
    Both failures are silent, and both corrupt exactly the comparison this
    script exists to make — a merged pair reports whichever verdict happened to
    land last and hides the disagreement.
    """
    runs: list[list] = [[]]
    seen: set[str] = set()
    for row in rows:
        if row.scenario_title in seen:
            runs.append([])
            seen = set()
        runs[-1].append(row)
        seen.add(row.scenario_title)
    return runs


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_id", help="The session to compare runs within.")
    parser.add_argument(
        "--min-run",
        type=int,
        default=2,
        help=(
            "Ignore runs with fewer verdicts than this when comparing "
            "(default 2). A run stopped early after one or two scenarios says "
            "nothing about agreement and would otherwise show every other "
            "scenario as '(absent)'."
        ),
    )
    args = parser.parse_args()

    async with async_session_factory() as db:
        rows = list(
            (
                await db.execute(
                    select(VerificationResult)
                    .where(VerificationResult.session_id == args.session_id)
                    .order_by(VerificationResult.created_at)
                )
            )
            .scalars()
            .all()
        )

    if not rows:
        print(f"No verification results stored for session {args.session_id}.")
        return 1

    all_runs = _group_into_runs(rows)
    print(
        f"session {args.session_id}: {len(rows)} verdicts across "
        f"{len(all_runs)} run(s)\n"
    )
    for index, run in enumerate(all_runs, start=1):
        counts: dict[str, int] = defaultdict(int)
        for row in run:
            counts[row.status] += 1
        stamp = run[0].created_at.strftime("%Y-%m-%d %H:%M")
        tally = "  ".join(f"{status}={n}" for status, n in sorted(counts.items()))
        skipped = "  (too short — not compared)" if len(run) < args.min_run else ""
        print(
            f"  run {index}  {stamp}  {len(run):>3} verdicts  {tally}{skipped}"
        )

    runs = [run for run in all_runs if len(run) >= args.min_run]
    if len(runs) < 2:
        print("\nFewer than two comparable runs stored — nothing to compare yet.")
        return 0

    by_scenario: dict[str, list[str | None]] = defaultdict(
        lambda: [None] * len(runs)
    )
    for index, run in enumerate(runs):
        for row in run:
            by_scenario[row.scenario_title][index] = row.status

    changed = {
        title: statuses
        for title, statuses in by_scenario.items()
        # Runs a scenario was absent from are not disagreement — a run may have
        # been stopped early, or the BDD may have gained scenarios since.
        if len({status for status in statuses if status}) > 1
    }

    print(
        f"\nscenarios whose verdict CHANGED between runs: "
        f"{len(changed)} of {len(by_scenario)}"
    )
    for title, statuses in sorted(changed.items()):
        print(f"  {title}")
        print(f"      {' -> '.join(s or '(absent)' for s in statuses)}")
    if not changed:
        print("  none — every scenario reached the same verdict in every run.")

    # Non-zero when verdicts disagree, so this can gate a change in CI.
    return 2 if changed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
