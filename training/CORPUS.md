# Training corpus provenance

Story 6.5, Task 1. The corpus itself lives in `training/corpus/` and is **git-ignored** —
these are third-party repositories carrying their own licences, and they are cloned
locally rather than vendored into this repo.

## Why an external corpus at all

Measured against the live database on 2026-08-08, the application's own data yields
**zero** usable training documents:

| Source | Result |
|---|---|
| `--from-db` (default `--db-source uploaded`) | `seen 2 · incomplete steps 2 · kept 0` |
| `--from-db --db-source all` | `seen 10 · unparsable 8 · incomplete 2 · kept 0` |
| `--from-uploads` | `training_datasets` holds 0 rows |
| Captured pairs (Story 6.4) | 0 rows have `acceptance_criteria` — capture has never fired on a real generation |

So the first fine-tune has to be trained on public Gherkin. As the application accumulates
real captured pairs, `--from-db` and `--from-uploads` become the better sources and this
corpus becomes the bootstrap it was always intended to be.

## Sources

All sources are **MIT licensed**. Harvesting is deliberately manual: licence suitability
is a human decision, not something a script should infer.

| Repository | Licence | Commit | `.feature` files |
|---|---|---|---|
| [cucumber/aruba](https://github.com/cucumber/aruba) | MIT | `f22911c9a1914a1273db32b1937284a045d0ec11` | 102 |
| [behat/behat](https://github.com/behat/behat) | MIT | `b077d2c8ddc6c78506e4372150e0b6d9db3e0781` | 251 |
| [cucumber/cucumber-js](https://github.com/cucumber/cucumber-js) | MIT | `43dd6f70d5c2f6d9d68935eb6ecb3696b2119053` | 77 |
| [cucumber/cucumber-jvm](https://github.com/cucumber/cucumber-jvm) | MIT | `ddf0e36f8e62dbed10e9fb2f099e95b2c5ff7cae` | 70 |
| [cucumber/cucumber-ruby](https://github.com/cucumber/cucumber-ruby) | MIT | `e12701f5607207478c32eecb97cc1f6fc4ad55b8` | 153 |
| [alphagov/whitehall](https://github.com/alphagov/whitehall) | MIT | `e196364e2de544bc19bf9e1a6cc1e38a9571d888` | 55 |

**Total: 708 `.feature` files.**

### Deliberately excluded

| Repository | Reason |
|---|---|
| `diaspora/diaspora` | **AGPL-3.0.** Whether a model trained on AGPL sources produces a derivative work is unsettled; not a call to make silently. Cloned, inspected, removed. |
| `cucumber/common` | Restructured upstream — contains no `.feature` files. |
| `SpecFlowOSS/SpecFlow` | Repository no longer resolves. |
| `chef/chef`, `alphagov/smart-answers`, `publify/publify` | No `.feature` files remain. |

## Reproducing the corpus

```bash
mkdir -p training/corpus && cd training/corpus
git clone --depth 1 https://github.com/cucumber/aruba.git
git clone --depth 1 https://github.com/behat/behat.git
git clone --depth 1 https://github.com/cucumber/cucumber-js.git
git clone --depth 1 https://github.com/cucumber/cucumber-jvm.git
git clone --depth 1 https://github.com/cucumber/cucumber-ruby.git
git clone --depth 1 https://github.com/alphagov/whitehall.git
```

Shallow clones do not pin the commit; the SHAs above record what was actually scanned.

## Scan result (2026-08-08)

```
$ uv run --project backend python training/build_dataset.py \
      --features-dir training/corpus --dry-run

Corpus scan
  seen                   708
  opted out              0
  unparsable             97
  outlines skipped       150
  no scenarios           89
  too many scenarios     9
  too large              4
  incomplete steps       280
  placeholder text       0
  duplicates             16
  ready pairs            0
  kept                   213

213 documents kept, 731 scenarios total
```

**213 documents → 213 training pairs** (one document becomes one AC→scenarios example;
the 731 scenarios are distributed across them).

### ⚠️ A parser fix more than doubled this corpus

The first scan of the initial three repositories kept only **45 documents / 80 scenarios**.
Investigation showed **181 of 474 files (38%) used Gherkin `Background:` blocks**, which
`parse_feature` did not understand — a Background declares steps shared by every scenario
in the feature, so scenarios relying on it appeared to have no `Given` and the whole
document was discarded as `incomplete_steps`.

Teaching the parser to inherit Background steps took the same three repositories from
**45 → 107 documents (+138%)** and **80 → 339 scenarios (+324%)**.

This was a **correctness fix, not a relaxed filter**: those scenarios always had a Given.
It also fixes a user-facing bug in Story 6.7 — a valid `.feature` file using a Background
was being rejected at upload with "every scenario needs a complete Given, When and Then".

### If the corpus needs to be bigger

213 examples is at the low end of the 500–2,000 range that is usually recommended, though
quality matters more than volume. The levers, in order of value:

1. **Add more permissively licensed repositories** — yield is currently ~30% of scanned
   files, so roughly 1,000 more `.feature` files would add ~300 documents.
2. **`outlines skipped: 150`** — `Scenario Outline` blocks are dropped because their
   `<placeholders>` make poor targets. Expanding outlines against their Examples tables
   would recover a large block, and is the single biggest remaining source.
3. **Real captured pairs** — the highest-value source by far, since they carry genuine
   acceptance criteria instead of back-generated ones. This needs the app to be used.

Do **not** widen the corpus by loosening the quality thresholds. They are why the
application's own 10 rows were correctly rejected, and a larger corpus of worse Gherkin
produces a worse model while looking like progress.

## Product-domain corpus (added 2026-08-17)

`training/corpus-product/` holds authored tickets rather than harvested Gherkin,
and it exists because of the conclusion RUN_LOG has reached from three
directions: the public corpus above is Gherkin from *testing frameworks*, where
scenarios describe running CLI commands, and no amount of it teaches product
reasoning.

| File | Tickets | Source | Provenance |
|---|---|---|---|
| `resume-generator.yaml` | 20 | `E:\Projects\Python\resume-generator` | Every criterion traceable to a section of that repo's `docs/requirements-analysis.md` |
| `user-management.yaml` | 8 | `E:\Projects\temp\user-mangemetn-server` | Observable behaviour from `App.jsx` / `Users.jsx`; the failure and validation cases are **authored**, not documented there |

**Both sides of every pair are written by hand.** No LLM is involved, which is
deliberate: if a language model wrote the Gherkin, the fine-tune would be
learning to imitate the general model it is meant to beat, and would be capped
at its quality by construction. The YAML is the reviewable source of record;
`build_product_pairs.py` renders it to `.jsonl` and validates every target
against `BDDGenerateResponse`.

**Licensing.** These are internal projects, not third-party code, so the MIT
attribution obligations above do not apply. The tickets paraphrase design
decisions from those repositories rather than copying source.

**Two rules the authored pairs are held to**, both enforced by
`build_product_pairs.py --check`:

1. Every `then` states an *observable outcome*, never a restatement of its
   criterion. Restated outcomes are the "fluent form, thin substance" defect
   RUN_LOG records, and training on them teaches it.
2. Every criterion has at least one scenario, so the reference itself scores
   1.000 coverage. An uncovered clause teaches the model that leaving one
   uncovered is acceptable.

Measured against the legacy corpus on the same metric: the authored pairs have a
**0.000 duplicate rate against the legacy corpus's 0.335**.
