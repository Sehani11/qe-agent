"""Shared training-data parsing, validation and storage (Stories 6.5-6.7).

**This module is the single source of truth for "is this usable training data?"**

The Gherkin parser and quality thresholds below were originally written inside
`training/build_dataset.py`. They live here now because two different callers
must reach the SAME verdict:

  * `app/api/v1/training.py` — decides whether to accept an uploaded file
  * `training/build_dataset.py` — decides whether to keep it when building a set

If those two ever diverge, the application accepts files the builder silently
discards, and the corpus looks healthy while being empty. That is not
hypothetical: Story 6.6 found both `uploaded` rows in the live database failing
the quality filter, giving an effective corpus of zero.

The direction of the dependency is forced. `training/` sits at the repo root,
outside the backend Docker build context, so the backend can never import it —
but `build_dataset.py` already puts `backend/` on `sys.path` and imports from
`app.*`. Shared code therefore lives here and the builder imports it.

`parse_feature` / `quality_reason` return REASON_* constants rather than
mutating a counter object, so the builder can map them onto its `Stats` fields
(the constant values are deliberately identical to those field names) and the
API can map them onto human messages via REASON_MESSAGES.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.training_dataset import TrainingDataset
from app.schemas.bdd import BDDGenerateResponse
from app.services.storage_service import (
    FOLDER_TRAINING_DATA,
    StorageServiceError,
    storage_service,
)

logger = logging.getLogger(__name__)

# --- Quality thresholds -----------------------------------------------------
# Public Gherkin is frequently poor: UI-step spaghetti, placeholder scenarios,
# 200-scenario mega-files. These bounds discard the obvious offenders.
MIN_SCENARIOS = 1
MAX_SCENARIOS = 20
MIN_STEP_CHARS = 8
MAX_STEP_CHARS = 400
MAX_FILE_CHARS = 20_000
PLACEHOLDER_RE = re.compile(r"\b(TODO|FIXME|XXX|TBD|lorem ipsum)\b", re.IGNORECASE)
STEP_RE = re.compile(r"^\s*(Given|When|Then|And|But)\b\s*(.*)$", re.IGNORECASE)
FEATURE_RE = re.compile(r"^\s*Feature:\s*(.+)$", re.IGNORECASE)
SCENARIO_RE = re.compile(r"^\s*Scenario(?:\s+Outline)?:\s*(.+)$", re.IGNORECASE)
OUTLINE_RE = re.compile(r"^\s*Scenario\s+Outline:", re.IGNORECASE)
EXAMPLES_RE = re.compile(r"^\s*Examples:", re.IGNORECASE)
TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
# The app renders its own scenarios with the originating AC clause as a
# comment (see scenariosToGherkin in the frontend). Capturing it back means a
# round trip through the editor keeps the clause attribution the model was
# trained to produce, instead of an LLM having to guess it again.
SOURCE_AC_RE = re.compile(r"^\s*#\s*Source AC:\s*(.+)$", re.IGNORECASE)

#: How many Examples rows one Scenario Outline may contribute.
#:
#: Outlines exist to run the SAME steps over many inputs, so expanding a
#: 40-row table would emit 40 near-identical scenarios and teach precisely the
#: repetition the fine-tune already over-produces (RUN_LOG records it emitting
#: a scenario whose steps restate the previous one). Three rows keeps the
#: variety that makes an outline worth having without letting one file
#: dominate the corpus.
MAX_OUTLINE_EXPANSIONS = 3
# Gherkin declares steps shared by every scenario in a feature exactly once, in
# a Background block. A parser that ignores it sees scenarios with no Given and
# discards them as incomplete — which wrongly rejected 38% of a real-world
# corpus, and wrongly rejects valid user uploads.
BACKGROUND_RE = re.compile(r"^\s*Background:", re.IGNORECASE)

# --- Rejection reasons ------------------------------------------------------
# The VALUES must match `Stats` field names in training/build_dataset.py so the
# builder can increment counters by name. test_training_data_service.py pins
# that alignment.
REASON_TOO_LARGE = "too_large"
REASON_UNPARSABLE = "unparsable"
REASON_NO_SCENARIOS = "no_scenarios"
REASON_TOO_MANY_SCENARIOS = "too_many_scenarios"
REASON_INCOMPLETE_STEPS = "incomplete_steps"
REASON_PLACEHOLDER_TEXT = "placeholder_text"

REASON_MESSAGES: dict[str, str] = {
    REASON_TOO_LARGE: (
        f"File is too large to be a useful training example "
        f"(limit {MAX_FILE_CHARS:,} characters)."
    ),
    REASON_UNPARSABLE: (
        "No Gherkin found. A feature file needs a 'Feature:' line and at least "
        "one 'Scenario:' block."
    ),
    REASON_NO_SCENARIOS: "No scenarios found in this feature file.",
    REASON_TOO_MANY_SCENARIOS: (
        f"Too many scenarios in one file (limit {MAX_SCENARIOS}). "
        "Split it into smaller feature files."
    ),
    REASON_INCOMPLETE_STEPS: (
        "Every scenario needs a complete Given, When and Then, each between "
        f"{MIN_STEP_CHARS} and {MAX_STEP_CHARS} characters. At least one "
        "scenario here does not."
    ),
    REASON_PLACEHOLDER_TEXT: (
        "Contains placeholder text (TODO/FIXME/XXX/TBD). Placeholders teach the "
        "model to write placeholders."
    ),
}

# Accepted upload kinds.
KIND_FEATURE = "feature"
KIND_JSONL = "jsonl"


class TrainingDataError(Exception):
    """Raised when supplied training data cannot be used.

    ``line_number`` is set for line-oriented (JSONL) failures so the caller can
    tell the user exactly where to look instead of "the file is invalid".
    """

    def __init__(self, message: str, line_number: int | None = None):
        self.message = message
        self.line_number = line_number
        super().__init__(self.message)


@dataclass
class Scenario:
    """One parsed Gherkin scenario, flattened to the app's schema shape."""

    title: str
    given: str
    when: str
    then: str
    #: The acceptance-criteria clause this scenario covers, when it is known.
    #: Empty for corpus files, which have no criteria behind them at all.
    #: Defaulted so every existing construction stays valid.
    source_ac_clause: str = ""


