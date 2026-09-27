# TODO

Things temporarily disabled in the frontend and commented out for later. Search
the codebase for `TODO(evaluation-runs)`, `TODO(training-opt-in)` and
`TODO(code-index)` to find every spot.

## Evaluation runs

The "Evaluation runs" section of the fine-tune page — saved batch comparisons
across several tickets — is hidden. The "Quick comparison" section below it
stays: that one is ad-hoc and saves nothing, so it still answers "is the
fine-tune any good on this ticket?" without the stored-run machinery.

- [`frontend/src/app/fine-tune/page.tsx`](frontend/src/app/fine-tune/page.tsx) —
  the `SavedRunsPanel` import and the whole `<section>` are commented out. The
  note that used to sit above the section (why saved runs come before the quick
  comparison) is folded into the same block comment, because a nested `*/`
  would have closed the JSX comment early.

Nothing else was touched. [`SavedRunsPanel.tsx`](frontend/src/components/evaluation/SavedRunsPanel.tsx)
is untouched and has no tests of its own, its hooks
(`useEvaluationRuns`/`useBatchComparison`/`useEvaluationReport`/`useDeleteRun`
in `useModelComparison.ts`) are still exported and still used by
`ModelComparisonPanel`, and the backend is unchanged — `/api/v1/evaluation/*`,
`evaluation_service.py` and the `evaluation_results` table all still work, so
runs saved before this are still there.

**To check later:**
- Decide whether saved evaluation runs are coming back, or should be removed
  properly (`SavedRunsPanel.tsx`, the four hooks above if nothing else uses
  them, and on the backend the evaluation routes, `evaluation_service.py`,
  `evaluation_report.py`, `evaluation_set.py` and the `evaluation_results`
  table).
- If re-enabling: uncomment the import and the section, then re-run the
  frontend test suite.

## Training opt-in toggle

The "Allow use for fine-tuning" switch is hidden from the session page. The
"Use fine-tuned model" switch beside it stays.

- [`frontend/src/app/session/[sessionId]/page.tsx`](frontend/src/app/session/%5BsessionId%5D/page.tsx) —
  the `TrainingOptInToggle` import and its use inside the toggle row are
  commented out. The row itself stays, because `FineTunedToggle` still lives
  there.

**Read this before leaving it hidden for long.** The switch was the only way a
user could *withdraw* consent. Its default is `true`
([`useTrainingOptIn.ts`](frontend/src/lib/hooks/useTrainingOptIn.ts), `DEFAULT`),
the value is per-browser in `localStorage`, and the backend ANDs it with the
deployment's `TRAINING_DATA_OPT_IN` policy — which also defaults to `true`
([`config.py`](backend/app/core/config.py#L164)). So with the control hidden:

- anyone who had already switched it off stays off, because their
  `localStorage` value survives;
- everyone else is opted in, with no way to change it from the UI.

The remaining lever is deployment-wide: set `TRAINING_DATA_OPT_IN=false` to
withhold consent for everyone. There is no per-user option while this is
hidden.

Nothing else was touched: `TrainingOptInToggle.tsx`, `useTrainingOptIn.ts` and
the backend's `_resolve_training_opt_in` are all unchanged, so restoring the
switch is just uncommenting two lines.

**To check later:**
- Decide whether per-session consent is coming back. If the answer is "no, the
  deployment policy is enough", say so somewhere durable — the consent story
  is currently spread across the hook, the config default and the
  `training_opt_in` columns on `bdd_files` and `training_datasets`.
- If re-enabling: uncomment the import and `<TrainingOptInToggle />`, then
  re-run the frontend test suite.

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
