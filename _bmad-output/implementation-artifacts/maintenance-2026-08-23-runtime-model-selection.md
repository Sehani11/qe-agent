# Maintenance Record — Runtime Model Selection

**Date:** 2026-08-23
**Kind:** Full-stack increment (provider abstraction + API + UI), performed outside the story workflow
**Status:** ✅ Implemented. Backend 717 passed; frontend `tsc` clean, ESLint clean, 203 passed / 2 skipped.
**Migration:** none
**Amends:** Stories 1.3, 2.5, 5.1, 5.5, 6.6 (each carries a pointer to this record)

---

## 1. The gap

Provider and model were deployment configuration: `LLM_PROVIDER` and `LLM_MODEL`
read from the environment by the factory, with `ClaudeProvider` hardcoding its
own model name and ignoring `LLM_MODEL` entirely. A user could not compare two
models on the same ticket without an operator editing `.env` and restarting.

Three things had to change together, because fixing only the first produces a
picker that appears to work and does not.

## 2. Selection is per request, not per process

`get_llm_provider(provider, model)` takes both as arguments; the environment is
the fallback for callers with no request behind them (the evaluation runner,
background jobs). Nothing global mutates, so two users can run different models
concurrently — a process-level override could not have supported that.

Every LLM-backed request body carries the pair via `LLMSelectionMixin`
(`app/schemas/llm.py`). They travel together always: a model only means anything
alongside its own provider, and `gpt-4o` sent with `claude` reaches Anthropic as
an unknown model name.

**`BDDGenerateRequest` declares the two fields inline instead of inheriting the
mixin.** `app/schemas/bdd.py` is loaded *by file path* with no package context by
`training/serve/app.py`, which needs `BDDGenerateResponse` without colliding with
its own top-level module named `app`. A cross-module import there breaks the
serving shim at startup. `test_bdd_request_matches_the_shared_llm_selection` pins
the copy to the original so the two cannot drift.

## 3. One key per vendor

`LLM_API_KEY` was a single provider-agnostic key. Selecting Claude on an
OpenAI-configured deployment handed the OpenAI key to Anthropic — non-empty, so
the "is a key configured" guard passed, and the failure surfaced as a 401 that
reads like a broken account rather than a missing setting.

`OPENAI_API_KEY` and `ANTHROPIC_API_KEY` are now resolved per provider by
`api_key_for()` in the LLM factory. `LLM_API_KEY` still serves whichever provider
`LLM_PROVIDER` names, so existing deployments are unaffected, and is deliberately
**not** a fallback for the others.

> `api_key_for()` is public because `vector_service` needs it too — see
> [the training-data record](maintenance-2026-08-23-training-data-loop.md) §4 for
> the embeddings regression this caused and how it was closed.

## 4. Capability, not just configuration

`ClaudeProvider.generate_with_tools` raised `NotImplemented`. Agentic
verification is built entirely on it, and
`agentic_verification_service` catches `LLMProviderError` **per scenario**, emits
an `error` event and continues — then emits `complete` regardless. Selecting
Claude therefore produced a run that finished normally, fired its success toast,
and verified nothing.

Two changes:

- **`ClaudeProvider.generate_with_tools` is implemented** against Anthropic's
  native tool-use protocol. Callers pass OpenAI-shaped messages and tools (what
  `agentic_verification_service` builds, shared across providers), so the
  provider translates both: the `system` message is hoisted to the top-level
  parameter (Anthropic rejects `role: "system"` in `messages`), and
  `function`/`parameters` becomes `input_schema`. All `tool_result` blocks return
  in **one** user message — splitting them is accepted by the API but trains the
  model out of requesting parallel calls. Round budget matches
  `OpenAIProvider`: at most `max_tool_rounds` API calls, the last forced to text
  with `tool_choice: {"type": "none"}`.
- **`LLMProvider.supports_tools`** (`False` on the base, `True` on Claude and
  OpenAI, `False` on Ollama). Verification resolves through
  `llm_with_tools_for()`, which returns **400 up front** rather than letting the
  per-scenario catch fake a completed run.

## 5. Changes

**Backend**
- `app/services/llm/factory.py` — `get_llm_provider(provider, model)`,
  `SUPPORTED_PROVIDERS`, `api_key_for()`.
- `app/services/llm/provider.py` — `supports_tools`.
- `app/services/llm/claude_provider.py` — model parameter, `DEFAULT_CLAUDE_MODEL`,
  the tool loop, `_split_system` / `_to_claude_tool` / `_text_of`.
- `app/services/llm/openai_provider.py` — model resolved once at construction,
  not read from settings per call (the multi-call tool loop must stay on one
  model).
- `app/services/llm/ollama_provider.py` — `model` property, `supports_tools = False`.
- `app/schemas/llm.py` — `LLMSelectionMixin`.
- `app/api/v1/llm_selection.py` — `llm_for`, `llm_with_tools_for`,
  `validate_selection`, `validate_bdd_selection`. Unknown provider → 400, not the
  500 a raw `LLMProviderError` produces through the global handler.
- `app/core/config.py` — `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`;
  `LLM_PROVIDER` default moved `claude` → `openai`.
- Routes: `chat.py`, `verification.py`, `bdd.py` resolve through the helpers.

**Frontend**
- `lib/types/llm.ts` — the model catalog. A static list on purpose: the backend
  accepts anything its SDK accepts, so this file is the product decision about
  what to offer. Each option carries its own provider, so the picker never
  composes the two independently.
- `providers/ModelProvider.tsx` — the choice in `localStorage`, read through
  `useSyncExternalStore` (same pattern as `ThemeProvider`). A stored model no
  longer in the catalog falls back to the default rather than being sent forever.
- `components/model/ModelMenu.tsx` + `ModelSelector.tsx` — one picker in the nav.
- `components/ui/switch.tsx` — `role="switch"` control (announces on/off, not
  checked/unchecked).
- `components/model/FineTunedToggle.tsx` — see
  [the training-data record](maintenance-2026-08-23-training-data-loop.md) §2.

## 6. UI placement — two reversals, recorded because the reasoning matters

The picker was first in the nav, then moved to one control per action, then moved
back to the nav. The final shape is **one nav picker + a fine-tuned toggle beside
Generate BDD**.

Per-action pickers were coherent but exposed a problem the global picker still
has: **the general model is one app-wide choice, while each flow accepts a
different set.** Ollama cannot verify. The per-action version solved this by
filtering the verification menu — which then showed a trigger whose own list
excluded it. The nav version instead lists everything and marks what a model
cannot do ("Cannot run verification"), because a single global picker has no way
to know which page it will be used from.

`requireTools`, `providerGroupsFor()` and `ModelMenu.triggerWarning` existed only
for the filtered variant and were removed with it.

## 7. Tests

- `test_llm_factory.py` — explicit selection overrides the configured default;
  per-provider model fallback; blank strings treated as unset.
- `test_llm_selection.py` — every LLM request carries the pair; the BDD inline
  copy matches the mixin; a provider's key is never used for another; a
  tool-less provider is refused for verification.
- `test_claude_tool_use.py` — message/tool translation, one round trip, parallel
  results in a single user message, the forced final round inside the call
  budget, API errors wrapped.
- `providers/__tests__/ModelProvider.test.tsx`, `ui/__tests__/switch.test.tsx`.

## 8. Rollout

No migration. **`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are optional** — a
deployment with only `LLM_API_KEY` keeps working for whichever provider
`LLM_PROVIDER` names. Selecting a provider with no key now returns a 400 naming
the variable to set, instead of a 401 from the vendor.
