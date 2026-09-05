# Story 6.1: BDD Model Provider Abstraction

Status: done

<!-- Note: Validation is optional. Run validate-create-story for quality check before dev-story. -->

## Story

As a **developer**,
I want a provider abstraction for BDD model selection that allows switching between a fine-tuned model and the general LLM,
so that the fine-tuned model can be integrated without modifying existing BDD generation code.

## 🛑 READ THIS FIRST — The Abstraction Already Exists

**Every acceptance criterion in `epics.md` for this story is already satisfied by working, tested code.** It landed out-of-band in commit `4e0baea` (alongside the storage service), before Epic 6 was opened. Verified 2026-08-07: **267 backend tests pass**, including 6 in `test_bdd_model_factory.py`.

**DO NOT create `bdd_model/`, rewrite the ABC, re-add the factory, or re-route `bdd_service.py`. It is all there and it works.**

| Original AC (epics.md) | Where it lives | Status |
|---|---|---|
| `BDDModelProvider` ABC at `backend/app/services/bdd_model/provider.py` | [provider.py:11](backend/app/services/bdd_model/provider.py#L11) | ✅ met |
| `BDD_MODEL_PROVIDER=general_llm` (default) → factory returns `GeneralLLMFallbackProvider` delegating to `LLMProvider` | [factory.py:19](backend/app/services/bdd_model/factory.py#L19), [general_llm_fallback.py:13](backend/app/services/bdd_model/general_llm_fallback.py#L13) | ✅ met |
| `BDD_MODEL_PROVIDER=fine_tuned` → factory returns `FineTunedModelProvider` calling the endpoint | [factory.py:13-16](backend/app/services/bdd_model/factory.py#L13-L16), [fine_tuned_provider.py:21](backend/app/services/bdd_model/fine_tuned_provider.py#L21) | ✅ met |
| `bdd_service.py` routes through `BDDModelProvider`, never LLM/fine-tuned directly (NFR-R6) | [bdd_service.py:55](backend/app/services/bdd_service.py#L55) | ✅ met |
| Pytest test verifies factory returns correct provider per env var | [test_bdd_model_factory.py:17-58](backend/tests/test_bdd_model_factory.py#L17-L58) | ✅ met |

Env wiring is complete too — `BDD_MODEL_PROVIDER`, `FINE_TUNED_MODEL_ENDPOINT`, `FINE_TUNED_MODEL_API_KEY` are present in [.env.example:14-16](.env.example#L14-L16), [docker-compose.yml:36-38](docker-compose.yml#L36-L38), [cfn/setup-infra.sh:41-43](cfn/setup-infra.sh#L41-L43), and [cfn/deploy-backend.sh:30-32](cfn/deploy-backend.sh#L30-L32).

**This story is therefore scoped as verification + gap-closure.** Three real defects in the shipped abstraction are listed below. They are what you implement.

## Acceptance Criteria

**AC1–AC5 (from epics.md) — verify, do not rebuild.**

1. **Given** the `BDDModelProvider` ABC exists at `backend/app/services/bdd_model/provider.py`
   **When** `BDD_MODEL_PROVIDER=general_llm` (default)
   **Then** `bdd_model/factory.py` instantiates `GeneralLLMFallbackProvider`, which delegates to the existing `LLMProvider` interface
2. **And** when `BDD_MODEL_PROVIDER=fine_tuned`, the factory instantiates `FineTunedModelProvider`, which calls the fine-tuned model endpoint
3. **And** `bdd_service.py` routes BDD generation through `BDDModelProvider` — never calling an LLM or fine-tuned model directly (NFR-R6)
4. **And** a Pytest test verifies the factory returns the correct provider based on the env var
5. **And** the dev agent confirms each of AC1–AC4 against the code above and records the confirmation in Completion Notes **without modifying the implementation** — if any AC is found unmet, fix only that AC

**AC6–AC8 — gap closure (scope added by this story; not in epics.md).**

6. **Given** `FineTunedModelProvider.generate_bdd` currently has no test coverage of its request or fallback branches
   **When** the test suite runs
   **Then** tests cover: (a) endpoint success returns the parsed JSON unchanged, (b) `Authorization: Bearer` header sent only when `FINE_TUNED_MODEL_API_KEY` is set, (c) HTTP error → falls back to `GeneralLLMFallbackProvider`, (d) transport error/timeout → falls back, (e) non-JSON body → falls back, (f) unset endpoint → falls back without any HTTP call, (g) fallback also failing → raises `BDDModelProviderError`
7. **And** a silent fallback becomes observable: when `FineTunedModelProvider` serves a request via the general LLM, the effective provider is recorded (not only the incidental warning), and `bdd_service` records the effective provider for every generation — so Story 6.3's evaluation pipeline can never mistake fallback output for fine-tuned output
8. **And** the fine-tuned HTTP timeout is bounded so that **timeout + fallback still fits NFR-P3's 30-second budget** — the current hard-coded `timeout=30.0` guarantees a breach whenever fallback triggers (30s wait, *then* a full LLM call). The timeout is configurable via a new `FINE_TUNED_MODEL_TIMEOUT_SECONDS` setting (default ≤ 12s), wired into every env surface listed in Task 4

## Context & Critical Background

### The three real gaps

**Gap 1 — the fine-tuned path is untested.** [test_bdd_model_factory.py](backend/tests/test_bdd_model_factory.py) covers the ABC, the factory's four selection branches, and the constructor warning. It never calls `generate_bdd`. So `FineTunedModelProvider`'s entire request/fallback body ([fine_tuned_provider.py:39-102](backend/app/services/bdd_model/fine_tuned_provider.py#L39-L102)) is unverified — including the `except` clause that decides when to fall back.

**Gap 2 — fallback is silent where it matters.** When the endpoint fails, the provider logs a warning and returns general-LLM output ([fine_tuned_provider.py:76-80](backend/app/services/bdd_model/fine_tuned_provider.py#L76-L80)). The response is byte-identical to a real fine-tuned response — by design (Story 6.2 AC: "response format is identical"). Story 6.3 must compare `fine_tuned` against `general_llm` output; if a misconfigured endpoint silently serves general-LLM text under the `fine_tuned` label, **the evaluation measures nothing and looks like it worked**. That is a data-integrity bug in the abstraction, so it belongs here, not in 6.3.

**Gap 3 — the timeout math breaks NFR-P3.** [fine_tuned_provider.py:66](backend/app/services/bdd_model/fine_tuned_provider.py#L66) uses `httpx.AsyncClient(timeout=30.0)`. NFR-P3 allows 30s total for BDD generation. On a hung endpoint the request burns the entire budget *before* the fallback LLM call even starts, so the fallback path always violates NFR-P3.

### What is deliberately NOT in scope

- Calling a real fine-tuned model, or anything needing a deployed endpoint → **Story 6.2** (blocked on external model-training infrastructure per [epics.md:190](_bmad-output/planning-artifacts/epics.md#L190))
- Comparative metrics, evaluation runs, `app.evaluate_models` → **Story 6.3**
- Validating that the endpoint's JSON matches `BDDGenerateResponse` before returning it → 6.2 (noted under Dev Notes)
- Any frontend change. BDD generation is provider-agnostic at the API boundary; `POST /api/v1/bdd/generate` response shape must not change.

## Tasks / Subtasks

- [x] **Task 1: Verification pass — no code changes** (AC: 1–5)
  - [x] Walk the AC table above against the live files; confirm each of AC1–AC4
  - [x] Run `cd backend; uv run pytest -q` → expect **267 passed** as the pre-change baseline
  - [x] Record confirmations in Completion Notes. If an AC is genuinely unmet, fix only that AC and say so explicitly

- [x] **Task 2: Cover the fine-tuned request + fallback branches** (AC: 6)
  - [x] Add tests to [backend/tests/test_bdd_model_factory.py](backend/tests/test_bdd_model_factory.py) (or a sibling `test_fine_tuned_provider.py` — either is fine; keep the module docstring convention)
  - [x] Mock `app.services.bdd_model.fine_tuned_provider.httpx.AsyncClient` using the **existing house pattern**: copy `_mock_response` / `_make_async_client` from [test_verification.py:45-61](backend/tests/test_verification.py#L45-L61). Do **not** add `respx`/`pytest-httpx` — no new dependency is needed
  - [x] Patch `settings.fine_tuned_model_endpoint` / `settings.fine_tuned_model_api_key` via `monkeypatch.setattr` on the **module-level `settings`**, matching [test_bdd_model_factory.py:65-70](backend/tests/test_bdd_model_factory.py#L65-L70) — note the provider reads settings in `__init__`, so patch **before** constructing
  - [x] Assert the fallback branches patch `GeneralLLMFallbackProvider.generate_bdd` (it is imported lazily inside `_fallback` — patch the class in its own module, `app.services.bdd_model.general_llm_fallback.GeneralLLMFallbackProvider`)
  - [x] Cover all seven cases (a)–(g) from AC6

- [x] **Task 3: Make the effective provider observable** (AC: 7)
  - [x] Add a `name` property to `BDDModelProvider` ([provider.py:11](backend/app/services/bdd_model/provider.py#L11)); `GeneralLLMFallbackProvider` → `"general_llm"`, `FineTunedModelProvider` → `"fine_tuned"`
  - [x] At the single fallback site, log at **INFO** with the effective provider, e.g. `bdd_model.effective_provider=general_llm configured=fine_tuned reason=<endpoint_error|endpoint_unset>` — one structured, greppable line
  - [x] In [bdd_service.py](backend/app/services/bdd_service.py), log the configured provider name once per generation at INFO
  - [x] Test with `caplog` (precedent: [test_bdd_model_factory.py:61-74](backend/tests/test_bdd_model_factory.py#L61-L74)): assert the effective-provider line appears on the fallback path and reports `general_llm`
  - [x] **Do NOT** add a field to `BDDGenerateResponse` — that model's JSON schema is passed to the model as `response_format`, so any new field would be handed to the LLM to fill in. **Do NOT** change the `generate_bdd` return type; `bdd_service` and the mocks in [test_bdd.py:74-79](backend/tests/test_bdd.py#L74-L79) depend on `dict[str, object]`
  - [x] If 6.3 later needs this persisted rather than logged, that is a 6.3 decision — leave a one-line note, do not build it

- [x] **Task 4: Bound the fine-tuned timeout to fit NFR-P3** (AC: 8)
  - [x] Add `fine_tuned_model_timeout_seconds: float = 12.0` to [core/config.py:33-40](backend/app/core/config.py#L33-L40), in the existing BDD block with a comment stating the NFR-P3 rationale
  - [x] Use it in [fine_tuned_provider.py](backend/app/services/bdd_model/fine_tuned_provider.py) in place of the hard-coded `30.0`
  - [x] Wire the env var into **all four** surfaces — missing one breaks prod deploys: [.env.example](.env.example#L14-L17), [docker-compose.yml](docker-compose.yml#L36-L39), `BACKEND_ENV_KEYS` in [cfn/setup-infra.sh](cfn/setup-infra.sh#L41-L44), and `BACKEND_ENV_KEYS` in [cfn/deploy-backend.sh](cfn/deploy-backend.sh#L30-L33) (the two arrays are explicitly kept in sync — update both)
  - [x] Test that the configured timeout reaches `httpx.AsyncClient`

- [x] **Task 5: Regression + lint**
  - [x] `cd backend; uv run pytest -q` → 267 + new tests, **zero regressions**
  - [x] `uv run ruff check app tests` clean on changed files (line-length 88, rules `E,F,I,N,W,UP,B,SIM,RUF` per [pyproject.toml:42-50](backend/pyproject.toml#L42-L50))

## Dev Notes

### Reuse map — everything you need already exists

| Piece | Location |
|---|---|
| BDD provider ABC | `app/services/bdd_model/provider.py` |
| Provider selection | `app/services/bdd_model/factory.py::get_bdd_model_provider` |
| General-LLM delegation | `app/services/bdd_model/general_llm_fallback.py` |
| Fine-tuned HTTP call + fallback | `app/services/bdd_model/fine_tuned_provider.py` |
| Underlying LLM interface | `app/services/llm/provider.py::LLMProvider.generate_structured` via `get_llm_provider()` |
| httpx async-client mock helpers | `tests/test_verification.py:45-61` |
| Settings monkeypatch pattern | `tests/test_bdd_model_factory.py:65-70` |
| `caplog` assertion pattern | `tests/test_bdd_model_factory.py:61-74` |

### Architecture rules that bind this story

- **Mandatory Rule 2:** never call LLM SDKs directly — always via `LLMProvider` ([architecture.md:308](_bmad-output/planning-artifacts/architecture.md#L308))
- **Mandatory Rule 3:** never call the fine-tuned model SDK directly — always via `BDDModelProvider` ([architecture.md:309](_bmad-output/planning-artifacts/architecture.md#L309))
- **Mandatory Rule 1:** no business logic in route handlers — this story touches `services/` and `core/config.py` only
- **NFR-R6:** fine-tuned model and general LLM independently swappable — the factory is the *only* place selection logic may live ([factory.py:5](backend/app/services/bdd_model/factory.py#L5))
- Python naming: modules `snake_case`, classes `PascalCase`, constants `UPPER_SNAKE_CASE` ([architecture.md:231-235](_bmad-output/planning-artifacts/architecture.md#L231-L235))

### Known rough edges — flag, don't fix here

- `bdd_service.generate_bdd_scenarios` catches bare `Exception` and reports it as "Failed to parse or validate model output" ([bdd_service.py:73](backend/app/services/bdd_service.py#L73)), which also swallows genuine programming errors. Out of scope; note it if you touch the file.
- `FineTunedModelProvider` returns `response.json()` unvalidated ([fine_tuned_provider.py:73](backend/app/services/bdd_model/fine_tuned_provider.py#L73)). A wrong-shaped endpoint response surfaces as a confusing parse error in `bdd_service` rather than triggering fallback. Correct fix belongs to **Story 6.2**, when a real endpoint exists to validate against.

### Project Structure Notes

**Modify:** `backend/app/services/bdd_model/provider.py`, `.../general_llm_fallback.py`, `.../fine_tuned_provider.py`, `backend/app/services/bdd_service.py`, `backend/app/core/config.py`, `.env.example`, `docker-compose.yml`, `cfn/setup-infra.sh`, `cfn/deploy-backend.sh`.
**Create (optional):** `backend/tests/test_fine_tuned_provider.py` — or extend `backend/tests/test_bdd_model_factory.py`.
**Do NOT modify:** `app/api/v1/bdd.py`, `app/schemas/bdd.py`, `app/services/llm/**`, any frontend file. No DB migration — this story adds no persistence.

### Testing Standards

`pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`, so async tests need no decorator), tests mirror `app/` structure under `backend/tests/`. Mock at the boundary with `unittest.mock.patch` / `AsyncMock` — the house style throughout this repo. No new test dependencies. Baseline to preserve: **267 passing**.

### References

- [Source: _bmad-output/planning-artifacts/epics.md#L842-L857] — Epic 6, Story 6.1 original ACs
- [Source: _bmad-output/planning-artifacts/epics.md#L187-L191] — Epic 6 scope, Phase 2 framing, external model dependency
- [Source: _bmad-output/planning-artifacts/architecture.md#L185-L196] — Model Architecture; `BDDModelProvider` abstraction
- [Source: _bmad-output/planning-artifacts/architecture.md#L305-L316] — Mandatory rules for all AI agents
- [Source: _bmad-output/planning-artifacts/epics.md#L79] — NFR-P3 (30s BDD generation budget)
- [Source: _bmad-output/planning-artifacts/epics.md#L96] — NFR-R6 (independently swappable models)
- [Source: git 4e0baea] — commit that introduced `bdd_model/` ahead of Epic 6

## Dev Agent Record

### Agent Model Used

claude-opus-5

### Debug Log References

- Pre-change baseline: `uv run pytest -q` → **267 passed**. Final: **284 passed** (+17), zero regressions.
- Tasks 3 and 4 followed red-green: the 4 observability tests and 2 timeout tests were confirmed failing before implementation (`AttributeError: 'Settings' object ... has no attribute 'fine_tuned_model_timeout_seconds'` for Task 4). Task 2's tests are characterization tests over already-shipped code, so they passed on first run by design — noted rather than faked as red.
- Lint: the 11 remaining ruff errors in touched files were verified pre-existing by linting the `HEAD` versions of `config.py`, `bdd_service.py`, and `test_bdd_model_factory.py` — identical rules and content, line numbers shifted only by insertions. This story introduced **0** new lint errors and removed **2** (both E501 in `fine_tuned_provider.py`, now fully clean).

### Completion Notes List

- **AC1–AC5 verified, not rebuilt (Task 1).** All four original ACs confirmed against the live code with no modification: the ABC at [provider.py:11](backend/app/services/bdd_model/provider.py#L11); factory selection at [factory.py:31-41](backend/app/services/bdd_model/factory.py#L31-L41); `GeneralLLMFallbackProvider` delegating via `get_llm_provider().generate_structured`; `FineTunedModelProvider` registered for `fine_tuned`; and `bdd_service.py` reaching a model *only* through `get_bdd_model_provider()` (NFR-R6 holds — no direct LLM or SDK call anywhere in the BDD path).
- **AC6 — fine-tuned path now covered.** 15 tests in `test_fine_tuned_provider.py` cover all seven required cases: endpoint success returns JSON verbatim with no fallback; `Authorization: Bearer` present only when the API key is set; HTTP error, transport error/timeout, and non-JSON body each degrade to the general LLM; an unset endpoint falls back with **no HTTP client constructed at all**; and a failing fallback surfaces `BDDModelProviderError`. Also asserts `system_prompt`/`response_format` reach the endpoint payload.
- **AC7 — silent fallback is now visible.** Added a `name` class attribute to `BDDModelProvider`, set to the exact `BDD_MODEL_PROVIDER` value that selects each provider; `test_provider_names_match_factory_keys` pins that invariant against the factory registry so the two can't drift. Every generation now emits `bdd_model.effective_provider=<name>` from whichever provider actually served it, and a degraded run additionally emits `configured=fine_tuned reason=endpoint_error|endpoint_unset`. `bdd_service` logs `bdd_model.configured_provider=<name> session_id=<id>` once per generation. Story 6.3 can therefore separate genuine fine-tuned output from general-LLM output wearing the `fine_tuned` label.
- **AC8 — NFR-P3 conflict resolved.** The hard-coded `timeout=30.0` consumed the entire 30s budget before the fallback LLM call could start, so every fallback breached NFR-P3. Now `FINE_TUNED_MODEL_TIMEOUT_SECONDS`, default **12.0s**, leaving ~18s for the fallback. `test_default_timeout_leaves_room_for_fallback_within_nfr_p3` asserts against the declared field default (not a live `Settings()`), so a stray `.env` value can't make the guard pass vacuously. Wired into all four env surfaces — `.env.example`, `docker-compose.yml`, and the deliberately-synced `BACKEND_ENV_KEYS` arrays in both `cfn/` scripts.
- **Scope held.** No change to `BDDGenerateResponse` (its JSON schema is handed to the model as `response_format`, so a new field would have been passed to the LLM to fill in), no change to the `generate_bdd` return type, no API/frontend change, no migration. The two known rough edges flagged in Dev Notes — `bdd_service`'s bare `except Exception` and the unvalidated endpoint JSON — were left untouched for Story 6.2 as specified.

### File List

**Backend — modified:**
- `backend/app/services/bdd_model/provider.py` — `name`, `effective_provider`, and `fallback_reason` attribution attributes on the ABC
- `backend/app/services/bdd_model/general_llm_fallback.py` — `name = "general_llm"`; records itself as effective on success
- `backend/app/services/bdd_model/fine_tuned_provider.py` — `name = "fine_tuned"`; `asyncio.timeout` total budget; `_redact()` / `_failure_detail()` credential-safe failure logging; widened fallback catch; records effective provider and fallback reason; wrapped lazy import
- `backend/app/services/bdd_service.py` — `_log_attribution()` emitting one correlated line per generation in a `finally` block
- `backend/app/core/config.py` — `fine_tuned_model_timeout_seconds: float = 12.0` with corrected NFR-P3 rationale
- `backend/tests/test_bdd_model_factory.py` — 5 tests (effective-provider recording, fresh-instance-per-call, single correlated attribution line, attribution on failure)

**Backend — created:**
- `backend/tests/test_fine_tuned_provider.py` — 18 tests covering the request, fallback, attribution, credential-redaction, decode-failure, and timeout-budget branches

**Config / deployment — modified:**
- `.env.example` — `FINE_TUNED_MODEL_TIMEOUT_SECONDS=12`
- `docker-compose.yml` — `FINE_TUNED_MODEL_TIMEOUT_SECONDS` passthrough with default
- `cfn/setup-infra.sh` — `FINE_TUNED_MODEL_TIMEOUT_SECONDS` in `BACKEND_ENV_KEYS`
- `cfn/deploy-backend.sh` — `FINE_TUNED_MODEL_TIMEOUT_SECONDS` in `BACKEND_ENV_KEYS`

## Senior Developer Review (AI)

**Date:** 2026-08-07 · **Outcome:** Changes Requested → **all High and Medium resolved** · **Reviewer model:** claude-opus-5

**Git vs File List:** 0 discrepancies — all 11 claimed files matched `git status` exactly. **Task audit:** all 5 `[x]` tasks had real supporting evidence. Findings below were verified by executing probe scripts, not by inspection alone.

### Action Items

- [x] **[High] H1 — Attribution logs had no correlation key.** `bdd_service` logged `configured_provider … session_id=X` while the providers logged `effective_provider` with no session id, from a different logger. Concurrent generations were unattributable, defeating the entire purpose of AC7 for Story 6.3. **Fixed:** providers now record `effective_provider` / `fallback_reason` as per-instance state and `bdd_service._log_attribution` emits one line carrying `session_id`, `configured`, `effective`, and `reason`. Safe because the factory returns a fresh instance per call — pinned by `test_factory_returns_a_fresh_instance_per_call`.
- [x] **[High] H2 — AC7 was overstated as complete.** `bdd_service` recorded the *configured* provider, not the *effective* one, and a failed generation produced no effective-provider record at all. **Fixed:** attribution now runs in a `finally` block, so every generation — success or failure — is attributed; `effective=none` marks a run that never reached a model. Covered by `test_bdd_service_logs_attribution_even_when_generation_fails`.
- [x] **[Med] M1 — Double log per fallback generation.** Empirically confirmed: one degraded generation emitted **2** `effective_provider` lines (one from `_fallback`, one from `GeneralLLMFallbackProvider`), double-counting for any log-scraping consumer and contradicting Task 3's own "one greppable line". **Fixed:** providers no longer log; attribution is emitted once by `bdd_service`. Guarded by `test_providers_emit_no_attribution_logs_of_their_own` and a `len(lines) == 1` assertion.
- [x] **[Med] M2 — AC8's timeout rationale was wrong.** `httpx.Timeout(12.0)` sets connect/read/write/pool to 12s *each* (verified), so it never guaranteed the 12s total the NFR-P3 argument assumed. **Fixed:** the exchange is wrapped in `asyncio.timeout(self._timeout)` for a true wall-clock bound, `TimeoutError` added to the fallback catch, and the `config.py` comment corrected. Proven by `test_slow_endpoint_is_bounded_by_total_wall_clock_budget`.
- [x] **[Med] M3 — Endpoint credentials leaked to logs.** Confirmed: real `raise_for_status()` produces `… for url 'https://…/gen?api-key=SUPERSECRET123'`, and the handler logged `str(e)`. **Fixed:** `_redact()` strips query/fragment/userinfo and `_failure_detail()` reports the status code or exception type instead of the raw message — host and path are retained so the log stays actionable. Covered by `test_endpoint_credentials_are_not_written_to_logs`.
- [x] **[Med] M4 — `UnicodeDecodeError` bypassed the fallback.** A badly-encoded response body escaped the provider and surfaced through `bdd_service`'s bare `except Exception` as a misleading parse error. **Fixed:** added to the caught tuple; covered by `test_undecodable_body_falls_back_to_general_llm`.
- [x] **[Low] L2 — Brittle `assert "reason=" not in caplog.text`** asserted over all captured logs. Resolved incidentally: that test was replaced with a direct state assertion on the provider.
- [ ] **[Low] L1 — `BDDModelProvider.name` defaults to `"unknown"`** rather than being abstract, so a future provider that forgets to set it would silently log `effective=unknown`. **Accepted, not fixed:** `test_provider_names_match_factory_keys` fails for any provider registered in the factory without a matching name, which covers the realistic path. Making it abstract is a larger interface change than this story warrants.

### Review Notes

The abstraction's structure (ABC → factory → two providers, `bdd_service` routing through the interface) held up — NFR-R6 is genuinely satisfied and no AC was found unimplemented. Every issue was in the *new* observability and timeout work, and the root cause was consistent: the attribution mechanism was designed as decorated logging rather than as data the caller owns. Moving `effective_provider` to instance state that `bdd_service` reads fixed H1, H2, and M1 together.

## Change Log

- 2026-08-07: Story drafted. Discovered AC1–AC5 already satisfied by commit `4e0baea` (267 tests passing); rescoped from build to verification + gap-closure covering the untested fine-tuned request/fallback branches, silent-fallback observability, and the NFR-P3 timeout conflict.
- 2026-08-07: Implemented Story 6.1. Verified AC1–AC5 against existing code with no modification. Added 17 tests (**267 → 284**, zero regressions) covering the previously untested `FineTunedModelProvider` request and fallback branches. Made the effective provider observable via a `name` attribute pinned to the factory keys plus structured INFO logging, so a silent degradation to the general LLM can no longer masquerade as fine-tuned output in Story 6.3's evaluation. Replaced the hard-coded 30s fine-tuned HTTP timeout — which guaranteed an NFR-P3 breach whenever fallback triggered — with `FINE_TUNED_MODEL_TIMEOUT_SECONDS` (default 12s), wired through all four env surfaces. No new dependencies; 0 new lint errors, 2 removed.
- 2026-08-07: Addressed code review findings — 7 items resolved (2 High, 4 Medium, 1 Low), 1 Low accepted. Reworked attribution from per-provider logging into per-instance state that `bdd_service` reads, emitting exactly one `bdd_model.generation` line per generation with `session_id` as the join key — fixing the missing correlation (H1), the unattributed failure path (H2), and the duplicate log line (M1) together. Replaced the per-phase httpx timeout with `asyncio.timeout` for a true total wall-clock bound (M2), redacted endpoint credentials from failure logs (M3), and widened the fallback catch to `UnicodeDecodeError` and `TimeoutError` (M4). **284 → 290 tests**, zero regressions; 0 new lint errors.
