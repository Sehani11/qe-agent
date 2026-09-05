# Story 2.4: Verification Results Display

Status: done

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-ui-redesign-feature-file.md)): Every Tailwind class listed in this story's Visual Reference is superseded. Cards are `rounded-lg border border-rule bg-card` (hairline rules, no shadow); PASS/FAIL use the shared `VerdictBadge` on the `pass`/`fail` signal tokens with a glyph as well as a colour; the summary bar and each verdict row carry a `.gutter-rule` tinted by signal. Behaviour — progressive append, failed rows auto-expanded, passed rows collapsed — is unchanged.

> **Amended 2026-08-22** ([maintenance record](maintenance-2026-08-22-verification-source-persistence.md)): The panel now renders a "Verified against" block above the results, listing the source the run was scoped to (repo URL, PR URL, or every file URL for exact-files mode). It is restored from the stored rows on session revisit, and hidden entirely for runs recorded before the source was persisted.

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **user** (stubbed as `DEV_USER_ID` in this epic),
I want to see a clear per-scenario pass/fail report with code-level justifications, implementation suggestions, and an overall pass rate,
So that I can quickly identify gaps in code coverage and share findings with my team.

## Acceptance Criteria

1. **Given** verification is complete for a session
   **When** the results panel renders
   **Then** each BDD scenario is displayed as a `VerificationResultRow` with its verdict badge (✅ PASS / ❌ FAIL) and natural-language justification referencing specific code sections (FR24)

2. **And** failed scenarios auto-expand to show the `implementation_suggestion` with actionable guidance (FR38)

3. **And** each result row includes a GitHub link to the relevant code file/line used during analysis (FR37)

4. **And** an overall pass percentage is displayed prominently at the top of the results panel (FR25)

5. **And** results appear progressively in real time as the SSE stream emits per-scenario verdicts — no waiting for all results at once

6. **And** the display is read-only on mobile viewports (desktop-first requirement)

7. **And** no RAG context panel is shown in this epic — that is added in Epic 4

## Tasks / Subtasks

- [x] **Task 1: Create `VerificationResultRow` component** (AC: 1, 2, 3)
  - [x] Create `frontend/src/components/pipeline/VerificationResultRow.tsx`
  - [x] Props: `verdict: VerificationVerdict` (from `@/lib/types/verification`)
  - [x] Show verdict badge: green "✅ PASS" or red "❌ FAIL" (`verdict.status`)
  - [x] Show `verdict.scenario_title` as the row heading
  - [x] Show `verdict.justification` as body text
  - [x] Show `verdict.code_reference` (file + function + line) as a code pill / inline reference
  - [x] Render each `verdict.github_links` entry as a clickable link (external, `target="_blank"`)
  - [x] Collapsed by default; expand on click to reveal full justification, code reference, and links
  - [x] Failed scenarios (`status === "fail"`) auto-expand and show `implementation_suggestion` highlighted in a callout block
  - [x] Passed scenarios (`status === "pass"`) have `implementation_suggestion` null — do not render the callout
  - [x] Match design system: `rounded-2xl`, `border`, `bg-white/90`, same shadow / spacing as `GitHubSourceSelector`

- [x] **Task 2: Create `VerificationResultsPanel` component** (AC: 1, 4, 5, 6, 7)
  - [x] Create `frontend/src/components/pipeline/VerificationResultsPanel.tsx`
  - [x] Consume `verificationResults: VerificationVerdict[]` and `verificationSummary: VerificationSummary | null` from `useSessionContext()`
  - [x] Do NOT render if `verificationResults.length === 0 && verificationSummary === null`
  - [x] **Summary bar at top** (shown as soon as `verificationSummary` is available):
    - [x] Display overall pass percentage: `Math.round((summary.passed / summary.total) * 100)%`
    - [x] Display counts: `N passed / N failed / N total`
    - [x] Use green accent when 100% pass, amber when partial, red when 0%
  - [x] **Results list**: render a `VerificationResultRow` for each entry in `verificationResults`
  - [x] Results stream in progressively — list grows as new verdicts arrive (no skeleton/spinner between rows; just appends)
  - [x] **Mobile read-only overlay**: same pattern as `GitHubSourceSelector` — `md:hidden` overlay, `pointer-events-none` on mobile
  - [x] No RAG context section — explicitly not included in this story

