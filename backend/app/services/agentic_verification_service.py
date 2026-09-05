"""Agentic Verification Service (Story 2.5).

Replaces the two-step fetch+verify flow with a single agentic run where the
LLM calls GitHub tools on demand until it has sufficient evidence to verdict.

Architecture rules enforced here:
- Never call LLM SDKs directly — always via LLMProvider.generate_with_tools()
- No business logic in route handlers
- DB persistence happens after each yield (streaming-safe)
- Tools live in github_tools.py — this service only orchestrates
"""

import asyncio
import json
import re
import uuid
import logging
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from json_repair import repair_json
from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.verification import FetchedFile, VerificationVerdict
from app.core.config import settings
from app.services import code_index_service, github_tools
from app.services.github_service import (
    GitHubServiceError,
    _parse_github_blob_url,
    _parse_pr_url,
    _parse_repo_url,
    fetch_exact_files_resolved,
    fetch_pull_request,
    get_pr_head_sha,
)
from app.services.github_tools import GITHUB_TOOL_SCHEMAS
from app.services.llm.provider import LLMProvider, LLMProviderError, ToolLoopStats
from app.services.verification_service import (
    _MAX_FILES_PER_SCENARIO,
    _select_relevant_files,
    build_verification_result,
    deduplicate_scenarios,
    format_rag_block,
    parse_bdd_scenarios,
    rag_payload_from_chunks,
    retrieve_rag_per_scenario,
    truncate_with_notice,
    verdict_event_dict,
)

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a senior QA engineer. For the given BDD scenario, evaluate whether \
the implementation code fully implements the scenario. The task message may \
already include code evidence (file contents or a pull-request diff) with a \
SOURCE SCOPE instruction — when it does, base your verdict on that evidence \
and follow the scope, calling tools only for missing context. Otherwise you \
MUST use the available GitHub tools to find the relevant implementation code \
and call at least one tool before forming a verdict.

Strategy (follow this order):
0. If the task message includes a REPOSITORY FILE TREE, use it: pick the likely \
paths straight from that list and call get_file_contents on them. Do not spend \
calls on search_code or list_directory rediscovering a layout you have already \
been given. Only fall back to steps 1-2 for something the tree does not show.
<<CANDIDATE_RULE>>
1. Try search_code with relevant keywords. If it returns an empty list (common \
for private repos), move to step 2.
2. Call list_directory("") to see the root structure, then list_directory on \
relevant subdirectories (e.g. components/auth, actions, lib, services, app/api).
3. CRITICAL: Once you identify a relevant file, you MUST call get_file_contents \
on it and read the actual code before forming a verdict. Never base a verdict \
solely on a file's name — always read the contents.
4. If a page delegates to a component (e.g. LoginForm, RegisterForm), read the \
component file too, not just the page file.
5. A frontend control is usually only half the feature. When the scenario \
describes filtering, persistence, authorization or any state change, follow the \
call chain into the backend — page -> hook -> API client -> route handler — and \
read the handler before judging. Code absent from a UI file is routinely present \
in the route that file calls.
6. A cross-cutting concern is usually enforced somewhere other than the file \
that appears to need it. Access control, redirects, authentication, \
authorization, validation and error handling are typically applied by a wrapper \
the component itself knows nothing about: a route definition, a guard or \
middleware, a decorator, an interceptor, a layout, or a base class. Before \
judging any scenario about who may reach a screen or an endpoint, read where \
that screen or endpoint is REGISTERED — the router or app entry point — not \
only the component or handler it renders. A page with no auth check inside it \
is not an unprotected page; it is very often a page wrapped in a guard one file \
away.
7. TRUNCATION: a file longer than the read cap comes back ending in a \
"[TRUNCATED — ... offset=N ...]" notice. That notice means you have NOT seen the \
whole file. You MUST call get_file_contents again with that offset (and keep \
going until no notice appears) before stating that anything is missing from it. \
Never report a feature absent when the part of the file you did not read could \
contain it.

After gathering sufficient evidence, respond with a JSON verdict matching this schema:
{
  "scenario_id": "<same as input>",
  "scenario_title": "<same as input>",
  "status": "pass" | "partial" | "fail" | "inconclusive",
  "justification": "<natural-language explanation referencing specific code>",
  "code_reference": {"file": "<path>", "function": "<name>", "line": <integer>},
  "github_links": ["<full GitHub blob URL e.g. https://github.com/org/repo/blob/main/path/to/file.py>"],
  "implementation_suggestion": "<actionable guidance for fixing the gap>" | null
}

