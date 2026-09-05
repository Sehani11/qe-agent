"""Verification helpers shared by the agentic verification service.

Once the pipeline was BDD parsing + a direct LLM pass over pre-fetched files;
that legacy runner was removed when the UI moved to /run-agentic. What remains
is the shared substrate: Gherkin parsing, file-relevance selection, RAG-block
formatting, and the single sources of truth for the verdict wire shape and the
persisted VerificationResult row.
"""

import re
import uuid
from difflib import SequenceMatcher

from app.models.verification_result import VerificationResult
from app.schemas.verification import FetchedFile, RagContextItem, VerificationVerdict
from app.services import knowledge_service as _ks
from app.services.evaluation_metrics import NEAR_DUPLICATE_RATIO

_SCENARIO_PATTERN = re.compile(
    r"^\s*(Scenario(?:\s+Outline)?)\s*:\s*(.+?)\s*$"
)

#: The traceability comment `scenariosToGherkin` writes above each scenario.
_SOURCE_AC_PATTERN = re.compile(r"^\s*#\s*Source AC\s*:\s*(.+?)\s*$", re.IGNORECASE)

_COMMENT_OR_BLANK_PATTERN = re.compile(r"^\s*(#.*)?$")

#: Punctuation stripper, matching the evaluation metrics' normalisation so a
#: scenario judged a duplicate here would be judged one there too.
_NORMALISE_RE = re.compile(r"[^a-z0-9\s]+")

#: Below this many normalised characters, only an exact repeat counts as a
#: duplicate. A real Gherkin scenario runs well past it; the floor exists so
#: two terse scenarios that differ by a single word are never merged.
_MIN_FUZZY_MATCH_LENGTH = 120

#: Similarity required to call two scenarios duplicates when they already share
#: a title AND an acceptance-criterion clause. Agreeing on both is near-certainly
#: one question asked twice, so less textual overlap is needed than for an
#: unrelated pair — but not none, because one AC can legitimately carry several
#: scenarios that differ in what they actually check.
#:
#: Calibrated against measured pairs rather than picked: reworded duplicates
#: scored 0.48 and 0.56, while a same-title pair testing genuinely different
#: things scored 0.36. The margin is real but modest, so the value sits nearer
#: the lower duplicate than the upper distinct case — and the identity gate in
#: front of it (byte-identical title AND clause) is what carries most of the
#: confidence. Both cases are pinned by tests; retune with those, not by feel.
_SAME_INTENT_RATIO = 0.45

_FILE_CONTENT_TRUNCATE = 24_000  # chars per file sent to LLM
_MAX_FILES_PER_SCENARIO = 6      # top-N most relevant files per scenario call

# Gherkin keywords and common English stopwords to ignore when scoring relevance
_STOPWORDS = frozenset(
    {
        "given", "when", "then", "and", "but", "the", "for", "with",
        "that", "should", "user", "scenario", "feature", "source", "are",
        "not", "has", "have", "from", "this", "will", "can",
    }
)


def _score_file_relevance(scenario: dict, file: FetchedFile) -> int:
    """Return a relevance score for a file against a scenario."""
    combined = (scenario["title"] + " " + scenario["text"]).lower()
    words = re.findall(r"\b[a-z]{3,}\b", combined)
    keywords = {w for w in words if w not in _STOPWORDS}
    if not keywords:
        return 0

    path_lower = file.path.lower()
    # Only scan the first 2 000 chars of content to keep scoring fast
    content_sample = file.content[:2_000].lower()

    score = 0
    for kw in keywords:
        if kw in path_lower:
            score += 3   # path match is a strong signal
        if kw in content_sample:
            score += 1
    return score


def _select_relevant_files(
    scenario: dict, files: list[FetchedFile]
) -> list[FetchedFile]:
    """Return the top-N most relevant files for this scenario."""
    if len(files) <= _MAX_FILES_PER_SCENARIO:
        return files
    scored = sorted(
        files, key=lambda f: _score_file_relevance(scenario, f), reverse=True
    )
    return scored[:_MAX_FILES_PER_SCENARIO]


