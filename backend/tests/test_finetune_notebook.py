"""The training notebook must not drift from the training script (Story 6.5).

`finetune_bdd.py` is what was debugged across eight Kaggle attempts;
`finetune_bdd.ipynb` is what actually runs on the GPU. The story claims the
notebook is generated from the script — this test is what makes that claim true
rather than aspirational. It lives under backend/tests so it runs with the
normal `pytest` command; a check nobody remembers to run is no check.

Also pins train_config.yaml against the script: a config key that turns nothing
is worse than no key, which is exactly what `use_gradient_checkpointing:
"unsloth"` and `export_gguf: true` were before this review.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_TRAINING_DIR = Path(__file__).resolve().parents[2] / "training"
_CONFIG_PATH = _TRAINING_DIR / "config" / "train_config.yaml"


def _load_sync_module():
    """Import training/sync_notebook.py by path (it is not a package)."""
    spec = importlib.util.spec_from_file_location(
        "sync_notebook", _TRAINING_DIR / "sync_notebook.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_notebook_matches_the_training_script():
    """If this fails: python training/sync_notebook.py"""
    sync = _load_sync_module()

    assert sync.sync(check_only=True) == 0, (
        "finetune_bdd.ipynb has drifted from finetune_bdd.py. "
        "Regenerate it with: python training/sync_notebook.py"
    )


def test_the_notebook_still_invokes_main():
    """Cell 2 defines main(); something has to call it."""
    import json

    sync = _load_sync_module()
    notebook = json.loads(sync.NOTEBOOK_PATH.read_text(encoding="utf-8"))
    code = "".join(
        "".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"
    )

    assert "main()" in code


# ---------------------------------------------------------------------------
# AC4 — train_config.yaml is the single source of truth, and every key is live
# ---------------------------------------------------------------------------


def _config() -> dict:
    return yaml.safe_load(_CONFIG_PATH.read_text(encoding="utf-8"))


def _script_text() -> str:
    return (_TRAINING_DIR / "finetune_bdd.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("model", "base_model"),
        ("model", "load_in_4bit"),
        ("model", "max_seq_length"),
        ("model", "chat_template"),
        ("model", "bnb_4bit_quant_type"),
        ("model", "bnb_4bit_use_double_quant"),
        ("model", "bnb_4bit_compute_dtype"),
        ("lora", "r"),
        ("lora", "use_gradient_checkpointing"),
        ("training", "num_train_epochs"),
        ("training", "fp16"),
        ("training", "eval_strategy"),
        ("evaluation", "sample_max_new_tokens"),
        ("evaluation", "score_holdout"),
        ("evaluation", "holdout_max_items"),
        ("output", "adapter_dir"),
    ],
)
def test_every_config_key_is_read_by_the_training_script(section, key):
    """A knob that turns nothing is a lie about how the run is configured."""
    config = _config()
    assert key in config[section], f"{section}.{key} missing from train_config.yaml"
    assert f'"{key}"' in _script_text(), (
        f"train_config.yaml declares {section}.{key} but finetune_bdd.py never "
        "reads it — wire it up or delete it."
    )


def test_no_gguf_export_is_promised_now_that_unsloth_is_gone():
    """`export_gguf: true` promised an export that never happened, and
    serve/README.md then told you to use its output."""
    config = _config()

    assert "export_gguf" not in config["output"]
    assert "gguf_quantization" not in config["output"]


def test_the_script_does_not_hard_code_hyperparameters_the_run_log_reports():
    """AC7 fields must be transcribed from config, not archaeology."""
    script = _script_text()

    assert "fp16=True" not in script
    assert "max_new_tokens=768" not in script
    assert 'bnb_4bit_quant_type="nf4"' not in script


def test_the_script_validates_the_config_before_spending_gpu_time():
    """A config older than the script must fail in seconds with a cause, not as
    a bare KeyError thirty lines into model loading."""
    script = _script_text()

    assert "def check_config(" in script
    assert script.index("check_config(cfg") < script.index("preflight()"), (
        "check_config must run BEFORE the GPU pre-flight"
    )


# ---------------------------------------------------------------------------
# The holdout metrics the run prints must be THE SAME metrics as the report
#
# The kernel cannot import backend/, so finetune_bdd.py carries its own copy of
# coverage / duplicate_rate / reference_alignment. Two implementations that
# disagree would make the run table and docs/evaluation-report.md quietly
# incomparable — numbers that look like each other and are not. These tests are
# what stop that, and they are the reason the copy is allowed to exist.
# ---------------------------------------------------------------------------

_METRICS_BEGIN = "# --- BEGIN inlined metrics"
_METRICS_END = "# --- END inlined metrics"


def _inlined_metrics() -> dict:
    """Exec the script's copied metric block, alone.

    Importing finetune_bdd.py would drag in torch, transformers and peft, none
    of which belong in the backend's test environment. The block is pure stdlib
    by construction; this test is also what keeps that true.
    """
    import re
    from difflib import SequenceMatcher
    from statistics import mean

    text = _script_text()
    assert _METRICS_BEGIN in text and _METRICS_END in text, (
        "the inlined-metrics sentinels are gone — this test can no longer find "
        "the block it pins, so restore them rather than deleting the test"
    )
    block = text[text.index(_METRICS_BEGIN): text.index(_METRICS_END)]

    namespace: dict = {"re": re, "SequenceMatcher": SequenceMatcher, "mean": mean}
    exec(compile(block, "finetune_bdd.py::metrics", "exec"), namespace)
    return namespace


#: Chosen to exercise what actually differs between two plausible
#: implementations: citation formatting, an unnumbered criteria block, near
#: duplicates with a negated `then`, and an item with nothing to align against.
_SCENARIOS = [
    {
        "source_ac_clause": "AC1",
        "feature": "Password Reset",
        "scenario": "Requesting a link",
        "given": "I am a registered user",
        "when": "I request a password reset link by email",
        "then": "I should receive a password reset link",
    },
    {
        "source_ac_clause": "ac 2:",
        "feature": "Password Reset",
        "scenario": "Rejecting an old link",
        "given": "I am a registered user",
        "when": "I request a password reset link by email",
        "then": "I should not receive a password reset link",
    },
    {
        "source_ac_clause": "AC9",
        "feature": "Password Reset",
        "scenario": "Unrelated",
        "given": "the service is down",
        "when": "I retry",
        "then": "I see an outage page",
    },
]

_CRITERIA = (
    "AC1: A registered user can request a password reset link by email.\n"
    "AC2 - A reset link older than one hour is rejected as expired.\n"
    "AC3. The user is told why the link failed."
)


def test_the_inlined_coverage_matches_the_backend_one():
    from app.services.evaluation_metrics import coverage as backend_coverage

    inlined = _inlined_metrics()["coverage"]

    for criteria, scenarios in [
        (_CRITERIA, _SCENARIOS),
        (_CRITERIA, []),
        ("no clauses here at all", _SCENARIOS),
        ("AC1: only one clause.", _SCENARIOS[:1]),
    ]:
        assert inlined(criteria, scenarios) == backend_coverage(criteria, scenarios)

    # Pin the value too, not just the agreement: two copies of the same mistake
    # would satisfy the comparison above and still be wrong.
    assert inlined(_CRITERIA, _SCENARIOS) == pytest.approx(2 / 3)
    assert inlined("no clauses here at all", _SCENARIOS) is None


def test_the_inlined_duplicate_rate_matches_the_backend_one():
    from app.services.evaluation_metrics import duplicate_rate as backend_duplicates

    inlined = _inlined_metrics()["duplicate_rate"]

    for scenarios in [_SCENARIOS, _SCENARIOS[:1], [], _SCENARIOS[:2]]:
        assert inlined(scenarios) == backend_duplicates(scenarios)

    # The RUN_LOG failure mode, and the reason the threshold is 0.90: a second
    # scenario whose steps copy the first with a negated `then` is caught — one
    # of the two is a duplicate. A copy that scored this 0.0 would report a
    # clean duplicate rate for exactly the defect the metric exists to find.
    assert inlined(_SCENARIOS[:2]) == 0.5
    assert inlined(_SCENARIOS) == pytest.approx(1 / 3)


def test_the_inlined_reference_alignment_matches_the_backend_one():
    from app.services.evaluation_metrics import (
        reference_alignment as backend_alignment,
    )

    inlined = _inlined_metrics()["reference_alignment"]

    for generated, reference in [
        (_SCENARIOS, _SCENARIOS[:2]),
        (_SCENARIOS, None),
        (_SCENARIOS, []),
        ([], _SCENARIOS),
    ]:
        assert inlined(generated, reference) == backend_alignment(generated, reference)

    assert inlined(_SCENARIOS, None) is None, (
        "a missing reference must score None, never 0.0 — scoring it zero would "
        "make a model look worse for an item that carries no ground truth"
    )


def test_the_inlined_ac_clauses_matches_the_backend_one():
    from app.services.evaluation_set import ac_clauses as backend_clauses

    inlined = _inlined_metrics()["ac_clauses"]

    for criteria in [_CRITERIA, "", "AC1: x\nAC1: again", "prose mentioning AC4 only"]:
        assert inlined(criteria) == backend_clauses(criteria)

    assert inlined(_CRITERIA) == ["AC1", "AC2", "AC3"]


def test_the_near_duplicate_threshold_is_the_same_number():
    from app.services import evaluation_metrics as backend

    assert _inlined_metrics()["NEAR_DUPLICATE_RATIO"] == backend.NEAR_DUPLICATE_RATIO


# ---------------------------------------------------------------------------
# Holdout scoring is wired into the run, and cannot cost the adapter
# ---------------------------------------------------------------------------


def test_the_run_scores_the_holdout():
    """Loss does not answer "is it any good at the task" — Run 3 has the worse
    eval loss and the better coverage."""
    script = _script_text()

    assert "def score_holdout(" in script
    assert "score_holdout(" in script[script.index("def main("):], (
        "score_holdout is defined but main() never calls it"
    )


def _script_helpers() -> dict:
    """Exec everything above `def main(` — metrics plus the scoring helpers.

    That whole region is pure stdlib on purpose (the torch/transformers work all
    happens inside main), which is what lets the scoring logic be tested at all
    without a GPU in the loop.
    """
    import json
    import re
    import time
    from difflib import SequenceMatcher
    from statistics import mean

    text = _script_text()
    region = text[text.index(_METRICS_BEGIN): text.index("def main(")]
    namespace: dict = {
        "re": re,
        "json": json,
        "time": time,
        "SequenceMatcher": SequenceMatcher,
        "mean": mean,
    }
    exec(compile(region, "finetune_bdd.py::helpers", "exec"), namespace)
    return namespace


def test_a_truncated_generation_is_not_reported_as_a_bad_model():
    """Run 4 lost one item and said only "UNPARSEABLE". It was the six-clause
    item — the shape most likely to hit the token cap, where the fix is a config
    line, not a better model."""
    reason = _script_helpers()["parse_failure_reason"]

    assert reason('{"scenarios": [{"given": "I am a regis', 768) == "truncated"
    assert reason("Here are some scenarios you might write.", 768) == "prose"
    # Run 5's two failures both came back as "malformed", which was true and
    # unactionable: it covered unparseable output and a single absent key alike.
    assert reason('{"scenarios": []}', 768) == "no scenarios array"
    assert reason('{"result": "ok"}', 768) == "no scenarios array"
    assert reason('{"scenarios": [}]}', 768) == "invalid JSON"

    import json as _json

    complete = {
        "source_ac_clause": "AC1",
        "feature": "f",
        "scenario": "s",
        "given": "g",
        "when": "w",
        "then": "t",
    }
    dropped = {k: v for k, v in complete.items() if k != "feature"}
    assert (
        reason(_json.dumps({"scenarios": [complete, dropped]}), 768)
        == "missing feature"
    ), "a dropped required field must be named, not filed under 'malformed'"


def test_a_reference_is_never_discarded_for_a_missing_field():
    """The human answers are ground truth. Applying the app's schema check to
    them would silently shrink the denominator of reference_alignment."""
    parse = _script_helpers()["parse_scenarios"]
    thin = '{"scenarios": [{"given": "a", "when": "b", "then": "c"}]}'

    assert parse(thin, strict=False) is not None
    assert parse(thin) is None, "the MODEL must still be held to the full schema"
    assert parse("not json") is None
    assert parse('{"scenarios": []}') is None


def test_the_run_reports_what_the_human_answers_score():
    """Measured on the real holdout, the human-authored references score 0.974
    coverage and 0.276 duplicates — NOT 1.0 and 0.0. Printing the model's
    numbers without that ceiling invites reading a matched score as a shortfall
    and a corpus-level duplicate rate as the model misbehaving."""
    script = _script_text()

    assert "def score_reference(" in script
    assert "human reference" in script, (
        "the baseline is computed but never reaches the run table"
    )


def test_the_adapter_is_saved_before_anything_generates():
    """Scoring runs after training and can raise. If the save came later, a bug
    in the metrics would throw away a GPU-hour of training that had already
    succeeded."""
    script = _script_text()
    body = script[script.index("def main("):]

    assert body.index("model.save_pretrained(") < body.index("score_holdout("), (
        "save the adapter BEFORE holdout scoring"
    )


def test_a_scoring_failure_does_not_abort_the_run():
    script = _script_text()

    assert "adapter is safe" in script, (
        "score_holdout must be called inside a try/except — it is measurement, "
        "and measurement must never destroy the thing it measures"
    )


def test_holdout_metrics_reach_the_fetch_table():
    """`--fetch` prints only rows RUN_LOG_ROW matches. A metric the run computes
    and the fetch drops is a metric nobody reads."""
    kaggle_run = _load_kaggle_run_module()
    raw = _kaggle_log(
        "| Holdout pairs | 19 |\n",
        "| Holdout items scored | 18 of 19 |\n",
        "| **Holdout AC-clause coverage** | **0.9737** |\n",
        "| Holdout duplicate rate | 0.2971 |\n",
        "| Holdout reference alignment | 0.3172 |\n",
        "| Holdout scoring wall clock | 4.2 min |\n",
    )

    rows = [
        ln
        for ln in kaggle_run.log_text(raw).splitlines()
        if kaggle_run.RUN_LOG_ROW.match(ln)
    ]

    assert len(rows) == 6, "a holdout metric row was dropped by the fetch regex"
    assert any("coverage" in r for r in rows)


# ---------------------------------------------------------------------------
# The dataset must actually reach Kaggle
#
# The kernel reads train_config.yaml out of the DATASET, while the notebook is
# pushed as a KERNEL. They version independently, so a push that silently fails
# to version the dataset leaves a fresh notebook running against a stale config
# — observed as a KeyError on keys the local file plainly had.
# ---------------------------------------------------------------------------


def _load_kaggle_run_module():
    """Import training/kaggle_run.py by path. Safe without the kaggle client:
    it is imported lazily inside _api()."""
    spec = importlib.util.spec_from_file_location(
        "kaggle_run", _TRAINING_DIR / "kaggle_run.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class _AlreadyExistsResponse:
    """What kaggle's dataset_create_new RETURNS — it does not raise — when the
    dataset is already there."""

    status = "error"
    error = (
        'The requested title "BDD fine-tuning pairs (Story 6.5)" is already in '
        "use by a dataset. Please choose another title."
    )


class _FakeApi:
    def __init__(self, create_result):
        self._create_result = create_result
        self.versioned = False

    def dataset_create_new(self, **_kwargs):
        if isinstance(self._create_result, Exception):
            raise self._create_result
        return self._create_result

    def dataset_create_version(self, **_kwargs):
        self.versioned = True


def test_an_existing_dataset_is_versioned_even_though_kaggle_only_returns_it():
    """THE REGRESSION: the client reports "already in use" by RETURNING an
    error response. Versioning only from an `except` branch meant every push
    reported success while the dataset stayed frozen at version 1."""
    kaggle_run = _load_kaggle_run_module()
    api = _FakeApi(_AlreadyExistsResponse())

    kaggle_run._push_dataset(api, _TRAINING_DIR / ".kaggle-staging" / "dataset")

    assert api.versioned, "a new dataset version was never pushed"


def test_a_raised_already_exists_still_versions():
    """Older clients raised instead of returning. Both must work."""
    kaggle_run = _load_kaggle_run_module()
    api = _FakeApi(Exception("409 Conflict: dataset already exists"))

    kaggle_run._push_dataset(api, _TRAINING_DIR / ".kaggle-staging" / "dataset")

    assert api.versioned


def test_a_genuine_create_does_not_push_a_redundant_version():
    kaggle_run = _load_kaggle_run_module()
    api = _FakeApi(object())  # no status/error attributes == success

    kaggle_run._push_dataset(api, _TRAINING_DIR / ".kaggle-staging" / "dataset")

    assert not api.versioned


def test_an_unrelated_failure_is_not_swallowed_as_already_exists():
    """A 401 must surface, not be quietly retried as a version push."""
    kaggle_run = _load_kaggle_run_module()
    api = _FakeApi(Exception("401 Unauthorized - check KAGGLE_KEY"))

    with pytest.raises(Exception, match="401"):
        kaggle_run._push_dataset(api, _TRAINING_DIR / ".kaggle-staging" / "dataset")

    assert not api.versioned


# ---------------------------------------------------------------------------
# The kernel log is a JSON ARRAY, not text
#
# Newlines inside it are escaped, so the file is effectively one physical line.
# Reading it raw makes every line-anchored regex fail — which is how a fully
# successful run reported "no RUN_LOG table found" with the table in the file.
# ---------------------------------------------------------------------------


def _kaggle_log(*chunks: str) -> str:
    """A log in Kaggle's real on-disk shape."""
    import json

    return json.dumps(
        [{"stream_name": "stdout", "time": i, "data": c} for i, c in enumerate(chunks)]
    )


