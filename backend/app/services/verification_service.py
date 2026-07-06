"""LLM Verification Service.

Parses BDD scenarios from Gherkin content, evaluates each against fetched
source code via the LLM provider, and streams per-scenario verdicts as SSE.

Architecture rules enforced here:
- Never call LLM SDKs directly — always via LLMProvider.generate_structured()
- No business logic belongs in route handlers
- DB persistence happens after each yield (streaming-safe)
"""

import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.verification_result import VerificationResult
from app.schemas.verification import FetchedFile, VerificationVerdict
from app.services.llm.provider import LLMProvider, LLMProviderError

logger = logging.getLogger(__name__)

_SCENARIO_PATTERN = re.compile(
    r"^\s*(Scenario(?:\s+Outline)?)\s*:\s*(.+?)$", re.MULTILINE
)

_SYSTEM_PROMPT = """\
You are a senior QA engineer and code reviewer. Your task is to evaluate
whether a given BDD scenario is covered by the provided source code.

Respond ONLY with a valid JSON object. No explanations outside the JSON.
Schema: {
  "scenario_id": "<same as input>",
  "scenario_title": "<same as input>",
  "status": "pass" | "fail",
  "justification": "<natural-language explanation referencing specific code>",
  "code_reference": {"file": "<path>", "function": "<name>", "line": <integer>},
  "github_links": ["<exact file path from [F:...] tag used as evidence>"],
  "implementation_suggestion": "<actionable guidance for fixing the gap>" | null
}

Rules:
- status="pass" only if the scenario's acceptance criteria is FULLY handled in the code
- status="fail" if ANY part is missing, incomplete, or incorrect
- justification MUST reference specific code (function name, variable, condition)
- implementation_suggestion MUST be non-null and actionable when status="fail"
- implementation_suggestion MUST be null when status="pass"\
"""

_FILE_CONTENT_TRUNCATE = 3_000   # chars per file sent to LLM
_MAX_FILES_PER_SCENARIO = 6      # top-N most relevant files per scenario call

# Gherkin keywords and common English stopwords to ignore when scoring relevance
_STOPWORDS = frozenset(
    {
        "given", "when", "then", "and", "but", "the", "for", "with",
        "that", "should", "user", "scenario", "feature", "source", "are",
        "not", "has", "have", "from", "this", "will", "can",
    }
)


def _resolve_github_links(
    links: list[str], files: list[FetchedFile]
) -> list[str]:
    """Replace relative file paths in LLM-generated links with real GitHub URLs.

    The LLM returns file paths (e.g. ``app/auth/login.py``) as evidence.
    This maps each path back to the ``github_url`` on the corresponding
    ``FetchedFile``, falling back to suffix-matching when the LLM omits a
    leading directory segment.  Absolute URLs are kept as-is.
    """
    url_map = {f.path: f.github_url for f in files if f.github_url}
    resolved: list[str] = []
    for link in links:
        if link.startswith("http://") or link.startswith("https://"):
            resolved.append(link)
            continue
        # Exact match
        if link in url_map:
            resolved.append(url_map[link])
            continue
        # Suffix match (LLM may strip leading dirs)
        matched = next(
            (url for path, url in url_map.items() if path.endswith(link) or link.endswith(path)),
            None,
        )
        resolved.append(matched if matched else link)
    return resolved


def _score_file_relevance(scenario: dict, file: FetchedFile) -> int:
    """Return a relevance score for a file against a scenario (higher = more relevant)."""
    words = re.findall(r"\b[a-z]{3,}\b", (scenario["title"] + " " + scenario["text"]).lower())
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
    """Return the most relevant files for this scenario, capped at _MAX_FILES_PER_SCENARIO."""
    if len(files) <= _MAX_FILES_PER_SCENARIO:
        return files
    scored = sorted(files, key=lambda f: _score_file_relevance(scenario, f), reverse=True)
    return scored[:_MAX_FILES_PER_SCENARIO]


def parse_bdd_scenarios(bdd_content: str) -> list[dict]:
    """Parse Gherkin text and return a list of scenario dicts.

    Each dict has keys: id (UUID string), title (str), text (str).
    Returns an empty list if no scenarios are found.
    """
    matches = list(_SCENARIO_PATTERN.finditer(bdd_content))
    scenarios: list[dict] = []

    for i, match in enumerate(matches):
        title = match.group(2).strip()
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(bdd_content)
        text = bdd_content[start:end].strip()
        scenarios.append(
            {
                "id": str(uuid.uuid4()),
                "title": title,
                "text": text,
            }
        )

    return scenarios