def deduplicate_scenarios(scenarios: list[dict]) -> tuple[list[dict], int]:
    """Drop scenarios that repeat one already in the list.

    Returns ``(kept, dropped_count)``, preserving the original order.

    A BDD file accumulates duplicates easily — the model emits two phrasings of
    one acceptance criterion, or a regenerated file is appended to an earlier
    one — and verification pays full price per scenario. Deciding the same
    question twice costs twice and, worse, can answer it two different ways in
    the same report, which is how a reader loses confidence in all of it.

    Matching is deliberately lexical (the same normalise-and-compare used by the
    evaluation metrics, at the same threshold): unarguable and reproducible,
    where a semantic match would silently merge two scenarios that differ in a
    detail exactly one of them was written to test.

    Short scenarios must match exactly to be dropped. Similarity is unreliable
    over a few words — "when they do A" and "when they do B" score as near
    identical because almost every character is shared boilerplate — and the
    cost of being wrong is asymmetric: an extra scenario costs one call, while a
    wrongly dropped one silently deletes coverage the reader still believes they
    have.

    Two scenarios that share a title *and* an acceptance-criterion clause are
    matched on a lower similarity bar. Agreeing on both is strong evidence of
    one question asked twice — the generator rephrasing a scenario it already
    wrote — which raw text similarity misses when the rewording is thorough. The
    bar is lowered rather than removed, since one AC can legitimately carry
    several scenarios that check genuinely different things.
    """
    kept: list[dict] = []
    kept_keys: list[tuple[str, str]] = []
    dropped = 0

    for scenario in scenarios:
        text = _normalise_scenario(scenario)
        identity = _scenario_identity(scenario)
        if any(
            _is_duplicate(text, identity, earlier_text, earlier_identity)
            for earlier_text, earlier_identity in kept_keys
        ):
            dropped += 1
            continue
        kept.append(scenario)
        kept_keys.append((text, identity))

    return kept, dropped


def _is_duplicate(
    text: str, identity: str, earlier_text: str, earlier_identity: str
) -> bool:
    """Whether one scenario repeats another already kept."""
    if text == earlier_text:
        return True

    # Same title and same acceptance criterion: a much weaker textual match is
    # enough, because the pair already agrees on what it is testing and why.
    if identity and identity == earlier_identity:
        return (
            SequenceMatcher(None, text, earlier_text).ratio() >= _SAME_INTENT_RATIO
        )

    if (
        len(text) < _MIN_FUZZY_MATCH_LENGTH
        or len(earlier_text) < _MIN_FUZZY_MATCH_LENGTH
    ):
        return False
    return SequenceMatcher(None, text, earlier_text).ratio() >= NEAR_DUPLICATE_RATIO


def _scenario_identity(scenario: dict) -> str:
    """Title plus acceptance clause, normalised — "" when there is no clause.

    Empty is deliberate for a file with no traceability comments: without a
    clause, a shared title alone is far too weak to merge on.
    """
    clause = (scenario.get("ac_clause") or "").strip()
    if not clause:
        return ""
    combined = f"{scenario.get('title', '')} {clause}"
    return " ".join(_NORMALISE_RE.sub(" ", combined.lower()).split())


def _normalise_scenario(scenario: dict) -> str:
    """Title plus steps, lowercased and stripped of punctuation.

    The title alone is too weak — two scenarios can share a title and test
    different things — and the steps alone too strong, since boilerplate Givens
    make unrelated scenarios look alike.
    """
    combined = f"{scenario.get('title', '')} {scenario.get('text', '')}"
    return " ".join(_NORMALISE_RE.sub(" ", combined.lower()).split())


