# Story 4.3: RAG-Enriched Verification Integration

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): Retrieval is now hybrid-lite (vector over-fetch + exact-identifier re-rank + metadata-filtered pull for named ticket keys), prompts ground in the full chunk text rather than the 300-char display snippet, and the random-embedding fallback was removed — without OpenAI embedding credentials, retrieval degrades to no-context instead of serving garbage vectors.

## Story

As an **authenticated user**,
I want the verification process to automatically retrieve relevant project context from my knowledge base,
so that the LLM's code analysis is grounded in real project knowledge and produces more accurate verdicts.

## Acceptance Criteria

1. **Given** an authenticated user has ingested project knowledge (Confluence docs, Jira stories) into Pinecone namespace `{user_id}:knowledge`
   **When** a POST to `/api/v1/verification/run` is made
   **Then** `verification_service.py` calls `knowledge_service.query_knowledge_base(user_id, bdd_content, top_k=5)` **before** the scenario loop to retrieve relevant chunks, and those chunks are injected into each scenario's LLM prompt *(FR35, FR41)*

2. **And** the retrieved knowledge chunks are appended to the LLM user prompt under a `PROJECT CONTEXT:` section, formatted so the LLM can reference them when justifying verdicts *(FR35)*

3. **And** each SSE `verdict` event includes a `rag_context` list of `{source, source_id, snippet}` objects representing retrieved chunks, and the `VerificationResult` DB row persists these as a JSONB `rag_context` column *(FR36)*

4. **And** RAG retrieval completes within 10 seconds during verification *(NFR-P6)*

5. **And** if no knowledge base exists for the user (Pinecone key absent, namespace empty, or any retrieval error), verification proceeds identically to the pre-RAG behavior — `rag_context` is `null` / `[]` and no error is surfaced to the user *(NFR-R3)*

## Tasks / Subtasks

- [x] Task 1: Add `query_knowledge_base()` to `knowledge_service.py` (AC: 1, 4, 5)
  - [x] 1.1 — Add `RagContextItem` TypedDict/dataclass to `knowledge_service.py` (or define inline as `list[dict]`)
  - [x] 1.2 — Implement `async def query_knowledge_base(user_id, query_text, top_k=5) -> list[dict]` that embeds `query_text`, calls `index.query()` in the `{user_id}:knowledge` namespace, and returns `[{source, source_id, snippet}]`
  - [x] 1.3 — Wrap entire function in try/except; return `[]` on any error (Pinecone unavailable, empty namespace, embed failure) — no exception propagates

- [x] Task 2: Add `RagContextItem` Pydantic model and update `VerificationVerdict` in `schemas/verification.py` (AC: 3)
  - [x] 2.1 — Add `class RagContextItem(BaseModel)` with fields `source: str`, `source_id: str`, `snippet: str`
  - [x] 2.2 — Add `rag_context: list[RagContextItem] | None = None` field to `VerificationVerdict`
  - [x] 2.3 — Update `_SYSTEM_PROMPT` in `verification_service.py` to document the `rag_context` field in the JSON schema comment (it is **not** LLM-generated — it is injected post-response)

- [x] Task 3: Update `_build_verification_prompt()` in `verification_service.py` (AC: 2)
  - [x] 3.1 — Add `rag_chunks: list[dict] | None = None` parameter
  - [x] 3.2 — When `rag_chunks` is non-empty, append a `PROJECT CONTEXT:` block **after** the CODE block in the user prompt

- [x] Task 4: Integrate RAG into `run_verification()` in `verification_service.py` (AC: 1, 3, 5)
  - [x] 4.1 — Before the scenario loop, call `await knowledge_service.query_knowledge_base(user_id, bdd_content)` — assign to `rag_chunks`
  - [x] 4.2 — Pass `rag_chunks` to `_build_verification_prompt()` for each scenario
  - [x] 4.3 — After LLM responds, attach `rag_context` to the verdict SSE event and the `VerificationResult` DB row
  - [x] 4.4 — Add `rag_context` key to the `verdict_event` dict yielded in the SSE stream

- [x] Task 5: Add `rag_context` column to `VerificationResult` model + Alembic migration (AC: 3)
  - [x] 5.1 — Add `rag_context: Mapped[list | None] = mapped_column(JSONB, nullable=True)` to `VerificationResult`
  - [x] 5.2 — Create `backend/alembic/versions/f5a6b7c8d9e0_add_rag_context_to_verification_results.py`
  - [x] 5.3 — Migration uses `op.add_column("verification_results", sa.Column("rag_context", postgresql.JSONB, nullable=True))`

