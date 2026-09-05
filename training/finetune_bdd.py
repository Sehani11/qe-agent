"""QLoRA fine-tune for BDD generation — Story 6.5.

Deliberately built on the plain Hugging Face stack: transformers +
BitsAndBytesConfig for 4-bit, peft for the LoRA adapter, and
`transformers.Trainer`. No unsloth, and no trl.

That is a considered choice, not laziness. Two Kaggle runs died inside
unsloth's monkey-patched training step with
`AttributeError: 'int' object has no attribute 'mean'` — a version skew between
unsloth and the transformers on Kaggle's image, which survived removing our own
version pins. unsloth buys speed; on 164 training examples there is no speed
worth buying, and its patching is an entire class of failure this file does not
have. `trl.SFTTrainer` is skipped for the same reason: its constructor
signature has moved between releases.

Everything here is API surface that has been stable for years.

Run it as a script or paste it into a notebook cell:

    python finetune_bdd.py            # reads train_config.yaml next to the data
"""

from __future__ import annotations

import json
import re
import time
from difflib import SequenceMatcher
from pathlib import Path
from statistics import mean

import torch
import yaml
from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    BitsAndBytesConfig,
    DataCollatorForLanguageModeling,
    Trainer,
    TrainingArguments,
)

# Kaggle mounts an attached dataset under /kaggle/input/<slug>/; a hand-uploaded
# session has the files beside the notebook.
SEARCH_ROOTS = [Path("/kaggle/input"), Path("."), Path("data")]

# The resolved chat template is written here beside the adapter, and
# training/serve/app.py reads the same filename to verify what it is serving.
# Duplicated rather than imported on purpose: this file is inlined verbatim into
# finetune_bdd.ipynb and must not import anything from training/serve/.
CHAT_TEMPLATE_FILENAME = "chat_template.jinja"


def find(filename: str) -> Path:
    for root in SEARCH_ROOTS:
        if not root.exists():
            continue
        direct = root / filename
        if direct.exists():
            return direct
        for hit in root.rglob(filename):
            return hit
    raise FileNotFoundError(f"{filename} not found in {SEARCH_ROOTS}")


def load_jsonl(name: str) -> list[dict]:
    path = find(Path(name).name)
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# Every key main() reads, by section. Kept explicit so a config that predates a
# code change is caught here rather than as a bare KeyError thirty lines into
# model loading — the notebook and the config travel to Kaggle through two
# different channels (kernel push vs dataset version) and can arrive skewed.
REQUIRED_KEYS = {
    "model": [
        "base_model",
        "load_in_4bit",
        "max_seq_length",
        "bnb_4bit_quant_type",
        "bnb_4bit_use_double_quant",
        "bnb_4bit_compute_dtype",
    ],
    "lora": [
        "r",
        "alpha",
        "dropout",
        "bias",
        "target_modules",
        "use_gradient_checkpointing",
    ],
    "training": [
        "num_train_epochs",
        "learning_rate",
        "lr_scheduler_type",
        "warmup_ratio",
        "per_device_train_batch_size",
        "gradient_accumulation_steps",
        "weight_decay",
        "optim",
        "seed",
        "logging_steps",
        "fp16",
        "eval_strategy",
    ],
    "data": ["train_path", "holdout_path"],
    "evaluation": ["sample_max_new_tokens"],
    "output": ["adapter_dir"],
}


def check_config(cfg: dict, path: Path) -> None:
    """Fail immediately, and legibly, on a config older than this script."""
    missing = [
        f"{section}.{key}"
        for section, keys in REQUIRED_KEYS.items()
        for key in keys
        if key not in (cfg.get(section) or {})
    ]
    if not missing:
        return
    raise RuntimeError(
        f"train_config.yaml is missing {len(missing)} key(s) this script reads:\n"
        + "".join(f"  - {name}\n" for name in missing)
        + f"\nLoaded from: {path}\n\n"
        "This almost always means the KERNEL IS ATTACHED TO AN OLD DATASET\n"
        "VERSION. The notebook is pushed as a kernel, but train_config.yaml\n"
        "ships inside the dataset - they version separately, so a fresh\n"
        "notebook can run against a stale config.\n\n"
        "Fix, in the Kaggle notebook editor:\n"
        "  1. Right panel -> Input -> remove the bdd-training-data-6-5 dataset\n"
        "  2. Add Input -> Datasets -> Your Datasets -> re-add it (latest)\n"
        "  3. Re-select Accelerator -> GPU T4 x2\n"
        "  4. Save Version -> Save & Run All\n\n"
        "Or push under a brand-new kernel, which always attaches the latest:\n"
        "  python training/kaggle_run.py --push --fresh"
    )