def test_the_run_log_table_survives_the_json_array_encoding():
    """THE REGRESSION: the table must be found in a real Kaggle log."""
    kaggle_run = _load_kaggle_run_module()
    raw = _kaggle_log(
        "adapter saved to /kaggle/working/outputs/bdd-lora\n",
        "\n| Field | Value |\n|---|---|\n",
        "| Final train loss | 0.4051 |\n| Wall clock | 23.9 min |\n",
        "| GPU | Tesla T4 |\n",
    )

    # Read raw, the way the bug did: no usable line breaks, so nothing matches.
    assert not [ln for ln in raw.splitlines() if kaggle_run.RUN_LOG_ROW.match(ln)]

    text = kaggle_run.log_text(raw)
    rows = [ln for ln in text.splitlines() if kaggle_run.RUN_LOG_ROW.match(ln)]

    assert len(rows) == 3
    assert any("Tesla T4" in r for r in rows)


def test_ansi_escapes_and_carriage_returns_are_stripped():
    """Progress bars redraw with \\r and colour with ANSI; either can hide a row."""
    kaggle_run = _load_kaggle_run_module()
    raw = _kaggle_log("\x1b[32m50%\x1b[0m eta 0:00:01\r| GPU | Tesla T4 |\n")

    text = kaggle_run.log_text(raw)

    assert "\x1b" not in text
    assert [ln for ln in text.splitlines() if kaggle_run.RUN_LOG_ROW.match(ln)]


