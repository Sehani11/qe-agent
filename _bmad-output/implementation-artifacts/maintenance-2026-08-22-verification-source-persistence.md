# Maintenance Record — Verification Source Persistence

**Date:** 2026-08-22
**Kind:** Small full-stack increment (schema + API + UI), performed outside the story workflow
**Status:** ✅ Implemented. Backend 620 passed, Ruff introduces no new violations; frontend `tsc` clean, ESLint clean, 156 passed / 2 skipped.
**Migration:** `a1b2c3d4e5f6_add_verification_source_to_results` (head; revises `f6b7c8d9e0a1`) — **applied 2026-08-22**
**Amends:** Stories 2.4, 2.5, 3.3 (each carries a pointer to this record)

---

## 1. The gap

A verification run is scoped to a source the user picks — a repository URL, a PR
URL, or a newline-separated list of file URLs — but nothing persisted it. Two
consequences on a revisited session:

- The stored verdicts said nothing about **what they had been checked against**.
  A `fail` could mean "not implemented" or "you pointed it at the wrong repo",
  and the row could not tell you which.
- The workspace could not restore the source field, so re-running a past session
  meant retyping the URL from memory.

`github_links` on the verdict does **not** cover this: those are links the LLM
cited for one scenario's evidence, not the source the run was scoped to. A run
that found nothing has no links at all.

## 2. Where it is stored, and why not on the session

Two nullable columns on **`verification_results`**:

| Column | Type | Holds |
|---|---|---|
| `verification_mode` | `String(20)` | `full_repo` \| `exact_files` \| `pull_request` |
| `github_input` | `Text` | exactly what the user submitted |

Per **row**, not per session. Verification results accumulate — nothing deletes
earlier rows — so one session can hold several runs against different sources.
Hanging the source off the session would make the newest run silently relabel
every older verdict, which is worse than not recording it at all.

`Text` rather than `String`: exact-files mode submits one URL per line and a
repo can contribute many, so there is no meaningful length bound.

Nullable with no backfill, matching the `jira_ticket_url` precedent: rows written
before these columns genuinely have no recorded source, and an empty string would
be indistinguishable from a run against empty input. **Every consumer must
tolerate `null`** — that is the shape of all existing production rows.

## 3. Changes

**Backend**
- `app/models/verification_result.py` — the two columns.
- `alembic/versions/a1b2c3d4e5f6_...` — add/drop. RLS is not touched here; it
  lives in `backend/supabase/migrations/001_rls_policies.sql`, applied manually.
- `app/schemas/session.py` — `StoredVerificationResult` exposes both, defaulting
  to `None`.
- `app/services/verification_service.py` — `build_verification_result` takes
  `mode` / `github_input`. They default to `None` **only** so the signature stays
  compatible with callers predating the columns; the live path always passes them.
- `app/services/agentic_verification_service.py` — passes the run's own `mode`
  and `github_input` into the builder.

**Frontend**
- `lib/types/session.ts` — `StoredVerificationResult` gains both as optional.
- `app/session/[sessionId]/page.tsx` — on restore, walks the results **newest
  first** and takes the first row that actually recorded a source, then restores
  `verificationMode` and `githubInput`. Newest-first matters: the endpoint
  returns oldest first, and older rows carry `null`.
- `components/pipeline/VerificationResultsPanel.tsx` — a "Verified against"
  block above the results, listing each URL as a link. Splits on `/\r?\n/` so a
  list pasted from Windows does not render as one run-on line. Hidden entirely
  when no source was recorded, so old sessions show nothing rather than an empty
  frame.

## 4. Tests

- `test_verification_service.py` — the builder records the source, and still
  builds a valid row when it is omitted.
- `test_sessions.py` — the endpoint surfaces both fields (multi-line input round
  trips), and reports `null` for rows without them.
- `VerificationResultsPanel.test.tsx` — the block renders with a link, lists
  every URL in exact-files mode, and is absent when no source was recorded.

The existing `_make_verification_result` mock had to set both fields explicitly.
It is a `MagicMock`, so an unset attribute returns a child mock, which Pydantic
rejects as "not a valid string" — the same trap Story 4.4 hit when it added
`rag_context`. **Any future nullable column on this table needs the same line.**

## 5. Rollout

Applied 2026-08-22 against the Supabase instance, stepping `f6b7c8d9e0a1` →
`a1b2c3d4e5f6`. Both columns verified present and nullable via
`information_schema`, not just the alembic log.

Existing verdict rows were not backfilled and report `null`, so past sessions
recorded before this show no "Verified against" block. That is the intended
degradation, not a bug to fix later: there is no way to recover which source
those runs used.
