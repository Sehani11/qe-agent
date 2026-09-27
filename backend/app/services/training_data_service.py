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

import csv
import hashlib
import io
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


# --- CSV authoring format ---------------------------------------------------
#
# CSV is an AUTHORING format only. It is converted to `.jsonl` pairs here, at
# upload, and is never stored or trained on as CSV — so nothing downstream
# (`build_dataset.py`, `--pairs-dir`, the trainer) needs to know it exists.
#
# Why it exists: a pair's assistant side is a JSON BDDGenerateResponse encoded
# as a STRING inside a JSON line, so hand-authoring one means nesting JSON in
# JSON and escaping every quote in the Gherkin. RUN_LOG.md has recorded since
# Run 1 that the corpus's real problem is DOMAIN — framework Gherkin describing
# CLI invocations rather than product behaviour — and the fix is human-authored
# product pairs. Until now the only way to write those was
# `training/corpus-product/*.yaml`, which needs an engineer and a repo checkout.
# A spreadsheet needs neither.
#
# The row model is one row per SCENARIO, grouped into one ticket (= one pair) by
# `ticket_id`. Ticket-level columns are read from a group's first row and may be
# left blank on its later rows.

#: Columns describing the ticket. Read from the group's first row.
CSV_TICKET_COLUMNS = (
    "ticket_id",
    "feature",
    "title",
    "as_a",
    "i_want",
    "so_that",
    "description",
)
#: The four that make a scenario. All four, or none (see `parse_csv_tickets`).
CSV_SCENARIO_COLUMNS = ("scenario", "given", "when", "then")
#: Columns describing one criterion and the scenario covering it.
CSV_ROW_COLUMNS = ("ac_number", "ac_text", *CSV_SCENARIO_COLUMNS)
#: Required header, in order. `source` is accepted as well but not required —
#: it carries provenance into `meta.provenance`, matching the YAML path.
CSV_COLUMNS = (*CSV_TICKET_COLUMNS, *CSV_ROW_COLUMNS)


def _cell(row: dict, column: str) -> str:
    """One cell, with its whitespace collapsed to single spaces.

    Collapsing is not cosmetic. Every one of these fields is rendered onto a
    SINGLE line — a Gherkin step, or an "AC3: ..." criterion — and a spreadsheet
    cell can hold newlines (Alt+Enter, or a pasted paragraph). A step carrying a
    newline produces broken Gherkin, and a criterion carrying one breaks the
    numbered list that `ac_clauses()` matches on.
    """
    return " ".join((row.get(column) or "").split())


def _read_csv_rows(text: str) -> list[tuple[int, dict]]:
    """Parse the CSV into ``(spreadsheet_row_number, row)`` pairs.

    Row numbers count the header as row 1, so the first data row is 2 — the
    number the author sees in their spreadsheet's gutter, which is the only row
    number worth putting in an error message.
    """
    reader = csv.reader(io.StringIO(text))
    try:
        header = [name.strip() for name in next(reader)]
    except StopIteration:
        raise TrainingDataError("The file is empty.") from None

    # A single-field header holding semicolons is an Excel export from a
    # semicolon-locale machine. Saying so beats "column 'ticket_id' is missing",
    # which is true but sends the author looking in the wrong place.
    if len(header) == 1 and ";" in header[0]:
        raise TrainingDataError(
            "This looks like a semicolon-separated export. Re-save it with "
            "comma separators (in Excel: Save As -> CSV UTF-8)."
        )

    missing = [name for name in CSV_COLUMNS if name not in header]
    if missing:
        raise TrainingDataError(
            f"Missing column(s): {', '.join(missing)}. The header row must name "
            f"every column: {', '.join(CSV_COLUMNS)}."
        )

    rows: list[tuple[int, dict]] = []
    for row_number, values in enumerate(reader, start=2):
        # Spreadsheets leave trailing blank lines behind on export, and a blank
        # line between ticket groups is a reasonable thing for an author to add.
        if not any(value.strip() for value in values):
            continue
        row = {
            name: (values[index] if index < len(values) else "")
            for index, name in enumerate(header)
        }
        rows.append((row_number, row))

    if not rows:
        raise TrainingDataError("The file has a header row but no data rows.")
    return rows