def test_the_bolded_eval_loss_is_captured():
    """AC7's headline number is emitted as `| **Final eval loss** | **0.49** |`.
    A regex anchored straight after the pipe drops exactly that row."""
    kaggle_run = _load_kaggle_run_module()
    raw = _kaggle_log(
        "| Final train loss | 0.4051 |\n",
        "| **Final eval loss** | **0.49482592940330505** |\n",
        "| Adapter | `outputs/bdd-lora/adapter_model.safetensors` |\n",
        "| Chat template | `outputs/bdd-lora/chat_template.jinja` |\n",
    )

    rows = [
        ln
        for ln in kaggle_run.log_text(raw).splitlines()
        if kaggle_run.RUN_LOG_ROW.match(ln)
    ]

    assert any("eval loss" in r for r in rows), "the eval loss row was dropped"
    assert any("Adapter" in r for r in rows)
    assert any("Chat template" in r for r in rows)
    assert len(rows) == 4


def test_plain_text_and_jsonl_logs_still_read():
    """Older/other kernels may not use the array form."""
    kaggle_run = _load_kaggle_run_module()

    assert "| GPU | Tesla T4 |" in kaggle_run.log_text("| GPU | Tesla T4 |\n")
    assert "hello" in kaggle_run.log_text('{"data": "hello\\n"}')
    assert kaggle_run.log_text("   ") == ""