- [x] **Task 3: Mount `VerificationResultsPanel` in session page** (AC: 1–7)
  - [x] Edit `frontend/src/app/session/[sessionId]/page.tsx`
  - [x] Import `VerificationResultsPanel`
  - [x] Mount below `GitHubSourceSelector` (conditional on `verificationResults.length > 0 || isVerifying`)
  - [x] No new state — reads from `SessionContext` which Story 2.3 already populated

- [x] **Task 4: Write frontend unit tests** (AC: 1–7)
  - [x] Create `frontend/src/components/pipeline/__tests__/VerificationResultsPanel.test.tsx`
  - [x] Test: renders nothing when `verificationResults` is empty and `verificationSummary` is null
  - [x] Test: renders summary bar with correct pass percentage when `verificationSummary` is provided
  - [x] Test: renders one `VerificationResultRow` per verdict in `verificationResults`
  - [x] Test: failed verdict row auto-expands and shows `implementation_suggestion`
  - [x] Test: passed verdict row does NOT show `implementation_suggestion` block
  - [x] Test: GitHub links render as anchor tags with `target="_blank"`
  - [x] Test: mobile overlay is rendered (`md:hidden` div present in DOM)

## Dev Notes

### What Already Exists (Story 2.3 handoff — DO NOT recreate)

| Symbol | File | Notes |
|---|---|---|
| `VerificationVerdict` | `frontend/src/lib/types/verification.ts:36` | Full type: `scenario_id`, `scenario_title`, `status`, `justification`, `code_reference`, `github_links`, `implementation_suggestion` |
| `CodeReference` | `frontend/src/lib/types/verification.ts:29` | `file: string`, `function: string`, `line: number` |
| `VerificationSummary` | `frontend/src/lib/types/verification.ts:54` | `total`, `passed`, `failed` |
| `verificationResults` | `frontend/src/context/SessionContext.tsx:39` | `VerificationVerdict[]` — populated by `useRunVerification` on each SSE verdict event |
| `verificationSummary` | `frontend/src/context/SessionContext.tsx:43` | `VerificationSummary | null` — set by `useRunVerification` on the SSE "complete" event |
| `setVerificationResults` | `SessionContext` | Accepts `results[]` or `(prev) => results[]` updater — already used by `useRunVerification` |
| `setVerificationSummary` | `SessionContext` | Accepts `VerificationSummary | null` |
| `useRunVerification` | `frontend/src/lib/hooks/useRunVerification.ts` | Connects SSE, populates context — DO NOT MODIFY |
| `handleRunVerification` | `frontend/src/app/session/[sessionId]/page.tsx:182` | Calls `runVerification(...)` — already wired to `GitHubSourceSelector.onRunVerification` |
| `isVerifying` | `useRunVerification` return | Derived from `useSSEStream.isStreaming` — single source of truth for loading state |

### SSE Flow (for Display Timing)

The SSE stream from `POST /api/v1/verification/run` produces events in this order:
1. Zero or more: `data: {"type": "verdict", ...}\n\n` — one per BDD scenario as it completes
2. Exactly one: `data: {"type": "complete", "total": N, "passed": M, "failed": K}\n\n`
3. Optionally: `data: {"type": "error", "message": "..."}\n\n` for per-scenario LLM failures

`useRunVerification` routes these to `SessionContext`:
- Each `verdict` event → `setVerificationResults(prev => [...prev, verdict])`
- `complete` event → `setVerificationSummary({total, passed, failed})`
- `error` event → `setGlobalError(message)` (already surfaced in session page header)

**Important**: `verificationResults` grows incrementally during the stream. The panel MUST render each row as it arrives — do NOT wait for the `complete` event before showing rows.

### Design System Rules (MUST FOLLOW)

Reference the existing components for patterns:

- **Card containers**: `rounded-3xl border border-sky-100 bg-white/90 p-6 shadow-[0_20px_70px_-30px_rgba(14,116,144,0.22)] backdrop-blur` — see `GitHubSourceSelector`
- **Badge — PASS**: green variant, e.g. `bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5 text-xs font-semibold`
- **Badge — FAIL**: red variant, e.g. `bg-rose-50 text-rose-700 border border-rose-200 rounded-full px-2 py-0.5 text-xs font-semibold`
- **Implementation suggestion callout**: amber/orange accent block, e.g. `bg-amber-50 border border-amber-200 rounded-xl p-3 text-sm text-amber-900`
- **Code pill / reference**: `font-mono text-xs bg-slate-100 border border-slate-200 rounded px-1.5 py-0.5 text-slate-700`
- **Section label**: `text-sm font-semibold uppercase tracking-[0.25em] text-sky-700`
- **Mobile overlay pattern** (same as `GitHubSourceSelector.tsx:70-77`):
  ```tsx
  <div className="relative">
    <div className="absolute inset-0 z-10 flex items-center justify-center bg-white/70 p-4 md:hidden">
      {/* read-only notice */}
    </div>
    <div className="pointer-events-none md:pointer-events-auto">
      {/* actual content */}
    </div>
  </div>
  ```
- **Lucide icons**: import from `lucide-react` — `CheckCircle2`, `XCircle`, `ChevronDown`, `ChevronUp`, `ExternalLink`, `Code2`

### Architecture Constraints (Epic 2)

- **No auth**: Epic 2 uses `DEV_USER_ID` stub. Do not add any auth checks or `useSession()` Supabase calls.
- **Pure display story**: This story has NO backend changes. All data flows from `SessionContext` populated by Story 2.3's `useRunVerification`. Do not add new API calls or routes.
- **No TanStack Query mutations**: Results come from SSE context, not from query-based fetching. No `useMutation` or `useQuery` needed.
- **Desktop-first**: Implement fully functional on `md+` viewports. Mobile is read-only overlay (not hidden — just overlaid). Same approach as `GitHubSourceSelector` and `BDDEditorPanel`.
- **No RAG panel**: The `VerificationResultsPanel` must NOT include any RAG context section. That is Epic 4 scope. Explicitly do not add a placeholder.

### Project Structure Notes

New files to create:
```
frontend/src/components/pipeline/VerificationResultRow.tsx          (new)
frontend/src/components/pipeline/VerificationResultsPanel.tsx       (new)
frontend/src/components/pipeline/__tests__/VerificationResultsPanel.test.tsx  (new)
```

Files to modify:
```
frontend/src/app/session/[sessionId]/page.tsx                        (mount panel)
```

No backend files change in this story.

### Testing Approach

Tests use the same Vitest + React Testing Library setup as `BDDEditorPanel.test.tsx` and `GitHubSourceSelector.test.tsx`. Check the existing test files for import patterns and mock setups:
- `frontend/src/components/pipeline/__tests__/BDDEditorPanel.test.tsx`
- `frontend/src/components/pipeline/GitHubSourceSelector.test.tsx`

The `SessionContext` must be mocked/provided in tests. Follow the exact same pattern used in the existing pipeline component tests.

### Learnings from Story 2.3 Code Review

- **Single source of truth for loading**: `isVerifying` derives from `useSSEStream.isStreaming` — do not add a separate `useState` for loading state in the new components
- **`useCallback` all callbacks**: Any function passed as a prop that could cause re-renders should be memoized
- **Stable object references**: Do not construct inline objects as component props that contain callbacks — memoize with `useCallback`/`useMemo` or lift to parent
- **Ruff E501**: Backend line length is 88 — this is frontend only, but TypeScript/Tailwind strings can be long; aim for reasonable line lengths
- **No `useEffect` for derived state**: Pass percentage and counts can be computed inline from `verificationSummary` during render — no need for `useEffect`

### Git Context (Recent Commits)

The codebase has been rapidly built in single-commit batches. Key patterns established in Stories 2.1–2.3:
- `"use client"` directive at top of all component files
- Lucide icons imported from `lucide-react`
- `useSessionContext()` for all shared state
- Tailwind utility classes only — no CSS modules or styled-components
- TypeScript strict types throughout

