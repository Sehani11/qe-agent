# Maintenance Record — Projects and Credential Scoping

**Date:** 2026-08-23
**Kind:** Architectural increment (new aggregate + schema + credential handling + UI), performed outside the story workflow
**Status:** ✅ Implemented. Backend 717 passed; frontend `tsc` clean, ESLint clean, 203 passed / 2 skipped.
**Migrations:** `8c4760c0942a_add_projects_and_scope_sessions` and `fc81d4a9e05c_scope_knowledge_sources_to_projects` (head) — **both applied 2026-08-23**
**Amends:** Stories 3.3, 3.5, 4.1, 4.2, 4.5, 4.6, 4.7, 5.5 (each carries a pointer to this record)

---

## 1. The gap

Every integration setting was deployment-wide: one Jira, one Confluence, one
GitHub PAT, one knowledge base. A single deployment could serve exactly one body
of work. Sessions had no grouping above `user_id`.

**Projects** are the container: a session belongs to one, and a project carries
the settings its sessions run against. Actions prepopulate from the project and
stay overridable, so it is a starting point rather than a lock.

## 2. Credentials are ENCRYPTED, not hashed

The original request was to hash them. That would not work, and the distinction
is load-bearing enough to record.

A password is only ever *verified*, so it is hashed — one-way, unrecoverable by
design. A Jira API token is *sent to Atlassian on every request*, so the
plaintext must come back out. Hashing these produces a database that looks
secure and an integration that can never authenticate.

`app/core/crypto.py` uses **Fernet** (AES-128-CBC + HMAC-SHA256), keyed by
`CREDENTIAL_ENCRYPTION_KEY`, held outside the database.

- **Fernet over raw AES** because it authenticates the ciphertext: a tampered row
  fails loudly instead of decrypting to garbage that is then sent to a third
  party as a credential.
- **A missing key refuses to run** rather than generating one. A per-process
  fallback key would encrypt happily and be unable to read anything back after a
  restart — every stored credential silently lost.
- **`encrypt("") == ""`**, so "not configured" stays distinguishable from
  "configured as blank" without a second column.

```bash
python -c "from app.core.crypto import generate_key; print(generate_key())"
```

Losing or rotating the key makes every stored credential unreadable. That
surfaces as a clear configuration error, not a silent auth failure.

### Nothing is ever serialized back

A stored token is write-only from the API's point of view. `ProjectResponse`
carries `has_jira_token: bool`, never a value — a masked string would still have
to be sent in order to be masked. Token fields follow a three-state convention:

- **omitted** → keep what is stored
- **`""`** → clear it
- **text** → replace it

So a form editing the Jira section cannot blank the GitHub token, and clearing
remains reachable.

## 3. Credential resolution is all-or-nothing per integration

`app/services/project_config_service.py` is the only place the project/environment
decision is made, so no service knows projects exist.

A project either supplies its **whole** credential set or none of it. Field-by-
field fallback looks tidier and is dangerous: it would pair a project's base URL
with the environment's token — an authentication failure at best, and with two
projects on one Atlassian tenant, a credential crossing a boundary it was never
meant to cross.

Two deliberate exceptions:

- **`github_repo` is read even when the token falls back.** A repository name is
  not a credential, so there is nothing to cross-contaminate, and a project
  naming a public repo without storing a token is a normal setup.
- **A credential that cannot be decrypted is treated as absent**, not raised. A
  rotated key would otherwise take down every request for that project; falling
  back keeps the app usable, and the settings page still shows the credential as
  configured, which is where the mismatch gets fixed.

Errors name *where* to fix things — "not configured for this project" vs "set
them in the server environment" — because "Jira is not configured" is
unactionable when there are two places it could live.

## 4. LLM keys are system config; the model choice is per project

`projects.llm_provider` / `llm_model` hold a **default** the nav picker
prepopulates from. The API keys stay in the environment: they are billing
credentials for the deployment, not per-project integration secrets, and putting
them per project would let every project spend against a different account with
no operator visibility.

The project supplies a default, not a lock — an explicit choice in the browser
wins, so switching projects moves someone who has never touched the picker
without overriding someone who has.

## 5. Knowledge is namespaced per project

`{user_id}:knowledge` → `{user_id}:{project_id}:knowledge`, via a single
`knowledge_namespace()` helper. Namespaces are no longer built inline — that is
what let seven call sites drift into agreement.

`knowledge_sources.project_id` records which namespace holds a row's vectors;
without it the sources list would offer deletes that reach into a namespace the
row does not live in.

`project_id=None` returns the pre-projects namespace. That is not a convenience
fallback — it is the only route to knowledge ingested before this change, and
`backend/scripts/migrate_knowledge_vectors.py` copies it forward. A test pins
that a real project can never collide with it, otherwise the separation would be
cosmetic.

Ticket Q&A namespaces (`{user_id}:{session_id}`) are unchanged: sessions already
belong to projects, so they are transitively scoped, and changing them would
strand existing vectors for no benefit.