- [x] Task 6: Update frontend types in `verification.ts` (AC: 3)
  - [x] 6.1 — Add `interface RagContextItem { source: string; source_id: string; snippet: string; }`
  - [x] 6.2 — Add `rag_context?: RagContextItem[]` field to `VerificationVerdict` interface

- [x] Task 7: Display RAG context in frontend verification results (AC: 3)
  - [x] 7.1 — Locate the verification results display component (likely in `frontend/src/components/pipeline/`)
  - [x] 7.2 — Add a collapsible "Knowledge Base Context" section that renders `rag_context` items: source badge, source_id, snippet
  - [x] 7.3 — If `rag_context` is empty/null, render nothing (no empty state needed)

- [x] Task 8: Write tests in `backend/tests/test_rag_verification.py` (AC: 1–5)
  - [x] 8.1 — `test_query_knowledge_base_returns_chunks` — mock Pinecone query result, assert structured return
  - [x] 8.2 — `test_query_knowledge_base_returns_empty_when_pinecone_unavailable` — assert returns `[]` with no exception when `pinecone_api_key=""` 
  - [x] 8.3 — `test_build_prompt_includes_rag_context` — assert `PROJECT CONTEXT:` block appears in prompt when chunks provided
  - [x] 8.4 — `test_run_verification_includes_rag_context_in_verdict_event` — mock `query_knowledge_base` + LLM, assert SSE `verdict` event contains `rag_context`
  - [x] 8.5 — `test_run_verification_proceeds_without_rag_when_empty` — mock `query_knowledge_base` returning `[]`, assert verdicts still emitted normally

## Dev Notes

### Overview

This story integrates the knowledge base (built in Stories 4.1–4.2) into the existing verification pipeline. The primary change is in `verification_service.py` — add one `await` call before the scenario loop, pass results to the prompt builder, and propagate them through to SSE events and DB persistence.

The key design decision: **query once per verification run** (not per-scenario). This is efficient (one embed call + one Pinecone query), and the full BDD content is used as the query text — it represents the user's intent for the entire session better than individual scenario texts.

### Task 1 — `query_knowledge_base()` Implementation Pattern

Add to `backend/app/services/knowledge_service.py`:

```python
async def query_knowledge_base(
    user_id: str,
    query_text: str,
    top_k: int = 5,
) -> list[dict]:
    """Query the user's Pinecone knowledge namespace for relevant chunks.

    Returns a list of dicts with keys: source, source_id, snippet.
    Returns [] on any failure (graceful degradation per NFR-R3).
    """
    if not settings.pinecone_api_key or not query_text.strip():
        return []

    namespace = f"{user_id}:knowledge"

    try:
        embeddings = await _embed_chunks([query_text])
        query_vector = embeddings[0]

        pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
        index = pc.Index(settings.pinecone_index_name)

        results = await asyncio.to_thread(
            index.query,
            vector=query_vector,
            top_k=top_k,
            namespace=namespace,
            include_metadata=True,
        )

        chunks = []
        for match in results.matches:
            meta = match.metadata or {}
            chunks.append({
                "source": meta.get("source", ""),
                "source_id": meta.get("source_id", ""),
                "snippet": meta.get("text", "")[:300],
            })
        return chunks

    except Exception:
        logger.warning(
            "RAG retrieval failed for user=%s — proceeding without context",
            user_id,
        )
        return []
```

**Critical notes:**
- Uses `_embed_chunks()` (already in `knowledge_service.py`) — **do not duplicate** embed logic
- Uses the same Pinecone client pattern as `_upsert_to_pinecone()` (lines 77-78)
- `namespace = f"{user_id}:knowledge"` — identical to upsert pattern — enforces isolation
- `asyncio.to_thread()` for Pinecone blocking call — identical to upsert pattern
- `snippet` is first 300 chars of `metadata["text"]` — keeps prompt token count manageable
- Full function wrapped in try/except — returns `[]` on any error, never raises

### Task 2 — Schema Changes

In `backend/app/schemas/verification.py`:

```python
class RagContextItem(BaseModel):
    """A single retrieved knowledge chunk attached to a verification verdict."""

    source: str = Field(..., description="Knowledge source type: 'confluence' or 'jira'")
    source_id: str = Field(..., description="Confluence page ID or Jira ticket key")
    snippet: str = Field(..., description="First 300 characters of the retrieved chunk")


class VerificationVerdict(BaseModel):
    # ... existing fields unchanged ...
    rag_context: list[RagContextItem] | None = Field(
        default=None,
        description="Knowledge chunks retrieved and used to enrich this verdict",
    )
```

**Important:** `rag_context` is **not** generated by the LLM. It is assembled from the Pinecone query results and attached to the verdict **after** the LLM responds. Do NOT add it to `_SYSTEM_PROMPT`.

The LLM verdict JSON schema in `_SYSTEM_PROMPT` must NOT include `rag_context` — it stays as-is. The `VerificationVerdict(**verdict_dict)` call will succeed because `rag_context` has `default=None`.

### Task 3 — Prompt Injection Format

Update `_build_verification_prompt()` signature:

```python
def _build_verification_prompt(
    scenario: dict,
    fetched_files: list[FetchedFile],
    rag_chunks: list[dict] | None = None,
) -> tuple[str, str]:
```

Append to the `lines` list **after** the CODE block, **before** `"\n".join(lines)`:

```python
    if rag_chunks:
        lines.append("")
        lines.append("PROJECT CONTEXT:")
        lines.append("The following knowledge base excerpts are relevant to this scenario:")
        for chunk in rag_chunks:
            lines.append(f"[{chunk['source'].upper()}:{chunk['source_id']}]")
            lines.append(chunk["snippet"])
            lines.append("")
```

This keeps the RAG context clearly delimited and separate from the CODE block.

### Task 4 — `run_verification()` Changes

Add one call **before** the scenario loop:

```python
async def run_verification(
    session_id: str,
    user_id: str,
    bdd_content: str,
    fetched_files: list[FetchedFile],
    llm: LLMProvider,
    db: AsyncSession,
) -> AsyncGenerator[str, None]:
    # ... existing filtered_files + scenarios parsing unchanged ...

    # RAG enrichment — query once, use for all scenarios
    from app.services import knowledge_service as _ks
    rag_chunks = await _ks.query_knowledge_base(user_id, bdd_content)

    # Convert to list[dict] for serialization — already in dict form from query_knowledge_base
    rag_context_payload = [
        RagContextItem(source=c["source"], source_id=c["source_id"], snippet=c["snippet"])
        for c in rag_chunks
    ] if rag_chunks else None

    for scenario in scenarios:
        system_prompt, user_prompt = _build_verification_prompt(
            scenario, filtered_files, rag_chunks=rag_chunks
        )
        # ... LLM call unchanged ...

        # Build verdict SSE event (add rag_context)
        verdict_event: dict = {
            "type": "verdict",
            # ... existing fields unchanged ...
            "rag_context": [c.model_dump() for c in rag_context_payload] if rag_context_payload else None,
        }
        yield _sse_event(verdict_event)

        # DB persistence — add rag_context
        result = VerificationResult(
            # ... existing fields unchanged ...
            rag_context=[c.model_dump() for c in rag_context_payload] if rag_context_payload else None,
        )
```

**Import note:** Use a local import of `knowledge_service` inside `verification_service.py` (as shown) to avoid circular imports. Both modules import from `app.core.config`, but neither imports the other at module level. The local import inside the function avoids any startup circularity risk.

Alternatively, add `from app.services import knowledge_service as _ks` at the top of `verification_service.py` — check if it causes circular imports first by tracing the import chain. If no circularity, prefer top-level import.

### Task 5 — Alembic Migration

File: `backend/alembic/versions/f5a6b7c8d9e0_add_rag_context_to_verification_results.py`

```python
"""add_rag_context_to_verification_results

Revision ID: f5a6b7c8d9e0
Revises: e3f4a5b6c7d8
Create Date: 2026-05-02 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, Sequence[str], None] = "e3f4a5b6c7d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "verification_results",
        sa.Column(
            "rag_context",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("verification_results", "rag_context")
```

**Chain:** `e3f4a5b6c7d8` (add_knowledge_sources_table) → `f5a6b7c8d9e0` (this).

### Task 6 — Frontend Types

In `frontend/src/lib/types/verification.ts`, locate the `VerificationVerdict` interface and add:

```typescript
export interface RagContextItem {
    source: string;       // "confluence" | "jira"
    source_id: string;    // page ID or ticket key
    snippet: string;      // first 300 chars of chunk
}

// Add to VerificationVerdict:
rag_context?: RagContextItem[];
```

### Task 7 — Frontend RAG Context Display

The architecture references a `RAGContextPanel` component. Find the existing verification results display — it's likely at `frontend/src/components/pipeline/VerificationResults.tsx` or similar. The panel should:

- Appear below each verdict's verdict card (or as a collapsible section at the bottom of all results)
- Render each item as: `[CONFLUENCE]` or `[JIRA]` badge + source_id + snippet text
- Use `hidden` / `sr-only` styling when `rag_context` is null or empty
- No loading state — RAG context arrives with the verdict SSE event

If a `RAGContextPanel.tsx` does not yet exist, create it at `frontend/src/components/pipeline/RAGContextPanel.tsx` as a pure display component that accepts `items: RagContextItem[]`.

### Project Structure Notes

**Files to create:**
- `backend/alembic/versions/f5a6b7c8d9e0_add_rag_context_to_verification_results.py`
- `backend/tests/test_rag_verification.py`
- `frontend/src/components/pipeline/RAGContextPanel.tsx` (if not present)

**Files to modify:**
- `backend/app/services/knowledge_service.py` — add `query_knowledge_base()`
- `backend/app/schemas/verification.py` — add `RagContextItem`, update `VerificationVerdict`
- `backend/app/services/verification_service.py` — integrate RAG into `run_verification()` + `_build_verification_prompt()`
- `backend/app/models/verification_result.py` — add `rag_context` column
- `frontend/src/lib/types/verification.ts` — add `RagContextItem`, update interface
- `frontend/src/components/pipeline/` — update verification results display

**Do NOT modify:**
- `backend/app/api/v1/verification.py` — routes need no changes; RAG is internal to the service
- `backend/app/services/vector_service.py` — reuse `_embed_chunks()` from `knowledge_service.py`
- Any auth/session code

### Existing Code Patterns to Reuse

From `knowledge_service.py` (lines 67-93) — Pinecone client pattern to mirror for queries:
```python
pc = pinecone.Pinecone(api_key=settings.pinecone_api_key)
index = pc.Index(settings.pinecone_index_name)
await asyncio.to_thread(index.upsert, vectors=vectors, namespace=namespace)
# → for query:
await asyncio.to_thread(index.query, vector=..., top_k=5, namespace=namespace, include_metadata=True)
```

From `verification_service.py` (line 250) — SSE verdict event pattern:
```python
verdict_event: dict = {
    "type": "verdict",
    "scenario_id": verdict.scenario_id,
    ...
}
yield _sse_event(verdict_event)
```

From `verification_service.py` (lines 258-270) — DB persistence pattern (already uses `db.flush()` not `db.commit()` — do the same for `rag_context`).

### Testing Approach

Use `pytest-asyncio` with `AsyncMock` — same pattern as `test_knowledge.py` and `test_jira_knowledge.py`.

Key mocking targets:
- `app.services.knowledge_service._embed_chunks` — mock to return `[[0.1] * 1536]`
- `app.services.knowledge_service.pinecone.Pinecone` — mock the client and `index.query`
- `app.services.verification_service.knowledge_service.query_knowledge_base` — AsyncMock returning fixture chunks

**Test fixture example:**
```python
RAG_CHUNKS = [
    {"source": "confluence", "source_id": "12345", "snippet": "Architecture decision: use FastAPI..."},
    {"source": "jira", "source_id": "PROJ-1", "snippet": "Acceptance criteria: user login flow..."},
]
```

### References

- Pinecone index.query() API: `index.query(vector=..., top_k=N, namespace=..., include_metadata=True)` returns object with `.matches` list; each match has `.metadata` dict and `.score` float
- [Source: backend/app/services/knowledge_service.py:67-93] — Pinecone upsert pattern (mirror for query)
- [Source: backend/app/services/knowledge_service.py:40-64] — `_embed_chunks()` — reuse directly
- [Source: backend/app/services/verification_service.py:150-175] — `_build_verification_prompt()` — extend, do not rewrite
- [Source: backend/app/services/verification_service.py:178-290] — `run_verification()` — integrate before scenario loop
- [Source: backend/app/schemas/verification.py:67-93] — `VerificationVerdict` — extend with `rag_context`
- [Source: backend/app/models/verification_result.py] — `VerificationResult` — add JSONB column
- [Source: backend/alembic/versions/a9d4e72f1c83_create_verification_results_table.py] — migration pattern
- [Source: backend/alembic/versions/e3f4a5b6c7d8_add_knowledge_sources_table.py] — `down_revision` for new migration
- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 4, Story 4.3: FR35, FR36, FR41
- [Source: _bmad-output/planning-artifacts/prd.md] — NFR-P6 (10s timeout), NFR-S8 (namespace isolation), NFR-R3 (no state destruction)

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

