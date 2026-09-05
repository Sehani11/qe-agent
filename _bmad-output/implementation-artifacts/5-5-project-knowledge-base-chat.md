# Story 5.5: Project Knowledge Base Q&A (General RAG Chat)

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-projects-and-credential-scoping.md)): The knowledge base is namespaced **per project** (`{user_id}:{project_id}:knowledge`), not per user, so `POST /chat/knowledge` now carries `project_id`. It also accepts `llm_provider` / `llm_model` ([model selection record](maintenance-2026-08-23-runtime-model-selection.md)).


Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-and-rag-hardening.md)): Answers now ground in the full retrieved chunk text (the 300-char snippet remains only for the sources display), and retrieval is hybrid-lite — exact identifiers like ticket keys are matched verbatim, not just by embedding distance.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want to ask natural-language questions about my whole project and get answers grounded in my ingested knowledge base,
so that I can quickly understand project context (architecture, related work, docs) without opening a specific ticket.

## Acceptance Criteria

1. **Given** an authenticated user has ingested project knowledge (Confluence/Jira/documents) into `{user_id}:knowledge`
   **When** a POST is made to `/api/v1/chat/knowledge` with a `question`
   **Then** the service retrieves the top-K relevant chunks from the user's knowledge namespace via `knowledge_service.query_knowledge_base` and grounds the LLM strictly in them (no ticket/session scoping)

2. **And** the answer streams as SSE `{"type":"token"}` events, then a `{"type":"sources"}` event listing the knowledge sources used, then `{"type":"complete"}`

3. **And** the LLM is called only via the `LLMProvider` interface, at low temperature, with a grounding prompt forbidding answers outside the retrieved context (graceful "not in the knowledge base" when empty)

4. **And** the endpoint requires authentication and is **not** scoped to a session; it is stateless — no chat history is persisted (there is no session to anchor it to)

5. **And** retrieval degrades gracefully (empty context, still answers) when Pinecone is unconfigured or times out — the endpoint never 500s on retrieval failure; an LLM failure emits `{"type":"error"}` and no `complete`

6. **And given** the user opens `/knowledge`, a `KnowledgeChatPanel` ("Ask about your project") streams the answer via `useKnowledgeChat`/`useSSEStream` and renders cited sources as clickable chips (Confluence/Jira links open in a new tab); read-only on mobile

## Context & Critical Background

Pure **composition** of two things that already exist — no new retrieval, no new infra:

- **Retrieval** already exists: [`knowledge_service.query_knowledge_base(user_id, question, top_k)`](backend/app/services/knowledge_service.py#L146) queries `{user_id}:knowledge`, returns `list[dict]` (`source, source_id, snippet, title, url`), bounded to 10s, never raises (returns `[]`).
- **Streaming chat pattern** already exists: [`chat_service.stream_chat_answer`](backend/app/services/chat_service.py#L101) (Story 5.1) — the SSE grounding/streaming shape to mirror, minus the session/DB persistence (this chat is session-less).

### Ticket chat vs. this (the distinction)

| | Ticket chat (5.1/5.2) | Project chat (this) |
|---|---|---|
| Namespace | `{user_id}:{session_id}` | `{user_id}:knowledge` |
| Scope | one ingested ticket | all ingested KB sources |
| Endpoint | `POST /chat/message` | `POST /chat/knowledge` |
| Session | required (ownership 403/404) | none — user-scoped only |
| Persistence | `chat_messages` per session | **stateless** (no session anchor) |
| Sources event | no | yes (cites KB docs) |

### Reuse map

| Piece | Location |
|---|---|
| KB retrieval | `knowledge_service.query_knowledge_base` |
| Streaming/grounding shape | `chat_service.stream_chat_answer` |
| LLM interface | `llm.generate_stream(prompt, system_prompt, temperature)` via `get_llm_provider()` |
| SSE frontend hook | `useSSEStream` (+ new optional `onSources`) |
| Chat UI shape | `ChatPanel.tsx` (mirrored as `KnowledgeChatPanel.tsx`) |
| Source-chip precedent | `RagContextPanel` / `RagContextItem` type |
| Mount point | `/knowledge` page (already hosts `KnowledgeBasePanel`) |

## Tasks / Subtasks

### Backend

- [x] **Task 1: `schemas/chat.py::KnowledgeChatRequest`** (AC: 1) — `{ question: str (min_length=1) }`, no `session_id`
- [x] **Task 2: `services/knowledge_chat_service.py::stream_knowledge_answer(user_id, question, llm)`** (AC: 1–5)
  - [x] Retrieve via `query_knowledge_base(user_id, question, top_k=5)`
  - [x] Build a grounded prompt (context = titled snippets; empty → "(no relevant project knowledge found)")
  - [x] Stream `token` events via `llm.generate_stream(..., temperature=0.2)`; on `LLMProviderError`/`Exception` emit `error` and return
  - [x] After the answer, emit a `sources` event (the retrieved chunks) then `complete`
  - [x] Stateless — no DB writes; retrieval never raises (KB service already guards)
- [x] **Task 3: `POST /api/v1/chat/knowledge`** (AC: 1, 4) — auth-only (`get_current_user`), no session/db; returns `StreamingResponse(text/event-stream)`
- [x] **Task 4: `tests/test_knowledge_chat.py`** (AC: 1–5) — token→sources→complete order + KB namespace used; empty retrieval still answers; LLM error emits error & no complete; endpoint streams for authed user; empty question → 422

### Frontend

- [x] **Task 5: `useSSEStream` — optional `onSources` callback** (AC: 6) — handle `{"type":"sources"}`; additive, existing callers unaffected
- [x] **Task 6: `lib/hooks/useKnowledgeChat.ts`** (AC: 6) — stateless: local messages, POST `/chat/knowledge` `{question}` with Bearer, accumulate tokens into the assistant bubble, attach `sources`, drop empty bubble on error
- [x] **Task 7: `components/knowledge/KnowledgeChatPanel.tsx` + mount on `/knowledge`** (AC: 6) — "Ask about your project", streamed answer, clickable source chips (new-tab), read-only on mobile
- [x] **Task 8: `KnowledgeChatPanel.test.tsx`** — empty state, send, answer+citations render, disabled while streaming, error banner

## Dev Notes

### Grounding prompt

Tuned for a project KB (vs. a single ticket): allow "the project knowledge base does not cover it", forbid outside knowledge, low temperature (0.2) so it stays grounded. Context lines carry the source title so the model can cite naturally: `- (Auth Architecture) <snippet>`.

### Why stateless

The ticket chat persists to `chat_messages` keyed by `session_id`. A project-wide chat has no session to key on; adding a durable "knowledge conversation" table (with its own RLS + migration) was out of scope for this increment. History therefore lives only in the component for the current mount. If persistent project-chat history is wanted later, add a `knowledge_chat_messages` table keyed by `user_id` (+ RLS) and seed the hook from it — the SSE contract wouldn't change.

### Sources event

`query_knowledge_base` already returns render-ready dicts (`source, source_id, snippet, title, url`). They pass straight into the `sources` SSE event and the frontend chips — no transformation. The `useSSEStream` change is a new optional `onSources` branch, so `ChatPanel`, verification, and pipeline streams are unaffected.

### Project Structure Notes

**Backend — modify:** `app/schemas/chat.py`, `app/api/v1/chat.py`. **Backend — create:** `app/services/knowledge_chat_service.py`, `tests/test_knowledge_chat.py`.
**Frontend — modify:** `src/lib/hooks/useSSEStream.ts`, `src/app/knowledge/page.tsx`. **Frontend — create:** `src/lib/hooks/useKnowledgeChat.ts`, `src/components/knowledge/KnowledgeChatPanel.tsx`, `src/components/knowledge/__tests__/KnowledgeChatPanel.test.tsx`.
**Do NOT modify:** `knowledge_service.query_knowledge_base`, `chat_service` (ticket chat), the `chat_messages` model. No migration.

### Testing Standards

Backend: `pytest`/`pytest-asyncio`, mock `query_knowledge_base` + a fake `generate_stream`; override `get_current_user`. Frontend: `vitest` + Testing Library, mock `useKnowledgeChat`. Full-suite regression; new backend code ruff-clean.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 5, Story 5.5
- [Source: backend/app/services/knowledge_service.py:146] — `query_knowledge_base` (retrieval, returns render-ready dicts, graceful)
- [Source: backend/app/services/chat_service.py:101] — `stream_chat_answer` (SSE streaming/grounding pattern mirrored)
- [Source: backend/app/services/llm/provider.py:34] — `generate_stream(prompt, system_prompt, temperature)`
- [Source: frontend/src/lib/hooks/useSSEStream.ts] — SSE hook (added `onSources`)
- [Source: frontend/src/components/pipeline/ChatPanel.tsx] — chat UI shape mirrored
- [Source: frontend/src/lib/types/verification.ts:41] — `RagContextItem` (source chip shape)

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- Backend new code ruff-clean (one docstring E501 wrapped).
- Frontend new files tsc- and eslint-clean; `useSSEStream` change is an additive optional callback.

### Completion Notes List

- **Composition, not new infra (AC1):** the endpoint reuses `query_knowledge_base` (namespace `{user_id}:knowledge`) verbatim — no new Pinecone code, no session scoping.
- **SSE contract (AC2):** `stream_knowledge_answer` yields `token`* → `sources` → `complete`; on failure yields `error` and stops (no `complete`). Verified by `test_streams_tokens_then_sources_then_complete` and `test_llm_error_emits_error_and_no_complete`.
- **Grounded + graceful (AC3/AC5):** low-temp `generate_stream` with a KB-scoped system prompt; empty retrieval still answers (`test_empty_retrieval_still_answers`); retrieval never raises (KB service guards + 10s budget).
- **Stateless (AC4):** no DB writes, no `session_id`; endpoint is auth-only. Empty question → 422.
- **Frontend (AC6):** `useKnowledgeChat` streams into one assistant bubble and attaches `sources`; `KnowledgeChatPanel` renders clickable source chips (new-tab) and is read-only on mobile; mounted under `KnowledgeBasePanel` on `/knowledge`.
- **Validation:** backend **250 pass** (+5 knowledge-chat tests), new code ruff-clean. Frontend `KnowledgeChatPanel` suite **5 pass**; changed frontend files tsc/eslint-clean; full frontend suite shows only the 3 pre-existing unrelated failing files (auth/GitHubSourceSelector/BDDEditorPanel), unchanged by this story.

### File List

**Backend — modified:**
- `backend/app/schemas/chat.py` — `KnowledgeChatRequest`
- `backend/app/api/v1/chat.py` — `POST /chat/knowledge` route

**Backend — created:**
- `backend/app/services/knowledge_chat_service.py` — `stream_knowledge_answer` + grounding prompt
- `backend/tests/test_knowledge_chat.py` — 5 tests

**Frontend — modified:**
- `frontend/src/lib/hooks/useSSEStream.ts` — optional `onSources` callback + `sources` event branch
- `frontend/src/app/knowledge/page.tsx` — mount `KnowledgeChatPanel` under the KB panel

**Frontend — created:**
- `frontend/src/lib/hooks/useKnowledgeChat.ts` — stateless knowledge-chat hook
- `frontend/src/components/knowledge/KnowledgeChatPanel.tsx` — "Ask about your project" chat UI
- `frontend/src/components/knowledge/__tests__/KnowledgeChatPanel.test.tsx` — 5 tests

## Change Log

- 2026-07-05: Implemented Story 5.5 — Project Knowledge Base Q&A (general RAG chat). Added `POST /api/v1/chat/knowledge` streaming a KB-grounded answer (namespace `{user_id}:knowledge`) via the existing `query_knowledge_base` retrieval + `LLMProvider` streaming, with a `sources` SSE event; stateless (no session). Frontend `KnowledgeChatPanel` + `useKnowledgeChat` mounted on `/knowledge`, with clickable source citations; `useSSEStream` gained an additive `onSources` callback. Backend 250 tests pass (+5); frontend +5 (KnowledgeChatPanel).