def preflight() -> str:
    """Fail immediately, and legibly, on a GPU this torch cannot drive."""
    if not torch.cuda.is_available():
        raise RuntimeError("No GPU. Set Accelerator to 'GPU T4 x2'.")

    name = torch.cuda.get_device_name(0)
    major, minor = torch.cuda.get_device_capability(0)
    arches = torch.cuda.get_arch_list()
    print(f"GPU: {name} (sm_{major}{minor}) | torch {torch.__version__}")
    print(f"torch was built for: {arches}")

    if f"sm_{major}{minor}" not in arches:
        raise RuntimeError(
            f"{name} is sm_{major}{minor}; this torch supports {arches}. "
            "Set Accelerator to 'GPU T4 x2' (sm_75). A P100 is sm_60 and too "
            "old for current torch wheels."
        )
    print("GPU is compatible - proceeding.")
    return name


# --- BEGIN inlined metrics ---------------------------------------------------
# Copied verbatim in behaviour from backend/app/services/evaluation_metrics.py
# and app.services.evaluation_set.ac_clauses.
#
# Copied rather than imported because a Kaggle kernel has no backend/ — the
# notebook is a single file on a machine that never sees this repository. The
# real hazard is not the duplication but the DRIFT: two implementations of
# "coverage" that disagree would make the numbers this run prints silently
# incomparable with the ones in docs/evaluation-report.md, which is worse than
# printing nothing. backend/tests/test_finetune_notebook.py extracts this block
# by these sentinels and asserts every function agrees with the backend
# original, so drift fails the normal test suite.

#: Above this similarity two scenarios are treated as the same scenario.
NEAR_DUPLICATE_RATIO = 0.90

_NORMALISE_RE = re.compile(r"[^a-z0-9\s]+")
#: What a scenario CITES. Deliberately looser than _AC_CLAUSE_RE: models write
#: "AC 1", "ac1", "AC1:", and penalising formatting would measure obedience to
#: a citation style rather than coverage.
_CLAUSE_RE = re.compile(r"\bAC\s*(\d+)", re.IGNORECASE)
#: What the acceptance criteria DECLARE. Requires a separator so that prose
#: mentioning "AC1" mid-sentence does not invent a clause to be covered.
_AC_CLAUSE_RE = re.compile(r"\bAC\s*(\d+)\s*[:\-\.]", re.IGNORECASE)


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    return " ".join(_NORMALISE_RE.sub(" ", (text or "").lower()).split())


def _steps(scenario: dict) -> str:
    return _normalise(
        f"{scenario.get('given', '')} {scenario.get('when', '')} "
        f"{scenario.get('then', '')}"
    )


def _cited_clause(scenario: dict) -> str | None:
    match = _CLAUSE_RE.search(str(scenario.get("source_ac_clause", "")))
    return f"AC{int(match.group(1))}" if match else None


def ac_clauses(acceptance_criteria: str) -> list[str]:
    """The distinct AC clause labels, in order of first appearance."""
    seen: list[str] = []
    for number in _AC_CLAUSE_RE.findall(acceptance_criteria):
        label = f"AC{int(number)}"
        if label not in seen:
            seen.append(label)
    return seen


def coverage(acceptance_criteria: str, scenarios: list[dict]) -> float | None:
    """Fraction of AC clauses with at least one scenario citing them.

    None when no clauses parse: scoring a malformed item 0 would drag the mean
    down for a reason that has nothing to do with the model. Breadth, not
    volume — three scenarios for one clause do not pay for a clause with none.
    """
    clauses = ac_clauses(acceptance_criteria)
    if not clauses:
        return None
    if not scenarios:
        return 0.0

    cited = {c for c in (_cited_clause(s) for s in scenarios) if c}
    return len(cited & set(clauses)) / len(clauses)