@dataclass
class FeatureDoc:
    """A parsed .feature file (or DB row) that survived parsing."""

    origin: str  # group key for the train/holdout split
    feature: str
    #: The acceptance criteria this document was generated from, when the
    #: source knows them — `bdd_files` rows carry the real ticket text. Empty
    #: for corpus `.feature` files, which is what back-generation exists for.
    acceptance_criteria: str = ""
    scenarios: list[Scenario] = field(default_factory=list)
    # Counted during parsing so the caller can report it; Scenario Outlines are
    # dropped rather than treated as a failure.
    outlines_skipped: int = 0

    def fingerprint(self) -> str:
        """Content hash used to drop near-duplicate corpora entries."""
        body = "|".join(
            f"{s.title}{s.given}{s.when}{s.then}".lower().split().__str__()
            for s in self.scenarios
        )
        return hashlib.sha256(f"{self.feature.lower()}{body}".encode()).hexdigest()


# --- Gherkin parsing --------------------------------------------------------


def parse_feature(text: str, origin: str) -> tuple[FeatureDoc | None, str | None]:
    """Parse a .feature file into a FeatureDoc.

    Returns ``(doc, None)`` on success or ``(None, REASON_*)`` when the text
    cannot be parsed at all. A parsed document may still be unusable — call
    :func:`quality_reason` next.

    Deliberately hand-rolled rather than pulling in a Gherkin dependency: the
    subset that matters here (Feature / Scenario / Given / When / Then) is small,
    and this must run inside the backend venv without new packages.
    """
    if len(text) > MAX_FILE_CHARS:
        return None, REASON_TOO_LARGE

    feature_name = ""
    scenarios: list[Scenario] = []
    outlines_skipped = 0
    buckets: dict[str, list[str]] = {}
    # Steps declared once in a Background block, prepended to every scenario.
    background: dict[str, list[str]] = {}
    in_background = False
    current: str | None = None
    title = ""
    # A "# Source AC:" comment precedes the Scenario it belongs to, so it is
    # parked here until that Scenario line arrives and claims it.
    pending_clause = ""
    title_clause = ""
    outline_clause = ""
    in_outline = False
    # Scenario Outline state: steps are held with their <placeholders> intact
    # until the Examples table arrives to substitute them.
    outline_title = ""
    outline_buckets: dict[str, list[str]] = {}
    in_examples = False
    examples_header: list[str] | None = None
    outline_rows_used = 0

    def merge_background(bucket: dict[str, list[str]], keyword: str) -> str:
        """Prepend the Background's steps to a scenario's own."""
        return " ".join(background.get(keyword, []) + bucket.get(keyword, [])).strip()

    def expand_outline_row(values: dict[str, str]) -> None:
        """Emit one concrete scenario by substituting an Examples row."""
        nonlocal outline_rows_used
        if outline_rows_used >= MAX_OUTLINE_EXPANSIONS or not outline_title:
            return

        def substitute(text_value: str) -> str:
            for column, value in values.items():
                text_value = text_value.replace(f"<{column}>", value)
            return text_value

        given = substitute(merge_background(outline_buckets, "given"))
        when = substitute(merge_background(outline_buckets, "when"))
        then = substitute(merge_background(outline_buckets, "then"))
        if not (given and when and then):
            return

        outline_rows_used += 1
        # Distinct titles: identical ones would read as duplicates to the
        # near-duplicate detector and to anyone reading the dataset.
        row_label = ", ".join(f"{k}={v}" for k, v in values.items() if v)
        scenarios.append(
            Scenario(
                title=f"{outline_title} [{row_label}]"[:MAX_STEP_CHARS],
                given=given,
                when=when,
                then=then,
                # Every row expanded from one outline covers the same clause.
                source_ac_clause=outline_clause,
            )
        )

    def close_outline() -> None:
        """Finish the outline in progress, counting it if nothing came of it."""
        nonlocal in_outline, in_examples, examples_header
        nonlocal outline_title, outline_buckets, outline_rows_used, outlines_skipped
        nonlocal outline_clause

        if in_outline and outline_rows_used == 0:
            # No Examples table, or every row was unusable — the placeholders
            # would have reached the model verbatim.
            outlines_skipped += 1

        in_outline = False
        in_examples = False
        examples_header = None
        outline_title = ""
        outline_buckets = {}
        outline_rows_used = 0
        outline_clause = ""

    def flush() -> None:
        """Emit the scenario just parsed, merging the Background steps in front."""
        nonlocal buckets, title, in_background, title_clause

        if in_background:
            # The block being closed is the Background itself: keep its steps
            # aside rather than emitting them as a scenario.
            background.update(buckets)
            buckets = {}
            in_background = False
            return

        if not title:
            return

        def merged(keyword: str) -> str:
            parts = background.get(keyword, []) + buckets.get(keyword, [])
            return " ".join(parts).strip()

        scenarios.append(
            Scenario(
                title=title.strip(),
                given=merged("given"),
                when=merged("when"),
                then=merged("then"),
                source_ac_clause=title_clause,
            )
        )
        buckets = {}
        title = ""
        title_clause = ""

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        if line.lstrip().startswith("#"):
            # Every other comment is still ignored; only this one carries data.
            if m := SOURCE_AC_RE.match(line):
                pending_clause = m.group(1).strip()
            continue

        if m := FEATURE_RE.match(line):
            feature_name = m.group(1).strip()
            continue

        if BACKGROUND_RE.match(line):
            flush()
            close_outline()
            in_background = True
            current = None
            continue

        if match := SCENARIO_RE.match(line):
            flush()
            close_outline()
            in_outline = bool(OUTLINE_RE.match(line))
            if in_outline:
                # Held, not discarded: the <placeholders> are substituted from
                # the Examples table below. 150 of 708 corpus files were being
                # thrown away whole for this.
                outline_title = match.group(1).strip()
                outline_buckets = {}
                outline_clause = pending_clause
                pending_clause = ""
                current = None
                continue
            title = match.group(1)
            title_clause = pending_clause
            pending_clause = ""
            current = None
            continue

        if in_outline:
            if EXAMPLES_RE.match(line):
                in_examples = True
                examples_header = None
                continue

            if in_examples and (row := TABLE_ROW_RE.match(line)):
                cells = [c.strip() for c in row.group(1).split("|")]
                if examples_header is None:
                    examples_header = cells
                else:
                    expand_outline_row(dict(zip(examples_header, cells, strict=False)))
                continue

            if m := STEP_RE.match(line):
                keyword = m.group(1).lower()
                body = m.group(2).strip()
                if keyword in {"given", "when", "then"}:
                    current = keyword
                if current and body:
                    outline_buckets.setdefault(current, []).append(body)
            continue

        if not (title or in_background):
            continue

        if m := STEP_RE.match(line):
            keyword = m.group(1).lower()
            body = m.group(2).strip()
            if keyword in {"given", "when", "then"}:
                current = keyword
            if current is None or not body:
                continue
            # BDD_SYSTEM_PROMPT asks for And/But to live INSIDE the Given/When/Then
            # text, so keep the connective rather than silently concatenating —
            # dropping it produces run-on nonsense like "X is displayed Y is issued".
            if keyword in {"and", "but"} and buckets.get(current):
                body = f"{keyword.capitalize()} {body}"
            buckets.setdefault(current, []).append(body)

    flush()
    close_outline()

    if not feature_name and not scenarios:
        return None, REASON_UNPARSABLE

    return (
        FeatureDoc(
            origin=origin,
            feature=feature_name or Path(origin).stem.replace("_", " ").title(),
            scenarios=scenarios,
            outlines_skipped=outlines_skipped,
        ),
        None,
    )


