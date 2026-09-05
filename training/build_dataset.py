"""Build a supervised fine-tuning dataset of (acceptance criteria -> Gherkin) pairs.

Story 6.2 needs a fine-tuned BDD model, and a fine-tune needs paired data that
this project does not yet capture: `sessions` stores only `jira_ticket_id`, and
editor corrections are never persisted. This script closes that gap by working
BACKWARDS from Gherkin that already exists.

    real .feature file  ->  LLM writes the AC that would produce it  ->  pair

The side the model learns to EMIT is therefore genuine human-authored Gherkin,
not model output imitating model output. Only the input side is synthesised.

Two sources:
  * --features-dir : local directories of .feature files (clone Cucumber/SpecFlow
                     repos yourself; harvesting is deliberately not automated so
                     licensing stays a human decision)
  * --from-db      : `bdd_files` rows, preferring source='uploaded' (human authored)

Every training target is validated against `BDDGenerateResponse` before it is
written, so the dataset can only contain shapes the application can actually
parse. Empty `scenarios` arrays are rejected: they validate successfully against
the Pydantic model (the field defaults to []) and would silently teach the model
to return nothing.

Usage:
    # No LLM or DB needed - parse, filter and report what you have
    uv run --project backend python training/build_dataset.py \
        --features-dir ./corpus --dry-run

    # Full build (uses LLM_PROVIDER and that vendor's key from .env)
    uv run --project backend python training/build_dataset.py \
        --features-dir ./corpus --from-db --out training/data
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ORIGINAL_CWD = Path.cwd()
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

# app.core.config declares env_file=".env", resolved against the CURRENT working
# directory. The backend's own .env lives in backend/; the repo-root .env holds
# frontend and AWS keys that Settings rejects as extra fields - and the resulting
# ValidationError prints their values. Run from backend/ so the right file wins.
os.chdir(REPO_ROOT / "backend")

from app.schemas.bdd import BDDGenerateResponse  # noqa: E402
from app.services.bdd_service import BDD_SYSTEM_PROMPT  # noqa: E402

# The Gherkin parser and quality thresholds live in the BACKEND, not here, so
# that the upload endpoint (Story 6.7) accepts exactly what this script keeps.
# Two copies of these rules means the app accepts files the builder discards.
from app.services.training_data_service import (  # noqa: E402
    FeatureDoc,
    TrainingDataError,
    feature_doc_from_json,
    pair_fingerprint,
    parse_feature,
    parse_jsonl_records,
    quality_reason,
)


def resolve_path(value: str) -> Path:
    """Resolve a CLI path against the directory the user actually ran from."""
    path = Path(value)
    return path if path.is_absolute() else (ORIGINAL_CWD / path).resolve()

BACKGEN_SYSTEM_PROMPT = (
    "You are a Business Analyst. You are shown BDD scenarios that were written "
    "for a real software ticket. Reconstruct the acceptance criteria that would "
    "have produced them.\n"
    "Requirements:\n"
    "- Write ACs the way a Jira ticket would, as numbered clauses (AC1, AC2, ...).\n"
    "- Describe intent and business rules, NOT Given/When/Then steps.\n"
    "- Never invent behaviour that the scenarios do not demonstrate.\n"
    "- Return one clause id per scenario, in the same order as the scenarios given."
)

BACKGEN_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "acceptance_criteria": {
            "type": "string",
            "description": "Numbered AC clauses, newline separated (AC1: ...)",
        },
        "scenario_clauses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Clause id per scenario, aligned to input order",
        },
    },
    "required": ["acceptance_criteria", "scenario_clauses"],
}


@dataclass
class Stats:
    """Counters so --dry-run can explain exactly what was discarded and why.

    Field names deliberately match the REASON_* values in
    ``app.services.training_data_service`` so a rejection reason maps straight
    onto a counter (see :func:`_count_reason`).
    """

    seen: int = 0
    opted_out: int = 0
    unparsable: int = 0
    outlines_skipped: int = 0
    no_scenarios: int = 0
    too_many_scenarios: int = 0
    too_large: int = 0
    incomplete_steps: int = 0
    placeholder_text: int = 0
    duplicates: int = 0
    ready_pairs: int = 0
    kept: int = 0

    def render(self) -> str:
        return "\n".join(
            f"  {k.replace('_', ' '):<22} {v}" for k, v in self.__dict__.items()
        )


def _count_reason(stats: Stats, reason: str) -> None:
    """Increment the counter named by a REASON_* constant."""
    setattr(stats, reason, getattr(stats, reason) + 1)


def _accept(
    text: str, origin: str, stats: Stats, content_format: str | None = None
) -> FeatureDoc | None:
    """Parse and quality-check one document, recording why it was discarded.

    This is the ONLY place the builder decides usability, and it delegates to
    the same backend functions the upload API calls.

    `content_format` selects the parser for `bdd_files` rows: "json" rows are
    serialized BDDGenerateResponse objects, everything else is Gherkin text.
    """
    if content_format == "json":
        doc, reason = feature_doc_from_json(text, origin)
    else:
        doc, reason = parse_feature(text, origin)
    if reason is not None:
        _count_reason(stats, reason)
        return None
    assert doc is not None
    stats.outlines_skipped += doc.outlines_skipped
    reason = quality_reason(doc)
    if reason is not None:
        _count_reason(stats, reason)
        return None
    return doc


# --- Sources ----------------------------------------------------------------


def collect_feature_files(dirs: Sequence[Path], stats: Stats) -> list[FeatureDoc]:
    """Parse and filter every .feature file under the given directories."""
    docs: list[FeatureDoc] = []
    seen: set[str] = set()

    for directory in dirs:
        if not directory.exists():
            print(f"  ! {directory} does not exist - skipped", file=sys.stderr)
            continue
        for path in sorted(directory.rglob("*.feature")):
            stats.seen += 1
            try:
                text = path.read_text(encoding="utf-8", errors="strict")
            except (OSError, UnicodeDecodeError):
                stats.unparsable += 1
                continue

            doc = _accept(text, origin=str(path), stats=stats)
            if doc is None:
                continue

            fp = doc.fingerprint()
            if fp in seen:
                stats.duplicates += 1
                continue
            seen.add(fp)
            docs.append(doc)
            stats.kept += 1

    return docs


def collect_pair_files(dirs: Sequence[Path], stats: Stats) -> list[dict]:
    """Load COMPLETE pairs from local .jsonl files — no back-generation.

    Validation goes through `parse_jsonl_records`, the same function the upload
    endpoint uses, for the reason recorded at the top of this file: two copies
    of the rules means the app accepts files the builder discards.

    That function deliberately drops `meta.origin` (it is written for uploads,
    where the caller knows the filename and the record does not). Here the
    origin is already in the record and is load-bearing — the train/holdout
    split is BY ORIGIN, so losing it would scatter one ticket's pairs across
    both sides of the boundary. It is re-attached from the raw line.

    Duplicates are dropped by content fingerprint, which makes the re-ingest
    workflow safe: pointing --pairs-dir at an existing training/data while also
    writing to it must not double every pair.
    """
    records: list[dict] = []
    seen: set[str] = set()

    for directory in dirs:
        if not directory.exists():
            print(f"  ! pairs dir not found: {directory}", file=sys.stderr)
            continue

        for path in sorted(directory.rglob("*.jsonl")):
            text = path.read_text(encoding="utf-8")
            try:
                parsed = parse_jsonl_records(text)
            except TrainingDataError as exc:
                print(f"  ! {path.name}: {exc}", file=sys.stderr)
                continue

            raw_lines = [ln for ln in text.splitlines() if ln.strip()]
            kept = 0
            for index, (record, raw) in enumerate(
                zip(parsed, raw_lines, strict=False), start=1
            ):
                try:
                    origin = json.loads(raw).get("meta", {}).get("origin")
                except (ValueError, AttributeError):
                    origin = None
                record["meta"]["origin"] = origin or f"{path.stem}#{index}"

                fingerprint = pair_fingerprint(record)
                if fingerprint in seen:
                    _count_reason(stats, "duplicate pair")
                    continue
                seen.add(fingerprint)
                records.append(record)
                kept += 1

            print(f"  {path.name}: {kept} pair(s)")

    return records


#: What each --db-source choice selects from `bdd_files.source`.
#:
#: `authored` is the pair worth having: both are human-written Gherkin, so they
#: train the model on people's work rather than on its own. Reaching `edited`
#: used to require `all`, which also pulled in every `generated` row — so the
#: highest-value signal the app collects could not be used without
#: self-distillation riding along.
#:
#: None means no source predicate at all (every row, consent permitting).
DB_SOURCE_GROUPS: dict[str, tuple[str, ...] | None] = {
    "uploaded": ("uploaded",),
    "edited": ("edited",),
    "authored": ("uploaded", "edited"),
    "generated": ("generated",),
    "all": None,
}


def build_db_queries(
    source: str, limit: int | None
) -> tuple[str, dict[str, object], str, dict[str, object]]:
    """Build the include and exclusion-count queries for the database source.

    Extracted as a pure function so the opt-out guarantee is testable without a
    database. This is the single point where captured data is filtered for
    consent — if it regresses, opted-out rows silently enter training sets and
    nothing else in the system would notice.

    Returns (include_query, include_params, exclude_count_query, exclude_params).
    """
    sources = DB_SOURCE_GROUPS.get(source, (source,))

    # Conditions are collected and joined rather than appended, because more
    # than one WHERE clause is not valid SQL.
    conditions = ["training_opt_in = true"]
    params: dict[str, object] = {}
    if sources is not None:
        # `= ANY(:sources)` rather than `IN`: one bound array covers a single
        # source and a group with the same SQL, and text() cannot expand an IN
        # list without bindparam(expanding=True).
        conditions.append("source = ANY(:sources)")
        params["sources"] = list(sources)

    query = (
        # content_format tells `generated` (serialized JSON) apart from
        # `uploaded`/`edited` (raw Gherkin) — they need different parsers.
        # acceptance_criteria is the REAL input half, stored at capture time
        # (Story 6.4). Selecting it is what lets a captured row skip
        # back-generation instead of having its criteria re-invented.
        "SELECT id, content, source, content_format, acceptance_criteria"
        " FROM bdd_files"
        f" WHERE {' AND '.join(conditions)}"
        " ORDER BY created_at DESC"
    )
    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    # Counted separately so an all-opted-out database is distinguishable from an
    # empty one — otherwise both produce the same "no usable documents" message.
    # Deliberately ignores `limit`: the operator wants the true total excluded.
    excluded_query = "SELECT count(*) FROM bdd_files WHERE training_opt_in = false"
    excluded_params: dict[str, object] = {}
    if sources is not None:
        excluded_query += " AND source = ANY(:sources)"
        excluded_params["sources"] = list(sources)

    return query, params, excluded_query, excluded_params


def build_upload_queries(
    user_id: str | None, limit: int | None = None
) -> tuple[str, dict[str, object], str, dict[str, object]]:
    """Build the include and exclusion-count queries for uploaded datasets.

    Story 6.7. Pure, for the same reason build_db_queries is: this is the only
    place a manually uploaded corpus is filtered for consent, and a regression
    here would silently enlarge a training set.

    `limit` is pushed into SQL rather than applied after the fact, because every
    row returned here costs a Storage download - trimming in memory afterwards
    would fetch the whole corpus to throw most of it away.

    Returns (include_query, include_params, exclude_count_query, exclude_params).
    """
    # Collected and joined - a second appended WHERE is not valid SQL.
    conditions = ["training_opt_in = true"]
    params: dict[str, object] = {}
    if user_id:
        conditions.append("user_id = :user_id")
        params["user_id"] = user_id

    query = (
        "SELECT id, filename, kind, storage_path FROM training_datasets"
        f" WHERE {' AND '.join(conditions)}"
        " ORDER BY created_at DESC"
    )
    if limit:
        query += " LIMIT :limit"
        params["limit"] = limit

    # Deliberately ignores `limit`: the operator wants the true total excluded.
    excluded_query = (
        "SELECT count(*) FROM training_datasets WHERE training_opt_in = false"
    )
    excluded_params: dict[str, object] = {}
    if user_id:
        excluded_query += " AND user_id = :user_id"
        excluded_params["user_id"] = user_id

    return query, params, excluded_query, excluded_params


async def process_upload_rows(
    rows: Sequence[tuple], stats: Stats
) -> tuple[list[FeatureDoc], list[dict]]:
    """Download and parse uploaded dataset rows.

    Returns (docs, ready_records):
      * docs          - .feature uploads, which still need their acceptance
                        criteria reconstructed by back-generation
      * ready_records - .jsonl uploads, which are ALREADY complete pairs and
                        must skip back-generation entirely; sending them through
                        it would discard the researcher's own input side and pay
                        an LLM to invent a worse one

    Separated from the query so it is testable without a database.
    """
    from app.services.storage_service import StorageServiceError, storage_service

    docs: list[FeatureDoc] = []
    ready: list[dict] = []
    seen: set[str] = set()

    for row_id, filename, kind, storage_path in rows:
        stats.seen += 1
        origin = f"upload:{row_id}:{filename}"

        try:
            # storage_path already carries the {user_id} prefix.
            data = await storage_service.download_file(folder="", path=storage_path)
        except StorageServiceError as exc:
            print(f"  ! {origin}: stored object unreadable ({exc})", file=sys.stderr)
            stats.unparsable += 1
            continue

        # Content was UTF-8 validated at upload, so a failure here means the
        # stored object is damaged. Report it rather than substituting
        # replacement characters into training data.
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            print(f"  ! {origin}: stored object is not valid UTF-8", file=sys.stderr)
            stats.unparsable += 1
            continue

        if kind == "jsonl":
            try:
                records = parse_jsonl_records(text)
            except TrainingDataError as exc:
                print(f"  ! {origin}: {exc.message}", file=sys.stderr)
                stats.unparsable += 1
                continue

            # `parse_jsonl_records` drops meta.origin (it cannot know the
            # filename), so the caller stamps it — and WHAT it stamps decides
            # the granularity of the train/holdout split, which is by origin.
            #
            # Stamping the upload id alone made every pair in one file a single
            # origin, so a 179-pair upload was one indivisible group: with two
            # uploaded files the split could only put one whole file on each
            # side, and a run came out "2 train / 179 holdout". Unusable, and
            # not what --pairs-dir does with the identical content.
            #
            # So the record's own origin is recovered from the raw line exactly
            # as collect_pair_files does, and namespaced under this upload so
            # two uploads can never merge into one group.
            raw_lines = [ln for ln in text.splitlines() if ln.strip()]
            for index, (record, raw) in enumerate(
                zip(records, raw_lines, strict=False), start=1
            ):
                # Uploaded pairs need de-duplication just as much as feature
                # files: the same dataset uploaded twice would otherwise enter
                # the corpus twice and reweight the fine-tune.
                fp = pair_fingerprint(record)
                if fp in seen:
                    stats.duplicates += 1
                    continue
                seen.add(fp)
                try:
                    inner = json.loads(raw).get("meta", {}).get("origin")
                except (ValueError, AttributeError):
                    inner = None
                # Falls back to the line number, so a file whose records carry
                # no origin still splits per pair rather than as one block.
                record["meta"]["origin"] = f"{origin}#{inner or index}"
                ready.append(record)
                stats.ready_pairs += 1
            continue

        doc = _accept(text, origin=origin, stats=stats)
        if doc is None:
            continue
        fp = doc.fingerprint()
        if fp in seen:
            stats.duplicates += 1
            continue
        seen.add(fp)
        docs.append(doc)
        stats.kept += 1

    return docs, ready


async def collect_uploads(
    user_id: str | None, limit: int | None, stats: Stats
) -> tuple[list[FeatureDoc], list[dict]]:
    """Read the manually uploaded corpus (Story 6.7)."""
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    query, params, excluded_query, excluded_params = build_upload_queries(
        user_id, limit
    )

    # statement_cache_size=0 is mandatory - see collect_db_rows.
    engine = create_async_engine(
        settings.database_url, connect_args={"statement_cache_size": 0}
    )
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(sql_text(query), params)).fetchall()
            stats.opted_out += (
                await conn.execute(sql_text(excluded_query), excluded_params)
            ).scalar() or 0
    finally:
        await engine.dispose()

    return await process_upload_rows(rows, stats)


async def collect_db_rows(
    source: str, limit: int | None, stats: Stats
) -> list[FeatureDoc]:
    """Parse `bdd_files` rows into FeatureDocs.

    'uploaded' rows are human-authored and by far the most valuable source;
    'generated' rows are this app's own LLM output, and training on them is pure
    self-distillation, so they are only included when explicitly requested via
    --db-source.

    Rows flagged `training_opt_in = false` are ALWAYS excluded regardless of
    --db-source, and counted into `stats.opted_out`. Note that flag is unrelated
    to the --db-source selection above despite both being described as "opt in":
    one is a per-row consent record, the other is a CLI choice about which
    sources are useful.
    """
    from sqlalchemy import text as sql_text
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.core.config import settings

    query, params, excluded_query, excluded_params = build_db_queries(source, limit)

    # statement_cache_size=0 is mandatory, not optional: DATABASE_URL points at
    # Supabase's PgBouncer transaction pooler, which does not support prepared
    # statements. Without it this raises DuplicatePreparedStatementError
    # intermittently — the app's own engine sets the same argument.
    engine = create_async_engine(
        settings.database_url, connect_args={"statement_cache_size": 0}
    )
    docs: list[FeatureDoc] = []
    seen: set[str] = set()
    try:
        async with engine.connect() as conn:
            rows = (await conn.execute(sql_text(query), params)).fetchall()
            stats.opted_out += (
                await conn.execute(sql_text(excluded_query), excluded_params)
            ).scalar() or 0
    finally:
        await engine.dispose()

    for row_id, content, row_source, content_format, acceptance_criteria in rows:
        stats.seen += 1
        doc = _accept(
            content or "",
            origin=f"db:{row_source}:{row_id}",
            stats=stats,
            content_format=content_format,
        )
        if doc is None:
            continue
        # Carried on the doc rather than paired up later: the fingerprint and
        # the dedupe below work on documents, so the criteria have to travel
        # with the one they belong to.
        doc.acceptance_criteria = (acceptance_criteria or "").strip()
        fp = doc.fingerprint()
        if fp in seen:
            stats.duplicates += 1
            continue
        seen.add(fp)
        docs.append(doc)
        stats.kept += 1

    return docs


# --- Back-generation --------------------------------------------------------


def _render_scenarios(doc: FeatureDoc) -> str:
    lines = [f"Feature: {doc.feature}", ""]
    for i, s in enumerate(doc.scenarios, start=1):
        lines += [
            f"Scenario {i}: {s.title}",
            f"  Given {s.given}",
            f"  When {s.when}",
            f"  Then {s.then}",
            "",
        ]
    return "\n".join(lines)


async def backgenerate(doc: FeatureDoc, semaphore: asyncio.Semaphore) -> dict | None:
    """Ask the LLM for the acceptance criteria that would produce these scenarios.

    Goes through the LLMProvider interface, never a vendor SDK, per the project's
    mandatory architecture rules.
    """
    from app.services.llm.factory import get_llm_provider
    from app.services.llm.provider import LLMProviderError

    prompt = (
        "Reconstruct the acceptance criteria for the following BDD scenarios.\n\n"
        f"{_render_scenarios(doc)}\n"
        f"Return exactly {len(doc.scenarios)} entries in scenario_clauses."
    )

    async with semaphore:
        try:
            result = await get_llm_provider().generate_structured(
                prompt=prompt,
                system_prompt=BACKGEN_SYSTEM_PROMPT,
                response_format=BACKGEN_SCHEMA,
            )
        except LLMProviderError as exc:
            print(
                f"  ! back-generation failed for {doc.origin}: {exc}",
                file=sys.stderr,
            )
            return None

    ac = str(result.get("acceptance_criteria", "")).strip()
    clauses = result.get("scenario_clauses") or []
    if not ac or not isinstance(clauses, list) or len(clauses) != len(doc.scenarios):
        print(
            f"  ! misaligned back-generation for {doc.origin} - dropped",
            file=sys.stderr,
        )
        return None

    return _build_pair(doc, ac, [str(c) for c in clauses])


def _build_pair(doc: FeatureDoc, ac: str, clauses: Sequence[str]) -> dict | None:
    """Assemble one training pair from criteria and per-scenario clauses.

    Shared by both routes so a pair built from stored criteria is byte-identical
    in shape to a back-generated one — the trainer must not be able to tell them
    apart, and the holdout split must treat them the same.
    """
    target = {
        "scenarios": [
            {
                "source_ac_clause": str(clause),
                "feature": doc.feature,
                "scenario": s.title,
                "given": s.given,
                "when": s.when,
                "then": s.then,
            }
            for clause, s in zip(clauses, doc.scenarios, strict=True)
        ]
    }

    # The application must be able to parse anything we train the model to emit.
    validated = BDDGenerateResponse.model_validate(target)
    if not validated.scenarios:
        print(f"  ! empty scenarios for {doc.origin} - dropped", file=sys.stderr)
        return None

    return {
        "messages": [
            {"role": "system", "content": BDD_SYSTEM_PROMPT},
            {"role": "user", "content": ac},
            {"role": "assistant", "content": validated.model_dump_json()},
        ],
        "meta": {"origin": doc.origin, "scenario_count": len(doc.scenarios)},
    }


def has_stored_pair(doc: FeatureDoc) -> bool:
    """True when this document already carries a genuine (criteria -> scenarios) pair.

    Requires BOTH halves: the criteria the scenarios came from, and a clause on
    every scenario saying which part of them it covers. A partial answer is not
    usable — `source_ac_clause` is required by BDDGenerateResponse, and filling
    the gaps with the whole criteria text would teach the model that every
    scenario derives from all of it.
    """
    return bool(doc.acceptance_criteria) and all(
        s.source_ac_clause for s in doc.scenarios
    )


def pair_from_stored(doc: FeatureDoc) -> dict | None:
    """Build the pair from what was captured, with no LLM call.

    Back-generation exists because a corpus `.feature` file has no criteria
    behind it. A row captured in the app does: the real ticket text is on
    `bdd_files.acceptance_criteria`, and the clause attribution survives in the
    `# Source AC:` comments the app writes. Re-inventing either is paying to
    replace real data with a worse guess — the same reason uploaded `.jsonl`
    pairs already skip this step.
    """
    return _build_pair(
        doc,
        doc.acceptance_criteria,
        [s.source_ac_clause for s in doc.scenarios],
    )


# --- Output -----------------------------------------------------------------


def split_by_origin(
    records: list[dict], holdout: float, seed: int
) -> tuple[list[dict], list[dict]]:
    """Split on the ORIGIN file, never on individual rows.

    Two scenarios from one feature file are near-duplicates; splitting per row
    would leak them across the boundary and make the holdout score a lie.
    """
    origins = sorted({r["meta"]["origin"] for r in records})
    rng = random.Random(seed)
    rng.shuffle(origins)
    cut = max(1, int(len(origins) * holdout)) if origins and holdout > 0 else 0
    holdout_origins = set(origins[:cut])

    train = [r for r in records if r["meta"]["origin"] not in holdout_origins]
    test = [r for r in records if r["meta"]["origin"] in holdout_origins]
    return train, test


def write_jsonl(path: Path, records: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            count += 1
    return count


# --- Entry point ------------------------------------------------------------


async def run(args: argparse.Namespace) -> int:
    stats = Stats()
    docs: list[FeatureDoc] = []
    # Pairs that arrive complete and must NOT be back-generated (Story 6.7).
    ready_records: list[dict] = []

    if args.features_dir:
        docs += collect_feature_files(
            [resolve_path(d) for d in args.features_dir], stats
        )

    if args.pairs_dir:
        print("\nLoading complete pairs (no back-generation):")
        ready_records += collect_pair_files(
            [resolve_path(d) for d in args.pairs_dir], stats
        )

    if args.from_db:
        try:
            docs += await collect_db_rows(args.db_source, args.limit, stats)
        except Exception as exc:  # the DB is an optional source - keep going
            print(f"  ! database source unavailable: {exc}", file=sys.stderr)

    if args.from_uploads:
        try:
            # `+=`, not `=`: --pairs-dir may already have filled this, and
            # rebinding would silently discard every authored product pair.
            upload_docs, upload_pairs = await collect_uploads(
                args.uploads_user, args.limit, stats
            )
            docs += upload_docs
            ready_records += upload_pairs
        except Exception as exc:  # optional source, same as the DB
            print(f"  ! uploaded corpus unavailable: {exc}", file=sys.stderr)

    # --limit caps documents sent for back-generation (bounding LLM spend) and
    # is applied again to the merged corpus below, so it always means "at most
    # this many training pairs".
    if args.limit:
        docs = docs[: args.limit]
        ready_records = ready_records[: args.limit]

    print(f"\nCorpus scan\n{stats.render()}")

    if not docs and not ready_records:
        if stats.opted_out:
            print(
                f"\nNo usable feature documents found — but {stats.opted_out} database "
                "row(s) were EXCLUDED because they are flagged training_opt_in=false.\n"
                "That flag is stamped at capture time from TRAINING_DATA_OPT_IN; "
                "changing the env var now will not reclassify existing rows.",
                file=sys.stderr,
            )
        else:
            print(
                "\nNo usable feature documents found.\n"
                "Clone some Cucumber/SpecFlow repos into a directory and pass "
                "--features-dir, upload a corpus in the app and pass "
                "--from-uploads, or point --from-db at a reachable database.",
                file=sys.stderr,
            )
        return 1

    if stats.opted_out:
        print(
            f"\nNote: {stats.opted_out} database row(s) excluded "
            "(training_opt_in=false)."
        )

    scenario_total = sum(len(d.scenarios) for d in docs)
    print(f"\n{len(docs)} documents kept, {scenario_total} scenarios total")
    if ready_records:
        print(
            f"{len(ready_records)} uploaded pairs already complete "
            "(skipping back-generation)"
        )

    if args.dry_run:
        print("\n--dry-run: stopping before any LLM calls. Sample:")
        for doc in docs[:3]:
            print(f"\n  origin: {doc.origin}")
            print(f"  feature: {doc.feature}")
            for s in doc.scenarios[:2]:
                print(f"    - {s.title}")
                print(f"        Given {s.given[:80]}")
                print(f"        When  {s.when[:80]}")
                print(f"        Then  {s.then[:80]}")
        for record in ready_records[:2]:
            print(f"\n  origin: {record['meta']['origin']} (ready pair)")
            print(f"    user: {record['messages'][-2]['content'][:80]}")
        return 0

    records: list[dict] = []

    # Documents that already carry a real (criteria -> scenarios) pair skip the
    # LLM entirely. Split here rather than inside backgenerate() so the progress
    # output stays honest about how much was captured versus invented.
    stored_docs = [d for d in docs if has_stored_pair(d)]
    needs_backgen = [d for d in docs if not has_stored_pair(d)]

    if stored_docs:
        stored = [pair_from_stored(d) for d in stored_docs]
        kept = [r for r in stored if r is not None]
        records += kept
        print(
            f"\n{len(kept)} pair(s) built from captured acceptance criteria "
            "- no back-generation needed"
        )

    if needs_backgen:
        print(
            f"\nBack-generating acceptance criteria for "
            f"{len(needs_backgen)} documents..."
        )
        semaphore = asyncio.Semaphore(args.concurrency)
        results = await asyncio.gather(
            *(backgenerate(d, semaphore) for d in needs_backgen)
        )
        built = [r for r in results if r is not None]
        records += built

        dropped = len(needs_backgen) - len(built)
        print(f"  {len(built)} pairs built, {dropped} dropped during back-generation")

    # Uploaded .jsonl pairs join here, AFTER back-generation and BEFORE the
    # split, so origin-level splitting still keeps one file's pairs together.
    records += ready_records
    if args.limit:
        records = records[: args.limit]

    if not records:
        print("\nNo pairs survived back-generation.", file=sys.stderr)
        return 1

    train, test = split_by_origin(records, args.holdout, args.seed)
    out = resolve_path(args.out)
    n_train = write_jsonl(out / "train.jsonl", train)
    n_test = write_jsonl(out / "holdout.jsonl", test)

    print(f"\nWrote {n_train} train / {n_test} holdout pairs to {out}/")
    print(
        "Holdout is split by source file, so no scenario appears on both sides.\n"
        "Review a sample by hand before training - synthetic ACs are the weakest\n"
        "part of this dataset and bad ones teach bad habits."
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build (acceptance criteria -> Gherkin) fine-tuning pairs.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--features-dir",
        action="append",
        default=[],
        help="Directory of .feature files (repeatable)",
    )
    parser.add_argument(
        "--pairs-dir",
        action="append",
        default=[],
        help=(
            "Directory of .jsonl files holding COMPLETE pairs, which skip "
            "back-generation entirely (repeatable). This is how authored "
            "product-domain tickets from training/corpus-product enter the set, "
            "and it also re-ingests a previously built training/data without "
            "paying for back-generation a second time."
        ),
    )
    parser.add_argument(
        "--from-db", action="store_true", help="Also read bdd_files rows"
    )
    parser.add_argument(
        "--from-uploads",
        action="store_true",
        help="Also read manually uploaded datasets (training_datasets + Storage)",
    )
    parser.add_argument(
        "--uploads-user",
        default=None,
        help="Restrict --from-uploads to one user_id (default: all users)",
    )
    parser.add_argument(
        "--db-source",
        choices=sorted(DB_SOURCE_GROUPS),
        default="uploaded",
        help=(
            "Which bdd_files rows to use (default: uploaded). 'edited' is the "
            "human-corrected output — the strongest signal the app collects, "
            "since it says exactly where the model was wrong. 'authored' is "
            "uploaded + edited, i.e. everything a person wrote. 'generated' "
            "rows parse (they are serialized JSON, not Gherkin) but training "
            "on them is self-distillation: this app's own LLM output can only "
            "teach the student to imitate the teacher it already calls at "
            "runtime."
        ),
    )
    parser.add_argument("--out", default="training/data", help="Output directory")
    parser.add_argument("--limit", type=int, default=None, help="Cap document count")
    parser.add_argument(
        "--holdout", type=float, default=0.1, help="Holdout fraction (default 0.1)"
    )
    parser.add_argument("--concurrency", type=int, default=4, help="Parallel LLM calls")
    parser.add_argument("--seed", type=int, default=1337, help="Split seed")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and report only - no LLM calls, no output files",
    )
    args = parser.parse_args()

    if (
        not args.features_dir
        and not args.pairs_dir
        and not args.from_db
        and not args.from_uploads
    ):
        parser.error(
            "provide --features-dir, --pairs-dir, --from-db and/or --from-uploads"
        )

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