def duplicate_rate(scenarios: list[dict]) -> float:
    """Fraction of scenarios that near-duplicate an earlier one.

    Read this together with coverage, never alone. A model can reach coverage
    1.000 by emitting one scenario against every clause, and this is the metric
    that stops that from looking like success — exactly the failure RUN_LOG
    records for Run 3.
    """
    if len(scenarios) < 2:
        return 0.0

    texts = [_steps(s) for s in scenarios]
    duplicates = 0
    for index, text in enumerate(texts):
        if any(
            SequenceMatcher(None, text, earlier).ratio() >= NEAR_DUPLICATE_RATIO
            for earlier in texts[:index]
        ):
            duplicates += 1
    return duplicates / len(texts)


def reference_alignment(
    generated: list[dict], reference: list[dict] | None
) -> float | None:
    """Mean best-match token overlap against the human-authored Gherkin.

    A crude lexical measure on purpose: it is here to be unarguable, not
    clever. High means the model reproduced the reference's language, which is
    not the same as being good.
    """
    if not reference or not generated:
        return None

    reference_tokens = [set(_steps(r).split()) for r in reference]
    scores: list[float] = []
    for scenario in generated:
        tokens = set(_steps(scenario).split())
        if not tokens:
            scores.append(0.0)
            continue
        best = max(
            (
                len(tokens & ref) / len(tokens | ref) if (tokens | ref) else 0.0
                for ref in reference_tokens
            ),
            default=0.0,
        )
        scores.append(best)
    return mean(scores) if scores else None


# --- END inlined metrics -----------------------------------------------------


#: Every field bdd_service requires of a scenario before it will serve it.
REQUIRED_SCENARIO_FIELDS = {
    "source_ac_clause",
    "feature",
    "scenario",
    "given",
    "when",
    "then",
}


