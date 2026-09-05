# Maintenance Record — Delete All Knowledge Sources

**Date:** 2026-08-22
**Kind:** Small full-stack increment (API + UI), performed outside the story workflow
**Status:** ✅ Implemented. Backend 625 passed, Ruff introduces no new violations; frontend `tsc` clean, ESLint clean, 159 passed / 2 skipped.
**Schema:** none — no migration needed
**Amends:** Story 4.7 (carries a pointer to this record)

---

## 1. What

Clearing a knowledge base meant deleting sources one at a time. Added a bulk
path: `DELETE /api/v1/knowledge/sources` (no id), surfaced as a **Delete all**
button in the Ingested sources header.

## 2. Not a loop over the per-source delete

The obvious implementation — iterate the list and call the existing delete — was
rejected for a substantive reason, not just N+1 requests.

`delete_source_vectors` targets vectors by the id prefix
`{source_type}_{source_ref}_chunk_`. Rows with **no `source_ref`** (ingested
before Story 4.7) cannot be targeted at all: Story 4.7 logs a warning and leaves
their vectors in place. Orphans from an earlier failed deletion are equally
unreachable. A loop would therefore leave a "cleared" knowledge base still
answering RAG and chat from vectors nothing could remove.

The bulk path instead wipes the user's whole namespace:

```
index.delete(delete_all=True, namespace=f"{user_id}:knowledge")
```

One call, and it collects exactly the vectors the per-source path cannot reach.
That makes **Delete all a genuine repair** for a knowledge base with untargetable
leftovers, not merely a convenience.

## 3. Behaviour

- **Scoped by `current_user` in the query**, so there is no way to ask for
  another user's rows and no per-row ownership check is needed.
- **Vectors first, then rows**, matching the single-source order: a partial
  failure leaves rows that can be retried, rather than rows pointing at vectors
  that are already gone.
- **Vector wipe is best-effort and never raises**, same contract as
  `delete_source_vectors` — a Pinecone hiccup must not block removing the rows.
  Pinecone 404s a namespace that never existed, which is success here.
- **Nothing to delete returns `{"deleted": 0}` with 200**, not 404. The caller
  asked for an empty knowledge base and that is the end state. It also skips the
  vector call and the commit entirely.

## 4. UI

`Delete all` sits in the Ingested sources header, `variant="destructive"`, behind
its own `ConfirmModal` with its own `pendingDeleteAll` state — deliberately
separate from `pendingDelete` so the two confirmations can never be open at once
or be mistaken for each other.

**Hidden entirely when the list is empty.** An action that cannot do anything is
noise, and a stray destructive control invites mis-clicks.

The confirmation names the count (*"all 2 sources"*) rather than just "all" —
"all" is not a quantity, and the number is the thing that makes the consequence
concrete. It also states what is lost: verification and project Q&A fall back to
code only.

The trigger and the modal's confirm **share the label "Delete all"** on purpose —
an action keeps its name from control through to confirmation. That makes
`getByRole("button", {name: "Delete all"})` ambiguous, so the test scopes the
confirm click with `within(screen.getByRole("dialog"))`. Do not "fix" the
ambiguity by renaming one of them.

## 5. Files

**Backend** — `app/api/v1/knowledge.py` (route, declared before
`/sources/{source_id}`), `app/services/knowledge_service.py`
(`_delete_namespace_sync`, `delete_all_source_vectors`,
`delete_all_knowledge_sources`).

**Frontend** — `lib/hooks/useKnowledge.ts` (`useDeleteAllKnowledgeSources`,
invalidates `["knowledge","sources"]`), `components/knowledge/KnowledgeBasePanel.tsx`.

## 6. Tests

`test_knowledge_sources.py` — every row removed with a single namespace wipe;
empty base is a 200 with no vector call and no commit; the query is user-scoped;
the wipe swallows backend errors; no-op without Pinecone configured.

`KnowledgeBasePanel.test.tsx` — button hidden when empty; confirm triggers the
mutation and the dialog names the count; cancel does nothing.

The `useKnowledge` mock in that test file had to gain
`useDeleteAllKnowledgeSources` — an unmocked hook returns `undefined` and every
test in the file fails on the destructured `.isPending`. **Any new hook this
panel consumes needs a line there.**
