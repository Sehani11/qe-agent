# Story 5.2: RAG Chat UI

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user**,
I want a chat interface on the pipeline workspace where I can ask questions about my Jira ticket,
so that I can comprehend complex tickets interactively before generating BDD.

## Acceptance Criteria

1. **Given** the user has a Jira ticket ingested for a session
   **When** they type a question into the chat input and press send
   **Then** the message is streamed via `useSSEStream()` to `POST /api/v1/chat/message` and response tokens appear progressively in the assistant bubble (FR9, NFR-P5)

2. **And** all previous questions and answers in this session are displayed above the input as chat history (FR11)

3. **And** on revisiting a session, past chat history is fetched via a TanStack Query hook (`GET /api/v1/chat/{session_id}/messages`) and re-displayed (FR12)

4. **And** a loading indicator is shown while awaiting the first streamed token from the LLM

5. **And** the chat interface is responsive but read-only on mobile viewports (input disabled/hidden on small screens, history still viewable)

6. **And** the user message appears immediately (optimistic), the streamed answer accumulates into a single assistant bubble, and on `error` an inline error is shown without losing the typed history

## Context & Critical Background

This story is the **frontend consumer of Story 5.1** (done). The backend contract is fixed:

- **`POST /api/v1/chat/message`** — body `{ session_id, question }`; streams SSE: `data: {"type":"token","content":"..."}` → `data: {"type":"complete"}`; errors `data: {"type":"error","message":"..."}`
- **`GET /api/v1/chat/{session_id}/messages`** — returns `[{ id, session_id, role, content, created_at }]` (oldest first)

**Already exists (reuse):**

| Piece | Location | Note |
|---|---|---|
| Generic SSE consumer (fetch + reader loop, abort, buffer) | `frontend/src/lib/hooks/useSSEStream.ts` | ⚠️ handles `log`/`complete`/`error`/`verdict` — **does NOT handle `token`** yet |
| Raw-fetch SSE auth + base-URL pattern | `frontend/src/lib/hooks/useRunVerification.ts` | `NEXT_PUBLIC_API_URL` + `supabase.auth.getSession()` Bearer |
| TanStack Query hook pattern (apiClient GET, `retry:false`) | `frontend/src/lib/hooks/useSession.ts` | model for the history hook |
| Session workspace page | `frontend/src/app/session/[sessionId]/page.tsx` | where the chat panel mounts (when a ticket/session exists) |
| `SessionContext` (`sessionId`, `jiraTicketId`) | `frontend/src/context/SessionContext.tsx` | gate chat on an ingested ticket |
| Mobile read-only overlay precedent | verification results components | pattern for AC5 |

## Tasks / Subtasks

### Frontend — SSE token support (AC1)

- [x] **Task 1: Add `onToken` to `useSSEStream`** (AC: 1)
  - [x] Add `onToken?: (content: string) => void` to `UseSSEStreamOptions`
  - [x] In the event switch, handle `json.type === "token"` → `optionsRef.current?.onToken?.(json.content as string)`
  - [x] The chat `complete` event (no `session_id`/`total`) already falls through the existing `type === "complete"` branch → sets `isStreaming=false` (no extra fields needed) — verify it does not misfire the pipeline/verification callbacks
  - [x] Do not disturb existing `log`/`verdict`/`complete`/`error` handling (regression-sensitive — used by pipeline + verification)

### Frontend — types + hooks (AC1, AC2, AC3)

- [x] **Task 2: `lib/types/chat.ts`** (AC: 2, 3)
  - [x] `export interface ChatMessage { id: string; session_id: string; role: "user" | "assistant"; content: string; created_at: string; }`
  - [x] `export interface ChatMessageRequest { session_id: string; question: string; }`

- [x] **Task 3: `useChatHistory(sessionId)` TanStack Query hook** (AC: 3)
  - [x] In `lib/hooks/useChat.ts` (or `useChatHistory.ts`): `useQuery({ queryKey: ["chat", sessionId], queryFn: apiClient.get<ChatMessage[]>(`/chat/${sessionId}/messages`) })`, `enabled: !!sessionId`, `retry: false`