None — implementation proceeded cleanly. Three test fixes applied during development:
1. `test_verdict_event_includes_rag_context` — replaced `AsyncMock()` with a proper async def mock that returns a valid verdict dict (AsyncMock default return was not a dict).
2. Pre-existing ruff E501 violations in `verification_service.py` — cleaned up `_resolve_github_links`, `_score_file_relevance`, `_select_relevant_files`.
3. F841 unused variable `original_generate` removed from test during ruff pass.

### Completion Notes List

- `query_knowledge_base()` uses "query once per run" strategy (full BDD content as query text) for efficiency — one embed + one Pinecone call instead of N calls per scenario.
- `rag_context_payload` is `None` (not `[]`) when no chunks returned, matching AC5 graceful degradation.
- `knowledge_service` imported as `_ks` alias at module level in `verification_service.py` — no circular import risk since neither module imports the other transitively.
- `query_knowledge_base()` uses "query once per run" strategy for efficiency — one embed + one Pinecone call serves all scenarios. Enforces 10s timeout via `asyncio.wait_for` (NFR-P6).
- `rag_context_payload` is `None` (not `[]`) when no chunks returned, matching AC5 graceful degradation.
- `knowledge_service` imported as `_ks` alias at module level in `verification_service.py` — no circular import risk.
- `rag_context` intentionally excluded from `_SYSTEM_PROMPT` (not LLM-generated); comment added to code to prevent confusion.
- 14 tests added (13 original + 1 multi-scenario propagation test). All 191 tests pass; ruff clean.
- Frontend `RAGContextPanel` renders with violet styling; returns null for empty/null `rag_context`.

### Code Review Fixes Applied (2026-05-02)

**H1 — NFR-P6 timeout:** Wrapped `_retrieve()` inner coroutine in `asyncio.wait_for(..., timeout=10.0)` with separate `TimeoutError` handler in `knowledge_service.query_knowledge_base`.

**M1 — Task 2.3 documentation:** Added 3-line comment above `_SYSTEM_PROMPT` in `verification_service.py` explaining `rag_context` is excluded because it is injected post-response, not LLM-generated.

**M2 — Missing assertion:** Added `assert result == []` to `test_namespace_scoped_to_user` after the `query_knowledge_base` call.

**M3 — Multi-scenario test:** Added `test_rag_context_shared_across_multiple_scenarios` — verifies `query_knowledge_base` called exactly once with 2-scenario BDD, and both verdict events carry identical `rag_context`.

**M4 — Architecture doc drift:** Updated `architecture.md` line 79: `user_id:workspace` → `user_id:knowledge` to match actual implementation.

### File List

- `backend/app/services/knowledge_service.py` — added `query_knowledge_base()` with 10s timeout (NFR-P6)
- `backend/app/schemas/verification.py` — added `RagContextItem` model; added `rag_context` field to `VerificationVerdict`
- `backend/app/services/verification_service.py` — integrated RAG into `_build_verification_prompt()` and `run_verification()`; added `rag_context` exclusion comment above `_SYSTEM_PROMPT`
- `backend/app/models/verification_result.py` — added `rag_context: Mapped[list | None]` JSONB column
- `backend/alembic/versions/f5a6b7c8d9e0_add_rag_context_to_verification_results.py` — created migration for `rag_context` column
- `backend/tests/test_rag_verification.py` — created 14-test suite for Story 4.3
- `frontend/src/lib/types/verification.ts` — added `RagContextItem` interface; added `rag_context` to `VerificationVerdict`
- `frontend/src/components/pipeline/RAGContextPanel.tsx` — created new component for knowledge base context display
- `frontend/src/components/pipeline/VerificationResultRow.tsx` — integrated `RAGContextPanel` into expanded verdict body