def feature_doc_from_json(
    content: str, origin: str
) -> tuple[FeatureDoc | None, str | None]:
    """Map a serialized ``BDDGenerateResponse`` onto a FeatureDoc.

    `bdd_files.content` holds two different formats, recorded in
    `content_format` since Story 6.4: `generated` rows are serialized JSON,
    `uploaded`/`edited` rows are raw Gherkin. `parse_feature` only understands
    the latter, so before this every `generated` row was counted as unparsable
    — 8 of the 10 rows in the live database.

    Deliberately maps the JSON straight onto the dataclasses rather than
    rendering Gherkin and re-parsing it: a round trip through text could only
    lose information.
    """
    try:
        payload = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None, REASON_UNPARSABLE

    try:
        validated = BDDGenerateResponse.model_validate(payload)
    except ValidationError:
        return None, REASON_UNPARSABLE

    if not validated.scenarios:
        return None, REASON_NO_SCENARIOS

    return (
        FeatureDoc(
            origin=origin,
            feature=(
                validated.scenarios[0].feature
                or Path(origin).stem.replace("_", " ").title()
            ),
            scenarios=[
                Scenario(
                    title=s.scenario,
                    given=s.given,
                    when=s.when,
                    then=s.then,
                    source_ac_clause=s.source_ac_clause,
                )
                for s in validated.scenarios
            ],
        ),
        None,
    )


