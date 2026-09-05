# Evaluation set — Story 6.3

**Assembled 2026-08-13.** 28 items, 101 AC clauses. AC1's ≥20 gate: **PASS**.

This document exists because Epic 6.3's opening premise is false. It reads
*"Given a set of 20+ real Jira tickets with known acceptance criteria"* — and
there are none. Measured against the live database on 2026-08-13:

| Source | Query | Result |
|---|---|---|
| Captured AC→Gherkin pairs | `bdd_files WHERE acceptance_criteria IS NOT NULL` | **0** |
| Distinct Jira tickets ever ingested | `count(DISTINCT jira_ticket_id) FROM sessions` | **5** |
| Manual uploads (Story 6.7) | `training_datasets` | **0** |

Story 6.4's capture has still never fired — it was 0 when Story 6.5 measured it
on 2026-08-08, and it is 0 today. So the set below is assembled from what
exists, and every item is labelled with how it came to exist.

---

## Composition

| Group | Items | AC provenance | Reference Gherkin | Scoring available |
|---|---|---|---|---|
| **off_domain** — Story 6.5 holdout | **18** | `back_generated` | ✅ human-authored, 70 scenarios | Structural + reference similarity + blinded judge |
| **on_domain** — curated | **10** | `agent_authored` | ❌ none | Structural + blinded judge only |

**These two groups must be reported separately.** They measure different
things, and averaging them yields a number that describes neither.

### off_domain — `training/data/holdout.jsonl` (18 items)

Story 6.5's holdout, split **by origin file** so no scenario appears in both
training and holdout — verified in `finetune_bdd.py` at train time, and pinned
again by `test_no_evaluation_item_comes_from_the_training_split`.

Source repositories:

| Repo | Items |
|---|---|
| aruba | 8 |
| behat | 5 |
| cucumber-js | 3 |
| alphagov/whitehall | 2 |

⚠️ **16 of 18 come from the test suites of testing frameworks.** Their Gherkin
describes running CLI commands (*"When I run `rspec`"*), not product behaviour.
Only the 2 whitehall items resemble the domain this application works in.

⚠️ **The AC text is LLM-back-generated**, reconstructed from the Gherkin by
`build_dataset.py` because the app has never captured a real pair. An AC derived
from its own reference describes that reference by construction, which flatters
any model that reproduces it. This is the weakest link in the whole evaluation.

✅ **What it is good for:** it is the only source with a human-authored
reference, so it is the only group where similarity to a real target can be
computed without an LLM judge.

### on_domain — `evaluation/curated_ac_sets.json` (10 items)

Product-behaviour acceptance criteria of the kind this application converts from
Jira tickets, across: authentication, authorization, payments, search, file
management, notifications, scheduling, api.

⚠️ **Provenance is `agent_authored`, not human.** These were written by the dev
agent for this evaluation. They are **not** real Jira tickets and are **not**
human-authored; labelling them otherwise would misstate the evaluation's
validity. They carry no reference Gherkin.

✅ **What they are good for:** they are on-distribution for the *product*, and
off-distribution for the *fine-tune*. The holdout is the reverse. The gap
between the two groups is the most informative number this evaluation can
produce — it separates "learned Gherkin's shape" from "learned this domain".

---

## Known limits of this set

1. **No `human_jira` items at all.** The provenance the epic actually asked for
   is absent. Every conclusion is weaker for it.
2. **Back-generated ACs on the reference group.** Circular by construction.
3. **Agent-authored ACs on the on-domain group.** Not real tickets, and written
   by the same kind of system being evaluated.
4. **Small n.** 28 items will not separate models that are close.

## The single best improvement available

**Use the application on 20+ real Jira tickets.** That would:

- fire Story 6.4's capture for the first time, creating genuine AC→Gherkin pairs;
- upgrade this set's provenance from `agent_authored` to `human_jira`;
- and seed the corpus every future fine-tune depends on — Story 6.5 identified
  back-generated ACs as the *"single biggest quality ceiling"* on the current model.

One activity fixes the evaluation, the training data, and the next fine-tune.
Nothing else on this list comes close.

---

## Reproducing

```python
from app.services.evaluation_set import load_evaluation_set
items = load_evaluation_set()   # 28 EvalItem, each labelled with group + provenance
```

`holdout.jsonl` is git-ignored derived data. If it is missing, the loader returns
only the 10 curated items and the AC1 gate fails — rebuild with
`training/build_dataset.py` rather than proceeding on a partial set.
