# Model evaluation report — Story 6.3

**Run:** `run-3-1.5b` · fine-tuned (`bdd-lora`) vs general LLM baseline.

> **Read the caveats before the numbers.** Three known confounds are listed below, any one of which changes what these tables mean.

## Results by group

The two groups measure different things and are **never averaged**. The gap between them is the more informative comparison: it separates *learned the shape of Gherkin* from *learned this domain*.

### off-domain (holdout, back-generated ACs, has reference)

| Metric | Fine-tuned | General LLM |
|---|---|---|
| Items scored (trustworthy) | 19 | 19 |
| Rows excluded as degraded | 0 | 0 |
| Generation success rate | 1.000 | 1.000 |
| AC-clause coverage | 0.974 | 1.000 |
| Duplicate-scenario rate | 0.296 | 0.107 |
| Reference alignment | 0.317 | 0.240 |
| Scenarios per item | 2.368 | 2.316 |
| Latency (s) | 12.377 | 3.074 |

### on-domain (curated product ACs, no reference)

| Metric | Fine-tuned | General LLM |
|---|---|---|
| Items scored (trustworthy) | 9 | 10 |
| Rows excluded as degraded | 1 | 0 |
| Generation success rate | 1.000 | 1.000 |
| AC-clause coverage | 1.000 | 1.000 |
| Duplicate-scenario rate | 0.389 | 0.000 |
| Reference alignment | n/a | n/a |
| Scenarios per item | 3.333 | 3.100 |
| Latency (s) | 62.819 | 4.237 |

**Metric definitions.** *AC-clause coverage*: fraction of AC clauses with at least one scenario citing them — breadth, not volume. *Duplicate rate*: scenarios that near-duplicate an earlier one, which catches a model padding coverage by restating itself. *Reference alignment*: lexical overlap with human-authored Gherkin; **holdout only**, and deliberately crude — it measures reproduction of the reference's language, not quality. *Success rate*: generations that returned usable scenarios at all.

## Human-edit percentage — NOT MEASURABLE

The epic asks for this metric. It requires rows where a user edited generated Gherkin and saved: `bdd_files` with `source='edited'`. There are **0**.

It is reported as not measurable rather than computed from an empty denominator. Story 6.4's capture mechanism is correct and tested; it has simply never been exercised, which is the same finding that has now blocked three Epic 6 stories.

## Subjective scoring

Not run for this evaluation.

## Caveats that determine how these numbers may be read

**1. Quantization drift (serving path).** The adapter trained under bitsandbytes NF4 and is served on a q4_K_M base. The shim's chat-template check cannot detect this — it compares prompt formats, not weights. **A poor fine-tuned score has two candidate causes**: the fine-tune's own weakness, and drift between training and serving. Ruling the second out requires running a subset through the adapter in `transformers`; see `training/serve/README.md`.

**2. Corpus domain (training).** 84% of the fine-tune's training corpus was testing-framework Gherkin about running CLI commands, not product behaviour. The off-domain group is close to that distribution; the on-domain group is not. Expect the fine-tune to look better on the former for reasons that do not generalise to real tickets.

**3. Evaluation-set provenance.** No item is a real Jira ticket. The holdout's ACs were LLM-back-generated *from their own reference*, which flatters any model reproducing it; the curated items were written by the dev agent. See `docs/evaluation-set.md`.

**Sample size is small.** These numbers will not separate models that are close together.

## Conclusion

_Pending._
