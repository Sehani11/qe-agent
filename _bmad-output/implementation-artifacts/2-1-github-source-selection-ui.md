# Story 2.1: GitHub Source Selection UI

Status: done

## Story

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want to select how I'll provide code for verification (specific files, full repo, or PR),
so that I can point the system at the right code before running scenario verification.

## Acceptance Criteria

1. **Given** the user is on the pipeline workspace page (`/session/[sessionId]`) with BDD content present in the `BDDEditorPanel`  
   **When** they reach the verification step  
   **Then** a mode selector is displayed with three options: **"Exact File Paths"**, **"Full Repository"**, and **"Pull Request"** (FR19)

2. **Given** the user selects "Exact File Paths"  
   **Then** a multi-line text input (textarea) is revealed for one or more GitHub file paths (one per line)

3. **Given** the user selects "Full Repository"  
   **Then** a single text input is revealed for a GitHub repository URL (e.g. `https://github.com/org/repo`)

4. **Given** the user selects "Pull Request"  
   **Then** a single text input is revealed for a PR URL (e.g. `https://github.com/org/repo/pull/42`)

5. **And** the selected mode and input values are stored in `SessionContext` so they are accessible to downstream verification hooks

6. **And** the "Verify" button remains **disabled** until:
   - A mode is selected **AND**
   - The required input field is non-empty

7. **And** the GitHub source selector component is read-only/disabled on mobile viewports (desktop-first requirement, consistent with BDD editor)

8. **And** no GitHub API calls are made in this story — this story is UI only; actual fetching is Story 2.2

## Tasks / Subtasks

- [x] **Task 1: Extend SessionContext for verification state** (AC: 5)
  - [x] Add `verificationMode: 'exact_files' | 'full_repo' | 'pull_request' | null` to `SessionContext` type in `src/context/SessionContext.tsx`
  - [x] Add `githubInput: string` field to `SessionContext` for the user's GitHub source value
  - [x] Add corresponding setter functions `setVerificationMode` and `setGithubInput`
  - [x] Ensure the existing `SessionContext` provider in `src/app/layout.tsx` exposes the new fields — do NOT create a new context, extend the existing one

- [x] **Task 2: Create `GitHubSourceSelector` component** (AC: 1, 2, 3, 4, 6, 7)
  - [x] Create file: `src/components/pipeline/GitHubSourceSelector.tsx`
  - [x] Use shadcn/ui `Tabs` or segmented control pattern for the three mode options
  - [x] For **"Exact File Paths"** mode: render a `<Textarea>` (shadcn/ui) with placeholder `"One file path per line, e.g. src/auth/routes.py"` — multi-line, min 3 rows
  - [x] For **"Full Repository"** mode: render a `<Input>` (shadcn/ui) with placeholder `"https://github.com/org/repo"`
  - [x] For **"Pull Request"** mode: render a `<Input>` (shadcn/ui) with placeholder `"https://github.com/org/repo/pull/42"`
  - [x] On mode change: clear `githubInput` in `SessionContext` and switch displayed input
  - [x] On input change: update `githubInput` in `SessionContext`
  - [x] Accept `onVerify: () => void` and `isVerifying: boolean` as props — render the "Verify" button inside this component
  - [x] "Verify" button: disabled when `verificationMode === null` OR `githubInput.trim() === ''` OR `isVerifying === true`
  - [x] On mobile viewport (`< 768px`): render as read-only/disabled (consistent with `BDDEditorPanel` pattern)

- [x] **Task 3: Extend TypeScript types** (AC: 5)
  - [x] Add `VerificationMode` type: `'exact_files' | 'full_repo' | 'pull_request'` to `src/lib/types/verification.ts`
  - [x] Add `GitHubSourceInput` interface with `mode: VerificationMode` and `value: string`

- [x] **Task 4: Wire `GitHubSourceSelector` into the session page** (AC: 1, 6)
  - [x] In `src/app/session/[sessionId]/page.tsx`, import and render `<GitHubSourceSelector />`
  - [x] Position it below the `BDDEditorPanel` in the pipeline flow (before the verification results section)
  - [x] Pass `onVerify` as a no-op stub for now (Story 2.2 will wire the actual fetch call)
  - [x] Pass `isVerifying={false}` stub (Story 2.2 will pass real loading state)
  - [x] Only render the selector when BDD content exists in `SessionContext` (consistent with existing BDD editor conditional rendering)

- [x] **Task 5: Verify no regressions** (AC: all)
  - [x] Run `npm run dev` and manually confirm the page renders without errors
  - [x] Confirm existing `BDDEditorPanel`, `TerminalProgressLog`, and download buttons are unaffected
  - [x] Confirm mode switching clears the input field correctly
  - [x] Confirm "Verify" button disabled states work across all three mode transitions

## Dev Notes

### Epic 2 Context — DEV_USER_ID Stub