def truncate_with_notice(content: str, path: str) -> str:
    """Cap one file's content for a prompt, announcing any truncation.

    The evidence block carries the files the user explicitly selected, so a file
    cut short here is one the verdict is *expected* to be based on. Cutting it
    silently is what turns "I could not see it" into "it is not implemented":
    at the old 3 000-char cap a 7 400-char route file ended one line above its
    filter implementation, and every filter was duly reported missing.

    Content that fits is returned unchanged, so the common case is unannotated.
    """
    total = len(content)
    if total <= _FILE_CONTENT_TRUNCATE:
        return content
    return (
        content[:_FILE_CONTENT_TRUNCATE]
        + f"\n\n[TRUNCATED — {_FILE_CONTENT_TRUNCATE} of {total} chars of "
        f"'{path}' shown; this is NOT the whole file. Call get_file_contents "
        f"with path='{path}' and offset={_FILE_CONTENT_TRUNCATE} to read the "
        f"rest BEFORE concluding that anything is missing from it.]"
    )


def parse_bdd_scenarios(bdd_content: str) -> list[dict]:
    """Parse Gherkin text and return a list of scenario dicts.

    Each dict has keys: id (UUID string), title (str), text (str), and
    ac_clause (str, empty when the file carries no traceability comment).
    Returns an empty list if no scenarios are found.

    Scenario boundaries are drawn at lines, not at the next header's match
    offset. The generated Gherkin puts a ``# Source AC:`` comment *above* each
    scenario, so a block that simply runs to the next header swallows the
    comment introducing that next scenario: every scenario then carries its
    neighbour's acceptance criterion into the verification prompt, and the first
    scenario's own clause is lost entirely. It also makes two identical
    scenarios compare as different, because each ends with a different
    neighbour — which is how duplicates survive deduplication.
    """
    lines = bdd_content.splitlines()
    headers = [i for i, line in enumerate(lines) if _SCENARIO_PATTERN.match(line)]
    scenarios: list[dict] = []

    for position, header in enumerate(headers):
        next_header = (
            headers[position + 1] if position + 1 < len(headers) else len(lines)
        )

        # Walk back off the trailing comment/blank block: it introduces the NEXT
        # scenario. Never past the header itself, so a scenario with no body
        # still keeps its title line.
        end = next_header
        while end > header + 1 and _COMMENT_OR_BLANK_PATTERN.match(lines[end - 1]):
            end -= 1

        match = _SCENARIO_PATTERN.match(lines[header])
        scenarios.append(
            {
                "id": str(uuid.uuid4()),
                "title": match.group(2).strip(),
                "text": "\n".join(lines[header:end]).strip(),
                "ac_clause": _leading_ac_clause(lines, header),
            }
        )

    return scenarios


def _leading_ac_clause(lines: list[str], header: int) -> str:
    """The ``# Source AC:`` clause introducing the scenario at ``header``.

    Scans back over the contiguous comment/blank block immediately above the
    scenario and stops at the first real Gherkin line, so a clause can never be
    picked up from an earlier scenario. Returns "" when the file carries no
    traceability comments, which is normal for hand-written feature files.
    """
    index = header - 1
    while index >= 0 and _COMMENT_OR_BLANK_PATTERN.match(lines[index]):
        match = _SOURCE_AC_PATTERN.match(lines[index])
        if match:
            return match.group(1).strip()
        index -= 1
    return ""


def format_rag_block(chunks: list[dict] | None) -> str:
    """Format retrieved knowledge chunks as a PROJECT CONTEXT prompt block.

    Returns "" when there are no chunks. Shared by the direct and agentic
    verification prompts so both surface knowledge-base context identically.
    """
    if not chunks:
        return ""
    lines = [
        "PROJECT CONTEXT:",
        "The following knowledge base excerpts are relevant to this scenario:",
    ]
    for chunk in chunks:
        lines.append(f"[{chunk['source'].upper()}:{chunk['source_id']}]")
        # Full chunk text when available; the 300-char snippet is a UI/persistence
        # artefact and loses most of the retrieved evidence.
        lines.append(chunk.get("text") or chunk.get("snippet", ""))
        lines.append("")
    return "\n".join(lines)