Rules:
- status="pass" only if the scenario's acceptance criteria is FULLY handled in the code
- status="partial" when you have READ code that implements the behaviour but \
it is not reachable the way this scenario describes. The usual shapes: the logic \
exists but is bound to a different trigger or entry point than the scenario \
names; it is implemented on one side of the stack and never called from the \
other; or it handles the general case but not the specific condition the \
scenario states. Cite BOTH the code that exists AND the missing link. Do not \
use "partial" for a scenario you simply have mixed feelings about — if the \
behaviour is absent it is "fail", and if you could not establish either it is \
"inconclusive".
- status="fail" ONLY on positive evidence of absence — you read the code that \
would contain the behaviour and it is not there, or a search across the \
repository for it came back empty. A file you did not read, or read only part \
of, is NOT evidence of absence.
- Never fail a scenario about access control, redirects or authorization on the \
strength of one component file. The guard is routinely applied where the route \
is registered. If you have not read the router or app entry point, you have not \
established absence, and the honest status is "inconclusive".
- status="inconclusive" when you could not gather the evidence either way: tools \
failed, the file was truncated and you could not read the rest, or the relevant \
code could not be located. Preferring "inconclusive" over a guessed "fail" is \
always correct — a wrong "fail" sends someone to rewrite code that already works.
- NEVER hedge inside a pass, partial or fail. If your justification would contain \
"assuming", "likely", "presumably", "not evident", "appears to", or "I cannot \
verify", the honest status is "inconclusive".
- justification MUST reference specific code (function name, variable, condition) \
for pass, partial and fail; for partial it MUST name both the code that exists \
and the link that is missing; for inconclusive it MUST state exactly what \
evidence is missing and which file or path would settle it.
- implementation_suggestion MUST be non-null and actionable when status="fail"
- implementation_suggestion MUST be non-null when status="partial", naming the \
wiring that would close the gap between the code that exists and the scenario
- implementation_suggestion MUST be non-null when status="inconclusive", naming \
the file, offset or search that would resolve it
- implementation_suggestion MUST be null when status="pass"
- code_reference MUST point at a line you actually read. If you did not read a \
specific line, set "line" to null rather than estimating it.
- In github_links, always use full GitHub URLs in the format \
https://github.com/{repo}/blob/{ref}/{path}
"""

#: Appended to the system prompt only when tool-result elision is on, so a run
#: with the window disabled sends the byte-identical prompt it always did.
#:
#: The placeholder itself already says this, but a rule in the system prompt is
#: what the model weighs against the "positive evidence of absence" rule it is
#: about to apply. Leaving it to the inline note alone risks the model treating
#: a transcript it can no longer read as a repository it has finished searching.
#: Where rule 0b goes when the code index is on. A sentinel rather than a
#: ``str.format`` placeholder because the prompt contains the verdict's JSON
#: schema, and its braces would make formatting raise.
_CANDIDATE_RULE_SLOT = "<<CANDIDATE_RULE>>"

#: Rule 0b — only sent when the run actually has a code index to draw on.
#:
#: Unconditional it was ~140 tokens on every round of every scenario telling
#: the model how to treat a CANDIDATE FILES block that was never going to
#: arrive. That is not just waste: an instruction about input the model cannot
#: see is one more thing for it to reconcile.
#:
#: The last two sentences are the load-bearing ones. Without them the model
#: reads an empty or irrelevant candidate list as proof the behaviour is
#: missing, which is the false ``fail`` this whole design risks.
_CANDIDATE_PROMPT_RULE = """\
0b. If the task message includes CANDIDATE FILES, open those with \
get_file_contents FIRST — they are a search result, not evidence, and you must \
read the actual code before forming any verdict. Absence of a file from that \
list is NOT evidence that it does not exist or that the behaviour is missing: \
the list is a similarity ranking, often incomplete and sometimes irrelevant, \
and it cannot show that something is not in the repository. When the candidates \
do not contain the behaviour, fall back to the tree and list_directory exactly \
as if no list had been given."""

#:
#: It is written as a Rules bullet so it can simply be appended: the Rules list
#: is the last section of the prompt, and the strategy steps above it are
#: numbered, which an appended item could not join without renumbering.
_ELISION_PROMPT_RULE = """\
- An older tool result may have been replaced by a note saying it was removed \
from the transcript to save space. That note means ONLY that the text is no \
longer in this conversation — the call succeeded and the file is unchanged. It \
is never evidence that anything is missing, and must not be cited as an \
absence. If a verdict would rest on code you can no longer see, call the tool \
again and read it before deciding.
"""


@dataclass
class _VerificationScope:
    """What one agentic run is allowed and expected to look at.

    ``default_ref`` pins the GitHub tools to the code the user actually chose:
    the blob URLs' branch in exact-files mode, the PR head commit in
    pull-request mode. Without it every tool call reads default-branch HEAD
    and the verdict is about the wrong code.

    ``evidence_files`` are pre-fetched and injected into the prompt so the
    verdict is grounded in the user's selection (the listed files, or the PR
    diff) rather than whatever the agent happens to explore.
    """

    repo: str
    default_ref: str = "HEAD"
    evidence_files: list[FetchedFile] = field(default_factory=list)
    instruction: str = ""


async def _prepare_scope(mode: str, github_input: str, pat: str) -> _VerificationScope:
    """Resolve the user's mode + input into a concrete verification scope.

    Raises GitHubServiceError on unparseable input, cross-repo file lists, or
    prefetch failures (bad URL, missing PR, auth).
    """
    if mode == "full_repo":
        owner, repo = _parse_repo_url(github_input)
        return _VerificationScope(repo=f"{owner}/{repo}")

    if mode == "exact_files":
        lines = [ln.strip() for ln in github_input.strip().splitlines() if ln.strip()]
        if not lines:
            raise GitHubServiceError("No file URLs provided.")
        parsed = [_parse_github_blob_url(url) for url in lines]
        repos = {f"{owner}/{repo}" for owner, repo, _, _ in parsed}
        if len(repos) > 1:
            # Silently keeping the first repo would judge files against the
            # wrong codebase — refuse instead.
            raise GitHubServiceError(
                "All file URLs must point at the same repository; got: "
                + ", ".join(sorted(repos))
            )
        files, first_ref = await fetch_exact_files_resolved(github_input, pat)
        return _VerificationScope(
            repo=repos.pop(),
            # Each file's content was fetched at its own URL's ref; tools are
            # pinned to the first URL's RESOLVED ref (the raw URL split can be
            # wrong for slash-containing branch names).
            default_ref=first_ref,
            evidence_files=files,
            instruction=(
                "SOURCE SCOPE: The user selected Exact File Paths mode. The file "
                "contents below are the code under verification — base your "
                "verdict on them. Contents may be truncated; call "
                "get_file_contents on a listed file if you need the rest of it. "
                "Do not base the verdict on files the user did not list."
            ),
        )

    if mode == "pull_request":
        owner, repo, _ = _parse_pr_url(github_input)
        head_sha = await get_pr_head_sha(github_input, pat)
        files = await fetch_pull_request(github_input, pat)
        return _VerificationScope(
            repo=f"{owner}/{repo}",
            default_ref=head_sha,
            evidence_files=files,
            instruction=(
                "SOURCE SCOPE: The user selected Pull Request mode. The unified "
                "diffs below are the changes under verification — judge whether "
                "the pull request's changes implement the scenario. Tools read "
                "from the PR's head commit; call get_file_contents when a diff "
                "needs surrounding context."
            ),
        )

    raise GitHubServiceError(f"Unknown mode: {mode}")


def _format_evidence_block(scenario: dict, files: list[FetchedFile]) -> str:
    """Format pre-fetched evidence files as a CODE block for one scenario.

    Uses the same relevance selection and per-file truncation as the direct
    verification path so token budgets stay comparable.
    """
    # Warning artefacts from the fetch layer (cap/truncation notices) are for
    # humans, not evidence — keep them out of relevance scoring and the prompt.
    files = [f for f in files if not f.path.startswith("[WARNING]")]
    if not files:
        return ""
    relevant = _select_relevant_files(scenario, files)
    lines = ["CODE:"]
    for f in relevant:
        lines.append(f"[F:{f.path}]")
        lines.append(truncate_with_notice(f.content, f.path))
        lines.append("[/F]")
    return "\n".join(lines)


def _shared_evidence_block(files: list[FetchedFile]) -> str | None:
    """Format the evidence once for the whole run, or None if it cannot be shared.

    Every scenario in a run is checked against the same repository, so the code
    block is usually identical for all of them — and an identical prefix is what
    prompt caching charges for once instead of per scenario. Building it a
    single time also removes the per-scenario relevance pass.

    Returns None when there are more files than one prompt should carry. Then
    each scenario gets its own relevance-selected subset, exactly as before:
    the caching saving is worth having, never worth trimming the evidence a
    verdict rests on.
    """
    files = [f for f in files if not f.path.startswith("[WARNING]")]
    if not files:
        return ""
    if len(files) > _MAX_FILES_PER_SCENARIO:
        return None

    lines = ["CODE:"]
    for f in files:
        lines.append(f"[F:{f.path}]")
        lines.append(truncate_with_notice(f.content, f.path))
        lines.append("[/F]")
    return "\n".join(lines)


def _make_tool_executor(
    pat: str,
    repo: str,
    default_ref: str = "HEAD",
    read_cache: dict | None = None,
) -> tuple:
    """Return (executor, tool_errors, tool_successes, files_read) for a PAT/repo/ref.

    tool_errors accumulates GitHubServiceError messages; tool_successes counts
    calls that returned data; files_read keeps each whole-file read (path ->
    text) so the verdict's code_reference can be checked against code the run
    actually saw. Callers must create a new executor per scenario to avoid
    cross-scenario bleed.

    ``default_ref`` is the scope's pinned ref (branch or PR head SHA). The
    model usually omits ``ref`` — and its schema default is "HEAD" — so both
    the missing and the literal-"HEAD" cases are rebound to the pinned ref;
    an explicit branch/SHA from the model is honoured.

    ``read_cache`` is shared across every scenario in a run. Scenarios are
    checked against one repository at one ref, so they converge on the same
    handful of files: without it, twenty scenarios reading the same route file
    make twenty identical HTTPS round-trips, each costing latency and a slice of
    the GitHub rate limit. Contents at a pinned ref are immutable, so reusing
    them cannot change a verdict — only how long it takes to reach it. The same
    cache also absorbs a model that re-reads a file it already has.

    Bookkeeping stays per-scenario even when the fetch is shared: ``files_read``
    must only ever hold what THIS scenario saw, or the citation check would
    start accepting lines from a file the verdict's own run never opened.
    """
    tool_errors: list[str] = []
    tool_successes: list[bool] = []
    files_read: dict[str, str] = {}
    cache = read_cache if read_cache is not None else {}

    def _bind_ref(tool_args: dict) -> str:
        ref = tool_args.get("ref") or default_ref
        return default_ref if ref == "HEAD" else ref

    async def _cached(key: tuple, fetch) -> str:
        """Return a cached tool result, or fetch and store it.

        Only successful reads are cached; an error is raised through so it lands
        in ``tool_errors`` for every scenario that hits it, and so a transient
        failure is not pinned for the rest of the run.
        """
        if key in cache:
            return cache[key]
        result = await fetch()
        cache[key] = result
        return result

    async def executor(tool_name: str, tool_args: dict) -> str:
        tool_args.setdefault("repo", repo)
        try:
            if tool_name == "search_code":
                query = tool_args["query"]
                result = await _cached(
                    ("search", tool_args["repo"], query),
                    lambda: github_tools.search_code(
                        query=query, repo=tool_args["repo"], pat=pat
                    ),
                )
            elif tool_name == "get_file_contents":
                # The model may omit offset, or send it as a string; a bad value
                # must not fail the read, so anything unparseable means "start".
                try:
                    offset = int(tool_args.get("offset") or 0)
                except (TypeError, ValueError):
                    offset = 0
                ref = _bind_ref(tool_args)
                result = await _cached(
                    ("file", tool_args["repo"], ref, tool_args["path"], offset),
                    lambda: github_tools.get_file_contents(
                        repo=tool_args["repo"],
                        path=tool_args["path"],
                        pat=pat,
                        ref=ref,
                        offset=offset,
                    ),
                )
                # Only a read from the start is a candidate whole file; a later
                # window is a fragment and cannot ground a line number.
                if offset == 0:
                    files_read[str(tool_args["path"]).lstrip("/")] = result
            elif tool_name == "list_directory":
                ref = _bind_ref(tool_args)
                result = await _cached(
                    ("dir", tool_args["repo"], ref, tool_args["path"]),
                    lambda: github_tools.list_directory(
                        repo=tool_args["repo"],
                        path=tool_args["path"],
                        pat=pat,
                        ref=ref,
                    ),
                )
            else:
                return f"Unknown tool: {tool_name}"
            tool_successes.append(True)
            return result
        except GitHubServiceError as exc:
            error_msg = f"GitHub API error: {exc.message}"
            tool_errors.append(error_msg)
            return error_msg

    return executor, tool_errors, tool_successes, files_read


#: Emitted by the read tools when a window is not the whole file. Content
#: carrying it is a fragment, so nothing about it can disprove a line number.
_TRUNCATION_MARKER = "[TRUNCATED —"

#: Extra attempts for a scenario whose LLM call was rate limited, on top of the
#: retries the provider SDK already makes internally. Those are tuned for a
#: single call; this layer exists because the scenarios in a run share one
#: account's limit, so the burst that caused the 429 is the run's own doing.
_RATE_LIMIT_RETRIES = 3

#: First backoff, doubling per attempt (5s, 10s, 20s). Long enough to matter
#: against a per-minute quota, short enough not to strand a streaming client.
_RATE_LIMIT_BACKOFF_SECONDS = 5.0

#: Phrases that mark a justification as a guess rather than a finding. Kept to
#: unambiguous hedges — "appears to be missing" is a hedge, while "assumes" as a
#: verb about the code ("the handler assumes a token") is not, so only the
#: first-person and epistemic forms are listed.
_HEDGE_PATTERN = re.compile(
    r"\b("
    r"assuming|presumably|likely handled|probably|"
    r"cannot verify|can't verify|could not verify|couldn't verify|"
    r"not evident|no way to (?:tell|know|verify)|unclear whether|"
    r"appears to be (?:missing|absent)|seems to be (?:missing|absent)|"
    r"not (?:fully )?(?:detailed|shown|included) in the (?:available )?excerpt|"
    r"outside (?:the|my) (?:current )?scope|with the current scope"
    r")\b",
    re.IGNORECASE,
)


def _is_hedged(justification: object) -> bool:
    """True when a justification admits it did not actually establish its claim."""
    return isinstance(justification, str) and bool(_HEDGE_PATTERN.search(justification))


#: A justification asserting that access control is missing. Deliberately
#: requires a negation next to the concept: a verdict that merely *mentions*
#: authentication ("the handler reads the bearer token") is a finding, not this
#: claim, and matching it would downgrade sound verdicts.
_MISSING_ACCESS_CONTROL_PATTERN = re.compile(
    r"\b(no|not|without|lacks?|lacking|missing|absence of|does not|doesn't|"
    r"there is no|never)\b[^.!?]{0,80}?\b("
    r"redirect(?:ed|ion)?|auth(?:enticated|entication|orization|orized)?|"
    r"access control|route guard|guard(?:ed)?|protected route|"
    r"sign(?:ed)?[ -]in check|login (?:check|redirect|page|screen)|"
    r"logged[ -]in check"
    r")\b",
    re.IGNORECASE,
)

#: Paths that register routes or bootstrap an app — where a guard usually lives.
#: Matched on the whole path, so it holds across the frameworks this tool sees
#: rather than encoding one project's layout. Two ways to qualify: a filename
#: that is conventionally the entry point, or a directory that holds routing and
#: middleware (``src/routes/users.ts`` is a route registration even though its
#: filename says nothing).
_ROUTE_REGISTRATION_PATTERN = re.compile(
    r"(^|/)(?:"
    r"app\.(?:tsx|jsx|ts|js|py)|main\.(?:tsx|jsx|ts|js|py)|"
    r"index\.(?:tsx|jsx|ts|js)|_app\.(?:tsx|jsx|ts|js)|"
    r"rout(?:e|er|es|ing)[^/]*|urls\.py|layout\.(?:tsx|jsx|ts|js)|"
    r"server\.(?:ts|js|py)"
    r")$"
    r"|(^|/)(?:routes?|router|routing|middlewares?|guards?)/",
    re.IGNORECASE,
)


def _claims_missing_access_control(justification: object) -> bool:
    """True when a justification asserts that access control is absent."""
    return isinstance(justification, str) and bool(
        _MISSING_ACCESS_CONTROL_PATTERN.search(justification)
    )


def _read_route_registration(known_files: dict[str, str]) -> bool:
    """True when the run read a file where routes are registered."""
    return any(_ROUTE_REGISTRATION_PATTERN.search(path) for path in known_files)


def _lookup_file(path: str | None, known_files: dict[str, str]) -> str | None:
    """Find the content the run read for ``path``, tolerating path spelling.

    The model cites a path in whatever form it saw; a leading slash or a
    repo-qualified prefix is the same file. Matching on the suffix keeps a
    cosmetic difference from disabling the check, while an ambiguous suffix
    (two files with the same tail) is treated as unknown rather than guessed.
    """
    if not path:
        return None
    needle = str(path).lstrip("/")
    if needle in known_files:
        return known_files[needle]
    matches = [
        content
        for known, content in known_files.items()
        if known.endswith("/" + needle) or needle.endswith("/" + known)
    ]
    return matches[0] if len(matches) == 1 else None


def _validate_code_reference(verdict_dict: dict, known_files: dict[str, str]) -> None:
    """Correct or drop a code_reference line the read code contradicts.

    Mutates ``verdict_dict`` in place. This only acts on what the run actually
    read in full: a line past the end of the file, or a line that does not carry
    the function the reference names while that function sits elsewhere in the
    same file. Everything else is left exactly as written — an unread file, a
    truncated window, or a reference with nothing to check against is not
    evidence of anything, and inventing a correction would be the same mistake
    in the other direction.

    The point is narrow but worth it: a citation that lands on the wrong line
    costs the reader their trust in every other citation in the report.
    """
    ref = verdict_dict.get("code_reference")
    if not isinstance(ref, dict):
        return

    content = _lookup_file(ref.get("file"), known_files)
    if content is None or _TRUNCATION_MARKER in content:
        return

    lines = content.splitlines()
    if not lines:
        return

    raw_line = ref.get("line")
    line = raw_line if isinstance(raw_line, int) and not isinstance(raw_line, bool) else None
    in_range = line is not None and 1 <= line <= len(lines)

    function = ref.get("function")
    function_line: int | None = None
    if isinstance(function, str) and function.strip():
        pattern = re.compile(rf"\b{re.escape(function.strip())}\b")
        for index, text in enumerate(lines, start=1):
            if pattern.search(text):
                function_line = index
                break

    if function_line is not None:
        # A cited line that does not mention the named function is wrong even
        # when it is in range; the function's own line is the better citation.
        cited_matches = in_range and re.search(
            rf"\b{re.escape(str(function).strip())}\b", lines[line - 1]
        )
        if not cited_matches:
            ref["line"] = function_line
    elif not in_range:
        # Nothing to repoint at and the line cannot be real — better absent
        # than wrong.
        ref["line"] = None


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
    scenario: dict,
    repo: str,
    tool_errors: list[str],
    tool_successes: list[bool],
    evidence_provided: bool = False,
    known_files: dict[str, str] | None = None,
) -> VerificationVerdict:
    """Apply post-processing rules and validate a verdict dict into the model.

    Raises ValueError (incl. pydantic ValidationError) if the dict does not
    satisfy the VerificationVerdict schema.
    """
    # The scenario identity is known — never trust the model's echo of it. A
    # garbled scenario_id would mislabel the result in the UI and break the
    # UUID parse at persist time (silently: the stream succeeds, the row is
    # lost). Same for the title.
    verdict_dict["scenario_id"] = scenario["id"]
    verdict_dict["scenario_title"] = scenario["title"]

    # AC 10 (tightened): a verdict must be backed by evidence — at least one
    # successful tool read, or code injected into the prompt (exact-files /
    # pull-request modes). This also catches the model skipping tools entirely
    # and verdicting on nothing, which the errors-only check used to let through.
    # Partial tool errors never override a verdict backed by successful reads.
    #
    # The unevidenced verdict lands on "inconclusive" rather than "fail": with no
    # code read, "not implemented" is a guess, and a guessed failure is the
    # expensive kind of wrong — it sends someone to rewrite working code. The
    # safety property is unchanged, since an unevidenced run still cannot pass.
    if not tool_successes and not evidence_provided:
        error_summary = (
            "; ".join(tool_errors[:3])
            if tool_errors
            else "the model read no code before verdicting on this scenario"
        )
        verdict_dict["status"] = "inconclusive"
        verdict_dict["implementation_suggestion"] = (
            f"Verification incomplete — no code evidence backed this verdict: {error_summary}"
        )

    # A directory listing is not code. ``tool_successes`` counts every tool
    # equally, so a run that called list_directory once — and never opened a
    # file — satisfied the guard above and could still report "pass". That is
    # the one remaining way to pass on nothing: the model recognises a filename,
    # infers what must be inside it, and is believed. Reading a file is exactly
    # what the system prompt calls CRITICAL, so requiring it here is enforcing
    # the instruction rather than adding a rule.
    #
    # ``known_files`` holds whole files this scenario actually read (plus
    # exact-files evidence), which makes it the honest test of "was any code
    # seen". Injected evidence satisfies it independently, so exact-files and
    # pull-request runs are untouched.
    elif (
        verdict_dict.get("status") in ("pass", "partial")
        and not evidence_provided
        and not known_files
    ):
        # "partial" is held to the same bar: it asserts that implementing code
        # was read and merely wired elsewhere, which no directory listing can
        # show. Exempting it would leave one verdict reachable on no evidence.
        verdict_dict["status"] = "inconclusive"
        verdict_dict["implementation_suggestion"] = (
            "Verification incomplete — this scenario was decided without any "
            "file being read; directory listings alone cannot show that "
            "behaviour is implemented. Re-run and open the relevant file with "
            "get_file_contents."
        )

    # A hedged justification is an inconclusive verdict wearing a decision's
    # clothes. The model is told not to hedge; when it does anyway, the words it
    # chose are better evidence of its confidence than the label it picked.
    elif verdict_dict.get("status") in ("fail", "partial") and _is_hedged(
        verdict_dict.get("justification")
    ):
        verdict_dict["status"] = "inconclusive"

    # A guard is normally applied where a route is registered, not inside the
    # component it protects. Reading only the component and finding no check
    # there establishes nothing — the page may well be wrapped one file away —
    # so a "fail" claiming missing access control, from a run that never opened
    # a router, is downgraded rather than published. This is the mechanical half
    # of the strategy rule; the prompt asks for the router to be read, and this
    # catches the runs where it was not.
    elif (
        verdict_dict.get("status") in ("fail", "partial")
        and _claims_missing_access_control(verdict_dict.get("justification"))
        and not _read_route_registration(known_files or {})
    ):
        verdict_dict["status"] = "inconclusive"
        verdict_dict["implementation_suggestion"] = (
            "Access control was judged from the component alone. Read where this "
            "route is registered (the router or app entry point) to see whether a "
            "guard already wraps it, then re-run. "
            + (verdict_dict.get("implementation_suggestion") or "")
        ).strip()

    # Evidence-based correction of the citation, before schema validation so a
    # repointed line is the one persisted and shown.
    _validate_code_reference(verdict_dict, known_files or {})

    # An inconclusive verdict must say what would settle it; the model is asked
    # for this, and a default keeps the contract when it omits one.
    if verdict_dict.get("status") == "inconclusive" and not verdict_dict.get(
        "implementation_suggestion"
    ):
        verdict_dict["implementation_suggestion"] = (
            "Evidence was insufficient to decide this scenario. Re-run after "
            "confirming the relevant files are reachable, and read any file "
            "whose contents came back truncated to the end."
        )

    # Same contract for "partial": the verdict's whole value is naming the gap
    # between the code that exists and the scenario, so a partial with no
    # suggestion is a fail with extra steps.
    if verdict_dict.get("status") == "partial" and not verdict_dict.get(
        "implementation_suggestion"
    ):
        verdict_dict["implementation_suggestion"] = (
            "The behaviour exists in the code cited above but is not reachable "
            "as this scenario describes. Wire the existing implementation to "
            "the trigger the scenario names."
        )

    # Resolve relative links to absolute GitHub URLs
    if "github_links" in verdict_dict:
        verdict_dict["github_links"] = _resolve_github_links(
            verdict_dict["github_links"], repo
        )

    return VerificationVerdict(**verdict_dict)


#: Sent back to a model that answered "inconclusive", quoting its own account
#: of what was missing.
#:
#: The last paragraph is the one that must not be softened. Pushing a model off
#: "inconclusive" without it would trade abstentions for guesses, and a guessed
#: "fail" is the worst thing this system can produce — it sends someone to
#: rewrite code that already works. The aim is to make it LOOK, not to make it
#: decide: an inconclusive verdict reached after reading the code is a good
#: answer, while one reached after two tool calls out of twenty is a shrug.
_ESCALATION_DIRECTIVE = """\
Your previous answer was "inconclusive". You said the evidence that would \
settle it is:

{gap}

You have NOT used your tool budget. Go and get that evidence now — read the \
file or run the search you just named. If the behaviour is not in the file you \
read, follow the call chain (page -> hook -> API client -> route handler) and \
read where the route is REGISTERED: filtering, authorization and validation \
are routinely applied by a wrapper the component itself knows nothing about.

Then answer again in the same JSON format. "inconclusive" is still the correct \
answer if you have now read that code and genuinely cannot tell — do NOT \
upgrade to "pass", "partial" or "fail" to satisfy this message. What is not \
acceptable is \
answering "inconclusive" again without having looked."""


async def _escalate_inconclusive(
    llm: LLMProvider,
    messages: list[dict],
    verdict: VerificationVerdict,
    tool_executor,
    max_tool_rounds: int,
    tool_result_window: int,
    stats: ToolLoopStats,
    scenario: dict,
    build,
) -> VerificationVerdict | None:
    """Run one more pass at a scenario the model declined to decide.

    Returns the new verdict, or None to keep the original — which covers every
    failure here, because an escalation that goes wrong must never cost a
    verdict the run already had.

    The second pass is cheaper than it looks: the run-scoped read cache means
    re-reading a file it already opened costs no GitHub call, so most of the
    price is the replayed conversation.
    """
    gap = " ".join(
        part
        for part in (verdict.justification, verdict.implementation_suggestion)
        if part
    ).strip()
    if not gap:
        # Nothing to point it at. Asking it to look harder with no direction is
        # just paying for the same answer.
        return None

    followup = [
        *messages,
        {"role": "user", "content": _ESCALATION_DIRECTIVE.format(gap=gap)},
    ]
    second = ToolLoopStats()
    try:
        raw = await llm.generate_with_tools(
            messages=followup,
            tools=GITHUB_TOOL_SCHEMAS,
            tool_executor=tool_executor,
            max_tool_rounds=max_tool_rounds,
            stats=second,
            tool_result_window=tool_result_window,
        )
    except LLMProviderError as exc:
        logger.info(
            "Escalation call failed for scenario=%r (%s); keeping the "
            "inconclusive verdict",
            scenario["title"],
            exc,
        )
        return None
    finally:
        # Counted even when the pass fails: it was spent either way, and a cost
        # line that hides a retry is how a run looks cheaper than it was.
        stats.merge(second)

    try:
        return build(_extract_json_from_text(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        logger.info(
            "Escalation returned an unusable verdict for scenario=%r (%s); "
            "keeping the inconclusive one",
            scenario["title"],
            exc,
        )
        return None


@dataclass
class _CodeIndexFreshness:
    """Whether this run may use the project's code index, and why not."""

    usable: bool
    warning: str = ""