- [x] **Task 4: `useChat(sessionId)` orchestration hook** (AC: 1, 2, 4, 6)
  - [x] Local `messages: ChatMessage[]` state, seeded from `useChatHistory` once loaded
  - [x] `sendMessage(question)`: push an optimistic `user` message; push an empty `assistant` placeholder; call `useSSEStream().connect(POST /chat/message, {session_id, question}, authHeaders)` with `onToken` appending to the placeholder's `content`, `onError` setting an inline error, and `isStreaming` driving the loading indicator
  - [x] Reuse the `useRunVerification` auth/base-URL approach (`NEXT_PUBLIC_API_URL` + supabase Bearer) — `useSSEStream` uses raw fetch
  - [x] Expose `{ messages, sendMessage, isStreaming, error, isHistoryLoading }`
  - [x] Guard: ignore send while `isStreaming` (matches `useSSEStream`'s single-flight `connect`)

### Frontend — UI (AC1–6)

- [x] **Task 5: `ChatPanel` component** (AC: 1, 2, 4, 5, 6)
  - [x] `frontend/src/components/pipeline/ChatPanel.tsx` — scrollable history (user right / assistant left bubbles), a streaming assistant bubble that fills as tokens arrive, a "thinking…" indicator until the first token (AC4), an input + send button
  - [x] Auto-scroll to the newest message; `Enter` sends (Shift+Enter newline); disable send while streaming or empty
  - [x] **Mobile (AC5):** below `md`, hide/disable the input with a read-only note; history remains scrollable
  - [x] Inline error banner on `error` (AC6); typed history preserved
  - [x] Loading skeleton while `isHistoryLoading`

- [x] **Task 6: Mount `ChatPanel` on the session page** (AC: 1)
  - [x] In `session/[sessionId]/page.tsx`, render `ChatPanel` when a session + ingested ticket exist (gate on `jiraTicketId` / `acceptanceCriteria`, consistent with how BDD/verification panels appear)
  - [x] Place it sensibly in the workspace (e.g. a collapsible section or alongside the BDD editor) without disrupting the existing layout

### Tests (AC1–6)

- [x] **Task 7: Frontend tests**
  - [x] `useSSEStream` test: a `token` event invokes `onToken`; a chat `complete` sets `isStreaming=false` without firing verification/pipeline callbacks (regression guard for Task 1)
  - [x] `ChatPanel` test (Vitest + `@testing-library/react`, mock `useChat`): renders history messages; typing + send calls `sendMessage`; streaming shows the thinking indicator then the accumulating assistant bubble; `error` renders the banner; empty/streaming disables send

## Dev Notes

### Extending `useSSEStream` is the right move (AC1)

The AC names `useSSEStream()` explicitly, and it already owns the fetch/reader/abort/buffer machinery. It just lacks a `token` branch. Add `onToken` and a `type === "token"` case — a ~4-line change. **Do not** fork a second SSE reader; that would duplicate the abort/buffer logic and drift. Be careful: the shared `complete` branch distinguishes pipeline (`session_id`) vs verification (`total`); a chat `complete` has neither, so it only needs to stop streaming — confirm it doesn't accidentally call `onComplete`/`onVerificationComplete` (it won't, since both are guarded by `in json` checks).

### Streaming accumulation pattern (AC1/AC6)

Keep the in-flight assistant message as the **last** element of `messages`; `onToken` appends `content += token` to it (immutably — replace the last element). The "thinking…" indicator shows while `isStreaming` and the last assistant bubble is still empty. On `complete`, `isStreaming` flips false and the bubble is final. On `error`, flip an `error` state and leave the (possibly partial) messages intact.

### History seeding + revisit (AC2/AC3)

`useChatHistory` fetches on mount (keyed by `sessionId`). Seed `useChat`'s local `messages` from it **once** (a ref-guarded effect, mirroring how `session/[sessionId]/page.tsx` seeds BDD/verification from saved data). New messages sent this session append to the same array. On a fresh session with no history, the GET returns `[]` (not 404).

### Auth + base URL (AC1)

