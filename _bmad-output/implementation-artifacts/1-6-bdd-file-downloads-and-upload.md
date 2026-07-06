# Story 1.6: bdd-file-downloads-and-upload

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **authenticated user** (stubbed as `DEV_USER_ID`),
I want to download the BDD content as a `.feature` file or CSV, and upload an existing `.feature` file leveraging Supabase Storage to replace generated BDD,
So that I can integrate BDD-AutoGen's output into my existing test tooling or bypass AI generation entirely.

## Acceptance Criteria

1. [x] **Given** BDD content exists in the `BDDEditorPanel`
2. [x] **When** the user clicks "Download .feature"
3. [x] **Then** the current editor content is downloaded as a UTF-8 `.feature` file named `{ticket_id}.feature` (FR16)
4. [x] **And given** the user clicks "Download CSV"
5. [x] **When** the BDD content is parsed into scenario rows
6. [x] **Then** a CSV file downloads with columns: Scenario, Given, When, Then, Expected Result (FR17)
7. [x] **And given** the user clicks "Upload .feature"
8. [x] **When** a valid `.feature` file is selected and uploaded
9. [x] **Then** the file is securely uploaded into a Supabase Storage bucket
10. [x] **And** the BDD editor content is replaced with the uploaded file's content, bypassing LLM generation entirely (FR18)

## Tasks / Subtasks

- [x] Task 1: Implement Client-Side File Downloads
  - [x] Add "Download .feature" button to the BDD pipeline UI.
  - [x] Wire a React handler that converts `SessionContext` BDD state to a Blob and triggers a native browser download (`a.download`).
  - [x] Add "Download CSV" button that parses the current Gherkin blob into CSV columns (Scenario, Given, When, Then, Expected Result) and triggers download.
- [x] Task 2: Configure Supabase Storage (Backend or Frontend Integration)
  - [x] Initialize Supabase client using anonymized keys or service keys.
  - [x] Create an upload route/handler to push `.feature` files into a designated Supabase Storage bucket (e.g., `bdd-uploads`). Ensure bucket is created or configured if necessary.
- [x] Task 3: Implement Upload Feature flow
  - [x] Add "Upload .feature" file input / button to pipeline UI.
  - [x] Create an upload process that physically uploads the file to Supabase Storage (per user request).
  - [x] Read the text payload of the uploaded file and inject it directly back into the `SessionContext`'s `bddContent` state.

## Dev Notes

- **Supabase Storage Mandate**: The user explicitly requested using Supabase Storage for file uploads. This means the `.feature` file shouldn't just be parsed client-side; it needs to be pushed to Supabase Storage during the upload lifecycle.
- **Client-Side Downloads**: Downloads do not inherently require backend processing. Since we already have the BDD content dynamically mapped inside `SessionContext`, client-side blob generation is hyper-efficient and architecturally safer than requesting binary streaming back from FastAPI.
- **Component Placement**: Action buttons (Upload, Download) should probably sit naturally within or above the `BDDEditorPanel` to keep context local.
- **Previous Story Context**: Story 1.5 now uses a simple request/response ingest flow with loader-based status feedback. Keep file actions decoupled from that request lifecycle so users can freely upload immediately.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#Story 1.6]
- [Source: _bmad-output/planning-artifacts/architecture.md#Frontend Architecture]

## Dev Agent Record

### Agent Model Used

gemini-2.5-pro

### Debug Log References

### Completion Notes List
- ✅ Task 1 implemented: Configured Client-Side blob manipulation exporting `.feature` contents locally into raw-text, and utilized naive chunking into a tabular Grid structure generating safe CSV formats leveraging JS string boundaries matching Gherkin specs.
- ✅ Task 2 configured: Deployed `supabase-js` initializing frontend stub instances pointed towards anonymous keys safely wrapped via standard JS imports bridging backend environment configs.
- ✅ Task 3 built: Attached hidden file-input mapped to Upload Lucide button. Converts loaded file contents implicitly into React tracking Contexts natively, whilst gracefully pushing payload buffers to Supabase Storage Bucket ("bdd-uploads").
- 🤖 **Review Feedback Fixed**:
  - **High:** Added `BDDEditorPanel.test.tsx` verifying component mounts, downloads, and upload errors resolving Red-Green-Refactor violations.
  - **Medium:** Added `uploadError` tracking state overlay UI ensuring Supabase bucket failures revert local session context gracefully instead of failing silently.

### File List
- `frontend/package.json` (modified)
- `frontend/package-lock.json` (modified)
- `frontend/vitest.config.ts` (new)
- `frontend/vitest.setup.ts` (new)
- `frontend/src/components/pipeline/BDDEditorPanel.tsx` (modified)
- `frontend/src/components/pipeline/__tests__/BDDEditorPanel.test.tsx` (new)
- `frontend/src/lib/supabase/client.ts` (new)
