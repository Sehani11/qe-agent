# TODO

Things temporarily disabled in the frontend and commented out for later. Search
the codebase for `TODO(fine-tune)` and `TODO(code-index)` to find every spot.

## Fine-tune feature

Disabled for now — the whole `/fine-tune` workflow (training data upload,
training runs, saved evaluation runs, quick comparison, wipe) and the two
per-session toggles that feed it.

- [`frontend/src/components/layout/AppNav.tsx`](frontend/src/components/layout/AppNav.tsx) —
  "Fine tune" nav link commented out.
- [`frontend/src/app/fine-tune/page.tsx`](frontend/src/app/fine-tune/page.tsx) — page now renders
  a "temporarily unavailable" placeholder; the real content (training data,
  training runs, evaluation runs, quick comparison, wipe panel) is commented
  out below it, ready to restore.
- [`frontend/src/app/session/[sessionId]/page.tsx`](frontend/src/app/session/%5BsessionId%5D/page.tsx) —
  `FineTunedToggle` and `TrainingOptInToggle` imports and rendering commented
  out. `code_index_enabled` note added near the verification request (see
  below).

**To check later:**
- Decide whether the fine-tune feature is coming back, or should be removed
  properly (including `TrainingDataPanel`, `TrainingRunsPanel`,
  `WipeTrainingDataPanel`, `ModelComparisonPanel`, `SavedRunsPanel`,
  `useFineTunedStatus`, `useTrainingData`, `useTrainingOptIn`,
  `useModelComparison`, and their tests).
- If re-enabling: uncomment the nav link, the page content, and the two
  toggles on the session page, then re-run the frontend test suite.

## Code index

Disabled for now, both halves: the opt-in switch on the verification panel that
lets a session use the index, and the card on the Knowledge page that builds it.

- [`frontend/src/components/pipeline/GitHubSourceSelector.tsx`](frontend/src/components/pipeline/GitHubSourceSelector.tsx) —
  the `useCodeIndexStatus` import, the `codeIndexEnabled`/`setCodeIndexEnabled`
  context destructuring, the derived `hasCodeIndex`/`codeIndexHint`, and the
  "Use code index" `<Switch>` are all commented out.
- [`frontend/src/components/pipeline/GitHubSourceSelector.test.tsx`](frontend/src/components/pipeline/GitHubSourceSelector.test.tsx) —
  the four tests covering that switch are commented out to match.
- [`frontend/src/app/session/[sessionId]/page.tsx`](frontend/src/app/session/%5BsessionId%5D/page.tsx) —
  `code_index_enabled` is still sent in the verify request, but always `false`
  now that nothing can flip it on.

- [`frontend/src/components/knowledge/KnowledgeBasePanel.tsx`](frontend/src/components/knowledge/KnowledgeBasePanel.tsx) —
  the whole "Code index" card (repository / branch fields, Index code button,
  status line) is commented out, along with its state, `handleIndexCode`, and
  the `useCodeIndexStatus`/`useIndexCode`/`useProject`/`useActiveProjectId`/
  `Code2` imports it was the only user of.
- [`frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx`](frontend/src/components/knowledge/__tests__/KnowledgeBasePanel.test.tsx) —
  the seven "code index card" tests are parked in a block comment. The
  `useKnowledge` mock still exports both code-index hooks, so restoring them is
  just uncommenting (plus the `codeIndexOptions` capture in the mock).

Nothing was removed on the backend: `/api/v1/knowledge/index/code` and the
`code_index_enabled` flag still work, so an index built before this still
exists and the feature can be switched back on from the UI alone.

**To check later:**
- Decide whether code index is coming back, or should be removed properly
  (frontend hooks `useCodeIndexStatus`/`useIndexCode` in `useKnowledge.ts`, and
  on the backend `code_index_service.py` plus the index/code route).
- If re-enabling: uncomment the blocks in `GitHubSourceSelector.tsx` and
  `KnowledgeBasePanel.tsx` and their matching tests, then re-run the frontend
  test suite.