def quality_reason(doc: FeatureDoc) -> str | None:
    """Return why this document would teach the model bad habits, or None."""
    if len(doc.scenarios) < MIN_SCENARIOS:
        return REASON_NO_SCENARIOS
    if len(doc.scenarios) > MAX_SCENARIOS:
        return REASON_TOO_MANY_SCENARIOS

    for s in doc.scenarios:
        # A scenario missing any of Given/When/Then is not a usable target.
        if not (s.given and s.when and s.then):
            return REASON_INCOMPLETE_STEPS
        for part in (s.title, s.given, s.when, s.then):
            if len(part) < MIN_STEP_CHARS or len(part) > MAX_STEP_CHARS:
                return REASON_INCOMPLETE_STEPS
            if PLACEHOLDER_RE.search(part):
                return REASON_PLACEHOLDER_TEXT
    return None


# --- JSONL validation -------------------------------------------------------


def _validate_pair(record: object, line_no: int) -> int:
    """Validate one already-decoded JSONL record, raising with its line number.

    Returns the number of scenarios on the assistant side.
    """
    if not isinstance(record, dict):
        raise TrainingDataError(
            f"Line {line_no}: each line must be a JSON object.", line_no
        )

    messages = record.get("messages")
    if not isinstance(messages, list) or not messages:
        raise TrainingDataError(
            f"Line {line_no}: missing a non-empty 'messages' array.", line_no
        )

    roles: set[str] = set()
    for message in messages:
        if (
            not isinstance(message, dict)
            or not isinstance(message.get("role"), str)
            or not isinstance(message.get("content"), str)
        ):
            raise TrainingDataError(
                f"Line {line_no}: every entry in 'messages' needs a string "
                "'role' and a string 'content'.",
                line_no,
            )
        roles.add(message["role"])

    if "user" not in roles:
        raise TrainingDataError(
            f"Line {line_no}: no 'user' message — the input half of the pair "
            "is missing.",
            line_no,
        )
    if "assistant" not in roles:
        raise TrainingDataError(
            f"Line {line_no}: no 'assistant' message — the output half of the "
            "pair is missing.",
            line_no,
        )

    assistant = next(m for m in reversed(messages) if m["role"] == "assistant")

    # The model is served behind FineTunedModelProvider, which parses its output
    # as BDDGenerateResponse. Training on any other shape produces a model this
    # application cannot read.
    try:
        target = json.loads(assistant["content"])
    except json.JSONDecodeError as exc:
        raise TrainingDataError(
            f"Line {line_no}: the assistant message must be JSON matching "
            "BDDGenerateResponse.",
            line_no,
        ) from exc

    try:
        validated = BDDGenerateResponse.model_validate(target)
    except ValidationError as exc:
        raise TrainingDataError(
            f"Line {line_no}: the assistant message does not match "
            f"BDDGenerateResponse ({exc.error_count()} problem(s)).",
            line_no,
        ) from exc

    # An empty list VALIDATES (the field defaults to []) and would quietly teach
    # the model to return nothing, so it must be rejected explicitly.
    if not validated.scenarios:
        raise TrainingDataError(
            f"Line {line_no}: the assistant message contains no scenarios.",
            line_no,
        )

    return len(validated.scenarios)