### References

- Story 2.3 acceptance criteria (AC 2, 3, 5): [2-3-llm-verification-service-and-api.md](_bmad-output/implementation-artifacts/2-3-llm-verification-service-and-api.md)
- `VerificationVerdict` type: [verification.ts](frontend/src/lib/types/verification.ts#L36)
- `SessionContext` verification state: [SessionContext.tsx](frontend/src/context/SessionContext.tsx#L39)
- `useRunVerification` hook: [useRunVerification.ts](frontend/src/lib/hooks/useRunVerification.ts)
- `GitHubSourceSelector` (mobile overlay pattern): [GitHubSourceSelector.tsx](frontend/src/components/pipeline/GitHubSourceSelector.tsx#L70)
- Epic 2 FR coverage: [epics.md](_bmad-output/planning-artifacts/epics.md) — FR24, FR25, FR37, FR38

## Dev Agent Record

### Agent Model Used

claude-sonnet-4-6

### Debug Log References

_No blockers encountered._

### Completion Notes List

- Implemented `VerificationResultRow`: collapsible row with verdict badge, justification, code pill (file›function:line), GitHub links, and amber callout for `implementation_suggestion` on failed scenarios. Failed rows auto-expand; passed rows collapsed by default.
- Implemented `VerificationResultsPanel`: reads `verificationResults` and `verificationSummary` from `useSessionContext()`. Summary bar shows pass %, counts, and colour-coded accent (emerald/amber/rose). Rows render progressively as SSE verdicts arrive. Mobile read-only overlay follows `GitHubSourceSelector` pattern. No RAG section.
- Mounted `VerificationResultsPanel` in session page below `GitHubSourceSelector`, conditionally shown when `verificationResults.length > 0 || verificationSummary !== null`. Added `verificationResults` and `verificationSummary` to context destructure.
- 12 unit tests written and passing (Vitest + React Testing Library). Pre-existing failures in `GitHubSourceSelector.test.tsx` (22) and `BDDEditorPanel.test.tsx` (1) confirmed as pre-existing before this story — not regressions.
- No backend changes — pure display story consuming SSE context from Story 2.3.
- Code review (claude-sonnet-4-6, 2026-04-10): 5 issues fixed — duplicate type imports removed; `code_reference` pill test added; GitHub link keys changed from index to URL; summary bar now renders `0%` when `total === 0`; page.tsx mount condition aligned with panel's null guard.

### File List

- `frontend/src/components/pipeline/VerificationResultRow.tsx` (new)
- `frontend/src/components/pipeline/VerificationResultsPanel.tsx` (new)
- `frontend/src/components/pipeline/__tests__/VerificationResultsPanel.test.tsx` (new)
- `frontend/src/app/session/[sessionId]/page.tsx` (modified — import + mount + context destructure)

## Senior Developer Review (AI)

**Review Date:** 2026-04-10
**Outcome:** Changes Requested → Fixed

### Action Items

- [x] [High] Remove duplicate `VerificationVerdict`/`VerificationSummary` type imports aliased as `VV`/`VS` in test file [`test.tsx:18`]
- [x] [High] Add test coverage for `code_reference` pill (file/function/line) — untested despite task marked complete [`test.tsx`]
- [x] [Med] Replace array-index `key` with URL string for GitHub link list [`VerificationResultRow.tsx:75`]
- [x] [Med] Summary bar silently hidden when `verificationSummary.total === 0` — now renders `0%` instead [`VerificationResultsPanel.tsx:16-19`]
- [x] [Med] Page mount condition `|| isVerifying` caused spurious component mount — changed to `|| verificationSummary !== null` [`page.tsx:273`]

## Change Log

- 2026-04-10: Story 2.4 implemented — `VerificationResultRow`, `VerificationResultsPanel` created; panel mounted in session page; 12 unit tests added (claude-sonnet-4-6)
- 2026-04-10: Code review fixes applied — 5 issues resolved (H1, H2, M1, M2, M3); tests now 12/12 passing
