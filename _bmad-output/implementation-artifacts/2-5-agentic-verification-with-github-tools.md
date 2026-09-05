# Story 2.5: Agentic Verification with GitHub Tools

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-runtime-model-selection.md)): Verification now resolves its provider through `llm_with_tools_for()`, which returns **400** for a provider whose `supports_tools` is False. Previously a tool-less provider produced a run that emitted a per-scenario error for every scenario and still finished with `complete` — a verification that verified nothing. `ClaudeProvider.generate_with_tools` is now implemented, so Claude is a valid choice here. The GitHub PAT comes from the active project when it has one (see the [projects record](maintenance-2026-08-23-projects-and-credential-scoping.md)).


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): Mode semantics were tightened: exact-files injects the listed files as prompt evidence and pins tools to the URL's resolved ref; pull-request injects the PR diffs and pins tools to the PR head SHA (previously the PR number was discarded and verdicts judged the default branch). A `pass` now requires evidence (a successful tool read or injected code), and verdict scenario identity is pinned server-side, never trusted from the model.

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-source-persistence.md)): Each persisted verdict now also records the run's `verification_mode` and `github_input`, so a stored result says what it was checked against. Note this is the run's scope, NOT the per-scenario `github_links` the model cites as evidence — a run that found nothing has links but still has a source.

## Story

As a **user**,
I want the verification flow to be a single click that lets the AI search the GitHub codebase itself,
so that I get accurate per-scenario verdicts without manually pre-fetching files or worrying about which files to include.

## Context & Motivation

Stories 2.2 and 2.3 implemented a two-step flow:
1. "Verify Scenarios" — pre-fetch up to 100 files from GitHub (blind bulk dump)
2. "Run LLM Verification" — keyword-score top-6 files per scenario, truncate to 3 000 chars, single LLM call