def test_the_push_names_the_accelerator_it_needs(tmp_path, monkeypatch):
    """`enable_gpu` alone takes the account default, which is often a P100.

    A P100 is sm_60 and current torch wheels build for sm_70 up, so the run
    dies at model loading with "no kernel image is available" — after the
    dataset upload and a GPU slot have already been spent. The shape is named
    explicitly, and this pins that it stays named: the failure is invisible
    until a full run has been burned.
    """
    kaggle_run = _load_kaggle_run_module()

    data = tmp_path / "data"
    data.mkdir()
    for name in ("train.jsonl", "holdout.jsonl"):
        (data / name).write_text("{}\n", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "train_config.yaml").write_text("model: {}\n", encoding="utf-8")
    (tmp_path / "finetune_bdd.ipynb").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(kaggle_run, "TRAINING", tmp_path)
    monkeypatch.setattr(kaggle_run, "DATA", data)
    monkeypatch.setattr(kaggle_run, "STAGING", tmp_path / ".staging")

    _, kernel_dir = kaggle_run.stage("someone")
    metadata = json.loads(
        (kernel_dir / "kernel-metadata.json").read_text(encoding="utf-8")
    )

    assert metadata["enable_gpu"] is True
    assert metadata["machine_shape"] == kaggle_run.MACHINE_SHAPE
    # One of the values ApiSaveKernelRequest.machine_shape documents; a typo
    # here is accepted at push time and only shows up as a failed run.
    assert metadata["machine_shape"] in {
        "NvidiaTeslaT4",
        "NvidiaTeslaP100",
        "Tpu1VmV38",
    }