def parse_csv_tickets(text: str) -> list[dict]:
    """Group an authored CSV into ticket dicts.

    The dicts carry the same keys `training/corpus-product/*.yaml` declares, so
    both authoring formats can feed one renderer. Raises
    :class:`TrainingDataError` naming the offending spreadsheet row.
    """
    if PLACEHOLDER_RE.search(text):
        raise TrainingDataError(REASON_MESSAGES[REASON_PLACEHOLDER_TEXT])

    order: list[str] = []
    tickets: dict[str, dict] = {}
    #: ticket_id -> {ac_number: ac_text}. Built alongside the scenarios because
    #: the criteria list is REASSEMBLED from the rows rather than declared once:
    #: that is what keeps the format flat, with no list packed into a cell.
    criteria: dict[str, dict[int, str]] = {}

    for row_number, row in _read_csv_rows(text):
        ticket_id = _cell(row, "ticket_id")
        if not ticket_id:
            raise TrainingDataError(
                f"Row {row_number}: 'ticket_id' is empty. Every row has to say "
                "which ticket it belongs to.",
                row_number,
            )

        if ticket_id not in tickets:
            ticket: dict = {"id": ticket_id, "scenarios": []}
            for column in CSV_TICKET_COLUMNS[1:]:
                value = _cell(row, column)
                if not value:
                    raise TrainingDataError(
                        f"Row {row_number}: '{column}' is required on the first "
                        f"row of ticket {ticket_id}.",
                        row_number,
                    )
                ticket[column] = value
            source = _cell(row, "source")
            if source:
                ticket["source"] = source
            order.append(ticket_id)
            tickets[ticket_id] = ticket
            criteria[ticket_id] = {}
        else:
            ticket = tickets[ticket_id]
            # A blank cell inherits the group's value and a filled one must
            # agree. Both are normal — spreadsheets get sorted and filled down —
            # but a DISAGREEING value means two rows describe one ticket two
            # ways, and silently keeping the first would train on whichever
            # happened to sort first.
            for column in CSV_TICKET_COLUMNS[1:]:
                value = _cell(row, column)
                if value and value != ticket[column]:
                    raise TrainingDataError(
                        f"Row {row_number}: '{column}' says {value!r}, but "
                        f"ticket {ticket_id} already declared "
                        f"{ticket[column]!r}. Leave the cell blank to reuse the "
                        "first row's value.",
                        row_number,
                    )

        number_text = _cell(row, "ac_number")
        if not number_text:
            raise TrainingDataError(
                f"Row {row_number}: 'ac_number' is empty. Every row names the "
                "criterion it belongs to.",
                row_number,
            )
        try:
            number = int(number_text)
        except ValueError:
            raise TrainingDataError(
                f"Row {row_number}: 'ac_number' must be a whole number, not "
                f"{number_text!r}.",
                row_number,
            ) from None
        if number < 1:
            raise TrainingDataError(
                f"Row {row_number}: 'ac_number' must be 1 or greater.",
                row_number,
            )

        ac_text = _cell(row, "ac_text")
        if not ac_text:
            raise TrainingDataError(
                f"Row {row_number}: 'ac_text' is empty.", row_number
            )
        established = criteria[ticket_id].get(number)
        if established is not None and established != ac_text:
            raise TrainingDataError(
                f"Row {row_number}: AC{number} of {ticket_id} was declared as "
                f"{established!r} and is now {ac_text!r}. One number, one "
                "criterion — use a new number for a different criterion.",
                row_number,
            )
        criteria[ticket_id][number] = ac_text

        filled = [name for name in CSV_SCENARIO_COLUMNS if _cell(row, name)]
        if not filled:
            # A criterion with no scenario. Legal on purpose: the YAML path's
            # `ticket_warnings` warns about an uncovered clause rather than
            # refusing it, because it makes a WORSE example and not an invalid
            # one, and the author is the right person to judge which.
            continue
        if len(filled) < len(CSV_SCENARIO_COLUMNS):
            absent = [n for n in CSV_SCENARIO_COLUMNS if n not in filled]
            raise TrainingDataError(
                f"Row {row_number}: {', '.join(absent)} empty. A scenario needs "
                "all of scenario, given, when and then — or leave all four "
                "blank to declare a criterion that nothing covers.",
                row_number,
            )
        for column in ("given", "when", "then"):
            step = _cell(row, column)
            if not MIN_STEP_CHARS <= len(step) <= MAX_STEP_CHARS:
                raise TrainingDataError(
                    f"Row {row_number}: '{column}' must be between "
                    f"{MIN_STEP_CHARS} and {MAX_STEP_CHARS} characters "
                    f"(it is {len(step)}).",
                    row_number,
                )

        ticket["scenarios"].append(
            {
                "clause": number,
                "scenario": _cell(row, "scenario"),
                "given": _cell(row, "given"),
                "when": _cell(row, "when"),
                "then": _cell(row, "then"),
            }
        )

    for ticket_id in order:
        ticket = tickets[ticket_id]
        numbers = criteria[ticket_id]
        # The numbers must be exactly 1..N. `ac_clauses()` in
        # evaluation_metrics.py matches clauses by their "AC<n>" label, and the
        # rendered ticket numbers its criteria by position — so a gap would
        # print AC1/AC2 for criteria the scenarios cite as AC1/AC3 and
        # misattribute every clause after the gap.
        if set(numbers) != set(range(1, len(numbers) + 1)):
            raise TrainingDataError(
                f"Ticket {ticket_id}: acceptance criteria must be numbered 1 to "
                f"{len(numbers)} with no gaps; found "
                f"{', '.join(str(n) for n in sorted(numbers))}."
            )
        if not ticket["scenarios"]:
            raise TrainingDataError(
                f"Ticket {ticket_id}: no scenarios. Every ticket needs at least "
                "one row with scenario, given, when and then filled in."
            )
        if len(ticket["scenarios"]) > MAX_SCENARIOS:
            raise TrainingDataError(
                f"Ticket {ticket_id}: too many scenarios (limit "
                f"{MAX_SCENARIOS}). Split it into more than one ticket."
            )
        ticket["acceptance_criteria"] = [numbers[n] for n in sorted(numbers)]

    return [tickets[ticket_id] for ticket_id in order]


