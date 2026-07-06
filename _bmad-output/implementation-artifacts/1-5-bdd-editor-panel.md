# Story 1.5: bdd-editor-panel

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to view and edit the generated BDD content in an in-browser code editor,
So that I can review, adjust, and correct any Gherkin scenarios before downloading or verifying.

## Acceptance Criteria

1. [x] **Given** the BDD generation API (Story 1.3) has returned Gherkin scenarios for a session
2. [x] **When** the user navigates to the pipeline workspace page (`/session/[sessionId]`)
3. [x] **Then** the `BDDEditorPanel` component renders the Gherkin content in Monaco Editor or CodeMirror with Gherkin syntax highlighting (FR15)
4. [x] **And** the user can edit any line of the BDD content directly in the editor
5. [x] **And** changes are captured in local React state via `SessionContext` and reflected immediately
6. [x] **And** the editor is read-only on mobile viewports (desktop-first requirement)
7. [x] **And** the session page's request lifecycle state feeds ingestion and generation progress state into the adjacent `TerminalProgressLog` component, which presents simplified status/loading feedback (NFR-P5)

## Tasks / Subtasks

- [x] Task 1: Initialize Session Pipeline State & Types
  - [x] Create shared session/loading types and state structures supporting the pipeline workspace.
  - [x] Use straightforward request lifecycle state for ingestion and generation progress instead of requiring streaming transport in the current Epic 1 flow.
- [x] Task 2: Create Session Context
  - [x] Create `frontend/src/context/SessionContext.tsx` providing a React Context that stores the shared variables required: BDD content string payload, `session_id`, log strings list, and loading statuses for ingestion. Let it wrap app layouts.
- [x] Task 3: Build `TerminalProgressLog` UI
  - [x] Scaffold `frontend/src/components/pipeline/TerminalProgressLog.tsx`.
  - [x] Render visually as a friendly status card that summarizes backend progress with clear copy, spinner/progress affordances, and step guidance rather than a terminal log.
- [x] Task 4: Integrate Robust BDD Editor
  - [x] Install Monaco Editor (`npm install @monaco-editor/react`) cleanly into the frontend project.
  - [x] Scaffold `frontend/src/components/pipeline/BDDEditorPanel.tsx` wrapping the editor conditionally ensuring read-only locks trigger on `max-width` mobile viewport bounds via Tailwind/Window queries.
  - [x] Ensure any text typed is written right back to `SessionContext` immediately.
  - [x] Ensure basic Cucumber/Gherkin syntax themes are applied to Monaco out-of-the-box (Keywords Given, When, Then colored).
- [x] Task 5: Assemble Pipeline Workspace Page
  - [x] Implement Next.js Dynamic route `frontend/src/app/session/[sessionId]/page.tsx`.
  - [x] Render a desktop-first responsive grid containing both `TerminalProgressLog` and `BDDEditorPanel` cleanly side-by-side using shadcn UI panels.

## Dev Notes

### Architecture & Pattern Compliance
- **Progress State:** The current Epic 1 workspace uses simple request lifecycle flags for ingestion and BDD generation, surfaced through `SessionContext` and the status panel.
- **Component File Conventions:** Next.js Server Components are default; `use client` must be at the very top of `SessionContext.tsx`, `TerminalProgressLog.tsx`, `BDDEditorPanel.tsx`, and the dynamic `page.tsx` since user interactions happen here exclusively.
- **Mobile Responsive Layout:** Ensure the BDD editor is locked `readOnly={true}` safely by querying screen bounds or utilizing Tailwind hidden bounds to render simplified read-only blocks instead to avoid Monaco blowing up mobile threads.

### Previous Story Intelligence
From Story 1.4 (`jira-ticket-ingestion`):
- The ingest endpoint now returns a plain JSON payload containing `session_id`, `jira_ticket_id`, and `acceptance_criteria`. The workspace should transition directly from fetch into BDD generation using that response.
- Previous review strictly banned event thread freezing. Keep the UI responsive by relying on simple loading flags and lightweight state updates rather than verbose live logs.

### References
- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.5]
- [Source: _bmad-output/planning-artifacts/architecture.md#Frontend Architecture]

## Dev Agent Record

### Agent Model Used

gemini-2.5-pro

### Debug Log References
- Mapped `React.Context` session state cleanly into the left-side status panel and editor workflow without requiring transport-level streaming for Epic 1 ingest.
- Mobile Layout locked inside relative flex blocks overriding `<Editor/>` pointer properties to guarantee desktop-first constraints natively.
- Enforced Gherkin theme support in Monaco without extra NPM package abstractions except base monaco react. 

### Completion Notes List
- ✅ Implemented Application-Wide `SessionContext` bounding Session states including BDD content string and live logging matrices. 
- ✅ Implemented custom Gherkin colorization rules so BDD keywords remain clear and readable within the Monaco editor surface.
- ✅ Rendered side-by-side terminal + editor pipeline sandbox under dynamic `app/session/[sessionId]/page.tsx`.
- ✅ **Demo Flow Integration:** Reworked the pipeline page so the user can ingest a Jira ticket, trigger real BDD generation from the backend API, and populate the Monaco editor with generated Gherkin instead of placeholder sandbox text.
- ✅ **API Hook Integration:** Added an axios-based API client and TanStack Query mutation hook so frontend BDD generation now follows the architecture pattern rather than relying on mock editor state.
- ✅ **UI Refresh:** Replaced the dark terminal-style presentation with a lighter, friendlier demo layout across the landing page, workspace shell, progress panel, and Monaco editor theme to better support stakeholder walkthroughs.
- ✅ **Demo Simplification:** Replaced visible real-time SSE log output with clearer loading and status states, and shifted the visual design to a cleaner blue-only demo palette.
- ✅ **Typography Update:** Applied Poppins as the primary interface typeface and slightly increased global sizing to improve readability in walkthroughs and demos.
- ✅ **Transport Simplification:** The current session flow now uses a standard ingestion API request/response and retains the same spinner/progress UX without relying on SSE.
- ✅ Resolved Typescript compiler bounds where `@monaco-editor/react` types weren't aggressively throwing implicit any boundaries. Build completes successfully.
- ✅ [AI-Review] Secured Terminal auto-scroll hijacking by bounding automatic `scrollTop` shifts exclusively to bounds where the user intends to sit at the absolute bottom of the log stream.
- ✅ [AI-Review] Documented `package.json` and `package-lock.json` files affected by Monaco Editor install.

### File List
- `frontend/package.json` (modified)
- `frontend/package-lock.json` (modified)
- `frontend/src/lib/types/session.ts` (new)
- `frontend/src/lib/hooks/useBDDGenerate.ts` (new)
- `frontend/src/lib/api/client.ts` (new)
- `frontend/src/context/SessionContext.tsx` (new)
- `frontend/src/components/pipeline/TerminalProgressLog.tsx` (new)
- `frontend/src/components/pipeline/BDDEditorPanel.tsx` (new)
- `frontend/src/app/session/[sessionId]/page.tsx` (new)
- `frontend/src/app/layout.tsx` (modified)