`useSSEStream` uses raw `fetch`, so — exactly like `useRunVerification` — resolve `NEXT_PUBLIC_API_URL` and attach the Supabase access token as a `Bearer` header before `connect(...)`. `apiClient` (used by the history GET) already injects the token via its interceptor.

### Project Structure Notes

**Frontend — modify:** `lib/hooks/useSSEStream.ts` (+`onToken`), `app/session/[sessionId]/page.tsx` (mount ChatPanel)
**Frontend — create:** `lib/types/chat.ts`, `lib/hooks/useChat.ts` (`useChat` + `useChatHistory`), `components/pipeline/ChatPanel.tsx`, `components/pipeline/__tests__/ChatPanel.test.tsx`, `lib/hooks/__tests__/useSSEStream.test.ts` (or extend existing)
**Do NOT modify:** the backend chat endpoints (done in 5.1); the verification/pipeline SSE event handling beyond adding the `token` branch

### Testing Standards

- Vitest + `@testing-library/react`. Mock `useChat` for `ChatPanel` (mutable module-level state, as in `KnowledgeBasePanel.test.tsx`). For the `useSSEStream` token test, drive it with a mocked `fetch` returning a `ReadableStream` of SSE chunks, or unit-test the parse branch. Keep changed files eslint + tsc clean. (Repo note: the frontend suite has known pre-existing failures in `GitHubSourceSelector`/`auth`/`BDDEditorPanel` — scope your green-check to your files.)

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 5, Story 5.2 (FR9, FR11, FR12; NFR-P5)
- [Source: _bmad-output/implementation-artifacts/5-1-rag-chat-api.md] — backend contract (SSE shape, history GET, message schema)
- [Source: frontend/src/lib/hooks/useSSEStream.ts] — extend with `onToken`
- [Source: frontend/src/lib/hooks/useRunVerification.ts] — raw-fetch SSE auth/base-URL pattern to mirror
- [Source: frontend/src/lib/hooks/useSession.ts] — TanStack Query hook pattern for history
- [Source: frontend/src/app/session/[sessionId]/page.tsx] — mount point + "seed from saved data" pattern
- [Source: frontend/src/context/SessionContext.tsx] — `sessionId`/`jiraTicketId` gating
- [Source: backend/app/api/v1/chat.py] / [backend/app/schemas/chat.py] — endpoint + `ChatMessageResponse` shape (types must match)
- [Source: _bmad-output/implementation-artifacts/4-5-knowledge-base-ingestion-ui.md] — panel/test conventions (mutable-mock pattern)

### Open Questions (non-blocking)

- **Q1 — Placement:** ChatPanel location on the session page (collapsible section vs. side-by-side with the BDD editor) is a UX judgment — confirm the preferred spot, or accept a sensible default (a collapsible "Ask about this ticket" section above the BDD editor).
- **Q2 — Mobile read-only rationale:** AC5 disables input on mobile (consistent with the verification results read-only-on-mobile precedent). Confirm we want full parity, or allow mobile chat too.

## Dev Agent Record

### Agent Model Used

claude-opus-4-8

### Debug Log References

- **Session-page gate fix:** initially gated `ChatPanel` on `acceptanceCriteria`, but that isn't restored on session revisit (only set at ingestion) — switched the gate to `jiraTicketId` (set at ingestion AND restored on revisit) so chat history shows when returning to a past session (AC3).
- **`set-state-in-effect` lint:** the original "seed history into state via effect" tripped `react-hooks/set-state-in-effect`. Refactored to render `history + localMessages` (a `useMemo`), keeping only this-session messages in state — cleaner, avoids the lint, and prevents a revisit duplicate (history already contains persisted turns).
- **jsdom scroll:** guarded `scrollRef.current?.scrollTo?.(...)` — `scrollTo` is undefined in jsdom and would throw during tests.

### Completion Notes List