async def _code_index_freshness(
    db: AsyncSession,
    user_id: str,
    project_id: uuid.UUID | str | None,
    repo: str,
    ref: str,
    pat: str,
) -> _CodeIndexFreshness:
    """Decide whether the stored code index still describes the code being read.

    An index is a snapshot; the verification loop reads live at a pinned ref,
    and pull-request mode pins to the head SHA deliberately. When those have
    diverged the index still returns confident answers — for files that may
    have been renamed, moved, or deleted since. Sending the agent there costs
    rounds and can end in a ``fail`` reasoned from the wrong code, so a stale
    index is refused and the run falls back to tree-only discovery.

    Never raises. Anything unexpected resolves to "do not use it": discovery is
    an accelerator, and the run is correct without it.
    """
    try:
        record = await code_index_service.get_code_index(db, user_id, project_id)
    except Exception:
        logger.warning(
            "Could not read the code-index record for project=%s", project_id,
            exc_info=True,
        )
        return _CodeIndexFreshness(usable=False)

    if record is None or not record.indexed_ref:
        return _CodeIndexFreshness(
            usable=False,
            warning=(
                "Code index is on for this run but this project has not been "
                "indexed yet. Index it from the Knowledge page, under Code "
                "index. Verification continues using the repository tree."
            ),
        )

    if record.source_ref and record.source_ref != repo:
        return _CodeIndexFreshness(
            usable=False,
            warning=(
                f"The code index was built from {record.source_ref}, but this "
                f"run reads {repo}. It was not used; verification continues "
                "using the repository tree."
            ),
        )

    head = await code_index_service.resolve_commit_sha(repo, ref, pat)
    if not head:
        # The comparison could not be made. Refusing here is the conservative
        # half of the same rule: an index that MIGHT be stale is exactly the
        # case this check exists for.
        return _CodeIndexFreshness(
            usable=False,
            warning=(
                f"Could not resolve {repo}@{ref} to check whether the code "
                "index is current, so it was not used. Verification continues "
                "using the repository tree."
            ),
        )

    if head != record.indexed_ref:
        return _CodeIndexFreshness(
            usable=False,
            warning=(
                f"The code index is stale — built at {record.indexed_ref[:7]}, "
                f"but {repo}@{ref} is now at {head[:7]}. It was not used, so "
                "the agent will not be sent to files that may have moved. "
                "Re-index from the Knowledge page, under Code index."
            ),
        )

    return _CodeIndexFreshness(usable=True)