def parse_jsonl_records(text: str) -> list[dict]:
    """Validate a JSONL training file and return its records.

    Records are normalised to the shape ``build_dataset.py`` writes —
    ``{"messages": [...], "meta": {"scenario_count": n}}`` — so an uploaded
    dataset can be merged straight into a build without further translation.
    The caller stamps ``meta["origin"]``.

    Blank lines are skipped. Raises :class:`TrainingDataError` naming the first
    offending 1-based line number.
    """
    records: list[dict] = []
    for line_no, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise TrainingDataError(
                f"Line {line_no}: not valid JSON ({exc.msg}).", line_no
            ) from exc
        scenario_count = _validate_pair(record, line_no)
        records.append(
            {
                "messages": record["messages"],
                "meta": {"scenario_count": scenario_count},
            }
        )

    if not records:
        raise TrainingDataError("The file contains no training pairs.")
    return records


def validate_jsonl(text: str) -> int:
    """Validate a JSONL training file and return its pair count."""
    return len(parse_jsonl_records(text))


# --- Sample datasets --------------------------------------------------------
#
# Built here, next to the validator, rather than checked in as static files.
# The accepted shape is not obvious — the assistant side must be a JSON
# BDDGenerateResponse *as a string*, and an empty scenarios array validates
# while teaching the model to return nothing — so a sample is the fastest way
# to communicate it. A checked-in file would drift the moment
# BDDGenerateResponse or BDD_SYSTEM_PROMPT changed, and would then be a sample
# that this very module rejects. Generating it from the same constants the
# validator uses makes that drift impossible, and
# `test_sample_datasets_pass_their_own_validator` pins it.

SAMPLE_JSONL_FILENAME = "sample-training-pairs.jsonl"
SAMPLE_FEATURE_FILENAME = "sample-scenarios.feature"