This is Epic 2 (GitHub Code Verification). **No auth is required.** The `DEV_USER_ID` environment stub from Epic 1 is still in place. This story is **UI only** — no backend calls, no GitHub API, no SSE. The full GitHub fetch + LLM verification pipeline is wired in Stories 2.2 and 2.3.

### Architecture-Mandated Patterns

1. **No raw API calls in this component** — zero backend calls.
2. **Extend `SessionContext`, don't create a new one** — extends `src/context/SessionContext.tsx` from Epic 1.
3. **No `any` in TypeScript** — `VerificationMode` typed properly.
4. **Mobile guard** — absolute overlay + `pointer-events-none` on inner content (matches `BDDEditorPanel`).

### Key Files

| File | Action |
|---|---|
| `src/context/SessionContext.tsx` | ADD `verificationMode`, `githubInput`, setters |
| `src/components/pipeline/GitHubSourceSelector.tsx` | CREATE |
| `src/components/pipeline/GitHubSourceSelector.test.tsx` | CREATE (unit tests) |
| `src/lib/types/verification.ts` | ADD `VerificationMode`, `GitHubSourceInput` types |
| `src/app/session/[sessionId]/page.tsx` | ADD `<GitHubSourceSelector />` below BDD editor |

## Senior Developer Review (AI)

**Review Date:** 2026-04-05
**Outcome:** Changes Requested → All Addressed → **Approved**
**Reviewer:** Claude Sonnet 4.6 (Thinking) — separate review context

### Action Items

- [x] **[High]** H1 — JSX at module scope in MODES constant (React element identity issue) → Fixed: changed to `ComponentType` refs, instantiated during render
- [x] **[High]** H2 — Redundant `aria-disabled` alongside native `disabled` on button (conflicting tab-order signals) → Fixed: removed `aria-disabled`
- [x] **[Med]** M1 — Zero unit tests despite Vitest being fully configured → Fixed: 22 tests written in `GitHubSourceSelector.test.tsx`, all pass
- [x] **[Med]** M2 — `verificationMode`/`githubInput` not reset on new ticket ingestion → Fixed: added `setVerificationMode(null)` + `setGithubInput("")` in `handleIngestTrigger`
- [x] **[Med]** M3 — Non-unique `id="github-source-input"` shared by textarea and input → Fixed: mode-specific IDs (`github-source-input-{mode}`)
- [x] **[Low]** L1 — Mobile overlay doesn't block pointer events on content behind it → Fixed: `pointer-events-none md:pointer-events-auto` on inner div
- [x] **[Low]** L3 — Code-path inputs missing `spellCheck={false}` / `autoComplete="off"` → Fixed on both textarea and inputs

### Review Notes (Skipped / Won't Fix)

- **M4** — Covered by H1 fix (same root cause, resolved together)
- **L2** — Architectural observation; intentional design for downstream story access; accepted as tech debt to revisit in Story 2.2

## Dev Agent Record

### Agent Model Used

Claude Sonnet 4.6 (Thinking)

### Debug Log References

- Initial implementation: `npx tsc --noEmit` — exit 0
- Post-review fixes: `npx tsc --noEmit` — exit 0
- Test run: `npx vitest run GitHubSourceSelector.test.tsx` — 22/22 passed

### Completion Notes List

- ✅ Task 3 (types): `src/lib/types/verification.ts` with `VerificationMode` + `GitHubSourceInput`
- ✅ Task 1 (SessionContext): Extended with `verificationMode`, `githubInput`, setters
- ✅ Task 2 (component): `GitHubSourceSelector.tsx` with segmented control, conditional inputs, Verify button, mobile guard
- ✅ Task 4 (page wiring): Conditional render below BDD editor, stub props per spec
- ✅ Task 5 (regressions): TS clean, dev server running, existing components untouched
- ✅ Code review fixes applied: H1, H2, M1, M2, M3, L1, L3 all resolved
- ℹ️ shadcn Tabs: Not present in project (uses `@base-ui/react`); implemented with native HTML + Tailwind matching established design language

### File List

- `src/lib/types/verification.ts` — CREATED
- `src/context/SessionContext.tsx` — MODIFIED (verificationMode, githubInput, setters; reset on ticket ingestion)
- `src/components/pipeline/GitHubSourceSelector.tsx` — CREATED (+ post-review fixes)
- `src/components/pipeline/GitHubSourceSelector.test.tsx` — CREATED (22 unit tests)
- `src/app/session/[sessionId]/page.tsx` — MODIFIED (import, bddContent destructure, GitHubSourceSelector render, M2 fix)

### Change Log

- 2026-04-05: Story 2.1 implementation — GitHub Source Selection UI (initial)
- 2026-04-05: Code review fixes — H1 (JSX module scope), H2 (aria-disabled), M1 (22 unit tests), M2 (stale state on ticket re-ingest), M3 (unique input IDs), L1 (pointer-events mobile), L3 (spellCheck/autoComplete). Status: done.
