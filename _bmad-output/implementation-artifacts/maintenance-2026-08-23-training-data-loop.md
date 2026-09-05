# Maintenance Record — Closing the Training-Data Loop

**Date:** 2026-08-23
**Kind:** Bug fixes + small increments across capture, dataset build, and consent
**Status:** ✅ Implemented. Backend 717 passed; frontend `tsc` clean, ESLint clean, 203 passed / 2 skipped.
**Migration:** none
**Amends:** Stories 6.4, 6.5, 6.6 (each carries a pointer to this record)

---

## 1. The gap

Epic 6 built capture, consent, dataset build and fine-tuning. End to end, **no
session data had ever trained a model.** The dataset behind `run-5-qwen1.5b` had
204 examples and zero from the database:

```
distinct origins: 204
origins that look like a bdd_files row id: 0
```

Every one came from `training/corpus/` and `corpus-product/`. Three independent
defects, each of which alone was enough to break the loop.

## 2. The app was not writing valid Gherkin

`scenariosToGherkin()` pushed the model's raw `given` / `when` / `then` values:

```
  Scenario: User registers with email and password
    a user is on the registration page        ← no "Given"
```

The system prompt asks for Given/When/Then syntax; the model routinely returns
the step text alone. Without keywords this is not Gherkin — Cucumber cannot run
it, and `parse_feature` discards the scenario as having **incomplete steps**.
Every `edited` and `uploaded` row the app had ever written was unparsable, and
every downloaded `.feature` file was unrunnable.

`renderStep()` now adds the keyword only when the line does not already carry one
(so a well-behaved model is not given `Given Given`), and turns an unkeyworded
continuation line into `And`.

This is a **forward fix only**. Rows written before it stay unparsable; they were
left alone deliberately.

## 3. The highest-value rows were unreachable

`--db-source` accepted `{uploaded, generated, all}`. `edited` rows — human
corrections, which Story 6.4 itself calls *"the highest-value training signal
available"* — could only be included via `all`, which also pulls in every
`generated` row. Training on those is self-distillation.

Two choices added, default unchanged:

| Choice | Selects | Use |
|---|---|---|
| `uploaded` *(default)* | `source='uploaded'` | files a person uploaded |
| **`edited`** | `source='edited'` | human corrections |
| **`authored`** | `uploaded` + `edited` | everything a person wrote — closes the loop |
| `generated` | `source='generated'` | self-distillation; see Story 6.5 |
| `all` | every row | diagnostics |

The source predicate moved from a scalar `source = :source` to
`source = ANY(:sources)` so one code path serves a single source and a group.
Three existing tests asserted the old parameter name; what they guarded
(parameterised not interpolated, exclusion count scoped, `generated` out of the
default) is intact and still asserted.

**The consent filter is untouched.** `training_opt_in = true` still applies to
every choice, and the parametrised tests now cover all five.

## 4. Real acceptance criteria were being thrown away

`bdd_files.acceptance_criteria` exists specifically as *"the input half of the
training pair"* (Story 6.4) and is genuinely populated. The builder's query never
selected it, so every DB row went through **back-generation** — an LLM call that
*reconstructs* criteria from the scenarios.

For a corpus `.feature` file that is necessary; there is no input side. For a
captured row it means paying an LLM to invent a worse replacement for data
already held — and the invented text is what the model then learns to map from.
It is the same argument the README already makes for uploaded `.jsonl` pairs,
not applied to DB rows.

Worst on exactly the rows that matter: an `edited` row inherits its parent's real
criteria, so *real ticket → human-corrected scenarios* was being degraded into
*invented ticket → human-corrected scenarios*.

Now:
- `build_db_queries` selects `acceptance_criteria`; `collect_db_rows` attaches it
  to the `FeatureDoc`.
- `parse_feature` reads back the `# Source AC:` comments the app writes, into a
  new `Scenario.source_ac_clause`. Every other comment is still ignored.
- `has_stored_pair()` requires **both** halves — the criteria and a clause on
  every scenario. A partial answer is not usable: `source_ac_clause` is required
  by `BDDGenerateResponse`, and filling gaps with the whole criteria text would
  teach the model that every scenario derives from all of it.