#: Two worked examples. Deliberately product-shaped (auth, permissions) rather
#: than "foo/bar": a sample is copied and edited far more often than it is
#: read, so it should model the domain writing people actually have to do.
_SAMPLE_PAIRS: list[tuple[str, list[dict[str, str]]]] = [
    (
        "AC1: A user who submits a correct email and password is signed in and "
        "taken to their dashboard.\n"
        "AC2: A user who submits a wrong password stays on the sign-in page and "
        "is told the credentials did not match.",
        [
            {
                "source_ac_clause": "AC1",
                "feature": "Sign in",
                "scenario": "Correct credentials sign the user in",
                "given": "a registered user is on the sign-in page",
                "when": "they submit their correct email and password",
                "then": "they are signed in and land on their dashboard",
            },
            {
                "source_ac_clause": "AC2",
                "feature": "Sign in",
                "scenario": "A wrong password is rejected",
                "given": "a registered user is on the sign-in page",
                "when": "they submit their email with the wrong password",
                "then": "they stay on the sign-in page and are told the "
                "credentials did not match",
            },
        ],
    ),
    (
        "AC1: Only a workspace admin can remove another member.\n"
        "AC2: An admin cannot remove themselves while they are the only admin.",
        [
            {
                "source_ac_clause": "AC1",
                "feature": "Workspace membership",
                "scenario": "An admin removes a member",
                "given": "an admin is viewing the members list of their workspace",
                "when": "they remove another member and confirm",
                "then": "that member no longer appears in the list and loses "
                "access to the workspace",
            },
            {
                "source_ac_clause": "AC2",
                "feature": "Workspace membership",
                "scenario": "The last admin cannot remove themselves",
                "given": "an admin is the only admin in their workspace",
                "when": "they try to remove their own membership",
                "then": "the removal is refused and they are told a workspace "
                "must keep at least one admin",
            },
        ],
    ),
]


