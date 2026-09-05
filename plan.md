# Hybrid discovery for agentic verification

> Implementation plan. Written to be executed in a fresh session with no prior
> context. Read this whole file before starting.

## Context — the problem this solves

Agentic verification currently finds code by exploring. For each BDD scenario the
agent calls `search_code` (which returns an empty list on private repos, the
normal case), then walks `list_directory` one directory at a time, then finally
reads files. Those exploration rounds happen **before any code is read**, and
every round in an agentic loop replays the entire conversation so far.

The cost is concentrated there. A single `get_file_contents` can return 40 000
characters (~10 000 tokens), and it is re-sent on every subsequent round of that
scenario. By round four or five a single scenario is sending 30 000+ tokens per
call. Measured against the project's OpenAI account — **30 000 TPM, tier 1** —
one scenario can consume the entire per-minute budget, which is why verification
rate-limits on OpenAI while working fine on Claude.

**What this plan does:** replace the *discovery* phase with semantic retrieval
over a Pinecone-indexed copy of the repository, so the agent is told which files
to open instead of hunting for them.

**What this plan explicitly does NOT do:** it does not replace the agent's file
reads with retrieved chunks. That distinction is the whole design — see the next
section, and do not compromise it.

## The rule that must not be broken

**Retrieved chunks are a pointer to where to look. They are never the evidence a
verdict rests on.** The agent must still call `get_file_contents` on the paths
retrieval suggests, and must still base `pass` / `fail` on what it read live.

The reason is that vector search cannot prove absence. It always returns the
top-k most similar chunks and never returns "this does not exist". A `fail`
requires positive evidence that behaviour is *not* in the code — and the
existing design goes to considerable lengths to protect that: the `inconclusive`
status, the hedge detector (`_HEDGE_PATTERN`), the "a file you did not read is
NOT evidence of absence" prompt rule, and the guard that a `pass` must be backed
by a real file read. Feeding retrieved snippets in as evidence would let the
model conclude "not implemented" from a sample it cannot know is complete, which
is exactly the false-`fail` those mechanisms exist to prevent.

Three secondary reasons retrieval cannot stand alone:

- **Multi-hop call chains.** System prompt rule 5 exists because a frontend
  control is half a feature (page → hook → API client → route handler).
  Similarity search returns similar chunks, not a call chain.
- **Identifier weakness.** `_query_matches_hybrid` in `knowledge_service.py`
  already documents this: cosine similarity on embeddings ranks the chunk
  literally containing `PROJ-142` or `login_handler` below generic prose.
- **Staleness.** An index is a snapshot; the loop reads live at a pinned ref,
  and PR mode pins to the head SHA deliberately.

## Current state (verified at commit `36e93c8`)

Everything below already exists and should be reused, not rebuilt.

| Piece | Where | Note |
|---|---|---|
| `VectorConfig`, `vector_config_for(project)` | `backend/app/services/project_config_service.py` | Per-project embedding vendor + index. Masks the key in `__repr__`. |
| `_embed_chunks(chunks, input_type, config)` | `backend/app/services/vector_service.py` | `input_type="document"` when storing, `"query"` when searching — Voyage embeds them asymmetrically. |
| `index_name_for(config)` | `backend/app/services/vector_service.py` | Resolves the project's index. |
| `_upsert_to_pinecone(...)` | `backend/app/services/knowledge_service.py` | Deletes a source's existing vectors first, then upserts. Takes `config`. |
| `knowledge_namespace(user_id, project_id)` | `backend/app/services/knowledge_service.py` | Returns `{user}:{project}:knowledge`. |
| `_query_matches_hybrid(...)` | `backend/app/services/knowledge_service.py` | Vector search + lexical re-rank on identifiers. Reusable. |
| `get_repo_tree(repo, pat, ref)` | `backend/app/services/github_tools.py` | Recursive tree, filters `node_modules`/build dirs, caps at `_TREE_PATH_CAP = 1500`. **Already wired** into `run_agentic_verification` (~line 797). |
| `get_file_contents(repo, path, pat, ref, offset)` | `backend/app/services/github_tools.py` | Paginated with announced truncation. |
| `retrieve_rag_per_scenario(...)` | `backend/app/services/verification_service.py` | Per-scenario KB retrieval — the template to copy for code retrieval. |
| `VERIFICATION_MAX_CONCURRENCY` | `backend/app/core/config.py` | Currently 2. |