- `pair_from_stored()` builds the pair with **no LLM call**. Both routes go
  through `_build_pair()` so a stored pair is byte-identical in shape to a
  back-generated one — the trainer must not be able to tell them apart, and the
  holdout split must treat them the same.

`uploaded` rows have `NULL` criteria (an upload has no ticket) and correctly
still back-generate.

## 5. Consent became a per-capture choice

`TRAINING_DATA_OPT_IN` was operator configuration stamped at write time
(Story 6.6). It is now also a per-request field on the BDD write paths, surfaced
as a switch beside Generate BDD.

**The two are ANDed — a client can only ever narrow the policy:**

| `TRAINING_DATA_OPT_IN` | request | stamped |
|---|---|---|
| `false` | `true` | **false** |
| `true` | `false` | false |
| `true` | absent | true |

A deployment forbidden from training on its users' content cannot have that
overridden by a checkbox, a stale client, or a hand-made request. It also keeps
the fail-closed property the column was built around: an absent flag inherits the
deployment's real choice, not "yes, train on this".

Applied to **all three** write paths — `/bdd/generate`, `/bdd/save` (the
strongest signal) and `/bdd/upload` (`build_dataset.py`'s default source). An
opt-out with a gap is not an opt-out.

`GET /api/v1/config` was added so the UI can render the toggle as *disabled*
when the deployment forbids training, rather than offering a choice that cannot
take effect.

## 6. Embeddings regression (introduced and closed here)

Splitting `LLM_API_KEY` into per-provider keys broke `embeddings_available()`:

```python
return settings.llm_provider == "openai" and bool(settings.llm_api_key)
```

Two faults. It read only the legacy key, so moving to `OPENAI_API_KEY` would have
silently emptied every RAG surface — retrieval returning no context, answers
ungrounded, nothing on screen saying why. And gating on `llm_provider` is wrong
on its own terms: the index is built with one embedding model
(`text-embedding-3-small`, 1536-d) and must stay consistent, so embeddings always
go to OpenAI regardless of which provider answers the question.

It now resolves through `api_key_for("openai")`, independent of the chat provider.

**Embeddings are deliberately NOT selectable from the model picker.** Vectors
from different models occupy different spaces, the Pinecone index dimension is
fixed, and Anthropic has no embeddings API. Changing the embedding model is a
migration, not a per-request choice.

## 7. The test suite was spending real money

Fixing §6 surfaced a test in `test_embedding_policy.py` making a **real OpenAI
embeddings call**: it patched `vector_service.settings` to simulate "no
credentials", the code moved its credential read elsewhere, and the now-inert
patch let the request out with whatever key the developer's `.env` holds. It
passed.

`tests/conftest.py` now blocks httpx's real transports for the whole suite
(`@pytest.mark.allow_network` opts out). `ASGITransport` is untouched so
TestClient still works.

> The first version of this guard blocked `socket.socket.connect` and **passed
> the probe while blocking nothing** — on Windows, asyncio's Proactor loop
> connects through IOCP and never touches that call. Any future network guard
> must be verified against a real outbound call, not assumed.

## 8. Tests

- `test_build_dataset.py` — `edited` selectable alone; `authored` selects both
  human sources and excludes `generated`; exclusion count scoped to the same
  rows; default unchanged; every CLI choice maps to a known group.
- `test_training_consent.py` — operator "no" cannot be overridden; a user may
  opt a capture out; an absent flag takes the deployment policy; all three write
  paths accept it.
- `scenariosToGherkin.test.ts` — keywords added when missing, never doubled,
  And/But continuations preserved, the AC clause comment survives.
- `useTrainingOptIn.test.tsx` — consent cannot be granted where forbidden.

## 9. Rollout

No migration. To close the loop on new sessions:

```bash
uv run --project backend python training/build_dataset.py \
    --from-db --db-source authored --out training/data
```

Still open: an `edited` row links to its `generated` parent via `parent_id` — a
ready-made `(rejected, chosen)` DPO pair — but the two sides are stored in
different formats and nothing normalises them yet. `scenariosToGherkin()` is the
canonical rendering to port.