@dataclass
class _ScenarioOutcome:
    """The result of verifying one scenario, ready to be streamed and stored.

    Failures are carried rather than raised so the consuming loop keeps its
    ordering and its stop rules in one place. ``llm_error`` is separated from
    ``error`` because only a provider failure is evidence about the *run* rather
    than about this scenario.
    """

    scenario: dict
    rag_chunks: list[dict]
    verdict: VerificationVerdict | None = None
    error: str | None = None
    llm_error: str | None = None
    #: True when the provider called the failure transient (a rate limit). Such
    #: a failure says nothing about the run's configuration, so it must never
    #: trigger the stop-the-run rule — it already survived this scenario's own
    #: retries, and the next scenario may well succeed.
    llm_error_retryable: bool = False
    #: What the agent loop cost. Stays at zero when the call never completed.
    stats: ToolLoopStats = field(default_factory=ToolLoopStats)


def _usage_payload(stats: ToolLoopStats) -> dict:
    """Render one scenario's loop cost for the SSE event and the log line.

    ``rounds_before_first_read`` is the number this whole measurement exists
    for: it says how many API calls the agent spent locating code before it
    read any, which is the cost semantic retrieval would remove. If it is
    already near zero, the repository tree in the shared prefix has done that
    job and an index would be buying very little.
    """
    return {
        "rounds": stats.rounds,
        "input_tokens": stats.input_tokens,
        "cached_input_tokens": stats.cached_input_tokens,
        "output_tokens": stats.output_tokens,
        "rounds_before_first_read": stats.rounds_before("get_file_contents"),
    }


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
    use_knowledge_base: bool = False,
    project_id: uuid.UUID | str | None = None,
    vector_config=None,
    max_concurrency: int | None = None,
    code_index_enabled: bool = False,
) -> AsyncGenerator[str, None]:
    """Stream per-scenario agentic verdicts as SSE events.

    For each BDD scenario, runs an LLM agent loop that calls GitHub tools
    on demand until it has sufficient evidence to produce a verdict.

    When ``use_knowledge_base`` is True, each scenario's prompt is enriched with
    project knowledge-base context relevant to that scenario, and the retrieved
    sources are attached to the verdict's ``rag_context``.

    When ``code_index_enabled`` is True, each scenario is also told which files
    semantic search suggests are relevant, so the agent opens them instead of
    hunting for them. Those suggestions are a POINTER, never evidence: the agent
    still reads every file live and still bases its verdict on what it read.
    Retrieval cannot prove absence — it always returns its top-k nearest chunks
    — so a candidate list is never grounds for a ``fail``.

    Scenarios run up to ``max_concurrency`` at a time but are streamed and
    persisted strictly in BDD order, so the wall-clock saving costs neither a
    predictable report nor safe use of the single database session.

    Yields:
        SSE-formatted strings ending with double newline:
        - ``data: {"type": "plan", ...}\\n\\n`` once, before any verdict
        - ``data: {"type": "verdict", ...}\\n\\n`` per scenario
        - ``data: {"type": "complete", ...}\\n\\n`` after all scenarios
        - ``data: {"type": "error", ...}\\n\\n`` on per-scenario or critical failures
    """
    # Resolved at call time, not as a default argument: a module-level default
    # would freeze whatever was configured when this module was first imported.
    if max_concurrency is None:
        max_concurrency = settings.verification_max_concurrency
    max_concurrency = max(1, max_concurrency)

    # Read once for the whole run, so every scenario sends the same system
    # prompt — the prompt is the cached prefix, and a value re-read per scenario
    # could change under a live config reload and cost every later cache hit.
    tool_result_window = max(0, settings.verification_tool_result_window)
    escalate_inconclusive = settings.verification_escalate_inconclusive
    # Rule 0b is dropped entirely when no candidates can arrive, rather than
    # left in as a instruction about input that will never appear.
    system_prompt = _SYSTEM_PROMPT.replace(
        f"{_CANDIDATE_RULE_SLOT}\n",
        f"{_CANDIDATE_PROMPT_RULE}\n" if code_index_enabled else "",
    )
    if tool_result_window:
        system_prompt += _ELISION_PROMPT_RULE

    scenarios = parse_bdd_scenarios(bdd_content)
    if not scenarios:
        yield _sse_event({"type": "error", "message": "No BDD scenarios found in content"})
        return

    # Verifying the same scenario twice costs twice and can answer it two
    # different ways in one report. Duplicates are dropped before any LLM call.
    scenarios, duplicates_skipped = deduplicate_scenarios(scenarios)
    if duplicates_skipped:
        logger.info(
            "Skipped %d duplicate scenario(s) of %d for session=%s",
            duplicates_skipped,
            duplicates_skipped + len(scenarios),
            session_id,
        )

    try:
        scope = await _prepare_scope(mode, github_input, pat)
    except GitHubServiceError as exc:
        yield _sse_event({"type": "error", "message": f"Could not prepare code source: {exc.message}"})
        return
    repo = scope.repo

    # RAG enrichment — per-scenario knowledge-base context, gated by the per-run
    # use_knowledge_base opt-in (default off). Graceful: [] per scenario on any
    # failure or when disabled.
    per_scenario_chunks = await retrieve_rag_per_scenario(
        user_id, scenarios, use_knowledge_base, project_id, vector_config
    )

    # Code-index discovery, gated the same way and just as graceful: [] per
    # scenario when disabled, unavailable, or stale. A stale index is refused
    # rather than used — it would send the agent to paths that have since moved
    # or gone, and a confidently wrong pointer is worse than none.
    per_scenario_candidates: list[list[dict]] = [[] for _ in scenarios]
    if code_index_enabled:
        freshness = await _code_index_freshness(
            db, user_id, project_id, repo, scope.default_ref, pat
        )
        if freshness.warning:
            yield _sse_event({"type": "warning", "message": freshness.warning})
        if freshness.usable:
            per_scenario_candidates = await code_index_service.find_candidate_files(
                user_id,
                project_id,
                [scenario["text"] for scenario in scenarios],
                config=vector_config,
            )

    total = len(scenarios)

    # The prompt is split so its shared half comes first and stays byte-identical
    # across scenarios: that is the whole basis of prompt caching, and building
    # the code block scenario-first (as this once did) makes a cache hit
    # impossible no matter what the provider supports.
    shared_evidence = _shared_evidence_block(scope.evidence_files)
    shared_parts = [f"GitHub repository: {repo}"]
    if scope.instruction:
        shared_parts.append(scope.instruction)

    # One recursive tree call replaces the directory walk every scenario would
    # otherwise repeat — several rounds each, before any code is read. It goes
    # in the shared prefix, so it is paid for once for the whole run and read
    # from cache after that. Best-effort: an empty tree simply leaves the agent
    # exploring with list_directory exactly as it did before.
    repo_tree = github_tools.format_repo_tree(
        await github_tools.get_repo_tree(repo, pat, scope.default_ref),
        compact=settings.verification_compact_tree,
    )
    if repo_tree:
        shared_parts.append(repo_tree)

    if shared_evidence:
        shared_parts.append(shared_evidence)
    shared_content = "\n\n".join(shared_parts)

    # One cache of GitHub reads for the whole run: scenarios converge on the
    # same files, and a pinned ref makes their contents immutable.
    read_cache: dict = {}

    # The plan is announced before any work so a client can show real progress
    # against the number of scenarios that will actually be verified, rather
    # than against a count that includes the duplicates just dropped.
    yield _sse_event(
        {
            "type": "plan",
            "total": total,
            "duplicates_skipped": duplicates_skipped,
            "max_concurrency": max_concurrency,
        }
    )

    async def _verify_one(
        scenario: dict, rag_chunks: list[dict], candidates: list[dict]
    ) -> _ScenarioOutcome:
        """Run one scenario end to end, returning its outcome rather than raising."""
        outcome = _ScenarioOutcome(scenario=scenario, rag_chunks=rag_chunks)

        # A fresh tool executor per scenario, so errors and reads never bleed
        # between scenarios now that several are in flight at once.
        tool_executor, tool_errors, tool_successes, files_read = _make_tool_executor(
            pat=pat, repo=repo, default_ref=scope.default_ref, read_cache=read_cache
        )

        # Only when the evidence could not be shared does a scenario pay for its
        # own relevance-selected block.
        evidence_block = (
            shared_evidence
            if shared_evidence is not None
            else _format_evidence_block(scenario, scope.evidence_files)
        )

        scenario_parts = [
            f"Evaluate this BDD scenario:\n{scenario['text']}",
            f"Scenario ID: {scenario['id']}",
        ]
        # The scenario's own acceptance criterion, when the feature file records
        # one. This used to arrive misattributed — the parser gave each scenario
        # the *next* one's clause — so the model was shown a criterion it was
        # not judging.
        if scenario.get("ac_clause"):
            scenario_parts.insert(
                1, f"Source acceptance criterion: {scenario['ac_clause']}"
            )
        rag_block = format_rag_block(rag_chunks)
        if rag_block:
            scenario_parts.append(rag_block)

        # Per-scenario, so it belongs AFTER the cache breakpoint with the other
        # per-scenario text. Folding it into the shared prefix would make that
        # prefix different for every scenario, which is not a cache hit for any
        # of them — the same trap the evidence block below documents.
        candidate_block = code_index_service.format_candidate_block(candidates)
        if candidate_block:
            scenario_parts.append(candidate_block)

        # A per-scenario evidence block (the >6-file fallback) must NOT go in the
        # cached message: folding it in makes the "shared" prefix different for
        # every scenario, which is not a cache hit for anyone. It belongs with
        # the other per-scenario text, after the breakpoint.
        per_scenario_evidence = (
            evidence_block if evidence_block and shared_evidence is None else ""
        )
        if per_scenario_evidence:
            scenario_parts.append(per_scenario_evidence)

        messages = [
            {"role": "system", "content": system_prompt},
            # Everything up to here repeats for every scenario in this run, so
            # it is marked as the cacheable prefix. The flag is a hint: a
            # provider without prefix caching drops it and behaves as before.
            {
                "role": "user",
                "content": shared_content,
                "cache_control": True,
            },
            {"role": "user", "content": "\n\n".join(scenario_parts)},
        ]

        # A rate limit is the one failure worth waiting out. Several scenarios
        # are in flight against one account, so a burst that trips the limit is
        # self-inflicted and short-lived: the SDK has already retried inside its
        # own budget, and a pause long enough for the window to roll over turns
        # a dead scenario into a completed one. A run that finishes with every
        # verdict is strictly more accurate than one that stops early.
        raw_response = None
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            # Fresh per attempt, and kept only once one succeeds. A retried
            # scenario starts its conversation over, so accumulating across
            # attempts would report rounds that never ran together and turn
            # "rounds before the first read" into a number about nothing.
            stats = ToolLoopStats()
            try:
                raw_response = await llm.generate_with_tools(
                    messages=messages,
                    tools=GITHUB_TOOL_SCHEMAS,
                    tool_executor=tool_executor,
                    max_tool_rounds=max_tool_rounds,
                    stats=stats,
                    tool_result_window=tool_result_window,
                )
                outcome.stats = stats
                break
            except LLMProviderError as exc:
                if not getattr(exc, "retryable", False):
                    outcome.llm_error = f"LLM call failed: {exc}"
                    return outcome
                if attempt == _RATE_LIMIT_RETRIES:
                    outcome.llm_error = f"LLM call failed: {exc}"
                    outcome.llm_error_retryable = True
                    return outcome
                delay = _RATE_LIMIT_BACKOFF_SECONDS * (2**attempt)
                logger.info(
                    "Rate limited on scenario=%r; retrying in %.0fs (attempt %d/%d)",
                    scenario["title"],
                    delay,
                    attempt + 1,
                    _RATE_LIMIT_RETRIES,
                )
                await asyncio.sleep(delay)

        # Parse + validate the verdict. If the first attempt fails (unparseable
        # JSON or schema mismatch), do ONE corrective re-ask — the model reformats
        # its own prior answer into valid JSON — before giving up. This is the
        # standard output-fixing retry pattern.
        evidence_provided = bool(evidence_block)
        # Citations can be checked against whole files this scenario saw: the
        # ones the agent read from the start, plus exact-files evidence.
        # Pull-request evidence is deliberately excluded — those entries are
        # unified diffs, whose line count has nothing to do with the file's, so
        # checking a file line against a patch would reject correct citations.
        # Tool reads win a clash: they are the newer read of the same path.
        known_files = {
            f.path.lstrip("/"): f.content
            for f in (scope.evidence_files if mode == "exact_files" else [])
            if not f.path.startswith("[WARNING]")
        } | files_read
        def _verdict_from(payload) -> VerificationVerdict:
            return _build_verdict(
                payload,
                scenario,
                repo,
                tool_errors,
                tool_successes,
                evidence_provided,
                known_files,
            )

        try:
            outcome.verdict = _verdict_from(_extract_json_from_text(raw_response))
        except (ValueError, json.JSONDecodeError) as exc:
            # Bound to an outer name: Python unbinds `exc` when the except block
            # ends, and the re-ask below needs the error to tell the model what
            # was wrong with its previous reply.
            parse_error = exc
            logger.warning(
                "Verdict parse/validation failed for scenario=%r: %s | raw_response=%.500r; re-asking once",
                scenario["title"],
                parse_error,
                raw_response,
            )
            try:
                fixed = await _reask_verdict(llm, raw_response, scenario, parse_error)
                outcome.verdict = _verdict_from(fixed)
            except (ValueError, json.JSONDecodeError, LLMProviderError) as retry_exc:
                logger.warning(
                    "Corrective re-ask failed for scenario=%r: %s",
                    scenario["title"],
                    retry_exc,
                )
                outcome.error = (
                    f"LLM returned malformed verdict for "
                    f"'{scenario['title']}': {retry_exc}"
                )
                return outcome

        # An "inconclusive" verdict is not an answer — it is a declined
        # question, and a report of them is worth little. The model reaches for
        # it early: measured runs returned inconclusive after two or three tool
        # calls out of a budget of twenty, and the same scenarios came back
        # inconclusive run after run, so this is a real gap rather than noise.
        #
        # It has already been made to say what evidence was missing (the prompt
        # requires implementation_suggestion to name the file or search that
        # would settle it), so the cheapest fix is to hand that back and let it
        # look. Only inconclusive scenarios pay for this.
        if escalate_inconclusive and outcome.verdict.status == "inconclusive":
            escalated = await _escalate_inconclusive(
                llm=llm,
                messages=messages,
                verdict=outcome.verdict,
                tool_executor=tool_executor,
                max_tool_rounds=max_tool_rounds,
                tool_result_window=tool_result_window,
                stats=outcome.stats,
                scenario=scenario,
                build=_verdict_from,
            )
            if escalated is not None:
                logger.info(
                    "Escalated inconclusive scenario=%r -> %s",
                    scenario["title"],
                    escalated.status,
                )
                outcome.verdict = escalated

        return outcome

    passed = 0
    failed = 0
    inconclusive = 0
    partial = 0
    # Run totals, so one number per run can be compared against another run of
    # the same BDD with a different setting — which is the measurement any
    # claim about a token saving has to rest on.
    run_rounds = 0
    run_input_tokens = 0
    run_cached_input_tokens = 0
    run_output_tokens = 0
    #: The last LLM failure, to recognise one that is not scenario-specific.
    previous_llm_error: str | None = None

    # A sliding window rather than launching everything at once. Starting every
    # scenario up front would run the whole batch before the first result is
    # read, which defeats the stop-on-repeated-failure rule below: a bad API key
    # would fail all of them before anyone noticed the first. The window bounds
    # the waste to what is genuinely in flight.
    pending: deque[asyncio.Task] = deque()
    next_index = 0

    def _fill_window() -> None:
        nonlocal next_index
        while len(pending) < max(1, max_concurrency) and next_index < total:
            pending.append(
                asyncio.create_task(
                    _verify_one(
                        scenarios[next_index],
                        per_scenario_chunks[next_index],
                        per_scenario_candidates[next_index],
                    )
                )
            )
            next_index += 1

    try:
        _fill_window()
        while pending:
            outcome = await pending.popleft()
            # Top up only after consuming, so the window stays the true limit.
            _fill_window()

            if outcome.llm_error:
                # The provider is built once for the whole run, so a failure that
                # repeats identically is about the provider, not about this
                # scenario — a missing key fails the twentieth call exactly as it
                # failed the first. Carrying on spends twenty doomed calls and
                # stacks twenty identical errors on the user, burying the one
                # fact they need.
                #
                # Sameness of message used to be the whole test, on the
                # reasoning that a transient failure "does not reproduce word
                # for word on different input". That was wrong: a provider
                # renders a rate limit as one fixed sentence regardless of
                # input, so the most ordinary transient failure there is looked
                # exactly like a missing API key — and a run that needed to wait
                # ten seconds was stopped and blamed on the user's config.
                #
                # The provider now says which kind it is, so ask it.
                if outcome.llm_error_retryable:
                    yield _sse_event(
                        {
                            "type": "error",
                            "message": (
                                f"{outcome.llm_error} This scenario was retried "
                                f"{_RATE_LIMIT_RETRIES} times and still hit the "
                                "limit, so it was skipped — the rest of the run "
                                "continues. If it keeps happening, lower "
                                "VERIFICATION_MAX_CONCURRENCY to send fewer "
                                "scenarios at once."
                            ),
                        }
                    )
                    continue

                if outcome.llm_error == previous_llm_error:
                    yield _sse_event(
                        {
                            "type": "error",
                            "message": (
                                f"{outcome.llm_error} — this failed for every "
                                "scenario, so the run was stopped. It is a "
                                "configuration problem rather than a problem "
                                "with your BDD."
                            ),
                        }
                    )
                    return
                previous_llm_error = outcome.llm_error
                yield _sse_event({"type": "error", "message": outcome.llm_error})
                continue

            if outcome.error:
                yield _sse_event({"type": "error", "message": outcome.error})
                continue

            verdict = outcome.verdict
            rag_context_payload = rag_payload_from_chunks(outcome.rag_chunks)
            scenario = outcome.scenario

            usage = _usage_payload(outcome.stats)
            run_rounds += usage["rounds"]
            run_input_tokens += usage["input_tokens"]
            run_cached_input_tokens += usage["cached_input_tokens"]
            run_output_tokens += usage["output_tokens"]
            # Logged as well as streamed: comparing two runs is the point, and
            # doing that from the server log needs no client support and
            # survives the browser tab being closed halfway through.
            logger.info(
                "Scenario cost session=%s scenario=%r rounds=%d "
                "rounds_before_first_read=%s input_tokens=%d (cached=%d) "
                "output_tokens=%d",
                session_id,
                scenario["title"],
                usage["rounds"],
                usage["rounds_before_first_read"],
                usage["input_tokens"],
                usage["cached_input_tokens"],
                usage["output_tokens"],
            )

            yield _sse_event(verdict_event_dict(verdict, rag_context_payload, usage))

            if verdict.status == "pass":
                passed += 1
            elif verdict.status == "inconclusive":
                inconclusive += 1
            elif verdict.status == "partial":
                partial += 1
            else:
                failed += 1

            # Persist after yielding. Use a SAVEPOINT per verdict: a persist
            # failure rolls back ONLY this row, not the whole run's transaction —
            # a plain db.rollback() here would discard every verdict already
            # flushed for earlier scenarios (the request commits once at
            # teardown). This stays in the consuming loop, never in the
            # concurrent workers: one AsyncSession is not safe to share between
            # tasks, and serialising the writes costs nothing next to the LLM
            # calls that actually take the time.
            try:
                async with db.begin_nested():
                    db.add(
                        build_verification_result(
                            session_id,
                            user_id,
                            verdict,
                            rag_context_payload,
                            mode=mode,
                            github_input=github_input,
                        )
                    )
                    await db.flush()
            except Exception as exc:
                logger.warning(
                    "Failed to persist verdict for session=%s scenario=%r: %s",
                    session_id,
                    scenario["title"],
                    exc,
                    exc_info=True,
                )
    finally:
        # Whatever ended the loop — completion, an early stop, or the client
        # hanging up — nothing in flight should outlive this generator.
        for task in pending:
            task.cancel()

    # ``inconclusive`` is additive: existing consumers read total/passed/failed
    # and keep working, and a client that has not been updated simply does not
    # show the third count rather than mis-reading one of the other two.
    logger.info(
        "Run cost session=%s scenarios=%d rounds=%d input_tokens=%d "
        "(cached=%d) output_tokens=%d tool_result_window=%d",
        session_id,
        total,
        run_rounds,
        run_input_tokens,
        run_cached_input_tokens,
        run_output_tokens,
        tool_result_window,
    )

    yield _sse_event(
        {
            "type": "complete",
            "total": total,
            "passed": passed,
            "failed": failed,
            "inconclusive": inconclusive,
            "partial": partial,
            # Additive, like ``inconclusive`` before it: a client that does not
            # know about ``usage`` reads the counts it always did.
            "usage": {
                "rounds": run_rounds,
                "input_tokens": run_input_tokens,
                "cached_input_tokens": run_cached_input_tokens,
                "output_tokens": run_output_tokens,
                "tool_result_window": tool_result_window,
            },
        }
    )
