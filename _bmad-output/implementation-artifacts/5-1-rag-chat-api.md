# Story 5.1: RAG Chat API

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-runtime-model-selection.md)): `POST /chat/message` accepts `llm_provider` / `llm_model`, so the answering model is the one selected in the UI rather than `LLM_PROVIDER`.


Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want a backend API endpoint that answers my natural-language questions about an ingested Jira ticket,
so that I can quickly understand the ticket's edge cases and requirements without reading it top to bottom.

## Acceptance Criteria

1. **Given** a POST to `/api/v1/chat/message` with `session_id` and `question`
   **When** the chat service embeds the question and queries Pinecone
   **Then** it retrieves the top-K most relevant chunks from the ticket's namespace `{user_id}:{session_id}` (FR10, NFR-S4)

2. **And** the retrieved chunks are sent to the LLM via the `LLMProvider` interface with a strict grounding prompt that forbids answers outside the retrieved context (FR10)

3. **And** the LLM response streams as SSE — `data: {"type": "token", "content": "..."}` events followed by a terminal `data: {"type": "complete"}` (NFR-P5); errors emit `data: {"type": "error", "message": "..."}`

4. **And** the chat message pair (user question + assistant answer) is persisted to the `chat_messages` table linked to `session_id` and `user_id` (FR11)

5. **And** the response is grounded exclusively in the ingested ticket — a question with no relevant retrieved context yields a graceful "I don't have that information in this ticket" answer, and **no cross-session or cross-user namespace is ever queried** (FR10, NFR-S4)

6. **And** a Pytest test verifies the endpoint never retrieves or returns content from outside its `{user_id}:{session_id}` namespace, and that both messages are persisted with the correct `user_id`

## Context & Critical Background

> 🔴 **PREREQUISITE BUG — the ticket namespace is wrong today.** Story 5.1 cannot work until this is fixed.

Epic 1 vectorized Jira tickets under the **DEV_USER_ID stub**, and that was never updated after Epic 3 added real auth:

- [`vector_service.embed_and_index_ticket`](backend/app/services/vector_service.py#L80) hardcodes `namespace = f"{settings.dev_user_id}:{session_id}"` and **takes no `user_id`**.
- [`ingestion.py`](backend/app/api/v1/ingestion.py#L33) has `current_user` but calls `embed_and_index_ticket(session_id=...)` **without passing it**.

So tickets live under `dev-stub:{session_id}`, but this story's AC queries `{user_id}:{session_id}`. **Task 1 fixes this** — otherwise chat retrieves nothing and per-user isolation (NFR-S4) is broken. This is not RAG-knowledge-base (`{user_id}:knowledge`, Epic 4) — it's the **per-session ticket** namespace.

> 🟡 **The `LLMProvider` interface has no streaming method.** [`provider.py`](backend/app/services/llm/provider.py) exposes `generate`, `generate_structured`, `generate_with_tools` — none stream. AC3 needs token streaming, so Task 2 adds `generate_stream()`.

**Already exists (reuse):**

| Piece | Location |
|---|---|
| `chat_messages` table + model (id, session_id, user_id, role, content, created_at) | `backend/app/models/chat_message.py` + migration `d2e3f4a5b6c7` — **no new migration** |
| Question/chunk embedding pattern (OpenAI text-embedding-3-small, 1536, dev fallback) | `knowledge_service._embed_chunks` (reuse) |
| Pinecone query pattern (top_k, include_metadata, `metadata["text"]`) | `knowledge_service.query_knowledge_base` |
| SSE serialization | `verification_service._sse_event` / `knowledge_service._sse` |
| LLM provider access | `from app.services.llm.factory import get_llm_provider` (routes call `get_llm_provider()`) |
| Session ownership check pattern | `sessions.py` (load Session, verify `user_id == current_user` → 403) |

## Tasks / Subtasks

### Backend — fix the ticket namespace (PREREQUISITE, AC1/AC5)

- [x] **Task 1: Vectorize tickets under the real user's namespace** (AC: 1, 5)
  - [x] Add `user_id: str` parameter to `vector_service.embed_and_index_ticket(...)`
  - [x] Change `namespace = f"{settings.dev_user_id}:{session_id}"` → `namespace = f"{user_id}:{session_id}"`
  - [x] In `ingestion.py`, pass `user_id=current_user` to the `embed_and_index_ticket(...)` call
  - [x] Update any existing `test_ingestion`/`test_vector_service` tests that assert the old namespace

### Backend — streaming on the provider interface (AC3)

- [x] **Task 2: Add `generate_stream()` to `LLMProvider`** (AC: 3)
  - [x] In `provider.py`, add `async def generate_stream(self, prompt, system_prompt="") -> AsyncGenerator[str, None]` with a **concrete default** on the ABC that awaits `self.generate(...)` and yields the whole string once (so Claude/Ollama work without native streaming)
  - [x] Override in `OpenAIProvider` with true streaming: `await self._client.chat.completions.create(..., stream=True)`, `async for chunk in stream:` yield `chunk.choices[0].delta.content or ""`; wrap errors in `LLMProviderError` (mirror the existing `generate` error handling)

### Backend — chat service + schemas (AC1–5)

- [x] **Task 3: `schemas/chat.py`** (AC: 1, 4)
  - [x] `class ChatMessageRequest(BaseModel)`: `session_id: str`, `question: str` (non-empty)
  - [x] `class ChatMessageResponse(BaseModel)` (for history GET, Task 6): `id`, `session_id`, `role`, `content`, `created_at` (`from_attributes=True`)

- [x] **Task 4: `chat_service.py`** (AC: 1, 2, 3, 4, 5)
  - [x] `async def stream_chat_answer(session_id, user_id, question, llm, db) -> AsyncGenerator[str, None]`
  - [x] Persist the **user** message first (`ChatMessage(role="user", ...)`), `flush`
  - [x] Embed the question via `knowledge_service._embed_chunks([question])`; query Pinecone in namespace `f"{user_id}:{session_id}"` (top_k=5, include_metadata) — reuse the `pinecone.Pinecone(...)` + `asyncio.to_thread(index.query, ...)` pattern; extract `metadata["text"]`
  - [x] Build a **strict grounding** system prompt: answer ONLY from the provided CONTEXT; if the context doesn't contain the answer, say so — never use outside knowledge (AC2, AC5)
  - [x] If no chunks retrieved → still call the LLM with empty context so it returns the graceful "not in this ticket" answer (do not fabricate)
  - [x] Stream via `llm.generate_stream(...)`; for each token yield `_sse({"type": "token", "content": tok})`; accumulate the full answer
  - [x] After streaming, persist the **assistant** message (`role="assistant"`, full answer), `commit`; yield `_sse({"type": "complete"})`
  - [x] Wrap LLM/Pinecone failures → yield `_sse({"type": "error", "message": ...})` (do not leak internals)

### Backend — endpoint + routing (AC1, AC3)

- [x] **Task 5: `POST /api/v1/chat/message`** (AC: 1, 3, 5)
  - [x] New `api/v1/chat.py`: verify the session exists and `session.user_id == current_user` → 403/404 (mirror `sessions.py`) **before** streaming
  - [x] `llm = get_llm_provider()`; return `StreamingResponse(chat_service.stream_chat_answer(...), media_type="text/event-stream")`
  - [x] Register the router in `api/v1/api.py`: `api_router.include_router(chat.router, prefix="/chat", tags=["chat"])`

- [x] **Task 6: `GET /api/v1/chat/{session_id}/messages`** (AC: 4 — unblocks Story 5.2)
  - [x] Ownership check → return the session's `ChatMessage` rows ordered `created_at ASC` as `list[ChatMessageResponse]`; `[]` when none

### Tests (AC6)

- [x] **Task 7: `backend/tests/test_chat.py`**
  - [x] `stream_chat_answer` queries Pinecone with namespace `f"{user_id}:{session_id}"` **exactly** (assert on `index.query` `namespace` kwarg) — never another user/session (NFR-S4)
  - [x] Persists exactly two `ChatMessage` rows: `role="user"` then `role="assistant"`, both with the correct `user_id`/`session_id`
  - [x] SSE stream contains `token` events then a terminal `complete` (mock `generate_stream` to yield `["Hello", " world"]`)
  - [x] Empty retrieval → still emits a grounded answer + `complete` (no crash)
  - [x] Endpoint returns 403 for a non-owner session, 404 for a missing session
  - [x] Mock `_embed_chunks`, `pinecone.Pinecone`, and `generate_stream` — same style as `test_rag_verification.py`

## Dev Notes

### Namespace is the crux (AC1/AC5)

Two distinct Pinecone namespaces exist in this app — do not confuse them:
- **`{user_id}:{session_id}`** — the per-session **Jira ticket** chunks (Epic 1 ingestion). ← **this story queries this**
- `{user_id}:knowledge` — the cross-session **knowledge base** (Epic 4 Confluence/Jira/docs).

Task 1 makes the ticket namespace actually use the real `user_id`. After the fix, a freshly-ingested ticket is queryable by chat. (Tickets ingested before the fix live under `dev-stub:...` and won't be found — acceptable for greenfield; note it if surfaced.)

### Streaming design (AC3)

Adding `generate_stream()` with a default ABC implementation that wraps `generate()` keeps all three providers working: Ollama/Claude yield the whole answer as one "token" (still a valid SSE stream), while **OpenAI** (the configured provider) streams true deltas. This respects the architecture rule (never call the SDK outside a provider) and avoids a big-bang change to Claude/Ollama. Reference `OpenAIProvider.generate` for the client + error-handling shape; add `stream=True` and iterate `delta.content`.

### Grounding prompt (AC2/AC5)

The system prompt must instruct: *"Answer using ONLY the CONTEXT below. If the answer isn't in the context, reply that the ticket doesn't contain that information. Never use outside knowledge."* Then append a `CONTEXT:` block of the retrieved chunk texts, and the user's question. This is the isolation guarantee at the prompt level; the namespace scoping is the guarantee at the retrieval level. AC5's "graceful no-answer" falls out of this prompt when context is empty.

### Persistence ordering (AC4)

Persist the **user** message before streaming (so it survives even if the LLM fails mid-stream), and the **assistant** message after the stream completes. Use `db.flush()` for the user row and `db.commit()` after the assistant row — consistent with `verification_service`'s streaming-safe persistence. Two rows per exchange.

### Project Structure Notes

**Backend — modify:** `vector_service.py` (user_id namespace), `ingestion.py` (pass current_user), `llm/provider.py` (+`generate_stream` default), `llm/openai_provider.py` (+streaming override), `api/v1/api.py` (register chat router)
**Backend — create:** `schemas/chat.py`, `services/chat_service.py`, `api/v1/chat.py`, `tests/test_chat.py`
**Do NOT modify:** the `chat_messages` model/migration (already exist); `knowledge_service._embed_chunks` internals (reuse); the Epic 4 `{user_id}:knowledge` paths

### Testing Standards

- `pytest`/`pytest-asyncio`, `AsyncMock`. Mock `pinecone.Pinecone`/`index.query`, `_embed_chunks`, and `generate_stream`. DB via `MagicMock` (add/flush/commit) — set explicit attrs for any `model_validate` (the 4.4 MagicMock caveat). Assert on the `index.query` `namespace` kwarg for the isolation test. Full-suite regression + ruff-clean on new code.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 5, Story 5.1 (FR10, FR11; NFR-P2, NFR-P5, NFR-S4)
- [Source: backend/app/services/vector_service.py:60-116] — `embed_and_index_ticket` (fix namespace here)
- [Source: backend/app/api/v1/ingestion.py:33-39] — pass `current_user` here
- [Source: backend/app/models/chat_message.py] — persistence target (exists)
- [Source: backend/app/services/llm/provider.py] — add `generate_stream`
- [Source: backend/app/services/llm/openai_provider.py:24-46] — `generate` shape to mirror for streaming
- [Source: backend/app/services/llm/factory.py:22] — `get_llm_provider()`
- [Source: backend/app/services/knowledge_service.py] — `_embed_chunks` (reuse), Pinecone query pattern
- [Source: backend/app/services/verification_service.py] — `_sse_event` + streaming-safe DB persistence pattern
- [Source: backend/app/api/v1/sessions.py] — session ownership check (403/404)
- [Source: backend/app/api/v1/api.py] — router registration pattern
- [Source: _bmad-output/implementation-artifacts/4-3-rag-enriched-verification-integration.md] — Pinecone query + graceful degradation precedent

### Open Questions (non-blocking)

- **Q1 — Re-ingestion of pre-fix tickets:** tickets ingested before Task 1 live under `dev-stub:{session_id}` and won't be retrievable by chat. Acceptable for MVP (re-ingest to fix), or add a one-off note in the Epic 5 retro?
- **Q2 — History GET ownership:** Task 6's `GET /chat/{session_id}/messages` is included here to unblock Story 5.2's revisit view; confirm it belongs in 5.1 vs 5.2.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- New files kept ruff-clean (reflowed 7 E501 lines). Pre-existing legacy debt left untouched and out of scope: `vector_service.py` (~21 old-style issues), `ingestion.py` (B008/I001), and pre-existing E501s in `provider.py`/`openai_provider.py`'s `generate_with_tools`/`generate_structured` (my `generate_stream` additions are clean). Tidied one pre-existing E501 in `api.py` since I was already editing it.
- `test_ingestion.py` unaffected: it mocks `embed_and_index_ticket` (AsyncMock absorbs the new `user_id` kwarg) and its auth stub returns `dev-stub`, so no assertion changed.

### Completion Notes List

- **Task 1 (prerequisite fix):** `embed_and_index_ticket` now takes `user_id` and vectorizes under `{user_id}:{session_id}`; `ingestion.py` passes `current_user`. Without this the chat would query an empty namespace. (Tickets ingested before this fix live under `dev-stub:{session_id}` — see open Q1.)
- **Task 2 (streaming):** Added `generate_stream()` to `LLMProvider` with a **concrete default** that wraps `generate()` (one chunk) so Claude/Ollama still stream validly; `OpenAIProvider` overrides with true `stream=True` delta streaming and the same error mapping as `generate`.
- **Task 4 (grounding + isolation):** `chat_service.stream_chat_answer` persists the user message first (survives mid-stream failure), retrieves ONLY from `{user_id}:{session_id}` (reusing `_embed_chunks` + the Pinecone query pattern), grounds the LLM with a strict "answer only from CONTEXT" system prompt, streams `token` events, persists the assistant message, then emits `complete`. LLM/retrieval failures emit an `error` event, never a 500.
- **Empty retrieval** still calls the LLM (with an empty CONTEXT block) so it returns the graceful "not in this ticket" answer rather than fabricating (AC5).
- **Endpoint:** `POST /chat/message` verifies session ownership (403/404) **before** streaming; `GET /chat/{session_id}/messages` returns history (unblocks Story 5.2). Router registered under `/chat`.
- **Validation:** Backend **223 pass** (+7 chat tests), new code ruff-clean. The isolation test asserts `index.query` is called with exactly `{user_id}:{session_id}` (NFR-S4).

### File List

**Backend — modified:**
- `backend/app/services/vector_service.py` — `embed_and_index_ticket` takes `user_id`, namespace `{user_id}:{session_id}`
- `backend/app/api/v1/ingestion.py` — pass `user_id=current_user`
- `backend/app/services/llm/provider.py` — added `generate_stream()` (default wraps `generate`)
- `backend/app/services/llm/openai_provider.py` — `generate_stream()` true streaming override
- `backend/app/api/v1/api.py` — registered chat router (+ import/format tidy)

**Backend — created:**
- `backend/app/schemas/chat.py` — `ChatMessageRequest`, `ChatMessageResponse`
- `backend/app/services/chat_service.py` — `stream_chat_answer` (retrieval + grounding + SSE + persistence)
- `backend/app/api/v1/chat.py` — `POST /message` (SSE) + `GET /{session_id}/messages` + ownership check
- `backend/tests/test_chat.py` — 7 tests (namespace isolation, persistence, streaming, empty retrieval, LLM error, 403/404, history)

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-04 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 6 ACs implemented. Verified the DB-session-in-streaming-response path is correct (`get_db` commits on normal completion with `expire_on_commit=False`, so the user question persists even on the LLM-error path). Namespace isolation `{user_id}:{session_id}` is enforced and tested.

### Action Items — all resolved

- [x] **[M1][Med]** `_retrieve_ticket_chunks` had **no timeout**, unlike the analogous `query_knowledge_base` (`asyncio.wait_for(10.0)`), leaving NFR-P2 (10s) unenforced → wrapped the embed+query in `asyncio.wait_for(_retrieve(), 10.0)` with a `TimeoutError` → `[]` graceful fallback. `[chat_service.py]`
- [x] **[L1][Low]** No happy-path streaming test through the endpoint → added `test_post_message_streams_tokens_for_owner` asserting an owner POST streams `token` events then `complete`. `[test_chat.py]`
- [x] **[L2][Low]** `generate_stream` hardcoded `temperature=0.7`; for grounded-only chat → added an optional `temperature` param (default 0.7; OpenAI honors it, default wrapper ignores) and `chat_service` passes `0.2`. `[provider.py, openai_provider.py, chat_service.py]`

**Post-fix validation:** backend **224 pass** (+1), new code ruff-clean (pre-existing `generate_with_tools`/`generate_structured` E501s untouched).

## Change Log

- 2026-07-04: Implemented Story 5.1 — RAG Chat API. Fixed the ticket-ingestion namespace to the real user (`{user_id}:{session_id}`), added `generate_stream()` to the LLM provider interface (+ OpenAI true streaming), and built the grounded, SSE-streaming `POST /chat/message` endpoint with `chat_messages` persistence and a history GET. Backend 223 tests pass (+7).
- 2026-07-04: Code review (adversarial) — Approve. Resolved 1 Medium + 2 Low: 10s retrieval timeout for NFR-P2 (M1), happy-path streaming endpoint test (L1), low temperature for grounded chat (L2). Backend 224 pass.
