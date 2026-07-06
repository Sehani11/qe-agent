"""Agentic Verification Service (Story 2.5).

Replaces the two-step fetch+verify flow with a single agentic run where the
LLM calls GitHub tools on demand until it has sufficient evidence to verdict.

Architecture rules enforced here:
- Never call LLM SDKs directly — always via LLMProvider.generate_with_tools()
- No business logic in route handlers
- DB persistence happens after each yield (streaming-safe)
- Tools live in github_tools.py — this service only orchestrates
"""

import json
import logging
import uuid
from collections.abc import AsyncGenerator

from json_repair import repair_json

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.verification_result import VerificationResult
from app.schemas.verification import VerificationVerdict
from app.services import github_tools
from app.services.github_service import (
    GitHubServiceError,
    _parse_github_blob_url,
    _parse_pr_url,
    _parse_repo_url,
)
from app.services.github_tools import GITHUB_TOOL_SCHEMAS
from app.services.llm.provider import LLMProvider, LLMProviderError
from app.services.verification_service import parse_bdd_scenarios

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior QA engineer. For the given BDD scenario, use the available \
GitHub tools to find the relevant implementation code, then evaluate whether \
the code fully implements the scenario. You MUST call at least one tool before \
forming a verdict.

Strategy (follow this order):
1. Try search_code with relevant keywords. If it returns an empty list (common \
for private repos), move to step 2.
2. Call list_directory("") to see the root structure, then list_directory on \
relevant subdirectories (e.g. components/auth, actions, lib, services, app/api).
3. CRITICAL: Once you identify a relevant file, you MUST call get_file_contents \
on it and read the actual code before forming a verdict. Never base a verdict \
solely on a file's name — always read the contents.
4. If a page delegates to a component (e.g. LoginForm, RegisterForm), read the \
component file too, not just the page file.

After gathering sufficient evidence, respond with a JSON verdict matching this schema:
{
  "scenario_id": "<same as input>",
  "scenario_title": "<same as input>",
  "status": "pass" | "fail",
  "justification": "<natural-language explanation referencing specific code>",
  "code_reference": {"file": "<path>", "function": "<name>", "line": <integer>},
  "github_links": ["<full GitHub blob URL e.g. https://github.com/org/repo/blob/main/path/to/file.py>"],
  "implementation_suggestion": "<actionable guidance for fixing the gap>" | null
}