# --- Ticket -> training pair -------------------------------------------------
#
# Moved here from `training/build_product_pairs.py`, which the backend cannot
# import (`training/` is outside the backend Docker build context — see the
# module docstring). The YAML script keeps its own copy for now; if the two ever
# need to agree exactly, the script is the side that should import THIS.


def render_ticket(ticket: dict) -> str:
    """The user-side message: the ticket as the application receives it.

    Prose first, then the numbered criteria. The prose is what makes this
    realistic — real tickets carry a story and a description, and a bare
    "AC1: ... AC2: ..." block is not something any Jira ticket has looked like.

    The numbered list is not decoration either. `ac_clauses()` in
    app/services/evaluation_metrics.py finds clauses by matching "AC<n>" with a
    separator; a ticket written as pure prose yields ZERO clauses, which makes
    coverage() return None for every item and silently disables the metric.
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
    the rendered Gherkin comment readable. Emitting only the sentence parses as
    no clause at all.
    """
    criteria = ticket["acceptance_criteria"]
    scenarios = []
    for entry in ticket["scenarios"]:
        index = entry["clause"]
        if not 1 <= index <= len(criteria):
            raise TrainingDataError(
                f"Ticket {ticket['id']}: scenario {entry['scenario']!r} cites "
                f"AC{index}, but the ticket declares {len(criteria)} criteria."
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


def ticket_warnings(ticket: dict) -> list[str]:
    """Authoring problems that make a pair a worse example than no pair.

    Warnings rather than errors: none of these produce an invalid record, they
    produce a MISLEADING one, and the author is the right person to judge which.
    They are logged at upload rather than shown, because the upload response is
    a per-file accept/reject and these are neither.
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


def build_pair_record(ticket: dict) -> dict:
    """One ticket -> one training pair, in the shape build_dataset.py emits."""
    # Imported here rather than at module scope for the same reason
    # build_sample_jsonl does: `training/build_dataset.py` loads this module
    # early and must not pull the service graph in with it.
    from app.services.bdd_service import BDD_SYSTEM_PROMPT

    # The application must be able to parse anything we train it to emit.
    validated = BDDGenerateResponse.model_validate(
        {"scenarios": build_scenarios(ticket)}
    )
    if not validated.scenarios:
        raise TrainingDataError(f"Ticket {ticket['id']}: no scenarios.")

    return {
        "messages": [
            {"role": "system", "content": BDD_SYSTEM_PROMPT},
            {"role": "user", "content": render_ticket(ticket)},
            {"role": "assistant", "content": validated.model_dump_json()},
        ],
        # origin drives the train/holdout split, which is BY ORIGIN and never
        # per scenario. One ticket is one origin: its scenarios are related, and
        # splitting them across the boundary would leak. `process_upload_rows`
        # reads this back off the stored line and namespaces it under the
        # upload, so two uploads can never merge into one group.
        "meta": {
            "origin": ticket["id"],
            "scenario_count": len(validated.scenarios),
            "provenance": ticket.get("source", "csv-upload"),
        },
    }


def parse_csv_records(text: str) -> list[dict]:
    """Validate an authored CSV and return its training-pair records."""
    records: list[dict] = []
    for ticket in parse_csv_tickets(text):
        for warning in ticket_warnings(ticket):
            logger.warning("CSV training data: %s", warning)
        records.append(build_pair_record(ticket))
    return records


def csv_to_jsonl(text: str) -> tuple[str, int]:
    """Convert an authored CSV into the `.jsonl` payload that gets stored.

    The result is re-validated through `parse_jsonl_records` — the same function
    that guards a hand-written `.jsonl` upload — so a bug in the conversion
    cannot put a record into Storage that `build_dataset.py` would later reject.
    That check is what lets this module keep its promise (see the module
    docstring) while accepting a format the builder has never heard of.
    """
    records = parse_csv_records(text)
    payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n"
    parse_jsonl_records(payload)
    return payload, len(records)


def validate_csv(text: str) -> int:
    """Validate an authored CSV and return its pair count."""
    return len(parse_csv_records(text))


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
SAMPLE_CSV_FILENAME = "sample-training-pairs.csv"

#: The ticket prose the CSV sample needs and `_SAMPLE_PAIRS` does not carry:
#: a .jsonl pair's user side is free text, while a CSV row builds it from
#: named fields. The criteria and scenarios still come from `_SAMPLE_PAIRS`,
#: so the two samples describe the same behaviour.
_SAMPLE_CSV_TICKET: dict[str, str] = {
    "ticket_id": "SAMPLE-1",
    "title": "Sign in with email and password",
    "as_a": "registered user",
    "i_want": "to sign in with my email and password",
    "so_that": "I can reach my dashboard without asking anyone for access",
    "description": (
        "The sign-in form takes an email address and a password and either "
        "signs the user in or explains why it could not. A wrong password "
        "must not reveal whether the email address is registered."
    ),
}

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


def build_sample_csv() -> str:
    """Return a valid `.csv` file, ready to open in a spreadsheet and edit.

    One row per scenario, ticket columns filled on the first row only — the
    shape `parse_csv_tickets` reads back. Generated from `_SAMPLE_PAIRS` like
    the other two samples, so it cannot drift into something this module
    rejects.

    Cells are written by `csv.writer`, which handles the quoting. They are NOT
    run through the report exporter's formula-injection sanitiser: every value
    here is a literal in this file, so there is no untrusted data to neutralise,
    and reaching into `report_service` for a private helper would couple two
    unrelated modules to say nothing.
    """
    criteria_block, scenarios = _SAMPLE_PAIRS[0]
    # "AC1: ..." lines -> the criterion text, indexed by number.
    criteria = [line.split(":", 1)[1].strip() for line in criteria_block.splitlines()]

    buffer = io.StringIO()
    # lineterminator, because csv.writer defaults to CRLF and the rest of this
    # module's generated text uses "\n".
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)

    for index, scenario in enumerate(scenarios):
        clause = int(scenario["source_ac_clause"].removeprefix("AC"))
        if index == 0:
            ticket_cells = [
                _SAMPLE_CSV_TICKET["ticket_id"],
                scenario["feature"],
                _SAMPLE_CSV_TICKET["title"],
                _SAMPLE_CSV_TICKET["as_a"],
                _SAMPLE_CSV_TICKET["i_want"],
                _SAMPLE_CSV_TICKET["so_that"],
                _SAMPLE_CSV_TICKET["description"],
            ]
        else:
            # Blank inherits from the group's first row. Showing that in the
            # sample is the point: it is the rule least likely to be guessed.
            ticket_cells = [_SAMPLE_CSV_TICKET["ticket_id"], *[""] * 6]
        writer.writerow(
            [
                *ticket_cells,
                clause,
                criteria[clause - 1],
                scenario["scenario"],
                scenario["given"],
                scenario["when"],
                scenario["then"],
            ]
        )

    return buffer.getvalue()


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


@dataclass
class ValidatedUpload:
    """An accepted upload: what to record, and the bytes to store.

    ``data`` is not always the bytes that arrived, and ``stored_filename`` not
    always the name they arrived under. A `.csv` upload is an AUTHORING format:
    it is converted to `.jsonl` pairs here so that nothing downstream — the
    dataset builder, `--pairs-dir`, the trainer — has to know CSV exists. The
    row keeps the name the user uploaded; only the object takes the new one.
    """

    kind: str
    item_count: int
    data: bytes
    stored_filename: str


def validate_upload(filename: str, data: bytes) -> ValidatedUpload:
    """Validate one uploaded file and return what should be stored for it.

    Raises :class:`TrainingDataError` with a message safe to show the user.
    """
    reject_unsafe_filename(filename)
    lower = filename.lower()
    if not lower.endswith((".feature", ".jsonl", ".csv")):
        raise TrainingDataError(
            "Only .feature, .jsonl and .csv files are accepted."
        )

    # utf-8-sig for CSV only: Excel's "CSV UTF-8" writes a BOM, which would
    # otherwise land inside the first header name and make `ticket_id`
    # unfindable — a baffling failure for the one format authored in Excel. The
    # other two keep plain utf-8; their accept/reject behaviour is pinned by
    # tests and is not what this change is about.
    try:
        text = data.decode("utf-8-sig" if lower.endswith(".csv") else "utf-8")
    except UnicodeDecodeError as exc:
        hint = (
            " Save it as CSV UTF-8 rather than an ANSI or Latin-1 export."
            if lower.endswith(".csv")
            else ""
        )
        raise TrainingDataError(
            f"File must be valid UTF-8 encoded text.{hint}"
        ) from exc

    if lower.endswith(".csv"):
        payload, pairs = csv_to_jsonl(text)
        # Stored as .jsonl because that is what the bytes now are. Naming the
        # object .csv would leave the only copy of the content contradicting its
        # own extension for anyone who ever looks in the bucket.
        return ValidatedUpload(
            kind=KIND_JSONL,
            item_count=pairs,
            data=payload.encode("utf-8"),
            stored_filename=f"{filename[:-4]}.jsonl",  # strip ".csv"
        )

    if lower.endswith(".jsonl"):
        return ValidatedUpload(KIND_JSONL, validate_jsonl(text), data, filename)

    doc, reason = parse_feature(text, origin=filename)
    if reason is not None:
        raise TrainingDataError(REASON_MESSAGES[reason])
    assert doc is not None
    reason = quality_reason(doc)
    if reason is not None:
        raise TrainingDataError(REASON_MESSAGES[reason])
    return ValidatedUpload(KIND_FEATURE, len(doc.scenarios), data, filename)


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

    A `.csv` upload is converted to `.jsonl` pairs before it is stored (see
    :class:`ValidatedUpload`), so the row's `kind` is "jsonl" while its
    `filename` still says .csv — the name the user chose is what makes the entry
    recognisable to them in the list.
    """
    validated = validate_upload(filename, data)
    # Belt and braces: validate_upload already refuses a filename with a path
    # component, but this is the line that builds the object key, so it does not
    # rely on a caller elsewhere having checked.
    reject_unsafe_filename(validated.stored_filename)

    dataset_id = uuid.uuid4()
    try:
        storage_path = await storage_service.upload_file(
            folder=FOLDER_TRAINING_DATA,
            path=f"{dataset_id}/{validated.stored_filename}",
            file_data=validated.data,
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
        kind=validated.kind,
        item_count=validated.item_count,
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