Relevant constants: `_FILE_CONTENT_CAP = 40_000` (`github_tools.py`),
`_FILE_CONTENT_TRUNCATE = 24_000` (`verification_service.py`). **Do not lower
these** — they were raised to fix a truncation bug where a route file was cut
one line above its filter implementation and every filter was reported missing.

---

## Implementation

### Step 0 — Measure before building (gate for everything below) — ✅ BUILT

The Context section's cost picture partly predates the repo tree: `get_repo_tree`
is **already** in the cached shared prefix (~line 797), which already removes most
of the blind `list_directory` walking described above. The 40–60% estimate was
made before that landed and may be stale. Do not build the Pinecone pipeline on
an assumed number.

**What now exists** (commit this section against, then run the measurement):

| Piece | Where |
|---|---|
| `ToolLoopStats` — rounds, normalised token counts, tool names per round | `backend/app/services/llm/provider.py` |
| `stats=` / `tool_result_window=` on `generate_with_tools` | all three providers |
| `_usage_payload`, per-scenario + per-run log lines | `backend/app/services/agentic_verification_service.py` |
| `usage` on the `verdict` and `complete` SSE events | `verification_service.verdict_event_dict` |
| `VERIFICATION_TOOL_RESULT_WINDOW` (default `0` = off) | `backend/app/core/config.py` |

Token counts are normalised across vendors: `input_tokens` is always the whole
prompt *including* cache hits. Anthropic excludes cache reads from its own
`input_tokens`, so its provider sums the three buckets back together — without
that, a cached Claude run looks an order of magnitude cheaper than an identical
OpenAI one purely because the vendors count differently.

**The measurement to run.** Verify one representative BDD file against a real
repo, then read the server log:

```
Scenario cost session=… rounds=4 rounds_before_first_read=1 input_tokens=… (cached=…) …
Run cost session=… scenarios=8 rounds=31 input_tokens=… (cached=…) tool_result_window=0
```

- If `rounds_before_first_read` is already ~0–1, the tree has done retrieval's
  job. **Stop here** and take the elision lever instead — Steps 1–6 would buy
  very little for a Pinecone dependency and an index to keep fresh.
- If it is consistently 3+, exploration is still real: proceed with Steps 1–6.
- Watch `cached` against `input_tokens`. On OpenAI, cached prompt tokens still
  count toward TPM, so a run that is mostly cache hits is cheap in dollars and
  no cheaper against the rate limit that is actually blocking. Only sending
  fewer tokens — fewer rounds, or elision — relieves a 429.
- Retrieval helps regardless on repos over `_TREE_PATH_CAP` (1 500 paths),
  where the tree is truncated and the agent is blind to the remainder.

**The complementary lever, now implemented and off by default.** The dominant
replay cost is not exploration rounds but large file reads — a 10 000-token file
fetched in round 2 is re-sent verbatim in every later round. Retrieval does not
touch that. `VERIFICATION_TOOL_RESULT_WINDOW=2` keeps the two newest tool results
verbatim and replaces older ones with a placeholder naming the call and stating
that the content is still retrievable and its removal is *not* evidence of
absence. A matching rule is appended to the system prompt, but only when the
window is on, so a disabled run sends the byte-identical prompt it always did.

Two things to know before turning it on:

- **It must pass Step 7 checks 3 and 4 first.** A model that can no longer see a
  file it read is one step from reporting the behaviour as missing. The
  placeholder wording and the prompt rule are the guard; the checks are the proof.
- **It is aimed at OpenAI.** On Anthropic the rolling cache breakpoint already
  makes the replay cheap, and rewriting an earlier block invalidates the prefix —
  so on Claude the knob likely costs more than it saves. Measure per provider.

### Steps 1–6 — ✅ BUILT

All of it landed. What exists, and where:

| Piece | Where |
|---|---|
| `chunk_code` — definition-boundary splitter, size cap, path-prefixed text | `backend/app/services/code_chunking.py` |
| `code_namespace` | `backend/app/services/knowledge_service.py` (beside `knowledge_namespace`) |
| `index_repository` — SSE, tarball fetch, batched embed/upsert | `backend/app/services/code_index_service.py` |
| `find_candidate_files` — paths + line ranges, never text | `backend/app/services/code_index_service.py` |
| `format_candidate_block`, prompt rule 0b | `code_index_service.py`, `agentic_verification_service.py` |
| `_code_index_freshness` — SHA comparison, refuse-and-warn | `backend/app/services/agentic_verification_service.py` |
| `code_index_enabled` on request, route, service | `schemas/verification.py`, `api/v1/verification.py` |
| `GET`/`POST /knowledge/index/code` | `backend/app/api/v1/knowledge.py` |
| `knowledge_sources.indexed_ref` + migration `d4f1a7b93e26` | `models/knowledge_source.py`, `alembic/versions/` |
| "Use code index" toggle, code-index card | `GitHubSourceSelector.tsx`, `KnowledgeBasePanel.tsx` |

Departures from the plan as written, and why:

- **`code_namespace` lives in `knowledge_service`, not `code_index_service`.**
  The knowledge-base deletion paths have to reach it. Code vectors are keyed
  per file path while the row recording the index is keyed by repository, so
  nothing targeting a `source_ref` can find them — deleting the row without a
  namespace wipe would strand every code vector with no record left to remove
  it. `delete_knowledge_source` now branches on `source_type == "code"`.
- **Code rows are excluded from `GET /knowledge/sources`.** The index has its
  own card showing the commit it was built at; listing it among ingested
  documents would offer a Delete beside the Re-index that is nearly always the
  action actually wanted.
- **Line ranges are per file, not per chunk.** `_upsert_to_pinecone` carries one
  `title` per source, and a per-chunk range cannot be stored without changing
  its signature. A file-level span does the job — telling the agent roughly
  where in a long file to start.
- **The `const x = ...` boundary requires an arrow function.** The plan's
  `const \w+ = ` would cut a constants or config file into one chunk per line,
  flooding the index with near-identical vectors that crowd out real source.
- **A `warning` SSE event needed frontend handling.** It was being emitted into
  a client that silently dropped unknown types. `useSSEStream` now has
  `onWarning`, surfaced as a toast rather than the global error banner — the
  run is still producing verdicts, and the banner reads as "this failed".

### Step 1 — A code-aware chunker

`chunk_text` in `vector_service.py` is prose-oriented: it splits on
`(?<=[.!?])\s+` (sentence boundaries) then packs 500-word blocks. Source code has
almost no sentence boundaries, so a whole file becomes one "sentence" and is cut
into arbitrary 500-word blobs — mid-function, decorators separated from their
functions, imports orphaned. **Do not reuse it for code.**

New module `backend/app/services/code_chunking.py`:

```python
def chunk_code(text: str, path: str) -> list[dict]:
    """Split source into retrievable units. Returns dicts with:
       {"text", "path", "start_line", "end_line", "symbol"}"""
```

Requirements:

- Split at top-level definition boundaries — regex on `^(async def |def |class |
  export (default )?(async )?function |const \w+ = |func |public |private )` is
  adequate and language-agnostic enough; a full parser is not needed for a
  discovery aid.
- Hard size cap per chunk (~1 500 chars); split oversized bodies at blank lines.
- **Prefix each chunk's embedded text with its path**, e.g.
  `"# backend/app/api/v1/verification.py\n\n<code>"`. Path tokens are a strong
  retrieval signal and this is the cheapest way to get them into the vector.
- Record `start_line` / `end_line` — the agent needs somewhere to jump to, and it
  makes `code_reference` line numbers checkable.

Test with a Python file, a TSX file, and a file with no recognisable boundaries
(the last must still produce sensible size-capped chunks, not one huge one).

### Step 2 — Index a repository

New `backend/app/services/code_index_service.py`:

```python
async def index_repository(
    user_id: str, project_id, repo: str, ref: str, pat: str,
    db: AsyncSession, config: VectorConfig,
) -> AsyncGenerator[str, None]:   # SSE progress, like ingest_confluence
```

- Namespace: **`{user_id}:{project_id}:code`** — a new function
  `code_namespace()` beside `knowledge_namespace()`. Separate namespace, *same*
  index: chunks are embedded with the project's configured vendor, so they share
  its dimension.