def parse_scenarios(completion: str, strict: bool = True) -> list[dict] | None:
    """The scenarios array from a generation, or None if it is not usable.

    `strict` applies the application's own schema check and is what the model
    is judged on. The human-authored references are read with strict=False:
    they are ground truth, and discarding one for a missing `feature` key would
    silently shrink the denominator of reference_alignment.
    """
    try:
        parsed = json.loads(
            completion[completion.index("{"): completion.rindex("}") + 1]
        )
    except Exception:
        return None

    scenarios = parsed.get("scenarios") if isinstance(parsed, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        return None
    if not all(isinstance(s, dict) for s in scenarios):
        return None
    if strict and not all(REQUIRED_SCENARIO_FIELDS <= set(s) for s in scenarios):
        return None
    return scenarios


def parse_failure_reason(completion: str, max_new_tokens: int) -> str:
    """Why a generation could not be used. The fixes are entirely different.

    Run 4 lost exactly one holdout item and the log said only "UNPARSEABLE",
    which reads as a model defect. The item it lost was the one with six AC
    clauses and the longest reference steps — i.e. the shape most likely to run
    out of tokens, where the fix is a config line rather than a better model.
    Guessing that from the parse rate alone is not something a later reader can
    do, so the run says which it was.
    """
    text = completion.strip()
    if "{" not in text:
        return "prose"  # no JSON attempted at all
    if text.count("{") > text.count("}"):
        return "truncated"  # cut off mid-object, almost always the token cap

    # Run 5 returned "malformed" for both of its failures, which was true and
    # useless: it covers a completion that is not JSON at all and one that is
    # perfect except for a single absent key, and those have nothing to do with
    # each other. Go the rest of the way and name the actual defect.
    try:
        parsed = json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        return "invalid JSON"

    scenarios = parsed.get("scenarios") if isinstance(parsed, dict) else None
    if not isinstance(scenarios, list) or not scenarios:
        return "no scenarios array"

    missing = sorted(
        {
            field
            for scenario in scenarios
            if isinstance(scenario, dict)
            for field in REQUIRED_SCENARIO_FIELDS - set(scenario)
        }
    )
    if missing:
        return "missing " + "/".join(missing)
    return "malformed"


_FAILURE_FIXES = {
    "truncated": "raise evaluation.sample_max_new_tokens",
    "prose": "the model ignored the schema — check the chat template",
    "invalid JSON": "well-bracketed but not parseable — inspect the completion",
    "no scenarios array": "valid JSON without a usable scenarios list",
    "malformed": "shape is right but unusable — inspect the completion",
}


def parse_failure_fix(reason: str) -> str:
    """What to do about a failure reason, including the dynamic 'missing' ones."""
    if reason.startswith("missing "):
        return (
            "the model dropped a required field; bdd_service rejects the whole "
            "response for this, so check every training target carries it"
        )
    return _FAILURE_FIXES.get(reason, "inspect the completion")


def generate(model, tokenizer, system_prompt: str, user_prompt: str, max_new_tokens: int) -> str:
    """One greedy completion, rendered with the model's own chat template.

    Greedy (`do_sample=False`) so that two runs of the same adapter produce the
    same numbers. A sampled decode would make every comparison in RUN_LOG.md
    partly a measurement of luck.
    """
    prompt = tokenizer.apply_chat_template(
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        tokenize=False,
        add_generation_prompt=True,
    )
    inputs = tokenizer([prompt], return_tensors="pt").to(model.device)
    generated = model.generate(
        **inputs, max_new_tokens=max_new_tokens, do_sample=False
    )
    return tokenizer.decode(
        generated[0][inputs["input_ids"].shape[1]:], skip_special_tokens=True
    )


def score_holdout(
    model, tokenizer, records: list[dict], max_new_tokens: int, limit: int = 0
) -> dict:
    """Generate against the whole holdout and score it the way Story 6.3 does.

    Loss answers "how surprised is the model by the reference tokens". It does
    not answer "did it cover the criteria" — Run 3 has the WORSE eval loss than
    Run 2 and the BETTER coverage, so the two can point in opposite directions.
    These metrics are the ones the evaluation report is written from.

    Doing it here rather than locally is a straight cost saving: the same 19
    items took ~62 s each through the local serving path, where the model does
    not fit in VRAM, against seconds each on the T4 that just trained it.

    READ THE SCOPE. This is in-process greedy decode against the holdout, so
    the numbers are comparable BETWEEN RUNS, which is what an ablation needs.
    They are NOT interchangeable with docs/evaluation-report.md, which scores a
    different item set through training/serve/app.py and Ollama, and so also
    measures the serving path.
    """
    items = records[:limit] if limit else records
    coverages: list[float] = []
    duplicates: list[float] = []
    alignments: list[float] = []
    counts: list[int] = []
    failures: list[str] = []
    parsed_ok = 0

    started = time.time()
    print(f"\n--- scoring {len(items)} holdout items ---")
    for index, record in enumerate(items, start=1):
        system_prompt = record["messages"][0]["content"]
        criteria = record["messages"][1]["content"]
        reference = parse_scenarios(record["messages"][2]["content"], strict=False)

        completion = generate(
            model, tokenizer, system_prompt, criteria, max_new_tokens
        )
        scenarios = parse_scenarios(completion)
        if scenarios is None:
            # Counted as a parse failure, not scored as a zero: an unparseable
            # generation is a serving failure, and averaging it in as 0.0
            # coverage would conflate "wrote nothing usable" with "covered no
            # clause". The parse rate below is where it shows up.
            reason = parse_failure_reason(completion, max_new_tokens)
            failures.append(reason)
            print(
                f"  [{index}/{len(items)}] UNPARSEABLE ({reason}: "
                f"{parse_failure_fix(reason)})"
            )
            # The tail alone was not enough to diagnose Run 5 — a completion can
            # look perfect in its last 120 characters and be missing a key three
            # scenarios earlier. Print both ends.
            stripped = completion.strip()
            print(f"      head: {stripped[:200]!r}")
            print(f"      tail: ...{stripped[-200:]!r}")
            continue

        parsed_ok += 1
        item_coverage = coverage(criteria, scenarios)
        item_alignment = reference_alignment(scenarios, reference)
        item_duplicates = duplicate_rate(scenarios)
        counts.append(len(scenarios))
        duplicates.append(item_duplicates)
        if item_coverage is not None:
            coverages.append(item_coverage)
        if item_alignment is not None:
            alignments.append(item_alignment)
        print(
            f"  [{index}/{len(items)}] {len(scenarios)} scenarios | "
            f"coverage {item_coverage if item_coverage is None else round(item_coverage, 3)} | "
            f"dup {item_duplicates:.3f} | "
            f"align {item_alignment if item_alignment is None else round(item_alignment, 3)}"
        )

    return {
        "n_items": len(items),
        "n_parsed": parsed_ok,
        # e.g. "1 truncated" — the per-item lines carry the fix, but only the
        # table rows survive `kaggle_run.py --fetch`, and a parse rate with no
        # cause attached is not something anyone can act on later.
        "failures": ", ".join(
            f"{failures.count(kind)} {kind}"
            for kind in dict.fromkeys(failures)
        ),
        "parse_rate": round(parsed_ok / len(items), 4) if items else None,
        "coverage": _mean(coverages),
        "duplicate_rate": _mean(duplicates),
        "reference_alignment": _mean(alignments),
        "scenarios_per_item": _mean(counts),
        "minutes": (time.time() - started) / 60,
    }


def _mean(values) -> float | None:
    return round(mean(values), 4) if values else None


def score_reference(records: list[dict], limit: int = 0) -> dict:
    """The same metrics over the HUMAN-AUTHORED answers — i.e. the ceiling.

    Costs no GPU time and stops the model's numbers being read against 1.0 and
    0.0, which are not the targets. Measured on the real holdout: the
    references themselves score coverage 0.974, because one item's human
    Gherkin does not cite every clause its back-generated AC block declares.
    Duplicates matter more — the human scenarios near-duplicate each other
    27.6% of the time, so a model at ~0.30 is reproducing its training data
    rather than misbehaving, and reporting that as a defect would be wrong.

    reference_alignment is absent by construction: comparing the references
    with themselves is 1.0 and says nothing.
    """
    items = records[:limit] if limit else records
    coverages: list[float] = []
    duplicates: list[float] = []
    counts: list[int] = []

    for record in items:
        criteria = record["messages"][1]["content"]
        scenarios = parse_scenarios(record["messages"][2]["content"], strict=False)
        if not scenarios:
            continue
        item_coverage = coverage(criteria, scenarios)
        if item_coverage is not None:
            coverages.append(item_coverage)
        duplicates.append(duplicate_rate(scenarios))
        counts.append(len(scenarios))

    return {
        "coverage": _mean(coverages),
        "duplicate_rate": _mean(duplicates),
        "scenarios_per_item": _mean(counts),
    }


def main() -> int:
    # find() returns the FIRST match and there can be several — an old dataset
    # version left attached alongside a new one resolves silently to the wrong
    # file, so print which one won before trusting anything in it.
    config_path = find("train_config.yaml")
    cfg = yaml.safe_load(config_path.read_text())
    print(f"config: {config_path}")
    print(json.dumps(cfg, indent=2))
    check_config(cfg, config_path)

    gpu_name = preflight()

    train_records = load_jsonl(cfg["data"]["train_path"])
    holdout_records = load_jsonl(cfg["data"]["holdout_path"])

    # The split is by ORIGIN FILE, never per row: two scenarios from one feature
    # file are near-duplicates, and splitting per row would leak them across the
    # boundary and make the eval loss a lie.
    train_origins = {r["meta"]["origin"] for r in train_records}
    holdout_origins = {r["meta"]["origin"] for r in holdout_records}
    leaked = train_origins & holdout_origins
    if leaked:
        raise RuntimeError(f"Holdout leak on origins: {sorted(leaked)[:3]}")
    print(f"train {len(train_records)} pairs / {len(train_origins)} origins")
    print(f"holdout {len(holdout_records)} pairs / {len(holdout_origins)} origins")
    print("no origin overlap - holdout is honest")

    base = cfg["model"]["base_model"]
    max_len = cfg["model"]["max_seq_length"]

    tokenizer = AutoTokenizer.from_pretrained(base, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # An override lets a run pin a template explicitly; null keeps the base
    # model's own. Either way the RESOLVED template is saved beside the adapter
    # below, because that file is what the serving shim verifies against.
    if cfg["model"].get("chat_template"):
        tokenizer.chat_template = cfg["model"]["chat_template"]

    quant = BitsAndBytesConfig(
        load_in_4bit=cfg["model"]["load_in_4bit"],
        bnb_4bit_quant_type=cfg["model"]["bnb_4bit_quant_type"],
        bnb_4bit_use_double_quant=cfg["model"]["bnb_4bit_use_double_quant"],
        bnb_4bit_compute_dtype=getattr(torch, cfg["model"]["bnb_4bit_compute_dtype"]),
    )
    model = AutoModelForCausalLM.from_pretrained(
        base,
        quantization_config=quant,
        device_map="auto",
        trust_remote_code=True,
    )
    use_grad_ckpt = cfg["lora"]["use_gradient_checkpointing"]
    model = prepare_model_for_kbit_training(
        model, use_gradient_checkpointing=use_grad_ckpt
    )
    model.config.use_cache = not use_grad_ckpt  # the two are incompatible

    model = get_peft_model(
        model,
        LoraConfig(
            r=cfg["lora"]["r"],
            lora_alpha=cfg["lora"]["alpha"],
            lora_dropout=cfg["lora"]["dropout"],
            bias=cfg["lora"]["bias"],
            target_modules=cfg["lora"]["target_modules"],
            task_type="CAUSAL_LM",
        ),
    )
    model.print_trainable_parameters()

    # Render each conversation with the model's own chat template. The serving
    # shim must reproduce this exact template or quality drops silently.
    def encode(records: list[dict]):
        rows = []
        for record in records:
            text = tokenizer.apply_chat_template(
                record["messages"], tokenize=False, add_generation_prompt=False
            )
            enc = tokenizer(text, truncation=True, max_length=max_len)
            rows.append({"input_ids": enc["input_ids"], "attention_mask": enc["attention_mask"]})
        return rows

    train_ds = encode(train_records)
    eval_ds = encode(holdout_records)
    print(f"tokenised: {len(train_ds)} train / {len(eval_ds)} eval")

    t = cfg["training"]
    args = TrainingArguments(
        output_dir="outputs/checkpoints",
        num_train_epochs=t["num_train_epochs"],
        learning_rate=float(t["learning_rate"]),
        lr_scheduler_type=t["lr_scheduler_type"],
        warmup_ratio=t["warmup_ratio"],
        per_device_train_batch_size=t["per_device_train_batch_size"],
        per_device_eval_batch_size=t["per_device_train_batch_size"],
        gradient_accumulation_steps=t["gradient_accumulation_steps"],
        weight_decay=t["weight_decay"],
        optim=t["optim"],
        seed=t["seed"],
        logging_steps=t["logging_steps"],
        fp16=t["fp16"],
        report_to=[],
        save_strategy="no",
        # AC7 requires a measured eval loss, so the holdout is genuinely evaluated.
        eval_strategy=t["eval_strategy"],
    )

    trainer = Trainer(
        model=model,
        args=args,
        train_dataset=train_ds,
        eval_dataset=eval_ds,
        data_collator=DataCollatorForLanguageModeling(tokenizer, mlm=False),
    )

    started = time.time()
    trainer.train()
    wall_clock_minutes = (time.time() - started) / 60

    history = trainer.state.log_history
    final_train_loss = next(
        (h["loss"] for h in reversed(history) if "loss" in h), None
    )
    final_eval_loss = next(
        (h["eval_loss"] for h in reversed(history) if "eval_loss" in h), None
    )

    # SAVE BEFORE MEASURING. Everything below this point is generation, and a
    # generation step that dies takes the whole session's GPU time with it if
    # the adapter is still only in memory. Training is the expensive part and it
    # is already done; scoring is worth minutes, not a re-run.
    adapter_dir = Path(cfg["output"]["adapter_dir"])
    model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)

    # Pin the chat template next to the adapter, explicitly rather than relying
    # on save_pretrained's version-dependent behaviour. training/serve/app.py
    # verifies the served model against THIS file: AC6 requires serving to use
    # the template training used, and after unsloth (and its Modelfile export)
    # was dropped there is nothing else carrying it across.
    template = tokenizer.chat_template or ""
    if template:
        (adapter_dir / CHAT_TEMPLATE_FILENAME).write_text(template, encoding="utf-8")
    else:
        print("WARNING: tokenizer has no chat_template; serving cannot be verified")

    print(f"adapter saved to {adapter_dir.resolve()}")
    print(f"chat template saved to {(adapter_dir / CHAT_TEMPLATE_FILENAME).resolve()}")

    # Does the tuned model emit something the APPLICATION can actually parse? A
    # low loss on a model that returns prose is worthless: bdd_service validates
    # every response against BDDGenerateResponse.
    model.config.use_cache = True  # gradient checkpointing turned this off
    sample_tokens = cfg["evaluation"]["sample_max_new_tokens"]
    system_prompt = train_records[0]["messages"][0]["content"]
    completion = generate(
        model,
        tokenizer,
        system_prompt,
        "AC1: A registered user can request a password reset link by email.\n"
        "AC2: A reset link older than one hour is rejected as expired.",
        sample_tokens,
    )
    print("\n--- sample generation ---")
    print(completion[:1200])
    parseable = parse_scenarios(completion) is not None
    print("BDDGenerateResponse-shaped:", "yes" if parseable else "NO")

    # Both default rather than being required: an old config attached to a new
    # notebook should still produce a trained adapter, and skipped scoring is
    # announced below rather than inferred from a missing table row.
    evaluation = cfg["evaluation"]
    scores = None
    if evaluation.get("score_holdout", True):
        try:
            scores = score_holdout(
                model,
                tokenizer,
                holdout_records,
                sample_tokens,
                limit=evaluation.get("holdout_max_items", 0),
            )
        except Exception as exc:  # noqa: BLE001 - never lose a trained adapter
            print(f"\nWARNING: holdout scoring failed ({exc!r}); adapter is safe.")
    else:
        print("\nholdout scoring: DISABLED by evaluation.score_holdout")

    # What the human-authored answers score on the same metrics. Free, and it
    # is the difference between "0.974 coverage" reading as a shortfall and
    # reading as the ceiling.
    baseline = score_reference(
        holdout_records, limit=evaluation.get("holdout_max_items", 0)
    )

    def _score(key: str) -> str:
        """A metric, or an honest n/a — never a 0.0 standing in for 'not run'."""
        if not scores or scores.get(key) is None:
            return "n/a"
        return f"{scores[key]}"

    def _versus(key: str) -> str:
        """The human ceiling, alongside — kept OUTSIDE the bold, so the headline
        number stays the model's own."""
        value = baseline.get(key)
        return f" (human reference: {value})" if value is not None else ""

    holdout_rows = (
        f"""| Holdout items scored | {scores['n_parsed']} of {scores['n_items']} |
| Holdout JSON parse rate | {_score('parse_rate')}{f" ({scores['failures']})" if scores.get('failures') else ""} |
| **Holdout AC-clause coverage** | **{_score('coverage')}**{_versus('coverage')} |
| Holdout duplicate rate | {_score('duplicate_rate')}{_versus('duplicate_rate')} |
| Holdout reference alignment | {_score('reference_alignment')} |
| Holdout scenarios per item | {_score('scenarios_per_item')}{_versus('scenarios_per_item')} |
| Holdout scoring wall clock | {scores['minutes']:.1f} min |"""
        if scores
        else "| Holdout scoring | not run |"
    )

    print(
        f"""
| Field | Value |
|---|---|
| Base model | `{base}` |
| Quantisation | 4-bit QLoRA (nf4, double quant) |
| LoRA rank / alpha / dropout | {cfg['lora']['r']} / {cfg['lora']['alpha']} / {cfg['lora']['dropout']} |
| LoRA target modules | {', '.join(cfg['lora']['target_modules'])} |
| Learning rate | {t['learning_rate']} |
| Epochs | {t['num_train_epochs']} |
| Effective batch size | {t['per_device_train_batch_size'] * t['gradient_accumulation_steps']} |
| Max sequence length | {max_len} |
| Seed | {t['seed']} |
| Train pairs | {len(train_records)} |
| Holdout pairs | {len(holdout_records)} |
| Final train loss | {final_train_loss} |
| **Final eval loss** | **{final_eval_loss}** |
{holdout_rows}
| Wall clock | {wall_clock_minutes:.1f} min |
| GPU | {gpu_name} |
| Emits parseable BDDGenerateResponse | {'yes' if parseable else 'NO - investigate before serving'} |
| Adapter | `{adapter_dir}/adapter_model.safetensors` |
| Chat template | `{adapter_dir}/{CHAT_TEMPLATE_FILENAME}` |

NOTE: everything below "Final eval loss" was measured IN THIS PROCESS, not
through training/serve/app.py. Serving the adapter through the shim is a
separate, still-unverified step - see training/serve/README.md.

The Holdout rows are comparable BETWEEN RUNS of this script - same items, same
greedy decode - which is what an ablation needs. They are NOT interchangeable
with docs/evaluation-report.md: that scores a different item set through Ollama,
and so measures the serving path as well as the model. Wall clock above covers
TRAINING only; holdout scoring is timed separately on its own row.
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
