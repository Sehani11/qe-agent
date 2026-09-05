# Story 6.6: Training-Data Opt-Out Control

> **Amended 2026-08-23** ([maintenance record](maintenance-2026-08-23-training-data-loop.md)): `TRAINING_DATA_OPT_IN` is now the deployment POLICY rather than the whole story: each BDD write path accepts a per-request `training_opt_in`, and the two are ANDed so a client can only ever narrow it. The UI exposes this as a switch beside Generate BDD, disabled (via `GET /api/v1/config`) when the deployment forbids training outright.


Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As an **operator**,
I want an environment switch that marks captured data as excluded from model training,
so that a deployment can keep its session history for debugging without that content ever feeding a fine-tune.

## Acceptance Criteria

1. **Given** `TRAINING_DATA_OPT_IN` is `false`
   **When** a `bdd_files` row is persisted by **any** of the three write paths
   **Then** the row is still written in full — history and debugging are unaffected — but is flagged as excluded from training

2. **And** when `TRAINING_DATA_OPT_IN` is `true` (the default, preserving today's behaviour) rows are flagged as usable for training

3. **And** the flag is stamped **at write time**, never evaluated at dataset-build time — flipping the env var later must not retroactively reclassify existing rows

4. **And** `training/build_dataset.py --from-db` excludes flagged-out rows **and reports how many it skipped**, so an all-opted-out database is never mistaken for a missing or empty one

5. **And** an Alembic migration adds the column as `NOT NULL` with a server default of true, so the 10 existing rows — captured under today's always-on behaviour — are correctly marked opted-in

6. **And** the env var is wired into all four surfaces: `.env.example`, `docker-compose.yml`, and `BACKEND_ENV_KEYS` in both `cfn/setup-infra.sh` and `cfn/deploy-backend.sh`

7. **And** Pytest tests verify both settings produce the correct flag on all three write paths, and that the migration's default marks pre-existing rows opted-in

## Context & Critical Background

### ⚠️ The epic's AC missed the most important write path

Epic 6.6 says *"when a BDD generation or an editor correction is persisted"* — it does not mention **uploads**. That omission would make the control nearly useless, because `build_dataset.py --from-db` **defaults to `--db-source uploaded`**: uploaded `.feature` files are human-authored and are the single most valuable training source in the database today. An opt-out that leaves the primary source opted-in is not an opt-out.

**All three write sites must be flagged** — [bdd.py:72](backend/app/api/v1/bdd.py#L72) `generated`, [bdd.py:164](backend/app/api/v1/bdd.py#L164) `edited`, [bdd.py:235](backend/app/api/v1/bdd.py#L235) `uploaded`. AC1 above is worded to cover all three; treat the epic text as the narrower, incorrect version.

### Why stamped at write time, not filtered at build time

The cheap implementation is to skip the column entirely and have `build_dataset.py` read the env var when it runs. Do not do that. Two reasons:

- **Consent belongs to the moment of capture.** If the switch is read at build time, turning it off today would exclude data captured while it was on — and turning it back on would silently re-include data captured while it was off. Neither matches what an operator means by "don't train on our data".
- **The builder runs somewhere else.** It can be run from a laptop, a notebook, or CI, each with a different `.env`. A per-row flag travels with the data; an env var read at build time does not.

### The builder's WHERE clause is string-concatenated — read before editing

[`collect_db_rows`](training/build_dataset.py) builds SQL by appending:

```python
query = "SELECT id, content, source FROM bdd_files"
if source != "all":
    query += " WHERE source = :source"
query += " ORDER BY created_at DESC"
```

Appending a second `WHERE` produces `... WHERE source = :source WHERE training_opt_in ...` — invalid SQL. Collect conditions into a list and join with `AND`, then append a single `WHERE` clause.

### Reuse map

| Piece | Location |
|---|---|
| Settings pattern | `app/core/config.py` — BDD block, lines 33-44 |
| Migration style | `alembic/versions/b8c9d0e1f2a3_*.py` (Story 6.4) — current head |
| Write sites | `app/api/v1/bdd.py` lines 72, 164, 235 |
| Builder DB source | `training/build_dataset.py::collect_db_rows` |
| Skip-counter pattern | `training/build_dataset.py::Stats` — add a field, `render()` prints it automatically |
| Env wiring precedent | `FINE_TUNED_MODEL_TIMEOUT_SECONDS` (Story 6.1) — same four files |
| Backend test patterns | `tests/test_bdd.py` |

## Tasks / Subtasks

- [x] **Task 1: Setting + schema** (AC: 1, 2, 5)
  - [x] Add `training_data_opt_in: bool = True` to [config.py](backend/app/core/config.py) in the BDD block, with a comment stating that it is stamped per row at write time
  - [x] New Alembic revision, `down_revision = "b8c9d0e1f2a3"` (current head — the DB is live now, so confirm with `alembic current`)
  - [x] Add `training_opt_in` to `bdd_files` as `sa.Boolean(), nullable=False, server_default=sa.true()` — the server default backfills existing rows in one step and keeps raw INSERTs safe. Keep the server default in place; do not drop it after backfill
  - [x] `downgrade()` drops the column
  - [x] Mirror on [models/bdd_file.py](backend/app/models/bdd_file.py) as `Mapped[bool]`

- [x] **Task 2: Stamp all three write paths** (AC: 1, 2)
  - [x] Set `training_opt_in=settings.training_data_opt_in` at [bdd.py:72](backend/app/api/v1/bdd.py#L72) (generated), [bdd.py:164](backend/app/api/v1/bdd.py#L164) (edited) and [bdd.py:235](backend/app/api/v1/bdd.py#L235) (uploaded)
  - [x] Read `settings` at call time, not import time, so tests can monkeypatch it
  - [x] No response shape changes — this flag is internal and must not appear in any API response

- [x] **Task 3: Builder filters and reports** (AC: 4)
  - [x] Refactor `collect_db_rows`'s SQL to accumulate conditions in a list joined by `AND` (see the trap above)
  - [x] Exclude `training_opt_in = false`
  - [x] Add `opted_out: int = 0` to `Stats`; count excluded rows and surface the count. `Stats.render()` iterates `__dict__`, so a new field prints with no further change
  - [x] If every candidate row was excluded, say so explicitly rather than reporting an empty corpus — the "no usable feature documents" message must not be the only signal

- [x] **Task 4: Env wiring** (AC: 6)
  - [x] `TRAINING_DATA_OPT_IN=true` in [.env.example](.env.example) with a one-line explanation
  - [x] `docker-compose.yml` passthrough with `:-true` default
  - [x] `BACKEND_ENV_KEYS` in **both** [cfn/setup-infra.sh](cfn/setup-infra.sh) and [cfn/deploy-backend.sh](cfn/deploy-backend.sh) — the two arrays are kept in sync by hand; missing one breaks prod

- [x] **Task 5: Tests** (AC: 7)
  - [x] `tests/test_bdd.py`: with the setting true and false, assert the persisted row's `training_opt_in` on **all three** endpoints (generate, save, upload)
  - [x] Assert the flag never appears in any response body
  - [x] Migration default: verify the model/column default is true so pre-existing rows are opted in

- [x] **Task 6: Apply migration + regression**
  - [x] `uv run alembic upgrade head` — **the database is live again**, so unlike Story 6.4 this must actually be applied and verified this time
  - [x] Confirm the 10 existing rows come out `training_opt_in = true`
  - [x] `uv run --directory backend pytest -q` → **300 baseline** + new, zero regressions
  - [x] `ruff check` — zero new errors (pre-existing: 2 E501 in `config.py`, 5 B008 + 4 E501 in `bdd.py`, 1 E501 in `test_bdd.py`)

## Dev Notes

### Schema choice

`NOT NULL` with `server_default=true` rather than a nullable column, because "unknown consent" is not a meaningful state — every row is either usable or not. The server default also means a row inserted by anything that predates this change still lands in a valid state.

Filter on `training_opt_in = false` for exclusion (not `IS NOT TRUE`), since `NOT NULL` guarantees there are no nulls to reason about.

### Anti-patterns for this story

- ❌ Don't read the env var inside `build_dataset.py` to decide inclusion — that is exactly the design this story rejects (see above)
- ❌ Don't skip the `uploaded` write path because the epic text omits it
- ❌ Don't expose `training_opt_in` in any API response — it is operator configuration, not user data
- ❌ Don't add a second `WHERE` to the builder's query string
- ❌ Don't touch `bdd_service.py`, `bdd_model/**` (Story 6.1) or the upload/save behaviour itself (Story 6.4)

### Project Structure Notes

**Backend — modify:** `app/core/config.py`, `app/models/bdd_file.py`, `app/api/v1/bdd.py`, `tests/test_bdd.py`. **Create:** one `alembic/versions/*.py`.
**Training — modify:** `training/build_dataset.py` (SQL conditions + `Stats.opted_out`), `training/README.md` (document the flag).
**Config — modify:** `.env.example`, `docker-compose.yml`, `cfn/setup-infra.sh`, `cfn/deploy-backend.sh`.
**No frontend work in this story.**

### Testing Standards

`pytest` + `pytest-asyncio` (`asyncio_mode="auto"`), DB mocked with `AsyncMock` per `tests/test_bdd.py`. Monkeypatch the setting via `app.api.v1.bdd.settings.training_data_opt_in` — note this requires `settings` to be read at call time, which Task 2 specifies. Note `training/` sits outside the backend package and is **not** covered by `ruff check app tests`; keep it clean by eye.

### Environment note

Alembic connects via `DIRECT_DATABASE_URL` (port 5432), not the transaction pooler on 6543 — that split is why DDL applies cleanly. Ad-hoc verification scripts must pass `connect_args={"statement_cache_size": 0}` or they hit `DuplicatePreparedStatementError` through PgBouncer; the app's engine already does this.

### References

- [Source: _bmad-output/planning-artifacts/epics.md] — Epic 6, Story 6.6 (note the missing `uploaded` path, corrected in AC1)
- [Source: _bmad-output/planning-artifacts/architecture.md] — Training-Data Governance rule
- [Source: backend/app/api/v1/bdd.py:72,164,235] — the three write sites
- [Source: training/build_dataset.py] — `collect_db_rows` SQL composition and `Stats`
- [Source: _bmad-output/implementation-artifacts/6-4-training-data-capture.md] — prior story: migration verification, baselines, PgBouncer note

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Backend **300 → 306 passed** (+6), zero regressions. Lint on this story's files: 13 → **12 after fixing the one E501 I introduced** — same as the pre-existing baseline, so zero net new.
- Red-green observed: all 5 write-path tests failed first with `ImportError: import error in app.api.v1.bdd.settings` — `settings` was not yet imported into the module, which is exactly what the monkeypatch target requires.
- Migration applied for real this time (the DB is live): `b8c9d0e1f2a3` → **`c9d0e1f2a3b4`**. Verified `training_opt_in` is `boolean`, `NOT NULL`, `default true`, and that **all 10 existing rows** came out `true`.
- Builder SQL validated against the live database — the rewritten `WHERE` composition executes cleanly with `--from-db`.
- Exclusion counting verified **inside a transaction that was rolled back**: with the 2 uploaded rows temporarily flagged out, the include query returned 0 and the exclusion counter returned 2. Confirmed afterwards that real data was unchanged (`[(True, 10)]`).

### Completion Notes List

- **AC1/AC2 — all three write paths stamped.** `generated`, `edited` and `uploaded` now carry `training_opt_in` from `settings.training_data_opt_in`. The epic text only mentioned generation and corrections; including `uploaded` was essential because it is `build_dataset.py`'s **default** DB source, so omitting it would have left the most valuable data opted-in regardless of the setting.
- **AC3 — write-time stamping.** `settings` is read inside the handlers, so the value captured is the one in force at that moment. Changing the env var later cannot reclassify existing rows.
- **AC4 — builder filters and reports.** `collect_db_rows` now accumulates conditions into a list joined with `AND` (a second appended `WHERE` would have been invalid SQL), excludes `training_opt_in = false`, and counts exclusions into `Stats.opted_out`. When *everything* was excluded, the failure message says so explicitly instead of reusing the generic "no usable feature documents" text.
- **AC5/AC6 — migration and wiring.** Column added `NOT NULL server_default=true`, so existing rows backfill in one step and raw INSERTs stay valid. Env var wired into all four surfaces including both hand-synced `cfn/` arrays.
- **AC7 — 6 tests.** Both settings across all three endpoints, plus an assertion that `training_opt_in` never appears in any response body (it is operator configuration, not user data), plus a check that the column default marks pre-existing rows opted-in.
- **Scope held.** No frontend work. No changes to `bdd_service.py`, `bdd_model/**`, or upload/save behaviour itself.

### ⚠️ Finding: your usable training corpus is currently zero

Running the builder against the live database surfaced something worth acting on before Story 6.5:

```
seen 2 · opted out 0 · incomplete steps 2 · kept 0
```

Both `uploaded` rows — the only human-authored Gherkin in the database — **fail the quality filter** because their scenarios lack a complete Given/When/Then. So the effective DB corpus is not "2 examples", it is **0**. This is not a defect in this story; it strengthens the case for Story 6.7 (manual upload) and for sourcing external `.feature` corpora before attempting 6.5.

### File List

**Backend — created:**
- `backend/alembic/versions/c9d0e1f2a3b4_add_training_opt_in_to_bdd_files.py` — `training_opt_in` NOT NULL, server default true

**Backend — modified:**
- `backend/app/core/config.py` — `training_data_opt_in: bool = True` with write-time rationale
- `backend/app/models/bdd_file.py` — `training_opt_in` column
- `backend/app/api/v1/bdd.py` — `settings` import; flag stamped on all three write paths
- `backend/tests/test_bdd.py` — 6 tests

**Training — modified:**
- `training/build_dataset.py` — `AND`-joined WHERE conditions, opt-out exclusion, `Stats.opted_out`, all-excluded messaging
- `training/README.md` — documented the flag and the exclusion counter

**Backend — created (review follow-up):**
- `backend/tests/test_build_dataset.py` — 11 tests covering the consent filter

**Config / deployment — modified:**
- `.env.example`, `docker-compose.yml`, `cfn/setup-infra.sh`, `cfn/deploy-backend.sh` — `TRAINING_DATA_OPT_IN`

## Senior Developer Review (AI)

**Date:** 2026-08-08 · **Outcome:** Changes Requested → **all findings resolved** · **Reviewer model:** claude-opus-5

**Git vs File List:** 0 discrepancies. **Task audit:** all `[x]` verified — `grep` confirms exactly three `BddFile(` construction sites exist and all three are flagged, so AC1 is genuinely complete rather than merely claimed.

### Action Items

- [x] **[High] H1 — The builder's DB engine lacked the PgBouncer guard.** `collect_db_rows` created its engine with no `connect_args`, while [database.py:19-21](backend/app/core/database.py#L19-L21) sets `statement_cache_size: 0` precisely because Supabase's transaction pooler rejects prepared statements — and the builder now issues two queries per connection. It happened to work in testing, which is the dangerous part: the failure is intermittent, and **this exact error was hit earlier in the same session** by an ad-hoc script missing the same argument. A corpus build dying halfway is a poor way to rediscover it. **Fixed:** engine now passes `statement_cache_size: 0`, with a test asserting the argument is present.
- [x] **[High] H2 — The enforcement point had zero automated tests.** `training/` contained no tests at all. The 6 original tests covered *stamping* (does a row get the right flag) but nothing covered *filtering* (does the builder actually exclude flagged rows) — so the entire privacy guarantee rested on one hand-written SQL string that no test exercised. A regression there would silently enlarge the corpus, and a bigger corpus looks like success. **Fixed:** extracted the query construction into a pure `build_db_queries()` and added `backend/tests/test_build_dataset.py` — 11 tests covering exclusion across every `--db-source`, exclusion surviving `--limit`, single-`WHERE` correctness, parameterisation rather than interpolation, the exclusion counter, and the reporting output. Placed under `backend/tests` so it runs with the normal `pytest` command rather than needing a separate invocation nobody would remember.
- [x] **[Med] M1 — The control failed open.** `training_opt_in` had a DB `server_default` of true and no Python-side default, so a future write path that forgot the flag would silently produce **opted-in** rows. For a consent control the safe direction is the configured choice, not "yes, train on this". **Fixed:** the ORM column now defaults to `lambda: settings.training_data_opt_in`, so omission inherits the deployment's actual setting. Covered by `test_omitting_the_flag_inherits_the_configured_setting`.
- [x] **[Med] M2 — The default assertion was vacuous.** `assert column.server_default is not None` would pass with a default of `false`, breaking AC5 while looking green. **Fixed:** now asserts the rendered default actually contains `true`.
- [x] **[Med] M3 — "opt-in" meant two different things in one docstring.** `collect_db_rows` described `--db-source generated` as "opt-in only", colliding with the new `training_opt_in` consent column. **Fixed:** the docstring now distinguishes the CLI source selection from the per-row consent record and states that opted-out rows are excluded regardless of `--db-source`.

### Review Notes

The backend half was sound: three write paths, all covered; migration applied and verified against live data; env wiring complete across all four surfaces. Both High findings were in `training/`, and they share a cause — that directory sits outside the backend package, so it inherits neither the test suite nor the lint run nor the engine configuration that the application gets for free. Anything security- or consent-relevant that lives there needs those things wired up deliberately. `build_dataset.py` is now ruff-clean as well, so it no longer silently drifts.

## Change Log

- 2026-08-08: Addressed code review findings — all 5 resolved (2 High, 3 Medium). Added the mandatory PgBouncer `statement_cache_size` guard to the builder's engine, and gave the consent filter its first automated coverage: `build_db_queries()` extracted as a pure function with 11 tests under `backend/tests/test_build_dataset.py`, so enforcement runs with the normal suite. Made the control fail closed by defaulting the ORM column to the configured setting, strengthened a vacuous default assertion, and disambiguated the docstring's two meanings of "opt-in". `training/build_dataset.py` is now ruff-clean. Backend **306 → 321** tests, zero regressions, zero new lint errors.
- 2026-08-08: Implemented Story 6.6. `TRAINING_DATA_OPT_IN` (default true) is now stamped onto every `bdd_files` row at write time across all three paths — generated, edited **and uploaded**, the last of which the epic AC had omitted despite it being the dataset builder's default source. `build_dataset.py` excludes opted-out rows and reports the count so an all-excluded database cannot be mistaken for an empty one. Migration `c9d0e1f2a3b4` applied and verified against the live database (all 10 existing rows opted-in). Backend **300 → 306** tests, zero regressions, zero net new lint errors. Surfaced that both uploaded rows fail the quality filter, so the usable DB corpus is currently zero.
- 2026-08-08: Story drafted. Corrected the epic's AC, which covered only generation and editor corrections — the `uploaded` write path is `build_dataset.py`'s default DB source and must be flagged too, or the opt-out misses the most valuable data. Documented why the flag is stamped at write time rather than filtered at build time, and the string-concatenated WHERE clause in the builder that a naive second condition would break.