Rules:
- status="pass" only if the scenario's acceptance criteria is FULLY handled in the code
- status="fail" if ANY part is missing, incomplete, or incorrect
- justification MUST reference specific code (function name, variable, condition)
- implementation_suggestion MUST be non-null and actionable when status="fail"
- implementation_suggestion MUST be null when status="pass"
- In github_links, always use full GitHub URLs in the format \
https://github.com/{repo}/blob/{ref}/{path}
"""


def _extract_repo_from_input(mode: str, github_input: str) -> str:
    """Return 'owner/repo' string from whatever the user entered."""
    if mode == "full_repo":
        owner, repo = _parse_repo_url(github_input)
        return f"{owner}/{repo}"
    elif mode == "pull_request":
        owner, repo, _ = _parse_pr_url(github_input)
        return f"{owner}/{repo}"
    elif mode == "exact_files":
        first_url = github_input.strip().splitlines()[0]
        owner, repo, _, _ = _parse_github_blob_url(first_url)
        return f"{owner}/{repo}"
    raise GitHubServiceError(f"Unknown mode: {mode}")


def _make_tool_executor(pat: str, repo: str) -> tuple:
    """Return (executor, tool_errors, tool_successes) bound to a PAT and default repo.

    tool_errors accumulates GitHubServiceError messages; tool_successes counts
    calls that returned data. Callers must create a new executor per scenario
    to avoid cross-scenario bleed.
    """
    tool_errors: list[str] = []
    tool_successes: list[bool] = []

    async def executor(tool_name: str, tool_args: dict) -> str:
        tool_args.setdefault("repo", repo)
        try:
            if tool_name == "search_code":
                result = await github_tools.search_code(
                    query=tool_args["query"],
                    repo=tool_args["repo"],
                    pat=pat,
                )
            elif tool_name == "get_file_contents":
                result = await github_tools.get_file_contents(
                    repo=tool_args["repo"],
                    path=tool_args["path"],
                    pat=pat,
                    ref=tool_args.get("ref", "HEAD"),
                )
            elif tool_name == "list_directory":
                result = await github_tools.list_directory(
                    repo=tool_args["repo"],
                    path=tool_args["path"],
                    pat=pat,
                    ref=tool_args.get("ref", "HEAD"),
                )
            else:
                return f"Unknown tool: {tool_name}"
            tool_successes.append(True)
            return result
        except GitHubServiceError as exc:
            error_msg = f"GitHub API error: {exc.message}"
            tool_errors.append(error_msg)
            return error_msg

    return executor, tool_errors, tool_successes


def _resolve_github_links(links: list[str], repo: str) -> list[str]:
    """Ensure all links are absolute GitHub blob URLs.

    If the LLM returns a bare file path instead of a full URL, convert it
    using the repo string.
    """
    resolved: list[str] = []
    for link in links:
        if link.startswith("http://") or link.startswith("https://"):
            resolved.append(link)
        else:
            resolved.append(f"https://github.com/{repo}/blob/HEAD/{link}")
    return resolved


def _sse_event(data: dict) -> str:
    """Serialize a dict to an SSE data line ending with double newline."""
    return f"data: {json.dumps(data)}\n\n"


# Keys that mark a dict as an actual verdict (vs. a stray preamble object the
# model may emit before it, e.g. {"type": "message"} or {"reasoning": ...}).
_VERDICT_MARKER_KEYS = frozenset({"scenario_id", "status", "justification"})


def _looks_like_verdict(obj: object) -> bool:
    """True if obj is a non-empty dict carrying at least one verdict field."""
    return isinstance(obj, dict) and bool(_VERDICT_MARKER_KEYS & obj.keys())


def _extract_json_from_text(text: str) -> dict:
    """Extract and parse the verdict JSON object from an LLM response string.

    Uses json_repair to handle all common LLM JSON quirks: markdown fences,
    trailing commas, missing commas, unescaped quotes, control characters,
    Python literals (None/True/False), and single-line comments.

    The model sometimes emits a preamble object (e.g. ``{"type": "message"}``
    or a reasoning blob) *before* the real verdict, in which case json_repair
    returns a list. We therefore scan every parsed object and prefer the one
    that looks like a verdict, only falling back to the first arbitrary dict
    when none match — otherwise we'd validate the junk object and fail.
    """
    # Try from the first '{', then fall back to the full text
    candidates = []
    start = text.find("{")
    if start != -1:
        candidates.append(text[start:])
    candidates.append(text)  # full text as last resort

    first_dict: dict | None = None
    last_exc: Exception = ValueError("No JSON object found in LLM response")
    for candidate in candidates:
        try:
            repaired = repair_json(candidate, return_objects=True)
        except Exception as exc:
            last_exc = exc
            continue
        # Normalize to a list of parsed objects (repair_json returns a dict,
        # a list, or a scalar depending on the input).
        objects = repaired if isinstance(repaired, list) else [repaired]
        for obj in objects:
            if _looks_like_verdict(obj):
                return obj
            if first_dict is None and isinstance(obj, dict) and obj:
                first_dict = obj

    if first_dict is not None:
        return first_dict

    raise ValueError(f"Could not parse JSON from LLM response: {last_exc}") from last_exc


# JSON shape shown to the model when re-asking it to repair an unparseable verdict.
_VERDICT_SCHEMA_HINT: dict[str, object] = {
    "scenario_id": "<same as the Scenario ID given below>",
    "scenario_title": "<the scenario title>",
    "status": "pass | fail",
    "justification": "<explanation referencing specific code>",
    "code_reference": {"file": "<path>", "function": "<name>", "line": "<integer or null>"},
    "github_links": ["<full GitHub blob URL>"],
    "implementation_suggestion": "<actionable guidance, or null when status=pass>",
}


async def _reask_verdict(
    llm: LLMProvider, raw_response: str, scenario: dict, error: Exception
) -> dict:
    """Re-ask the LLM to reformat its own prior answer into a valid verdict.

    Standard 'output-fixing' retry: the analysis already happened in the tool
    loop, so this is a pure reformat — we do not re-run tools or change the
    decision, only coerce the structure into the schema. Returns a verdict dict
    (may still be invalid; the caller validates).
    """
    prompt = (
        "Your previous reply could not be parsed into the required verdict format.\n"
        f"Validation error: {error}\n\n"
        f"Scenario ID: {scenario['id']}\n"
        f"Scenario title: {scenario['title']}\n\n"
        "Previous reply (contains your analysis and decision):\n"
        f"{raw_response}\n\n"
        "Return ONLY the corrected verdict as a single JSON object matching the "
        "schema. Preserve your original pass/fail decision, justification, and "
        "evidence — only fix the structure. Do not add commentary or extra objects."
    )
    return await llm.generate_structured(
        prompt=prompt,
        system_prompt=(
            "You reformat a QA verdict into strict JSON. Never change the verdict "
            "decision or evidence; only fix the JSON structure."
        ),
        response_format=_VERDICT_SCHEMA_HINT,
    )


def _build_verdict(
    verdict_dict: dict,
    repo: str,
    tool_errors: list[str],
    tool_successes: list[bool],
) -> VerificationVerdict:
    """Apply post-processing rules and validate a verdict dict into the model.

    Raises ValueError (incl. pydantic ValidationError) if the dict does not
    satisfy the VerificationVerdict schema.
    """
    # AC 10: only force status=fail if ALL tool calls failed (no successful reads).
    # Partial errors (e.g. 302 on a directory the LLM didn't need) should not
    # override a verdict backed by successfully fetched file evidence.
    if tool_errors and not tool_successes and verdict_dict.get("status") == "pass":
        error_summary = "; ".join(tool_errors[:3])
        verdict_dict["status"] = "fail"
        verdict_dict["implementation_suggestion"] = (
            f"Verification incomplete — GitHub API errors prevented full analysis: {error_summary}"
        )

    # Resolve relative links to absolute GitHub URLs
    if "github_links" in verdict_dict:
        verdict_dict["github_links"] = _resolve_github_links(
            verdict_dict["github_links"], repo
        )

    return VerificationVerdict(**verdict_dict)


async def run_agentic_verification(
    session_id: str,
    user_id: str,
    bdd_content: str,
    mode: str,
    github_input: str,
    llm: LLMProvider,
    db: AsyncSession,
    pat: str,
    max_tool_rounds: int = 20,
) -> AsyncGenerator[str, None]:
    """Stream per-scenario agentic verdicts as SSE events.

    For each BDD scenario, runs an LLM agent loop that calls GitHub tools
    on demand until it has sufficient evidence to produce a verdict.

    Yields:
        SSE-formatted strings ending with double newline:
        - ``data: {"type": "verdict", ...}\\n\\n`` per scenario
        - ``data: {"type": "complete", ...}\\n\\n`` after all scenarios
        - ``data: {"type": "error", ...}\\n\\n`` on per-scenario or critical failures
    """
    scenarios = parse_bdd_scenarios(bdd_content)
    if not scenarios:
        yield _sse_event({"type": "error", "message": "No BDD scenarios found in content"})
        return

    try:
        repo = _extract_repo_from_input(mode, github_input)
    except GitHubServiceError as exc:
        yield _sse_event({"type": "error", "message": f"Could not determine repo: {exc.message}"})
        return

    total = len(scenarios)
    passed = 0
    failed = 0

    for scenario in scenarios:
        # Create a fresh tool executor per scenario so tool_errors don't bleed across scenarios
        tool_executor, tool_errors, tool_successes = _make_tool_executor(pat=pat, repo=repo)

        messages = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    f"Evaluate this BDD scenario:\n{scenario['text']}"
                    f"\n\nScenario ID: {scenario['id']}"
                    f"\n\nGitHub repository: {repo}"
                ),
            },
        ]

        try:
            raw_response = await llm.generate_with_tools(
                messages=messages,
                tools=GITHUB_TOOL_SCHEMAS,
                tool_executor=tool_executor,
                max_tool_rounds=max_tool_rounds,
            )
        except LLMProviderError as exc:
            yield _sse_event({"type": "error", "message": f"LLM call failed: {exc}"})
            continue

        # Parse + validate the verdict. If the first attempt fails (unparseable
        # JSON or schema mismatch), do ONE corrective re-ask — the model reformats
        # its own prior answer into valid JSON — before giving up. This is the
        # standard output-fixing retry pattern.
        try:
            verdict = _build_verdict(
                _extract_json_from_text(raw_response), repo, tool_errors, tool_successes
            )
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "Verdict parse/validation failed for scenario=%r: %s | raw_response=%.500r; re-asking once",
                scenario["title"],
                exc,
                raw_response,
            )
            try:
                fixed_dict = await _reask_verdict(llm, raw_response, scenario, exc)
                verdict = _build_verdict(fixed_dict, repo, tool_errors, tool_successes)
            except (ValueError, json.JSONDecodeError, LLMProviderError) as retry_exc:
                logger.warning(
                    "Corrective re-ask failed for scenario=%r: %s",
                    scenario["title"],
                    retry_exc,
                )
                yield _sse_event({
                    "type": "error",
                    "message": f"LLM returned malformed verdict for '{scenario['title']}': {retry_exc}",
                })
                continue

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

        # Persist to DB after yielding
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
            logger.warning(
                "Failed to persist verdict for session=%s scenario=%r: %s",
                session_id,
                scenario["title"],
                exc,
                exc_info=True,
            )
            try:
                await db.rollback()
            except Exception:
                pass

    yield _sse_event({"type": "complete", "total": total, "passed": passed, "failed": failed})