def rag_payload_from_chunks(chunks: list[dict]) -> list[dict] | None:
    """Normalize retrieved chunks into a rag_context payload (or None if empty)."""
    if not chunks:
        return None
    items = [
        RagContextItem(
            source=c["source"],
            source_id=c["source_id"],
            snippet=c["snippet"],
            title=c.get("title", ""),
            url=c.get("url", ""),
        )
        for c in chunks
    ]
    return [item.model_dump() for item in items]


def verdict_event_dict(
    verdict: VerificationVerdict,
    rag_context_payload: list[dict] | None,
    usage: dict | None = None,
) -> dict:
    """Build the SSE ``verdict`` event payload — shared by both verification paths.

    Single source of truth for the verdict wire shape so the direct and agentic
    services can't silently diverge on fields (e.g. rag_context).

    ``usage`` carries what the agent loop cost for this scenario — rounds and
    token counts. Only the agentic path has a loop to measure, so it stays
    None on the direct path rather than being faked with zeros, which would
    read as "this run was free" instead of "not applicable here".
    """
    return {
        "type": "verdict",
        "scenario_id": verdict.scenario_id,
        "scenario_title": verdict.scenario_title,
        "status": verdict.status,
        "justification": verdict.justification,
        "code_reference": verdict.code_reference.model_dump(),
        "github_links": verdict.github_links,
        "implementation_suggestion": verdict.implementation_suggestion,
        "rag_context": rag_context_payload,
        "usage": usage,
    }


def build_verification_result(
    session_id: str,
    user_id: str,
    verdict: VerificationVerdict,
    rag_context_payload: list[dict] | None,
    mode: str | None = None,
    github_input: str | None = None,
) -> VerificationResult:
    """Construct a VerificationResult row — shared by both verification paths.

    Persistence (flush / savepoint) stays with the caller; this only builds the
    ORM object so the column list has one source of truth.

    ``mode`` and ``github_input`` record the source the verdict was produced
    against, so a revisited session can show it and restore the workspace's
    source field. They default to None only so the signature stays compatible
    with callers that predate the columns; the live path always passes them.
    """
    return VerificationResult(
        session_id=session_id,
        user_id=user_id,
        scenario_id=uuid.UUID(verdict.scenario_id),
        scenario_title=verdict.scenario_title,
        status=verdict.status,
        justification=verdict.justification,
        code_reference=verdict.code_reference.model_dump(),
        github_links=verdict.github_links,
        implementation_suggestion=verdict.implementation_suggestion,
        rag_context=rag_context_payload,
        verification_mode=mode,
        github_input=github_input,
    )


async def retrieve_rag_per_scenario(
    user_id: str,
    scenarios: list[dict],
    enabled: bool,
    project_id: uuid.UUID | str | None = None,
    config=None,
) -> list[list[dict]]:
    """Retrieve knowledge-base context for EACH scenario, in parallel.

    Querying per scenario (with that scenario's own Gherkin + AC text) yields
    context relevant to that specific scenario, which is more accurate than a
    single shared query over the whole BDD. Returns a list aligned with
    ``scenarios`` (each element the chunks for that scenario).

    ``enabled`` is the caller's per-run opt-in (the ``use_knowledge_base`` request
    flag) — when False, no query runs and every scenario gets an empty list. Uses
    ``query_knowledge_base_batch`` (one embeddings request for all scenarios,
    shared client, per-query failure isolation), which never raises and stays
    within the NFR-P6 budget. Shared by the direct and agentic verification paths.
    """
    if not enabled:
        return [[] for _ in scenarios]

    return await _ks.query_knowledge_base_batch(
        user_id, [s["text"] for s in scenarios], project_id=project_id, config=config
    )