def build_sample_jsonl() -> str:
    """Return a valid `.jsonl` training file, ready to upload or edit.

    One JSON object per line, in the chat format ``build_dataset.py`` emits:
    a system message carrying the serving prompt, the acceptance criteria as
    the user turn, and the target scenarios as a JSON-encoded assistant turn.
    """
    # Imported here, not at module scope: this keeps the module's import graph
    # unchanged for `training/build_dataset.py`, which loads it early.
    from app.services.bdd_service import BDD_SYSTEM_PROMPT

    lines: list[str] = []
    for criteria, scenarios in _SAMPLE_PAIRS:
        record = {
            "messages": [
                {"role": "system", "content": BDD_SYSTEM_PROMPT},
                {"role": "user", "content": criteria},
                {
                    "role": "assistant",
                    # A STRING of JSON, not a nested object — this is the exact
                    # shape the served model must emit for the provider to
                    # parse it, and the most common thing to get wrong.
                    "content": json.dumps(
                        {"scenarios": scenarios},
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "meta": {"scenario_count": len(scenarios)},
        }
        lines.append(json.dumps(record, ensure_ascii=False))
    return "\n".join(lines) + "\n"


def build_sample_feature() -> str:
    """Return a valid `.feature` file, ready to upload or edit.

    The `# Source AC:` comments are not decoration: `parse_feature` reads them
    back, so a file that carries them keeps its clause attribution instead of
    an LLM having to re-invent it during back-generation.
    """
    _, scenarios = _SAMPLE_PAIRS[0]
    lines = [
        "# A sample feature file in the shape this app accepts.",
        "# Every scenario needs a complete Given, When and Then; the",
        "# '# Source AC:' comment is kept as the clause the scenario covers.",
        "",
        f"Feature: {scenarios[0]['feature']}",
        "",
    ]
    for scenario in scenarios:
        lines += [
            f"  # Source AC: {scenario['source_ac_clause']}",
            f"  Scenario: {scenario['scenario']}",
            f"    Given {scenario['given']}",
            f"    When {scenario['when']}",
            f"    Then {scenario['then']}",
            "",
        ]
    return "\n".join(lines)


def pair_fingerprint(record: dict) -> str:
    """Content hash of one training pair, used to drop duplicates.

    The FeatureDoc equivalent of this exists as ``FeatureDoc.fingerprint``.
    Uploaded pairs need it too: re-uploading the same .jsonl would otherwise
    enter every pair twice, and duplicate examples reweight a fine-tune toward
    whatever was duplicated.
    """
    body = json.dumps(record.get("messages", []), sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


# --- Upload orchestration ---------------------------------------------------


# A filename arrives from the client's multipart Content-Disposition header and
# is used to build a storage object path, so it must be a plain name. Without
# this, "../../victim/x.feature" walks out of {user_id}/training-data/ — past the
# storage RLS policy, which keys off the FIRST path segment being the owner.
_PATH_SEPARATOR_RE = re.compile(r"[/\\]")


def reject_unsafe_filename(filename: str) -> None:
    """Raise unless ``filename`` is a plain name with no path component."""
    if (
        not filename
        or filename.strip() in {"", ".", ".."}
        or _PATH_SEPARATOR_RE.search(filename)
        or filename.startswith("..")
    ):
        raise TrainingDataError(
            "Filename must be a plain file name with no path separators."
        )


def validate_upload(filename: str, data: bytes) -> tuple[str, int]:
    """Validate one uploaded file, returning ``(kind, item_count)``.

    Raises :class:`TrainingDataError` with a message safe to show the user.
    """
    reject_unsafe_filename(filename)
    lower = filename.lower()
    if not (lower.endswith(".feature") or lower.endswith(".jsonl")):
        raise TrainingDataError("Only .feature and .jsonl files are accepted.")

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TrainingDataError("File must be valid UTF-8 encoded text.") from exc

    if lower.endswith(".jsonl"):
        return KIND_JSONL, validate_jsonl(text)

    doc, reason = parse_feature(text, origin=filename)
    if reason is not None:
        raise TrainingDataError(REASON_MESSAGES[reason])
    assert doc is not None
    reason = quality_reason(doc)
    if reason is not None:
        raise TrainingDataError(REASON_MESSAGES[reason])
    return KIND_FEATURE, len(doc.scenarios)


async def store_dataset(
    user_id: str,
    filename: str,
    data: bytes,
    db: AsyncSession,
) -> TrainingDataset:
    """Validate, store in Supabase Storage, and record one uploaded dataset.

    Unlike the .feature upload in `api/v1/bdd.py`, Storage is NOT best-effort
    here: it holds the only copy of the content, so a storage failure must fail
    the upload rather than leave a row pointing at nothing. The object is
    written first for the same reason.
    """
    kind, item_count = validate_upload(filename, data)
    # Belt and braces: validate_upload already refuses a filename with a path
    # component, but this is the line that builds the object key, so it does not
    # rely on a caller elsewhere having checked.
    reject_unsafe_filename(filename)

    dataset_id = uuid.uuid4()
    try:
        storage_path = await storage_service.upload_file(
            folder=FOLDER_TRAINING_DATA,
            path=f"{dataset_id}/{filename}",
            file_data=data,
            content_type="text/plain",
            user_id=user_id,
        )
    except StorageServiceError as exc:
        raise TrainingDataError(
            f"Could not store the file: {exc.message}"
        ) from exc

    # created_at is set here rather than left to the column default, which only
    # materialises at flush — this value is returned in the response, so it must
    # not depend on flush timing (same reasoning as BDD /save in Story 6.4).
    dataset = TrainingDataset(
        id=dataset_id,
        created_at=datetime.now(UTC),
        user_id=user_id,
        filename=filename,
        kind=kind,
        item_count=item_count,
        storage_path=storage_path,
        # Stamped at write time from the deployment's setting, never
        # re-evaluated when a dataset is built (Story 6.6).
        training_opt_in=settings.training_data_opt_in,
    )
    db.add(dataset)
    await db.commit()
    return dataset


async def discard_stored_object(dataset: TrainingDataset) -> None:
    """Remove a dataset's object from Storage, best-effort.

    Best-effort is Story 4.7's pattern: if Storage fails the caller still
    deletes the row, so a user can always clear a broken entry. Refusing would
    leave them stuck with something they cannot remove.

    Separate from `delete_dataset` so a bulk wipe can remove many objects
    before committing once, rather than committing per row.
    """
    try:
        # storage_path already contains the user_id prefix, so it is passed
        # whole rather than re-scoped.
        await storage_service.delete_file(folder="", path=dataset.storage_path)
    except StorageServiceError as exc:
        logger.warning(
            "Training dataset object not removed (row deleted anyway): %s",
            exc.message,
        )


async def delete_dataset(dataset: TrainingDataset, db: AsyncSession) -> None:
    """Delete an uploaded dataset's stored object and its row."""
    await discard_stored_object(dataset)
    await db.delete(dataset)
    await db.commit()