- **Task 1 (shared hook):** `useSSEStream` extended with `onToken` + a `type === "token"` branch — purely additive. A chat `complete` (no `session_id`/`total`) falls through the existing complete branch → stops streaming without firing the pipeline/verification callbacks (regression-tested).
- **Streaming accumulation:** `useChat` keeps the in-flight assistant bubble as the last local message; `onToken` appends immutably. The "Thinking…" indicator shows while streaming and that bubble is still empty (AC4). On `error`, an inline banner shows and the typed history is preserved (AC6).
- **History (AC2/AC3):** `useChatHistory` (TanStack Query, `retry:false`) fetches `GET /chat/{id}/messages`; rendered before this-session messages. On revisit the panel remounts (keyed route) so local state resets and history repopulates.
- **Auth/SSE:** mirrors `useRunVerification` — resolves `NEXT_PUBLIC_API_URL` + Supabase Bearer for the raw-fetch SSE POST; the history GET uses `apiClient` (interceptor).
- **Mobile (AC5):** input hidden below `md` with a read-only note; history stays scrollable.
- **Placement:** ChatPanel renders in the session workspace above the GitHub verification section, gated on a session + ingested ticket.
- **Validation:** `ChatPanel` (9) + `useSSEStream` (2) tests pass; changed files eslint + tsc clean (only pre-existing `useKnowledge.ts` debt remains). Backend unchanged.

### File List

**Frontend — modified:**
- `frontend/src/lib/hooks/useSSEStream.ts` — added `onToken` + `token` event branch
- `frontend/src/app/session/[sessionId]/page.tsx` — mount `ChatPanel` (gated on session + `jiraTicketId`)

**Frontend — created:**
- `frontend/src/lib/types/chat.ts` — `ChatMessage`, `ChatMessageRequest`
- `frontend/src/lib/hooks/useChat.ts` — `useChatHistory` + `useChat` (SSE send + token accumulation)
- `frontend/src/components/pipeline/ChatPanel.tsx` — chat UI (history, streaming bubble, thinking indicator, input, mobile read-only, error)
- `frontend/src/components/pipeline/__tests__/ChatPanel.test.tsx` — 9 tests
- `frontend/src/lib/hooks/__tests__/useSSEStream.test.ts` — 2 tests (token branch + verification-complete regression guard)

## Senior Developer Review (AI)

**Reviewer:** claude-opus-4-8 (adversarial code-review workflow) · **Date:** 2026-07-05 · **Outcome:** Approve (all findings fixed)

Git File List matched actual changes. All 6 ACs implemented. Verified the `history + localMessages` design is safe against window-refocus duplication — the global QueryClient sets `refetchOnWindowFocus: false` + `staleTime: 60s`, so history stays stable mid-session.

### Action Items — all resolved

- [x] **[M1][Med]** The most intricate logic (`useChat`: token accumulation, optimistic push, history/local merge) had **no direct test** — `ChatPanel.test` mocked `useChat` wholesale. Added `useChat.test.ts` (6 tests): history-before-local ordering, optimistic user+placeholder push + `connect` args, token accumulation into the last assistant bubble, empty-bubble trim on error, and no-send guards. `[useChat.test.ts]`
- [x] **[L1][Low]** A blank assistant bubble lingered next to the error banner on failure → `onError` now trims the trailing empty assistant placeholder. `[useChat.ts]`
- [x] **[L2][Low]** The message list re-rendered every row per token → extracted a `React.memo` `MessageBubble`, so only the streaming row re-renders. `[ChatPanel.tsx]`

**Post-fix validation:** `useChat` (6) + `ChatPanel` (9) + `useSSEStream` (2) = **17 tests pass**; changed files eslint + tsc clean.

## Change Log

- 2026-07-05: Implemented Story 5.2 — RAG Chat UI. Extended `useSSEStream` with `onToken`, added `useChat`/`useChatHistory` and a `ChatPanel` (streaming answers, history, thinking indicator, mobile read-only) mounted on the session workspace. Consumes the Story 5.1 chat API. ChatPanel 9 tests + useSSEStream 2 tests pass; changed files eslint + tsc clean.
- 2026-07-05: Code review (adversarial) — Approve. Resolved 1 Medium + 2 Low: added a direct `useChat` test suite (M1), trimmed the empty assistant bubble on error (L1), memoized message rows (L2). 17 frontend tests pass.