def _build_verification_prompt(
    scenario: dict,
    fetched_files: list[FetchedFile],
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a single scenario evaluation.

    Only the most relevant files are included (capped at _MAX_FILES_PER_SCENARIO)
    and each file is truncated to _FILE_CONTENT_TRUNCATE chars to stay within
    token limits.
    """
    relevant = _select_relevant_files(scenario, fetched_files)

    lines = [
        f"SCENARIO_ID:{scenario['id']}",
        f"TITLE:{scenario['title']}",
        scenario["text"],
        "",
        "CODE:",
    ]

    for file in relevant:
        lines.append(f"[F:{file.path}]")
        lines.append(file.content[:_FILE_CONTENT_TRUNCATE])
        lines.append("[/F]")

    return _SYSTEM_PROMPT, "\n".join(lines)


async def run_verification(
    session_id: str,
    user_id: str,
    bdd_content: str,
    fetched_files: list[FetchedFile],
    llm: LLMProvider,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    """Stream per-scenario verdicts as SSE events.

    Yields:
        SSE-formatted strings ending with double newline.
        - ``data: {"type": "verdict", ...}\\n\\n`` per scenario
        - ``data: {"type": "complete", ...}\\n\\n`` after all scenarios
        - ``data: {"type": "error", ...}\\n\\n`` on critical or per-scenario failures
    """
    # Filter truncation-notice artefacts inserted by fetch_full_repo
    filtered_files = [f for f in fetched_files if not f.path.startswith("[WARNING]")]

    scenarios = parse_bdd_scenarios(bdd_content)
    if not scenarios:
        yield _sse_event(
            {"type": "error", "message": "No BDD scenarios found in content"}
        )
        return

    total = len(scenarios)
    passed = 0
    failed = 0

    for scenario in scenarios:
        system_prompt, user_prompt = _build_verification_prompt(
            scenario, filtered_files
        )

        try:
            verdict_dict = await llm.generate_structured(user_prompt, system_prompt)
        except LLMProviderError as exc:
            yield _sse_event({"type": "error", "message": f"LLM call failed: {exc}"})
            continue

        # Resolve relative file paths in github_links to absolute GitHub URLs
        if "github_links" in verdict_dict:
            verdict_dict["github_links"] = _resolve_github_links(
                verdict_dict["github_links"], filtered_files
            )

        try:
            verdict = VerificationVerdict(**verdict_dict)
        except Exception as exc:
            yield _sse_event(
                {
                    "type": "error",
                    "message": (
                        f"LLM returned malformed verdict"
                        f" for '{scenario['title']}': {exc}"
                    ),
                }
            )
            continue

        # Yield verdict SSE event
        verdict_event: dict = {
            "type": "verdict",
            "scenario_id": verdict.scenario_id,
            "scenario_title": verdict.scenario_title,
            "status": verdict.status,
            "justification": verdict.justification,
            "code_reference": verdict.code_reference.model_dump(),
            "github_links": verdict.github_links,
            "implementation_suggestion": verdict.implementation_suggestion,
        }
        yield _sse_event(verdict_event)

        if verdict.status == "pass":
            passed += 1
        else:
            failed += 1

        # Persist result to DB after yielding
        try:
            result = VerificationResult(
                session_id=session_id,
                user_id=user_id,
                scenario_id=uuid.UUID(verdict.scenario_id),
                scenario_title=verdict.scenario_title,
                status=verdict.status,
                justification=verdict.justification,
                code_reference=verdict.code_reference.model_dump(),
                github_links=verdict.github_links,
                implementation_suggestion=verdict.implementation_suggestion,
            )
            db.add(result)
            await db.flush()
        except Exception as exc:
            # Persistence failure must not interrupt the SSE stream, but log it
            logger.warning(
                "Failed to persist verdict for session=%s scenario=%r: %s",
                session_id,
                scenario["title"],
                exc,
                exc_info=True,
            )

    yield _sse_event(
        {"type": "complete", "total": total, "passed": passed, "failed": failed}
    )


def _sse_event(data: dict) -> str:
    """Serialize a dict to an SSE data line ending with double newline."""
    return f"data: {json.dumps(data)}\n\n"