## 6. Session scoping

`sessions.project_id` is **NOT NULL**. `ensure_project()` therefore guarantees
one: the requested project → the caller's oldest → a freshly created
"Project-1". An unowned id falls through rather than erroring — it is
indistinguishable from a stale id left in another browser, and refusing would
strand the user with no way forward.

`GET /api/v1/sessions` filters on `project_id`, applied to **both the count and
the page**. The total drives how many pages the client offers, so filtering only
the page would leave the pager sized for every project and offer pages that come
back empty.

## 7. Changes

**Backend**
- `app/core/crypto.py`, `app/core/config.py` (`CREDENTIAL_ENCRYPTION_KEY`).
- `app/models/project.py`; `project_id` on `session.py` and `knowledge_source.py`.
- `app/schemas/project.py`, `app/api/v1/projects.py` — CRUD. Another user's
  project returns **404, not 403**: a 403 confirms the id exists, which is a
  membership oracle. Deleting the caller's only project is refused (409).
- `app/services/project_config_service.py` — resolution + `ensure_project`.
- `jira_service` / `confluence_service` take a credentials object; neither reads
  `settings.*` for credentials any more.
- `knowledge_service` — `knowledge_namespace()`, project threaded through
  ingest/query/delete.
- `app/api/v1/config.py` — operator policy the UI needs to render honestly.
- `scripts/migrate_knowledge_vectors.py`.

**Frontend**
- `lib/stores/projectStore.ts` — **Zustand**, the first store in the app. It
  holds only the id: the project's data is server state owned by React Query,
  and a second copy here would be the stale one the UI reads.
- `lib/hooks/useProjects.ts` — `useSyncActiveProject()` corrects a stored id that
  no longer resolves (first visit, deleted elsewhere, another account's id).
  Called from `ProjectSwitcher`, which is in the nav, so it runs on every page.
- `components/project/ProjectSwitcher.tsx`, `app/settings/page.tsx`.
- Secret fields never prefill; empty means "keep what is stored".
- Project-scoped queries put the project in the **query key**, so switching
  refetches instead of serving the previous project's rows from cache.

## 8. Migrations

`8c4760c0942a` creates `projects`, adds `sessions.project_id` nullable,
backfills a `Project-1` per distinct `user_id`, then makes the column NOT NULL —
the constraint can only be enforced once every row has a value.

`fc81d4a9e05c` adds `knowledge_sources.project_id` and backfills to the same
Project-1. It stays **nullable**, unlike the sessions column: a source can be
written by a path with no project, and an ingestion that failed before embedding
leaves a row worth keeping. Forcing a project onto those would invent an
association the vectors do not have.

**Vectors are not moved by a migration.** Pinecone is outside the database and
cannot be updated in the same transaction. `migrate_knowledge_vectors.py`
**copies** rather than moves — re-running is idempotent (deterministic vector
ids overwrite), and a mistake is recoverable without re-ingesting.

### Rollout, 2026-08-23

```
alembic_version : fc81d4a9e05c
projects        : 1 (Project-1)
sessions        : 37 total, 0 without a project
knowledge_sources: 20, all Project-1
vectors copied  : 9   (source namespace left intact)
```

Verified via `information_schema` and `describe_index_stats`, not just the
alembic log. 20 source rows produced 9 vectors because re-ingesting a page
overwrites by deterministic id — expected, not loss.

**Delete the pre-projects namespace by hand** once the app is confirmed
answering from the new one.

## 9. Two migration mistakes worth not repeating

- **The first revision id was hand-picked (`a1b2c3d4e5f6`) and collided** with
  `add_verification_source_to_results`, which also had the same `down_revision`.
  Alembic warned "present more than once" and treated the DB as already at head,
  so nothing ran. **Generate revision ids; do not invent them.**
- **The migration created `ix_projects_user_id` twice** — once via `index=True`
  on the column, once via an explicit `op.create_index`. Transactional DDL rolled
  the whole thing back cleanly, verified before retrying.

## 10. Tests

- `test_crypto.py` — round trip (the property hashing cannot provide), ciphertext
  salted per call, empty stays empty, a different key fails loudly, tampering
  rejected, a missing key refuses rather than inventing one.
- `test_projects.py` — no response contains a secret; omitted token left alone;
  empty clears; another user's project is 404; last project protected; sessions
  filtered on both count and page.
- `test_project_credentials.py` — all-or-nothing per integration, in both
  directions; repo kept when the token falls back; an undecryptable token is
  treated as absent.
- `test_knowledge_namespace.py` — two projects differ; two users differ; stable
  for one project; UUID and string forms agree; a project never collides with
  the pre-projects namespace.
- `projectStore.test.ts`, `useProjects.test.tsx`.

## 11. Still open

- The **`user_credentials` table** named in `architecture.md` was never built;
  credentials live on `projects`. The architecture doc is corrected in this pass.
- Project **membership is single-user**. `projects.user_id` is the owner and
  there is no sharing model.