- File list comes from `get_repo_tree`. Filter to source extensions
  (`.py .ts .tsx .js .jsx .go .java .rb .cs .php .rs .kt .swift .vue .svelte`)
  and skip files over ~200 KB.
- Fetch the repo as **one tarball** — `GET /repos/{repo}/tarball/{sha}` — and
  extract only the filtered paths. One request instead of thousands, no rate
  limit exposure, and it sidesteps `get_file_contents`'s 40 000-char window
  (which would otherwise silently truncate any file above the cap unless you
  page through offsets). Fall back to per-file `get_file_contents` only if the
  tarball fetch fails.
- Chunk, embed in batches of `_EMBED_BATCH_SIZE` (100) with
  `input_type="document"`, upsert via `_upsert_to_pinecone` with
  `source="code"` and `source_id=<path>`.
- Record a `KnowledgeSource` row with `source_type="code"`, `source_ref=<repo>`,
  and **the indexed commit SHA in `title` or a new column** — Step 5 needs it.

Resolve `ref` to a concrete SHA before indexing (`GET /repos/{repo}/commits/{ref}`),
so "indexed at" means something.

### Step 3 — Retrieve candidate files per scenario

In `code_index_service.py`:

```python
async def find_candidate_files(
    user_id, project_id, scenario_texts: list[str], config, top_k: int = 8,
) -> list[list[dict]]:
```

- One `_embed_chunks(..., input_type="query")` call for **all** scenarios, as
  `query_knowledge_base_batch` already does — not one call per scenario.
- Query the `:code` namespace; reuse `_query_matches_hybrid` for the lexical
  re-rank on identifiers.
- **Return deduplicated file PATHS with line ranges, not chunk text.** Collapse
  multiple chunk hits in one file into a single entry. This is what keeps the
  result a pointer rather than evidence, and it keeps the injected block tiny
  (~200 tokens instead of ~4 000).
- Never raises: return `[[] for _ in scenarios]` on any failure. Discovery is an
  accelerator; a run must work without it exactly as it does today.

### Step 4 — Wire into verification

In `backend/app/services/agentic_verification_service.py`:

- `run_agentic_verification` gains `code_index_enabled: bool = False` (mirroring
  `use_knowledge_base`), plumbed from `AgenticVerificationRequest` and the
  verification route.
- Call `find_candidate_files` once, alongside the existing
  `retrieve_rag_per_scenario` call (~line 754).
- Inject per scenario, in the **per-scenario message** — not the cached shared
  prefix, since it differs per scenario:

```
CANDIDATE FILES (semantic search suggests these are relevant — they are
suggestions, NOT evidence):
  backend/app/api/v1/verification.py  (lines 40-95)
  frontend/src/lib/hooks/useRunVerification.ts  (lines 100-140)
```

- Add system prompt rule **0b**, immediately after the existing tree rule:

> If the task message includes CANDIDATE FILES, open those with
> `get_file_contents` first — they are a search result, not evidence, and you
> must read the actual code before forming any verdict. Absence of a file from
> that list is not evidence it does not exist; fall back to the tree and
> `list_directory` when the candidates do not contain the behaviour.

That last sentence is load-bearing. Without it the model will treat an empty or
irrelevant candidate list as proof of absence.

### Step 5 — Freshness

A stale index silently sends the agent to the wrong files.

- Before a run, compare the current head SHA of the verification ref with the
  indexed SHA. On mismatch, either skip retrieval (fall back to tree-only
  discovery) or emit a warning event — **do not silently use the stale index**.
- Add a "Re-index code" action to the project settings Knowledge base card, and
  surface "indexed at `<sha>` · `<date>`".
- Re-indexing reuses `_upsert_to_pinecone`'s existing delete-then-upsert, so a
  file that shrank does not leave stale tail chunks.

### Step 6 — Frontend

- Verification form: a "Use code index" toggle beside the existing knowledge-base
  toggle, disabled with an explanatory tooltip when the project has no index.
- Knowledge page (`/knowledge`) → Code index card: index status, indexed
  SHA/date, and a re-index button that streams the SSE progress the way
  Confluence ingestion does. (The plan said "Project settings"; the knowledge
  base actually lives on its own page, which is where the card went.)

