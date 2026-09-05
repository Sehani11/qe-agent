# Epic 6 — The Super Simple Version

*(For the full details, see [epic-6-explained.md](epic-6-explained.md))*

---

## What is it? (one line)

We tried to **train our own small AI model** to write BDD test scenarios,
instead of paying for Claude/OpenAI every time.

---

## The story, like you'd tell a friend

1. Our app takes a Jira ticket and writes Gherkin tests (Given/When/Then)
   using a big AI like Claude.
2. We wondered: *"Can we teach a small, free model to do just this one job?"*
3. So we collected examples, trained a model on a free Kaggle GPU,
   plugged it into the app, and compared it against the big AI.
4. **Result: the big AI is still better.** Our model was slower and made more
   mistakes. So we keep it as a research toy, not in production.
5. **Why did it lose?** Bad study material — we trained it on the wrong kind of
   examples (test files from open-source tools, not real product tickets).
   The fix is better data, then train again.

---

## How does fine-tuning work? (kitchen analogy)

- A big AI model is like a **chef who can cook anything**.
- Fine-tuning = giving that chef a **short course in one dish only** (writing
  BDD tests).
- We don't rebuild the chef's brain (too expensive). We just add a small
  "recipe notebook" on top — called a **LoRA adapter**. It's a tiny file
  (161 MB) that sits on top of the big model (15 GB).
- Training this notebook took **21 minutes on a free Kaggle GPU**.

### What did we feed it?

The model learns from pairs: *"here's the requirement → here's the test"*.

Problem: our app never saved real pairs. So we cheated (smartly):

- We downloaded thousands of **real, human-written test files** from GitHub.
- We asked an AI: *"guess what requirement produced this test"* (working
  backwards).
- That gave us **182 practice pairs** → the model studied them 3 times over.

---

## The on/off switch

One setting decides which brain the app uses:

```
BDD_MODEL_PROVIDER=general_llm    ← big AI (Claude/OpenAI) — the default
BDD_MODEL_PROVIDER=fine_tuned     ← our trained model
```

**Safety net:** if our model is broken or too slow, the app silently uses the
big AI instead. You can't see this in the UI — only in the backend log:

```
effective=fine_tuned    ← our model answered ✅
effective=general_llm   ← it fell back, our model was NOT used ❌
```

---

## How to run it (5 steps)

```bash
# 1. Build the practice pairs
uv run --project backend python training/build_dataset.py --features-dir training/corpus --out training/data

# 2. Send everything to Kaggle
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --push

# 3. IN THE BROWSER: open the Kaggle link it printed,
#    pick "GPU T4 x2", click "Save & Run All". Wait ~21 min.

# 4. Download the trained result
PYTHONUTF8=1 uv run --project backend python training/kaggle_run.py --fetch

# 5. Serve it locally (needs Ollama installed):
#    register the model, start the shim, flip the switch —
#    exact commands in training/serve/README.md
```

---

## How to test it

```bash
# Normal tests
uv run --project backend pytest

# The real exam: our model vs the big AI, same questions, scored
uv run --project backend python -m app.evaluate_models --run-id run-1
```

---

## The scoreboard (why it's not in production)

| | Big AI | Our model |
|---|---|---|
| Covers every requirement | ✅ 100% | ⚠️ 87% |
| Repeats itself | ✅ Never | ⚠️ Sometimes |
| Speed | ✅ ~3 sec | ❌ ~45+ sec (no GPU) |

**Verdict:** training worked, but the model studied the wrong textbook.
Get real examples from our own app (they're being collected now), retrain,
then maybe flip the switch.

---

## 4 traps to remember

1. **Kaggle GPU:** start the run from the browser with "GPU T4 x2" picked —
   otherwise you get an old GPU (P100) that crashes. And verify your phone on
   Kaggle, or nothing downloads.
2. **Old settings file:** if you get a `KeyError` about a setting that clearly
   exists, your Kaggle notebook is stuck on an **old copy of the data**. The
   notebook and the settings file are uploaded separately, so they can fall out
   of step. Remove and re-add the dataset in the right-hand panel, or push a
   brand-new notebook with `--fresh`.
3. **Windows:** always put `PYTHONUTF8=1` before the kaggle commands.
4. **Silent fallback:** the app never errors when our model fails — always
   check the log for `effective=fine_tuned`.