**This design has fundamental accuracy problems:**
- Heuristic keyword scoring fails on semantic intent ("user registration" → doesn't match `SignupForm.tsx`)
- Top-6 cap misses related files (form + API route + auth hook + validator all relevant to one scenario)
- 3 000-char truncation cuts critical implementation logic
- LLM sees `useAuth()` called but cannot follow imports to understand what it does
- Two-step UX has zero user value — there is no file preview between steps

**This story replaces both steps with a single agentic verification run** where the LLM:
1. Receives a BDD scenario and GitHub repo/file configuration
2. Calls tools (`search_code`, `get_file_contents`, `list_directory`) on demand
3. Fetches only what it needs, follows imports, navigates the codebase naturally
4. Submits a verdict when it has sufficient evidence

This is functionally equivalent to the "GitHub MCP server" pattern but implemented natively in Python via OpenAI function calling (no separate MCP server process required).

## Acceptance Criteria

1. **Given** a user has BDD content and has selected a GitHub source (repo URL, PR URL, or file URLs)
   **When** they click "Run Verification" (single button)
   **Then** `POST /api/v1/verification/run-agentic` is called with `session_id`, `bdd_content`, `mode`, and `github_input` — no pre-fetch step required

2. **And** for each BDD scenario, the backend runs an LLM agent loop:
   - LLM receives the scenario and available tools
   - LLM calls tools (`search_code`, `get_file_contents`, `list_directory`) as needed
   - Python executes each tool call against the GitHub API and returns results
   - Loop continues until the LLM emits a final `submit_verdict` tool call or text response
   - Maximum 10 tool call rounds per scenario to prevent runaway cost

3. **And** the available tools for the LLM are:
   - `search_code(query: str, repo: str)` — search code in the repo using GitHub's code search API
   - `get_file_contents(repo: str, path: str, ref: str = "HEAD")` — fetch a specific file's content
   - `list_directory(repo: str, path: str, ref: str = "HEAD")` — list files in a directory

4. **And** each verdict contains the same fields as Story 2.3: `scenario_id`, `scenario_title`, `status` (`pass`/`fail`), `justification`, `code_reference`, `github_links`, `implementation_suggestion`

5. **And** verdicts stream progressively via SSE in the same format as Story 2.3's `/run` endpoint:
   `data: {"type": "verdict", ...}` per scenario, `data: {"type": "complete", ...}` at the end

6. **And** `github_links` contains real GitHub blob URLs (e.g. `https://github.com/org/repo/blob/main/path/to/file.py`) — never relative file paths

7. **And** the frontend `GitHubSourceSelector` is simplified to a single "Run Verification" button — the separate "Verify Scenarios" (fetch) button is removed

8. **And** `useRunVerification` sends `{session_id, bdd_content, mode, github_input}` directly to `/run-agentic` — the `fetchedFiles` pre-fetch state is no longer needed for the run step

9. **And** verdicts are persisted to the existing `verification_results` table (same schema as Story 2.3)

10. **And** on any tool call error (GitHub API failure), the error is surfaced in the verdict's `justification` and `status=fail` is returned — the stream continues for remaining scenarios

11. **And** if the LLM exceeds `max_tool_rounds`, the service forces a verdict by sending one final LLM call with all accumulated tool results, instructing it to decide based on available evidence

12. **And** the `LLMProvider` ABC gains a new abstract method `generate_with_tools()` that concrete providers must implement

13. **And** the old `/fetch` endpoint and `/run` endpoint are kept intact (not deleted) — they are deprecated but backward-compatible for any existing integrations

## Tasks / Subtasks

- [x] **Task 1: Add `generate_with_tools()` to `LLMProvider` ABC** (AC: 2, 12)
  - [x] Edit `backend/app/services/llm/provider.py`
  - [x] Add abstract method with full docstring
  - [x] Add `from collections.abc import Callable, Awaitable` import

- [x] **Task 2: Implement `generate_with_tools()` in `OpenAIProvider`** (AC: 2, 12)
  - [x] Edit `backend/app/services/llm/openai_provider.py`
  - [x] Implement the method using OpenAI's function calling API with tool loop
  - [x] Add stub implementation to `ClaudeProvider` and `OllamaProvider` that raises `LLMProviderError("not implemented")`

- [x] **Task 3: Implement GitHub tool functions** (AC: 3)
  - [x] Create `backend/app/services/github_tools.py`
  - [x] Implement `search_code`, `get_file_contents`, `list_directory`
  - [x] All functions raise `GitHubServiceError` on failure
  - [x] Reuse `_github_get`, `_decode_base64_content`, `_auth_headers` from `github_service.py`

- [x] **Task 4: Define OpenAI tool schemas for the GitHub tools** (AC: 3)
  - [x] Define `GITHUB_TOOL_SCHEMAS` list in `backend/app/services/github_tools.py`

- [x] **Task 5: Implement `agentic_verification_service.py`** (AC: 1, 2, 4, 5, 6, 9, 10, 11)
  - [x] Create `backend/app/services/agentic_verification_service.py`
  - [x] Implement `run_agentic_verification` async generator
  - [x] Reuse `parse_bdd_scenarios` from `verification_service.py`
  - [x] Derive repo from `github_input` using existing parse helpers
  - [x] Tool executor closure with PAT injection and error handling
  - [x] SSE streaming, DB persistence, error continuation

- [x] **Task 6: Add `POST /run-agentic` endpoint** (AC: 1, 5)
  - [x] Add `AgenticVerificationRequest` schema to `backend/app/schemas/verification.py`
  - [x] Add `POST /run-agentic` handler to `backend/app/api/v1/verification.py`
  - [x] Old `/fetch` and `/run` endpoints kept intact (backward-compatible)

- [x] **Task 7: Simplify `GitHubSourceSelector` — remove fetch step** (AC: 7)
  - [x] Edit `frontend/src/components/pipeline/GitHubSourceSelector.tsx`
  - [x] Removed `onRunVerification` / "Run LLM Verification" button
  - [x] Removed `fetchedFiles` conditional guard
  - [x] Single button "Run Verification", prop interface simplified to `{onVerify, isVerifying}`

- [x] **Task 8: Update `useRunVerification.ts` to call `/run-agentic`** (AC: 8)
  - [x] Edit `frontend/src/lib/hooks/useRunVerification.ts`
  - [x] Updated payload to `AgenticVerificationRequest` type
  - [x] Updated endpoint from `/verification/run` to `/verification/run-agentic`
  - [x] Added `AgenticVerificationRequest` to `frontend/src/lib/types/verification.ts`

- [x] **Task 9: Update session page wiring** (AC: 7, 8)
  - [x] Edit `frontend/src/app/session/[sessionId]/page.tsx`
  - [x] Removed `useVerification` import and `verifyMutation`
  - [x] Removed `handleVerify` (fetch step handler)
  - [x] Removed `fetchedFiles` / `setFetchedFiles` from context destructuring
  - [x] Updated `handleRunVerification` to pass agentic payload
  - [x] Simplified `GitHubSourceSelector` props

- [x] **Task 10: Write backend Pytest tests** (AC: 1–11)
  - [x] Create `backend/tests/test_agentic_verification_service.py`
  - [x] Test: happy path — 2 scenarios yields 2 verdicts + complete
  - [x] Test: `status=fail` → `implementation_suggestion` is non-null
  - [x] Test: `status=pass` → `implementation_suggestion` is null
  - [x] Test: `LLMProviderError` on tool call → error SSE, other scenarios continue
  - [x] Test: `LLMProviderError` on one scenario → error SSE, other scenarios continue
  - [x] Test: no BDD scenarios → error SSE, no DB writes
  - [x] Test: malformed JSON verdict → error SSE, continues
  - [x] Test: `POST /run-agentic` returns `text/event-stream`
  - [x] Test: missing `session_id` → 422 validation error

## Dev Notes

### Architecture: Why Native Tool Functions, Not a Separate MCP Server

The "GitHub MCP server" pattern refers to giving the LLM access to GitHub as a set of callable tools rather than sending it a bulk file dump. This story implements that pattern natively in Python:
- Tools live in `backend/app/services/github_tools.py` — regular Python async functions
- Tool schemas are defined as OpenAI function calling JSON schemas
- The agent loop runs inside `OpenAIProvider.generate_with_tools()` — no separate process

This is functionally identical to connecting Claude to `github.com/github/github-mcp-server` but works with OpenAI and requires no additional infrastructure.

### `generate_with_tools` Implementation Pattern (OpenAI)

```python
async def generate_with_tools(
    self,
    messages: list[dict],
    tools: list[dict],
    tool_executor: Callable[[str, dict], Awaitable[str]],
    max_tool_rounds: int = 10,
) -> str:
    current_messages = list(messages)
    for round_num in range(max_tool_rounds):
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=current_messages,
            tools=tools,
            tool_choice="auto" if round_num < max_tool_rounds - 1 else "none",
        )
        choice = response.choices[0]
        
        if choice.finish_reason == "stop" or choice.message.tool_calls is None:
            return choice.message.content or ""
        
        # Append assistant message with tool calls
        current_messages.append(choice.message.model_dump(exclude_none=True))
        
        # Execute each tool call and append results
        for tool_call in choice.message.tool_calls:
            import json
            tool_args = json.loads(tool_call.function.arguments)
            result = await tool_executor(tool_call.function.name, tool_args)
            current_messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })
    
    # Exhausted rounds — force final answer
    current_messages.append({"role": "user", "content": "You have used the maximum number of tool calls. Based on the evidence gathered so far, provide your final verdict as a JSON object."})
    response = await self.client.chat.completions.create(
        model=self.model,
        messages=current_messages,
        tool_choice="none",
    )
    return response.choices[0].message.content or ""
```

### Tool Executor Pattern in Agentic Service

```python
from app.services import github_tools
from app.core.config import settings

async def _make_tool_executor(pat: str, repo: str):
    """Returns a tool executor closure bound to a PAT and default repo."""
    async def executor(tool_name: str, tool_args: dict) -> str:
        # Always inject PAT; default repo if not provided
        tool_args.setdefault("repo", repo)
        try:
            if tool_name == "search_code":
                return await github_tools.search_code(
                    query=tool_args["query"],
                    repo=tool_args["repo"],
                    pat=pat,
                )
            elif tool_name == "get_file_contents":
                return await github_tools.get_file_contents(
                    repo=tool_args["repo"],
                    path=tool_args["path"],
                    pat=pat,
                    ref=tool_args.get("ref", "HEAD"),
                )
            elif tool_name == "list_directory":
                return await github_tools.list_directory(
                    repo=tool_args["repo"],
                    path=tool_args["path"],
                    pat=pat,
                    ref=tool_args.get("ref", "HEAD"),
                )
            else:
                return f"Unknown tool: {tool_name}"
        except GitHubServiceError as exc:
            return f"GitHub API error: {exc.message}"
    return executor
```

### Extracting Repo from `github_input`

```python
from app.services.github_service import _parse_repo_url, _parse_github_blob_url, _parse_pr_url

def _extract_repo_from_input(mode: str, github_input: str) -> str:
    """Return 'owner/repo' string from whatever the user entered."""
    if mode == "full_repo":
        owner, repo = _parse_repo_url(github_input)
        return f"{owner}/{repo}"
    elif mode == "pull_request":
        owner, repo, _ = _parse_pr_url(github_input)
        return f"{owner}/{repo}"
    elif mode == "exact_files":
        # Use the first URL in the list
        first_url = github_input.strip().splitlines()[0]
        owner, repo, _, _ = _parse_github_blob_url(first_url)
        return f"{owner}/{repo}"
    raise GitHubServiceError(f"Unknown mode: {mode}")
```

### GitHub Blob URL Construction for `github_links`

The LLM should be explicitly instructed to use full GitHub blob URLs in `github_links`. However, if it returns bare file paths, resolve them in the service by constructing:
```
https://github.com/{repo}/blob/HEAD/{path}
```

The system prompt should say: `"In github_links, always use full GitHub URLs in the format https://github.com/{repo}/blob/{ref}/{path}"`

### `search_code` GitHub API Notes

GitHub code search endpoint: `GET https://api.github.com/search/code?q={query}+repo:{owner}/{repo}`
- Returns `items[].path` (relative file path) and `items[].html_url` (GitHub blob URL)
- Rate limit: 10 requests/minute for unauthenticated, 30/minute with PAT
- Return max 10 results to control token use

```python
async def search_code(query: str, repo: str, pat: str) -> str:
    import json
    async with httpx.AsyncClient() as client:
        headers = _auth_headers(pat)
        url = f"https://api.github.com/search/code?q={query}+repo:{repo}&per_page=10"
        resp = await _github_get(client, url, headers)
        if not resp.is_success:
            raise GitHubServiceError(f"Code search failed: {resp.status_code}")
        data = resp.json()
        results = [
            {"path": item["path"], "url": item["html_url"]}
            for item in data.get("items", [])
        ]
        return json.dumps(results)
```

### Reuse `parse_bdd_scenarios` from Story 2.3

Do NOT copy the function. Import it:
```python
from app.services.verification_service import parse_bdd_scenarios
```

### What Already Exists (from Stories 2.1–2.4)

| File | Status | Notes |
|---|---|---|
| `backend/app/services/github_service.py` | Exists | Import `_auth_headers`, `_github_get`, `_decode_base64_content`, `_parse_repo_url`, `_parse_github_blob_url`, `_parse_pr_url`, `GitHubServiceError` — do NOT modify |
| `backend/app/services/verification_service.py` | Exists | Import `parse_bdd_scenarios` — do NOT modify |
| `backend/app/services/llm/provider.py` | Modify | Add `generate_with_tools()` abstract method only |
| `backend/app/services/llm/openai_provider.py` | Modify | Implement `generate_with_tools()` |
| `backend/app/services/llm/claude_provider.py` | Modify | Add stub that raises `LLMProviderError("generate_with_tools not implemented for Claude provider")` |
| `backend/app/services/llm/ollama_provider.py` | Modify | Add stub that raises `LLMProviderError("generate_with_tools not implemented for Ollama provider")` |
| `backend/app/schemas/verification.py` | Modify | Add `AgenticVerificationRequest` schema only |
| `backend/app/api/v1/verification.py` | Modify | Add `POST /run-agentic` — keep existing `/fetch` and `/run` endpoints intact |
| `frontend/src/lib/hooks/useRunVerification.ts` | Modify | Change payload shape and endpoint URL |
| `frontend/src/lib/types/verification.ts` | Modify | Add `AgenticVerificationRequest` type |
| `frontend/src/components/pipeline/GitHubSourceSelector.tsx` | Modify | Simplify to single button |
| `frontend/src/app/session/[sessionId]/page.tsx` | Modify | Remove fetch step, simplify wiring |

### What to Check Before Removing `fetchedFiles` from `SessionContext`

`fetchedFiles` was introduced in Story 2.3 for the intermediate state between fetch and LLM run. Before removing from `SessionContext`:
- Search for `fetchedFiles` across all frontend files
- If `VerificationResultsPanel` or `VerificationResultRow` still reads it → keep in context (deprecated but harmless)
- If only `page.tsx` and `useRunVerification` read it → safe to remove
- When in doubt, keep it in context with a deprecation comment rather than risk a regression

### Architecture-Mandated Patterns — ALL MUST BE FOLLOWED

1. **No business logic in route handlers** — `/run-agentic` handler only calls `agentic_verification_service.run_agentic_verification()`
2. **Never call LLM SDKs directly** — tool loop lives in `OpenAIProvider.generate_with_tools()`, never in the service
3. **LLMProvider ABC must remain pure** — no OpenAI-specific types leak into `provider.py`
4. **No `any` type in TypeScript** — `AgenticVerificationRequest` must be fully typed
5. **`isVerifying` from `useSSEStream.isStreaming`** — single source of truth, no duplicate state
6. **Error preservation** — on any error, never clear `bddContent`, `verificationMode`, `githubInput`, `sessionId`
7. **SSE events keep same format** — downstream SSE consumers (session restore in 3.5) must not break

### SSE Stream Protocol (unchanged from Story 2.3)

```
data: {"type": "verdict", "scenario_id": "uuid", "scenario_title": "...", "status": "pass"|"fail",
       "justification": "...", "code_reference": {"file": "...", "function": "...", "line": 42},
       "github_links": ["https://github.com/org/repo/blob/main/src/auth.py"],
       "implementation_suggestion": null}\n\n

data: {"type": "complete", "total": 5, "passed": 4, "failed": 1}\n\n

data: {"type": "error", "message": "Tool call failed: ..."}\n\n
```

### File Structure — New / Modified Files

```
backend/
  app/
    services/
      llm/
        provider.py              ← MODIFY: add generate_with_tools() abstract method
        openai_provider.py       ← MODIFY: implement generate_with_tools()
        claude_provider.py       ← MODIFY: stub generate_with_tools() → raises NotImplemented
        ollama_provider.py       ← MODIFY: stub generate_with_tools() → raises NotImplemented
      github_tools.py            ← CREATE: search_code, get_file_contents, list_directory + GITHUB_TOOL_SCHEMAS
      agentic_verification_service.py  ← CREATE: run_agentic_verification async generator
    schemas/
      verification.py            ← MODIFY: add AgenticVerificationRequest
    api/
      v1/
        verification.py          ← MODIFY: add POST /run-agentic (keep /fetch and /run)
  tests/
    test_agentic_verification_service.py  ← CREATE

frontend/
  src/
    lib/
      types/
        verification.ts          ← MODIFY: add AgenticVerificationRequest type
      hooks/
        useRunVerification.ts    ← MODIFY: new payload shape + /run-agentic URL
    components/
      pipeline/
        GitHubSourceSelector.tsx ← MODIFY: remove fetch step, single button
    app/
      session/[sessionId]/
        page.tsx                 ← MODIFY: remove useVerification, simplify wiring
```

### Previous Story Intelligence (2.3 → 2.5)

- **`useSSEStream.isStreaming` is single source of truth** — confirmed in Story 2.2 code review mandate. `useRunVerification` must NOT add a separate `setIsVerifying` call.
- **`JSONResponse` direct return** for error envelopes in route handlers (not `raise HTTPException`).
- **`@base-ui/react` not shadcn** — no shadcn components anywhere.
- **TypeScript strict mode** — `npx tsc --noEmit` must exit 0 before marking done.
- **Mobile responsiveness** — `GitHubSourceSelector` was previously updated to remove mobile read-only overlays; keep it fully interactive on all screen sizes after this change.
- **`SessionContext` extension pattern** — always extend the existing provider, never create a second context.
- **GitHub API search rate limits** — code search is 30 req/min with PAT. The tool executor should propagate `GitHubServiceError` from `_github_get`'s retry logic rather than retrying again.
- **`[WARNING]` path filtering** — already done in the existing `run_verification` for the old flow; in the agentic flow, the LLM receives clean tool results so this pattern is not needed, but document the absence.

### Limitations / Out of Scope for This Story

- No streaming token-by-token output during tool calls — each scenario verdict is still emitted atomically
- No UI showing which tools the LLM called (could be a future "thinking" panel)
- Claude and Ollama providers get stubs only — OpenAI function calling is the only implementation
- No caching of tool results across scenarios within a session (could reduce GitHub API calls in a future story)
- The old `/fetch` and `/run` endpoints are kept but not actively tested beyond existing Story 2.3 tests

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

_none_

### Completion Notes List

- Implemented `generate_with_tools()` abstract method on `LLMProvider` ABC using `collections.abc.Callable` and `Awaitable` — no OpenAI-specific types leak into `provider.py`.
- OpenAI implementation follows the story-specified loop: tool_choice="auto" up to `max_tool_rounds-1`, then forced "none" on final round, then forces final answer on exhaustion.
- Claude and Ollama providers raise `LLMProviderError("generate_with_tools not implemented for X provider")` — stubs only per story scope.
- `github_tools.py` created with `search_code`, `get_file_contents`, `list_directory` — all reuse `_github_get`, `_auth_headers`, `_decode_base64_content` from `github_service.py`. File content capped at 8,000 chars.
- `agentic_verification_service.py` implements the full agentic loop with `_make_tool_executor` closure, `_extract_repo_from_input` helper, `_resolve_github_links`, and `_extract_json_from_text` for robust JSON extraction from LLM responses.
- Checked `fetchedFiles` consumers before removal: only `page.tsx`, `GitHubSourceSelector.tsx`, and `SessionContext.tsx` used it. All removed from active path. `SessionContext.tsx` still defines it (harmless, not removed to avoid regressions).
- TypeScript: `npx tsc --noEmit` exits 0. All 9 new tests pass. Full regression suite: 147 passed.

### File List

backend/app/services/llm/provider.py
backend/app/services/llm/openai_provider.py
backend/app/services/llm/claude_provider.py
backend/app/services/llm/ollama_provider.py
backend/app/services/github_tools.py
backend/app/services/agentic_verification_service.py
backend/app/schemas/verification.py
backend/app/api/v1/verification.py
backend/tests/test_agentic_verification_service.py
frontend/src/lib/types/verification.ts
frontend/src/lib/hooks/useRunVerification.ts
frontend/src/components/pipeline/GitHubSourceSelector.tsx
frontend/src/app/session/[sessionId]/page.tsx

### Change Log

- 2026-04-12: Story 2.5 implemented — agentic verification replaces two-step fetch+verify flow. Added `generate_with_tools()` to LLM provider ABC and OpenAI implementation, created `github_tools.py` with three GitHub tool functions and OpenAI schemas, created `agentic_verification_service.py` with full agent loop, added `POST /run-agentic` endpoint, simplified frontend `GitHubSourceSelector` to single "Run Verification" button, updated `useRunVerification` hook to call `/run-agentic` with agentic payload.