---

## Expected effect

- **Tokens per scenario:** removes whatever exploration rounds the repo tree has
  not already removed, and each removed round also removes a full replay of
  everything before it. The original **40–60%** estimate predates the tree being
  in the shared prefix — treat Step 0's measurement as the real number, and
  expect the eviction lever (Step 0) to matter at least as much as retrieval.
- **Accuracy:** this plan mostly *protects* accuracy rather than raising it. The
  genuine gains are indirect: fewer scenarios killed mid-run by rate limits
  (every dead scenario is a lost verdict), no path-guessing, and candidate line
  ranges making `code_reference` checkable. If accuracy is a primary goal, the
  levers live elsewhere: model choice per scenario difficulty, and the Step 7
  known-good BDD suite run *routinely* (not once) as a regression harness.
- **Cost:** embedding a repo is a one-off per commit and is cheap (Voyage: 200M
  tokens free, then $0.06/1M).

**This is an optimization, not the fix for the current rate limiting.** The
immediate unblocks remain: set the project's model to Claude (the Anthropic key
is configured and idle), or add billing to the OpenAI account to leave tier 1.

## Step 7 — Verification

1. `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -q` — **1 036 tests
   pass** (984 originally, plus 19 in `tests/test_verification_measurement.py`
   and 33 in `tests/test_code_index.py`); that number must not go down.
2. ✅ Unit tests written: `chunk_code` on Python/TSX/unstructured input;
   `find_candidate_files` returning paths not text; the empty-index and
   failed-retrieval fallbacks; every freshness branch; the deletion paths.
3. **The accuracy check that matters — STILL OUTSTANDING.** Everything above is
   automated and passing; checks 3, 4 and 5 need a real repository, a real
   index and real LLM calls, so they are yours to run before this is trusted in
   anger. Run the migration first (`alembic upgrade head`), index a repository
   from the Knowledge page (`/knowledge`), under Code index, then:
   Take a BDD file with known-good verdicts.
   Run it with the code index off, then on. Verdicts must not change. If a `pass`
   becomes a `fail`, or anything becomes `inconclusive`, the candidate list is
   being treated as evidence — revisit the rule 0b wording before going further.
4. Confirm a scenario whose feature is genuinely **missing** still returns `fail`
   (not a `fail` invented from an unhelpful candidate list, and not
   `inconclusive`). This is the case the whole design risks breaking.
5. Token comparison: read the `Run cost session=…` log line with the index off
   vs on, same BDD, same repo. Confirm the reduction is real rather than
   assumed. Step 0 built this line; it needs no further work.
6. `./.venv/Scripts/python.exe -m ruff check app/` — **79 pre-existing errors**;
   do not add to that count.
7. Frontend: `cd frontend && npx vitest run` (**374 passing**, up from 364) and
   `npx tsc --noEmit -p tsconfig.json`. Note that a full-suite run under load
   can produce spurious worker-timeout failures in `sessions/page.test.tsx` and
   others; re-run the named file alone before believing one.
8. Checks 3 and 4 apply unchanged to `VERIFICATION_TOOL_RESULT_WINDOW`, and must
   be run against it separately before it is turned on in any environment. It is
   a second, independent way for the agent to lose sight of evidence, so passing
   them for the code index says nothing about passing them for elision.

## Risks

- **The candidate list gets treated as evidence.** The single biggest risk, and
  the reason for the rule-0b wording and check 3/4 above.
- **Stale index sends the agent to the wrong files.** Mitigated by Step 5;
  prefer skipping retrieval over using a stale index.
- **GitHub rate limits during indexing.** A large repo is thousands of file
  fetches. Batch, cache, and consider the tree API's blob endpoint for bulk reads.
- **Chunker quality.** A regex splitter is adequate for discovery but will
  mis-split unusual files. It only has to be good enough to point at the right
  file — which is why returning paths rather than chunk text limits the damage.
- **Redundancy with the repo tree.** `get_repo_tree` already gives the agent the
  full path list for ~1 300 tokens with zero staleness. Retrieval must beat that
  baseline to be worth its infrastructure — that is exactly what Step 0 measures,
  *before* any of Steps 1–6 are built.
