# Fine-tuning run log

Story 6.5, AC7. One section per training run, newest first.

> ### Runs from 2026-08-16 carry quality metrics, not just loss
>
> The run now scores the whole holdout on the GPU before the session ends and
> prints AC-clause coverage, duplicate rate, reference alignment and the JSON
> parse rate into each run's table — see
> [KAGGLE.md](KAGGLE.md#the-table-has-quality-metrics-not-just-loss). Runs 1–3
> predate this and have loss only; their quality numbers came from the separate
> served evaluation, which is why only Run 3 has any.
>
> **The human-authored references were scored too, and the targets are not 1.0
> and 0.0.** On the 19-item holdout the references themselves score **0.974
> coverage** (one item's human Gherkin does not cite every clause its
> back-generated AC block declares) and **0.276 duplicate rate**.
>
> That last number recalibrates one line below. Run 3's **off-domain** duplicate
> rate of 0.297 is measured on this same 19-item holdout, so it sits essentially
> **level with the corpus's own repetition** — the fine-tune is reproducing its
> training data there, not degrading. The 0.389 figure that "repetition got
> materially worse" refers to is the **on-domain** group, which has no reference
> and so no baseline; that finding stands, and the off-domain one should not be
> read the same way. Note the general LLM's 0.107 is *better than the humans*.



> ### ✅ Status: **RUN 5 COMPLETE** — 2026-08-17
>
> Every value below is measured, taken from the Kaggle kernel log of a run that
> actually executed. Nothing here is estimated or inferred.

---

## Run 5 — 2026-08-17 · FIRST RUN ON THE PRODUCT-DOMAIN DATASET

**Run by:** Chamath (Kaggle UI) · **Platform:** Kaggle · **Kernel:** [bdd-fine-tune-story-6-5](https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5) · **Outcome:** COMPLETE

Same 1.5B base and identical hyperparameters as Runs 3 and 4. **Only the dataset
changed** — 198 pairs to 226, of which 28 are the authored product-domain
tickets (dataset v2, below).

| Field | Value |
|---|---|
| Base model | `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit` |
| Train pairs | 204 |
| Holdout pairs | 22 |
| Final train loss | 0.5621 |
| **Final eval loss** | **0.5542** |
| Holdout items scored | 20 of 22 |
| Holdout JSON parse rate | 0.9091 (2 malformed) |
| **Holdout AC-clause coverage** | **0.980** (human reference: 1.000) |
| Holdout duplicate rate | 0.3629 (human reference: 0.289) |
| Holdout reference alignment | 0.3175 |
| Holdout scenarios per item | 3.00 (human reference: 3.27) |
| Holdout scoring wall clock | 9.1 min |
| Wall clock (training) | 8.8 min |
| GPU | Tesla T4 |

### ⚠️ The aggregate duplicate rate is wrong about this run

0.3629 against Run 4's 0.3222 reads as a regression, and it is the number the
table reports. **Split the holdout by domain and it inverts.** Items 20–22 are
the three product-domain items; 3–19 are the legacy testing-framework corpus
(1 and 2 failed to parse):

| Holdout group | Items | Duplicate rate | Coverage | Scenarios per item |
|---|---|---|---|---|
| Legacy testing-framework | 17 | **0.407** | 1.000 | 2.88 |
| **Product-domain (new)** | 3 | **0.111** | 0.867 | 3.67 |

**Repetition is 3.7× lower on the domain the new data covers.** Two of the three
product items produced **four scenarios with zero duplicates** — something no
item in Run 4 managed at any length. Run 4's three items with 3+ scenarios
scored 0.667, 0.800 and 0.833; Run 5 has three items at 0.000.

The aggregate hides it because 17 of the 20 scored items are still the legacy
corpus. **This is the first direct evidence that the corpus domain — not model
capacity, not dataset size — is what drives the repetition**, which is the claim
RUN_LOG has been making since Run 1 without being able to measure it. 28 pairs
at 12.4% of the set moved the metric that far on the slice they cover.

**Do not over-read three items.** It is a strong signal on a tiny sample, not a
result. The way to make it a result is more product-domain pairs and a holdout
that is not 85% off-domain.

### What got worse, honestly

**Coverage fell 1.000 → 0.980**, and the single miss is on a product item:
RG-002 declares five criteria and the model covered three. Its 0.867 is the
weakest coverage in the run, on the domain that is otherwise its best.

**Reference alignment is lower on product items** (0.244) than on legacy ones
(0.330). Alignment is crude lexical overlap against the reference wording, and
the legacy references share heavy boilerplate ("I run `rspec`") that is easy to
match. Read it as a weak signal here, not a quality verdict.

### Eval loss is now BELOW train loss

0.5542 against 0.5621 — the first run where the holdout scores better than the
training set. That normally means the holdout is easier than the training data,
which is plausible here: the split is by origin, and the three product items are
cleanly written with no duplicated targets. It is not evidence of anything good
about the model, and it is another reason not to read the loss column as
quality.

### The two parse failures — and a correction to Run 4

Both failed as `malformed`, **not `truncated`**. Run 4's write-up hypothesised a
token cap, and that hypothesis is now falsified for this failure mode: item 1
(aruba `expand_path.feature`) is the same item that failed in Run 4, and its
completion is well-formed JSON that runs to a proper close.

`malformed` turned out to be too coarse to act on — it covers unparseable output
and a single absent key alike. `parse_failure_reason()` now names the defect
(`invalid JSON`, `no scenarios array`, `missing feature`) and the run prints both
the head and the tail of the failing completion, because 120 characters of tail
was not enough to diagnose either of these.

Worth a look next run: item 2's completion (behat `role_filters.feature`)
described **counting apples**, which is the subject of a different corpus file
entirely. One item, so not a finding — but if it recurs it points at the model
falling back on memorised corpus patterns instead of reading the ticket.

---

> ### 📦 Dataset v2 — 2026-08-17 · PRODUCT-DOMAIN PAIRS ADDED
>
> **198 → 226 pairs** (204 train / 22 holdout). The 28 new pairs are authored
> product tickets from two real repositories, built by
> `training/build_product_pairs.py` from the reviewable YAML in
> `training/corpus-product/`. No LLM wrote either side of them.
>
> **The measurement that justifies the whole exercise.** Scoring the human
> references of each group with the same metric the runs use:
>
> | Reference group | Duplicate rate | Scenarios per item |
> |---|---|---|
> | Legacy testing-framework corpus | **0.335** | 3.11 |
> | New product-domain pairs | **0.000** | 4.33 |
>
> **A third of the legacy corpus's own scenarios near-duplicate another scenario
> in the same answer.** Runs 1–4 have all reported the model repeating itself
> and inferred the corpus was to blame; this measures it. The model was
> imitating its targets faithfully. The new pairs contain no duplicates at all,
> which is the first change to the data that addresses the defect directly
> rather than adding volume.
>
> **Do not over-claim this.** The product pairs are 12.4% of the set — close to
> the 16% whitehall share that Run 1 already had and that did not move the
> result. 28 pairs is a corpus correction in the right direction, not a fix, and
> the honest expectation is a small improvement in duplicate rate, not a
> transformation.
>
> **Holdout composition changed, so eval loss is not comparable to Runs 1–4.**
> Same rule as Run 1 vs Run 2: a loss can only be compared against an identical
> eval set. The holdout metrics (coverage, duplicates, alignment) remain
> comparable, since they do not depend on the item set being identical.
>
> The previous dataset is snapshotted at `training/data-v1/` (git-ignored).

> ### ✅ Status: **RUN 4 COMPLETE** — 2026-08-16
>
> Every value below is measured, taken from the Kaggle kernel log of a run that
> actually executed. Nothing here is estimated or inferred.

---

## Run 4 — 2026-08-16 · FIRST RUN WITH IN-PROCESS QUALITY METRICS

**Run by:** Chamath (Kaggle UI) · **Platform:** Kaggle · **Kernel:** [bdd-fine-tune-story-6-5](https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5) · **Outcome:** COMPLETE

**Config identical to Run 3** — same 1.5B base, same 179/19 split, same seed,
learning rate, epochs, batch size, LoRA rank and sequence length. Nothing was
tuned. The only change is that the run now scores the whole holdout on the GPU
before the session ends.

| Field | Value |
|---|---|
| Base model | `unsloth/Qwen2.5-1.5B-Instruct-bnb-4bit` |
| Quantisation | 4-bit QLoRA (nf4, double quant) |
| LoRA rank / alpha / dropout | 16 / 16 / 0.0 |
| Learning rate | 2.0e-4 (linear, 5% warmup) |
| Epochs | 3 |
| Effective batch size | 8 |
| Max sequence length | 2048 |
| Seed | 3407 |
| Train pairs | 179 |
| Holdout pairs | 19 |
| Final train loss | 0.5007 |
| **Final eval loss** | **0.5686** |
| Holdout items scored | 18 of 19 |
| Holdout JSON parse rate | 0.9474 |
| **Holdout AC-clause coverage** | **1.0000** (human reference: 0.9737) |
| Holdout duplicate rate | 0.3222 (human reference: 0.2757) |
| Holdout reference alignment | 0.3027 |
| Holdout scenarios per item | 2.1111 (human reference: 2.7895) |
| Holdout scoring wall clock | 5.8 min |
| Wall clock (training) | 7.2 min |
| GPU | Tesla T4 |
| Emits parseable `BDDGenerateResponse` | yes |
| Adapter | `training/.kaggle-staging/output/outputs/bdd-lora/`, 70.49 MB |

### It reproduced Run 3, which gives the loss a noise floor

Train 0.5007 against Run 3's 0.5012; eval 0.5686 against 0.5684. **The same
configuration retrained twice lands within ±0.0005**, so that is the resolution
of the eval-loss column — GPU nondeterminism, not a real difference.

Worth having: it means Run 2 → Run 3's **+0.0736** gap is ~150× the noise floor
and can be read as real. It also means any future run claiming a win of a few
thousandths is claiming nothing.

### Coverage is 1.000 on every scored item — above the human ceiling

Not a mean pulled up by good items: all 18 scored items came back at 1.000. The
human-authored references score 0.9737 on the same set, because one item's
Gherkin does not cite every clause its AC block declares. **The model cites all
of them.**

This corroborates Story 6.3's on-domain finding (0.867 → 1.000 for the 1.5B)
from a second, independent direction, on a different item set and without the
serving path in the way.

### The duplicate rate is understated by the model's terseness

0.3222 against the corpus's own 0.2757 looks like a small overshoot. The
per-item log says otherwise:

| What the model wrote | Items | Duplicate rate |
|---|---|---|
| 1 scenario | 6 | 0.000 — mechanically, nothing to duplicate |
| 2 scenarios | 9 | 0.500 on **7 of 9** |
| 3–6 scenarios | 3 | 0.667, 0.800, 0.833 |

Two things follow. **When it writes a second scenario, that scenario restates
the first ~78% of the time** — the exact defect Runs 1 and 2 recorded (a `when`
copied from the previous clause and a `then` merely negated), still present.
And **the more it writes, the more it repeats**: the three items with 3+
scenarios are its three worst.

Its 2.11 scenarios per item against the references' 2.79 therefore flatters the
headline number — six single-scenario items score a free 0.000. Pooled across
all output rather than averaged per item, **18 of the 38 scenarios it generated
(47%) near-duplicate an earlier one in the same answer.** The per-item mean
stays the metric of record for comparability with the evaluation report, but it
is the gentler of the two readings.

### The one lost item was a token cap, not a bad model

Item 1 of 19 produced no parseable JSON. It is the item with **six AC clauses
and the longest reference steps** — the shape most likely to run past the
768-token generation budget mid-object.

The run said only `UNPARSEABLE`, which reads as a model defect and sent this
write-up looking for one. `parse_failure_reason()` now separates `truncated`
from `prose` and `malformed`, prints the fix and the tail of the completion, and
carries the count into the table row. **Next run will state the cause rather
than implying the wrong one.** If it reports `truncated`, raise
`evaluation.sample_max_new_tokens`.

### In-process scoring tracks the served evaluation closely

Run 3's served off-domain numbers against Run 4's in-process ones, on the same
19-item holdout:

| Metric | Run 3, served via Ollama | Run 4, in-process |
|---|---|---|
| AC-clause coverage | 0.974 | 1.000 |
| Duplicate rate | 0.297 | 0.322 |
| Reference alignment | 0.317 | 0.303 |

Every metric lands within ~0.03. These are two different adapters (a retrain)
read through two different paths (Ollama q4 GGUF vs in-process bnb-4bit), so
this is corroboration, not a controlled comparison — **it does not make the
in-process numbers a substitute for `python -m app.evaluate_models`**, which
also measures the serving path and the latency budget. What it does support is
using them as the fast quality signal between runs.

### Cost

5.8 minutes to score 19 items on the T4 — ~18 s per item, against the **62.8 s
per item** Story 6.3 measured locally, where the model does not fit in VRAM.
Total session 13.0 min, comfortably inside the free quota.

### Artefacts

`training/.kaggle-staging/output/outputs/bdd-lora/` — **git-ignored and
overwritten by the next `--fetch`.** Run 3's 1.5B adapter was preserved before
this fetch and is safe at `training/outputs/run-3-qwen1.5b/`; Run 4's adapter is
byte-for-byte the same size and was produced from the same config. Nothing has
been converted to GGUF or registered in Ollama, so the serving decision below is
untouched.

---

## Run 3 — 2026-08-15 · MODEL-SIZE ABLATION (1.5B)

**Run by:** Chamath (Kaggle UI) · **Platform:** Kaggle · **Kernel:** [bdd-fine-tune-story-6-5](https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5) · **Outcome:** COMPLETE

**A controlled one-variable experiment.** `base_model` is the *only* thing that
changed from Run 2. Same dataset (the identical 179/19 split), same seed, same
learning rate, epochs, batch size, LoRA rank and sequence length. **The eval
losses below are therefore directly comparable** — unlike Run 1 vs Run 2, where
the holdout itself changed and the numbers could not be subtracted.

| Field | Run 2 (7B) | **Run 3 (1.5B)** |
|---|---|---|
| Base model | `Qwen2.5-7B-Instruct-bnb-4bit` | **`Qwen2.5-1.5B-Instruct-bnb-4bit`** |
| All parameters | 7,655,986,688 | **1,562,179,072** |
| Trainable parameters | 40,370,176 (0.527%) | **18,464,768 (1.182%)** |
| Final train loss | 0.4052 | **0.5012** |
| **Final eval loss** | **0.4948** | **0.5684** |
| Wall clock | 23.9 min | **6.9 min** |
| Adapter size | 154.05 MB | **70.49 MB** |
| Emits parseable `BDDGenerateResponse` | yes | **yes** |

Everything else — quantisation (nf4, double quant, fp16), LoRA 16/16/0.0, the
seven target modules, LR 2.0e-4 linear with 5% warmup, 3 epochs, effective batch
8, seq len 2048, seed 3407, T4 — identical to Run 2.

### Result: 4.9× smaller, 3.5× faster, ~15% higher eval loss

The 1.5B costs **+0.0736 eval loss (+14.9%)** for **1/4.9 the parameters** and
**1/3.5 the training time**. That is a far better trade than the parameter count
suggests, and it is the first evidence in this log that **capacity is not the
binding constraint on this task**.

That matters because it corroborates the corpus argument from a new direction.
Story 6.3 and Run 2 both concluded the limit is the training data's *domain*.
If model capacity were the limit, cutting it by 4.9× should have hurt far more
than 15%.

### ⚠️ The smaller model handled the hard clause BETTER — on one prompt

Given the same probe used for Runs 1 and 2:

> AC1: A registered user can request a password reset link by email.
> AC2: A reset link older than one hour is rejected as expired.

**Run 2 (7B)** ignored expiry entirely — it reused AC1's `when` verbatim and
negated its `then`:

```
"when":"I request a password reset link by email",
"then":"I should not receive a password reset link"
```

**Run 3 (1.5B)** produced a genuinely different, expiry-aware scenario:

```
"scenario":"Resetting a password with an expired link",
"given":"...And I have a password reset link that has been used more than once in the last 60 minutes",
"when":"I try to use the password reset link",
"then":"the password reset link should not work"
```

The `when` is no longer copied from AC1, the scenario is named for expiry, and
the `given` **attempts the temporal constraint** the 7B never modelled. It is
still **factually wrong** — "used more than once in the last 60 minutes" is not
"issued more than an hour ago" — so this is confabulation, not comprehension.

**Read this cautiously. It is ONE prompt, not an evaluation.** It cannot support
"1.5B beats 7B"; the proper test is `python -m app.evaluate_models`. What it
does show, and what the loss column actively hides, is that **eval loss is not
tracking the quality anyone cares about here** — the model with the *worse* loss
produced the *better* scenario on the clause that matters. That is the same
warning Run 1 recorded ("fluent form, thin substance"), now with a
counter-example attached.

### Why this run exists

A 7B at q4 needs ~5 GB and so runs on **CPU** on a 2 GB laptop GPU (measured:
~45 s/request against a 12 s budget). A 1.5B at q4 is ~1.1 GB and fits in VRAM,
which makes a local demo viable without raising any timeout. The ablation was
the side benefit that justified doing it properly rather than as a hack.

### ✅ Evaluated end to end — 2026-08-15 (`run-3-1.5b`, 29 items, 58 rows)

Served through `training/serve/app.py` → Ollama `bdd-lora-1.5b`, scored by
`python -m app.evaluate_models`. 1 of 29 fine-tuned rows fell back and was
excluded (3%). Report: [docs/evaluation-report.md](../docs/evaluation-report.md).

**On-domain (the 10 curated product-behaviour items). This group is IDENTICAL
to the one Story 6.3 used for the 7B, so these columns are directly comparable:**

| Metric | 7B (Story 6.3) | **1.5B (Run 3)** | General LLM |
|---|---|---|---|
| Items scored | 10 | 9 (1 degraded) | 10 |
| **AC-clause coverage** | 0.867 | **1.000** | 1.000 |
| Duplicate rate | 0.125 | **0.389** | 0.000 |
| Scenarios per item | 2.700 | 3.333 | 3.100 |
| Latency | 72.6 s | 62.8 s | 4.2 s |

**Off-domain (holdout). NOT directly comparable to Story 6.3 — that run scored
18 items, this one 19, because the parser change altered the holdout:**

| Metric | 7B (18 items) | **1.5B (19 items)** | General LLM (19) |
|---|---|---|---|
| AC-clause coverage | 1.000 | 0.974 | 1.000 |
| Duplicate rate | 0.461 | **0.297** | 0.107 |
| **Reference alignment** | 0.488 | **0.317** | 0.240 |
| Latency | 74.5 s | **12.4 s** | 3.1 s |

### What the evaluation shows

**1. The 1.5B fixed the 7B's headline weakness.** On-domain AC-clause coverage
went 0.867 → **1.000**, level with the general LLM. That was the single metric
Story 6.3 used to conclude the fine-tune lost, and it is measured on the same
ten items. A model with 4.9× fewer parameters covers the criteria the larger one
missed — further evidence that capacity was never the constraint.

**2. Reference alignment still favours the fine-tune** (0.317 vs 0.240), the only
metric grounded in human-authored Gherkin. The margin is narrower than the 7B's
0.488 vs 0.176, but the item sets differ by one, so treat that gap as indicative.

**3. Repetition got materially worse.** On-domain duplicates rose 0.125 → 0.389:
close to two in five scenarios repeat another. Coverage and duplication must be
read together — a model can reach 1.000 coverage by emitting the same scenario
against every clause, and the duplicate rate is what stops that from looking like
success. **This is the 1.5B's real regression**, and it is the reason its perfect
coverage should not be reported on its own.

**4. Latency is still far outside the budget**, at 62.8 s on-domain against a
12 s limit, on a machine where the model does not fit in VRAM.

**Verdict: a genuinely mixed result, and better than the 7B where it counts.**
The serving decision is unchanged — `general_llm` stays the production default,
because a 0.389 duplicate rate and 60 s latency are not shippable. But "the
fine-tune loses on coverage" is no longer true, and any write-up repeating that
line from Story 6.3 needs correcting.

### Artefacts

`training/.kaggle-staging/output/outputs/bdd-lora/` — **git-ignored and
overwritten by the next `--fetch`.** Preserve it before running another fetch.
Run 2's 7B adapter was preserved for exactly this reason at
`training/outputs/run-2-qwen7b/`; Run 1's is at `training/outputs/outputs/bdd-lora/`
and remains the only one registered in Ollama as `bdd-lora`.

---

## Run 2 — 2026-08-15

**Run by:** Chamath (Kaggle UI) · **Platform:** Kaggle · **Kernel:** [bdd-fine-tune-story-6-5](https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5) · **Outcome:** COMPLETE

First run on the post-`Scenario Outline` corpus. Same hyperparameters as Run 1;
**only the dataset changed**, which is what makes the comparison below readable
at all.

| Field | Value |
|---|---|
| Base model | `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` |
| Quantisation | 4-bit QLoRA (nf4, double quant, fp16 compute) |
| LoRA rank / alpha / dropout | 16 / 16 / 0.0 |
| LoRA target modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Learning rate | 2.0e-4 (linear, 5% warmup) |
| Epochs | 3 |
| Effective batch size | 8 (2 × 4 accumulation) |
| Max sequence length | 2048 |
| Seed | 3407 |
| Train pairs | **179** |
| Holdout pairs | **19** |
| Final train loss | **0.4052** |
| **Final eval loss** | **0.4948** |
| Wall clock | **23.9 min** |
| GPU | Tesla T4 |
| Emits parseable `BDDGenerateResponse` | **yes** — *in-process only, same caveat as Run 1* |
| Adapter | `training/.kaggle-staging/output/outputs/bdd-lora/`, 154.05 MB |

> **Artefact location differs from Run 1.** `--fetch` unpacks into
> `training/.kaggle-staging/output/`, **not** `training/outputs/` — Run 1's copy
> under `training/outputs/outputs/bdd-lora/` was placed there by hand. Both are
> git-ignored and both still exist. **Run 1 is the one converted to GGUF and
> registered in Ollama as `bdd-lora`**; Run 2 has not been converted, so every
> serving and evaluation result on record still describes Run 1.

### ⚠️ The eval losses are NOT comparable across runs

**0.4948 vs Run 1's 0.3976 does not mean Run 2 is worse.** The holdout itself
changed — 19 items drawn from the expanded corpus, against Run 1's 18. A loss is
only comparable against an identical eval set, so this pair of numbers cannot be
subtracted. Comparing them would be the same error as reading a different exam's
marks as a decline.

What *is* comparable is the train/eval gap, and it is similar: 0.4052 → 0.4948
(0.090) against Run 1's 0.3483 → 0.3976 (0.049). Slightly wider, still not the
signature of over-fitting at 3 epochs.

### The `Scenario Outline` expansion did not fix the semantics

Prompted with the same unseen AC pair used for Run 1, the model produced
**materially the same answer, including the same defect**:

```json
{"scenarios":[
 {"source_ac_clause":"AC1","feature":"Password Reset",
  "scenario":"Requesting a password reset link",
  "given":"I am a registered user",
  "when":"I request a password reset link by email",
  "then":"I should receive a password reset link"},
 {"source_ac_clause":"AC2","feature":"Password Reset",
  "scenario":"Rejecting an old reset link",
  "given":"I am a registered user",
  "when":"I request a password reset link by email",
  "then":"I should not receive a password reset link"}]}
```

AC2 is about **link expiry**. As in Run 1, its `when` is copied from AC1 and its
`then` is merely AC1 negated. Nothing models "issued more than an hour ago".

**This is the useful result of Run 2.** +9% more pairs of the same kind bought
no semantic improvement whatsoever, which is direct evidence for the conclusion
Story 6.3 reached from the other direction: the constraint is the corpus's
*domain*, not its *size*. More testing-framework Gherkin cannot teach product
reasoning. Only Story 6.4's captured pairs and product-domain uploads (6.7) can.

### Serving decision unchanged

Nothing here justifies revisiting
[serve/README.md](serve/README.md)'s local-only decision. Run 2 was not
converted to GGUF, and on this evidence there is no reason to spend the ~20 GB
and the GPU hours to find out whether an unchanged sample generation serves any
better.

---

## Run 1 — 2026-08-09

**Run by:** Chamath (Kaggle UI) · **Platform:** Kaggle · **Kernel:** [bdd-fine-tune-story-6-5](https://www.kaggle.com/code/chamathranaweera/bdd-fine-tune-story-6-5) · **Outcome:** COMPLETE

| Field | Value |
|---|---|
| Base model | `unsloth/Qwen2.5-7B-Instruct-bnb-4bit` |
| Quantisation | 4-bit QLoRA (nf4, double quant, fp16 compute) |
| LoRA rank / alpha / dropout | 16 / 16 / 0.0 |
| LoRA target modules | q_proj, k_proj, v_proj, o_proj, gate_proj, up_proj, down_proj |
| Trainable parameters | 40,370,176 of 7,655,986,688 (**0.527%**) |
| Learning rate | 2.0e-4 (linear, 5% warmup) |
| Epochs | 3 |
| Effective batch size | 8 (2 × 4 accumulation) |
| Max sequence length | 2048 |
| Seed | 3407 |
| Train pairs | 164 |
| Holdout pairs | 18 |
| Final train loss | **0.3483** |
| **Final eval loss** | **0.3976** |
| Wall clock | **20.9 min** |
| GPU | Tesla T4 (sm_75) |
| Emits parseable `BDDGenerateResponse` | **yes** — *in-process, see caveat below* |
| Adapter | `training/outputs/outputs/bdd-lora/adapter_model.safetensors`, 161.5 MB |
| Chat template | `training/outputs/outputs/bdd-lora/chat_template.jinja` |

> **Where the artefacts are.** Stories 6.2 and 6.3 both need them:
> `training/outputs/outputs/bdd-lora/` (git-ignored; the doubled directory is
> how `kaggle_run.py --fetch` unpacks the kernel output). It holds the LoRA
> adapter, the tokenizer, and the chat template the run trained with. There is
> **no GGUF and no Modelfile** — that export was Unsloth-specific and left with
> unsloth. Converting for Ollama is a manual step documented in
> [serve/README.md](serve/README.md).

> **What "emits parseable `BDDGenerateResponse`" does and does not mean.** It
> was measured by `finetune_bdd.py` calling `model.generate()` **inside the
> Kaggle kernel**, immediately after training. It was **not** measured through
> `training/serve/app.py`. No request has ever been served by this model via the
> shim, so the end-to-end path the application would use is still unproven —
> Story 6.5 Task 7's last item, and it is open.

Training stack: plain `transformers` + `BitsAndBytesConfig` + `peft` +
`transformers.Trainer`. **No unsloth, no trl** — see the note at the bottom.

### Dataset provenance

Built 2026-08-08 by `training/build_dataset.py` from six MIT-licensed public
Gherkin repositories — see [CORPUS.md](CORPUS.md) for sources, commit SHAs and
the full scan output.

```
708 .feature files scanned → 213 documents kept → 182 pairs built
                             (31 dropped during back-generation)
                          → 164 train / 18 holdout, split by origin file
```

Verified after writing and again at train time: every record is
system/user/assistant, every system message matches `BDD_SYSTEM_PROMPT`, every
assistant message validates against `BDDGenerateResponse` with a non-empty
`scenarios` array, and the train/holdout origin sets are disjoint (164 and 18
distinct origins, zero overlap).

### What the model actually produces

Prompted with an AC pair it had never seen:

> AC1: A registered user can request a password reset link by email.
> AC2: A reset link older than one hour is rejected as expired.

```json
{"scenarios":[
 {"source_ac_clause":"AC1","feature":"Password Reset",
  "scenario":"Requesting a password reset link",
  "given":"I am a registered user",
  "when":"I request a password reset link by email",
  "then":"I should receive a password reset link"},
 {"source_ac_clause":"AC2","feature":"Password Reset",
  "scenario":"Rejecting an old reset link",
  "given":"I am a registered user",
  "when":"I request a password reset link by email",
  "then":"I should not receive a password reset link"}]}
```

**Structurally: correct.** Valid JSON, correct schema, one scenario per AC
clause, traceability populated, a sensible feature grouping. The application
can parse this without complaint — which was the hard requirement.

**Semantically: weak, and the eval loss does not show it.** AC2 is about *link
expiry*. The model produced a scenario whose `when` is identical to AC1's and
whose `then` is merely AC1 negated. It never modelled "a link issued more than
an hour ago" or "the user is told it has expired". It learned the *shape* of the
task convincingly and the *reasoning* barely at all.

### ⚠️ Read the eval loss in context

**0.3976 does not mean this model beats the general LLM on real tickets.**

84% of the training pairs came from the test suites of *testing frameworks*
(behat, aruba, cucumber-*), where Gherkin describes running CLI commands rather
than product behaviour. Only 16% (alphagov/whitehall) resembles the domain this
application works in. And the input side of every pair is synthetic —
acceptance criteria were reconstructed backwards by an LLM, because the app has
never captured a real pair.

So the loss measures fluency in the corpus it was given, and that corpus is off
domain. The sample above is exactly what that looks like from the inside:
fluent form, thin substance. **Story 6.3 is the story that answers whether this
is actually better, and it should run before any serving spend is committed.**

### Improving the next run

In order of expected value:

1. **Real captured pairs.** Use the app so Story 6.4's capture fires, and upload
   product-domain `.feature` files through Story 6.7's page. Genuine ACs paired
   with genuine scenarios would fix the semantic weakness above; nothing else
   here will.
2. **More product-domain corpora** — weight toward application repositories over
   testing-framework repositories. Yield is ~30% of scanned files.
3. ~~**Expand `Scenario Outline` blocks**~~ — **DONE 2026-08-13, and it was
   smaller than this entry implied.** `parse_feature` now substitutes Examples
   rows (capped at 3 per outline, so one 40-row table cannot flood the corpus
   with near-identical scenarios).

   Measured: **213 → 222 documents (+4%)** and **+77 scenarios** (9% of the
   corpus total). Not the large win predicted here. The error was reading
   *"outlines skipped: 150"* as 150 discarded **documents** — it counts outline
   **blocks**, and most sat inside files already kept for their regular
   scenarios. Only 9 files were rescued outright.

   Worth having anyway: it also fixes a live upload bug, since a user
   submitting a `Scenario Outline` file was previously rejected — the same
   class of defect as the `Background:` gap this story found.
4. **Epochs.** Train 0.3483 vs eval 0.3976 is a modest, healthy gap — not
   over-fitted at 3 epochs. There is room to train longer once the corpus is
   better; there is little point doing so on this one.

### Why no unsloth

The first plan used unsloth for speed. Two runs on a T4 died inside its
monkey-patched training step with `AttributeError: 'int' object has no
attribute 'mean'`, a version skew against the transformers on Kaggle's image
that survived removing our own version pins. It was replaced with the plain
Hugging Face stack, which trained end to end on the first attempt.

On 164 examples there is no speed worth buying, and the replacement is API
surface that has been stable for years. `trl.SFTTrainer` was avoided for the
same reason — its constructor signature has moved between releases.

Cost of that decision: the GGUF/Modelfile export was unsloth-specific and is
gone. The LoRA adapter is saved normally; GGUF conversion can be done separately
if Story 6.2's serving decision calls for it.

**That cost was larger than it first looked**, and the code review caught it.
The Modelfile was not just a convenience — it was the *only* thing carrying the
training chat template into serving, which AC6 requires. Removing unsloth
silently deleted that guarantee while `training/serve/app.py` and its README
went on asserting it. The replacement does not restore the automatic path (there
is no export to restore); it makes the gap **visible** instead: the run now
writes `chat_template.jinja` beside the adapter, and the shim compares what
Ollama is serving against that file, reporting `match` / `mismatch` / `unknown`
on `/health` and at startup. `unknown` is not a pass.
