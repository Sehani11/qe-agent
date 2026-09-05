"""Drive the Story 6.5 fine-tune on Kaggle's free GPU, end to end.

The training run itself is not something a dev agent can fake, and this script
does not try to: it uploads the real dataset, pushes a real GPU kernel, waits
for Kaggle to finish it, and pulls back the real log. What lands in RUN_LOG.md
comes from an execution that actually happened.

    python training/kaggle_run.py --all        # push, wait, fetch
    python training/kaggle_run.py --push       # push only
    python training/kaggle_run.py --status     # poll once
    python training/kaggle_run.py --fetch      # download output + print the log table
    python training/kaggle_run.py --purge      # delete the dataset + kernel from Kaggle

Credentials (both are required — a key alone is not enough):

    KAGGLE_USERNAME=<your kaggle username>
    KAGGLE_KEY=<32-char hex key from kaggle.json>

Get them from kaggle.com -> Settings -> API -> "Create New Token", which
downloads a kaggle.json containing both. KAGGLE_API_KEY is also accepted as an
alias for KAGGLE_KEY.

Prerequisites on the Kaggle account:
  * phone-verified — Kaggle refuses `enable_gpu` and `enable_internet` otherwise
  * free GPU quota remaining (~30 h/week; this run needs well under an hour)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

# Windows consoles default to cp1252, which cannot encode the characters used
# below; without this the script dies on its own progress messages.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, OSError):
        pass

REPO_ROOT = Path(__file__).resolve().parents[1]
TRAINING = REPO_ROOT / "training"
DATA = TRAINING / "data"
STAGING = TRAINING / ".kaggle-staging"

DATASET_SLUG = "bdd-training-data-6-5"

#: The accelerator to ask for, by name.
#:
#: REQUIRED, not a nicety. Without it the session takes the account default,
#: which is commonly a P100 — sm_60, while current torch wheels build for
#: sm_70 and up — so the run dies at model loading with "no kernel image is
#: available for execution on the device". `diagnose()` matches that signature.
#:
#: The Kaggle client did not always expose this; when it did not, the
#: accelerator had to be chosen in the browser and an API push reset it. That
#: is no longer true, and the instructions saying so are stale. Supported
#: values are documented on `ApiSaveKernelRequest.machine_shape`:
#: NvidiaTeslaT4, NvidiaTeslaP100, Tpu1VmV38.
MACHINE_SHAPE = "NvidiaTeslaT4"
# Kaggle derives the kernel slug from the TITLE, not from the id you supply,
# and silently creates a differently-named kernel if they disagree. These two
# are kept consistent deliberately; _resolve_kernel_ref covers the rest.
#
# The title must therefore slugify to the slug: lowercased, spaces to dashes,
# punctuation dropped. "BDD fine-tune (Story 6.5)" slugified to
# `bdd-fine-tune-story-6-5`, which is what the kernel was called until the
# story number stopped meaning anything to anyone reading the account.
KERNEL_TITLE = "BDD fine-tune"
KERNEL_SLUG = "bdd-fine-tune"


def use_fresh_kernel_name() -> None:
    """Push under a brand-new kernel id instead of updating the existing one.

    A kernel created while the account lacked GPU/internet entitlement keeps
    `enable_gpu=false, enable_internet=false` on its record, and re-pushing the
    same id does not lift them — the session then dies instantly with an empty
    log. Creating a new kernel makes Kaggle evaluate entitlement afresh.
    """
    global KERNEL_TITLE, KERNEL_SLUG
    stamp = time.strftime("%m%d-%H%M")
    KERNEL_TITLE = f"BDD fine tune {stamp}"
    KERNEL_SLUG = KERNEL_TITLE.lower().replace(" ", "-")
POLL_SECONDS = 30
POLL_TIMEOUT_SECONDS = 3 * 60 * 60  # a T4 run of this size is minutes, not hours


class SetupError(RuntimeError):
    """Something the operator has to fix before this can run."""


def _load_dotenv() -> None:
    """Pull KAGGLE_* values out of the repo-root .env into the environment.

    Only KAGGLE_* keys are read: the rest of that file holds unrelated
    credentials, and app.core.config already rejects it wholesale. Existing
    environment variables win, so an explicit export can still override.
    """
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("KAGGLE") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if value and not os.environ.get(key):
            os.environ[key] = value


def _credentials() -> str:
    """Return the Kaggle username, after validating both halves are present."""
    _load_dotenv()
    username = os.environ.get("KAGGLE_USERNAME", "").strip()
    key = (
        os.environ.get("KAGGLE_KEY")
        or os.environ.get("KAGGLE_API_KEY")
        or ""
    ).strip()

    if not username:
        raise SetupError(
            "KAGGLE_USERNAME is not set.\n"
            "Kaggle authenticates on username + key; the key alone cannot "
            "identify an account. Both are in the kaggle.json you get from\n"
            "  kaggle.com -> Settings -> API -> Create New Token"
        )
    if not key:
        raise SetupError("Neither KAGGLE_KEY nor KAGGLE_API_KEY is set.")

    # Kaggle keys are 32 lowercase hex characters. Catching a mismatched token
    # here beats a 401 three steps later with no explanation.
    if not re.fullmatch(r"[0-9a-f]{32}", key):
        print(
            f"  ! warning: the key does not look like a Kaggle API key "
            f"(expected 32 hex characters, got {len(key)}). If authentication "
            "fails, this is why.",
            file=sys.stderr,
        )

    os.environ["KAGGLE_USERNAME"] = username
    os.environ["KAGGLE_KEY"] = key
    return username


def _api():
    """Authenticate lazily — importing kaggle reads credentials at import time."""
    _credentials()
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    return api


def _require_dataset_files() -> None:
    missing = [
        str(p)
        for p in (DATA / "train.jsonl", DATA / "holdout.jsonl")
        if not p.exists()
    ]
    if missing:
        raise SetupError(
            "Missing dataset files: "
            + ", ".join(missing)
            + "\nBuild them first:\n"
            "  uv run --project backend python training/build_dataset.py "
            "--features-dir training/corpus --out training/data"
        )


def stage(username: str) -> tuple[Path, Path]:
    """Lay out the dataset and kernel folders Kaggle's API expects."""
    _require_dataset_files()

    dataset_dir = STAGING / "dataset"
    kernel_dir = STAGING / "kernel"
    for d in (dataset_dir, kernel_dir):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir(parents=True)

    # --- dataset: the training pairs plus the config that produced them ---
    shutil.copy2(DATA / "train.jsonl", dataset_dir)
    shutil.copy2(DATA / "holdout.jsonl", dataset_dir)
    shutil.copy2(TRAINING / "config" / "train_config.yaml", dataset_dir)
    (dataset_dir / "dataset-metadata.json").write_text(
        json.dumps(
            {
                "title": "BDD fine-tuning pairs (Story 6.5)",
                "id": f"{username}/{DATASET_SLUG}",
                "licenses": [{"name": "other"}],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # --- kernel: the notebook, wired to the dataset above ---
    shutil.copy2(TRAINING / "finetune_bdd.ipynb", kernel_dir)
    (kernel_dir / "kernel-metadata.json").write_text(
        json.dumps(
            {
                "id": f"{username}/{KERNEL_SLUG}",
                "title": KERNEL_TITLE,
                "code_file": "finetune_bdd.ipynb",
                "language": "python",
                "kernel_type": "notebook",
                # Private: this dataset is derived training data, and the repo's
                # governance work (Stories 6.6/6.7) exists to keep it that way.
                "is_private": True,
                "enable_gpu": True,
                # `enable_gpu` alone only says "a GPU"; this says WHICH. See
                # MACHINE_SHAPE — the account default is a P100 the current
                # torch build cannot use.
                "machine_shape": MACHINE_SHAPE,
                # Needed to pip install unsloth and pull the base model weights.
                "enable_internet": True,
                "dataset_sources": [f"{username}/{DATASET_SLUG}"],
                "competition_sources": [],
                "kernel_sources": [],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return dataset_dir, kernel_dir


def _reports_already_exists(outcome: object) -> bool:
    """Whether Kaggle is saying the dataset exists — RAISED or merely RETURNED.

    `dataset_create_new` does not raise when the dataset is already there. It
    builds an `ApiCreateDatasetResponse` with `status = "error"` and returns it,
    so an `except` clause alone never sees it. Both shapes are checked here.
    """
    parts = [str(outcome)]
    for attribute in ("status", "error", "message"):
        value = getattr(outcome, attribute, None)
        if value:
            parts.append(str(value))
    text = " ".join(parts).lower()
    return "already" in text or "in use" in text or "409" in text


def _push_dataset(api, dataset_dir: Path) -> None:
    """Create the dataset, or push a NEW VERSION when it already exists.

    Getting this wrong is silent and expensive. The old code only versioned from
    an `except` branch, but the client *returns* the "already in use" verdict
    instead of raising it — so every push reported "created" while the dataset
    stayed frozen at version 1. The kernel then trained against a
    `train_config.yaml` that predated the unsloth removal, and the failure
    surfaced as a `KeyError` on a key the local file plainly had.
    """
    existing = False
    try:
        result = api.dataset_create_new(
            folder=str(dataset_dir), public=False, dir_mode="skip"
        )
        existing = _reports_already_exists(result)
        if not existing:
            print("  created")
    except Exception as exc:
        if not _reports_already_exists(exc):
            raise
        existing = True

    if existing:
        api.dataset_create_version(
            folder=str(dataset_dir),
            version_notes=f"rebuild {time.strftime('%Y-%m-%d %H:%M')}",
            dir_mode="skip",
        )
        print("  new version pushed")


def push(api, username: str) -> None:
    dataset_dir, kernel_dir = stage(username)

    print(f"-> uploading dataset {username}/{DATASET_SLUG} (private)")
    _push_dataset(api, dataset_dir)

    # Kaggle needs a moment to finish ingesting a brand-new dataset before a
    # kernel can declare it as a source.
    time.sleep(10)

    print(f"-> pushing kernel {username}/{KERNEL_SLUG} (GPU, internet on)")
    api.kernels_push(folder=str(kernel_dir))
    print(f"  https://www.kaggle.com/code/{username}/{KERNEL_SLUG}")


def _resolve_kernel_ref(api, username: str) -> str:
    """Find the kernel Kaggle actually created.

    A push whose title does not slugify to the requested id lands under a
    different slug, and every later call then 403s with a message about
    permissions rather than about naming. Match on title first.
    """
    try:
        for kernel in api.kernels_list(user=username, page_size=50):
            if getattr(kernel, "title", None) == KERNEL_TITLE:
                return str(kernel.ref)
    except Exception:
        pass
    return f"{username}/{KERNEL_SLUG}"


def status(api, username: str) -> str:
    result = api.kernels_status(_resolve_kernel_ref(api, username))

    # The client returns a model object on some versions and a plain dict on
    # others; read either without caring which.
    def field(name: str, *aliases: str):
        for candidate in (name, *aliases):
            if isinstance(result, dict) and candidate in result:
                return result[candidate]
            if hasattr(result, candidate):
                return getattr(result, candidate)
        return None

    raw_state = field("status")
    # KernelWorkerStatus is an IntEnum: .value is a bare number, so prefer
    # .name and fall back to the string form for older clients.
    state = str(
        getattr(raw_state, "name", None) or raw_state or "unknown"
    ).split(".")[-1]
    message = field("failureMessage", "failure_message")
    print(f"  status: {state}" + (f" — {message}" if message else ""))
    return state


def wait(api, username: str) -> str:
    deadline = time.time() + POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        state = status(api, username).lower()
        if state in {
            "complete",
            "error",
            "cancelacknowledged",
            "cancelrequested",
            "cancel_acknowledged",
            "cancel_requested",
        }:
            return state
        time.sleep(POLL_SECONDS)
    raise SetupError("Timed out waiting for the kernel to finish.")


# `\*{0,2}` is load-bearing: the notebook emits the headline metric as
# `| **Final eval loss** | **0.4948** |`, and without tolerating the markdown
# bold this regex dropped the one number AC7 actually requires while happily
# reporting every row around it.
RUN_LOG_ROW = re.compile(
    r"^\s*\|\s*\*{0,2}\s*("
    r"Base model|Quantisation|LoRA|Learning|Epochs|Effective|Max sequence|Seed"
    # `Holdout` alone, not `Holdout pairs`: the run also prints the scored
    # holdout metrics (coverage, duplicate rate, reference alignment), and those
    # rows are the point of running the scoring at all.
    r"|Train pairs|Holdout|Final|Wall clock|GPU|Emits|Adapter|Chat template"
    r")"
)

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def log_text(raw: str) -> str:
    """Decode Kaggle's kernel log into the text a terminal would have shown.

    The `.log` is a JSON **array** of stream records — `[{"stream_name": ...,
    "data": "..."}, ...]` — not plain text and not JSONL. Every printed line
    lives inside a JSON string with its newlines ESCAPED, so the file is
    effectively ONE physical line: `splitlines()` yields nothing to match
    against and any line-anchored regex comes back empty.

    That is not hypothetical. A fully successful run reported "no RUN_LOG table
    found" while the table sat in the file, because the rows were read raw.
    `diagnose()` kept working throughout and hid the problem: it uses
    `re.search` over the whole blob, which does not care about line boundaries.
    """
    raw = raw.strip()
    if not raw:
        return ""

    parts: list[str] = []
    try:
        records = json.loads(raw)
    except ValueError:
        records = None

    if isinstance(records, list):
        parts = [
            record.get("data", "") if isinstance(record, dict) else str(record)
            for record in records
        ]
    else:
        # JSONL, or a genuinely plain-text file (a .ipynb lands here too).
        for line in raw.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except ValueError:
                parts.append(line + "\n")
                continue
            parts.append(
                parsed.get("data", "") if isinstance(parsed, dict) else line + "\n"
            )

    # Progress bars redraw with \r; without this the table can share a line
    # with a spinner and fail to match.
    return _ANSI.sub("", "".join(parts)).replace("\r", "\n")


# A kernel with no network fails first at pip, with this signature. Kaggle
# STORES enable_internet=true for an unverified account and then runs the
# session without DNS anyway, so the metadata looks correct and the log does
# not obviously say "no internet".
_NO_INTERNET = re.compile(
    r"Temporary failure in name resolution|Failed to establish a new connection",
    re.IGNORECASE,
)


def diagnose(text: str) -> str | None:
    """Turn a known failure signature into the actual cause and its fix."""
    if _NO_INTERNET.search(text):
        return (
            "The kernel had NO INTERNET, so every pip install failed.\n"
            "  enable_internet was accepted and stored, but Kaggle only honours\n"
            "  it for phone-verified accounts - an unverified account runs with\n"
            "  no DNS and no warning.\n"
            "  Fix: kaggle.com -> Settings -> Phone Verification, then re-push.\n"
            "  (Without internet the base model cannot be downloaded either, so\n"
            "  there is no useful workaround inside the kernel.)"
        )
    if re.search(r"401 Client Error|gated repo|awaiting a review", text, re.I):
        return (
            "The base model is GATED on Hugging Face and the kernel has no token.\n"
            "  Fix: switch model.base_model in train_config.yaml to an ungated "
            "base, or add HF_TOKEN as a Kaggle secret."
        )
    if re.search(
        r"no kernel image is available|not compatible with the current PyTorch",
        text,
        re.I,
    ):
        return (
            "Kaggle allocated a GPU too old for the installed PyTorch.\n"
            "  A P100 is sm_60; current torch wheels build for sm_70 and up, so\n"
            "  model loading dies with 'no kernel image is available'.\n"
            "  The push now names the accelerator explicitly (MACHINE_SHAPE =\n"
            f"  {MACHINE_SHAPE}), so a run pushed by this script should not hit\n"
            "  this. Seeing it means the session predates that change.\n"
            "  Fix: re-push. `python training/kaggle_run.py --push`\n"
            "  If it survives a fresh push, the requested shape was refused -\n"
            "  check the quota (kaggle.com -> Settings) and that the account is\n"
            "  phone-verified, then select 'GPU T4 x2' in the browser instead."
        )
    if re.search(r"'int' object has no attribute 'mean'", text):
        return (
            "Version skew between unsloth and transformers/trl.\n"
            "  The patched training step received an int where it expected a\n"
            "  tensor. Almost always caused by pinning trl or transformers away\n"
            "  from what the installed unsloth expects.\n"
            "  Fix: install plain `unsloth` and let it resolve its own stack -\n"
            "       do not pin trl/transformers with --no-deps."
        )
    if re.search(r"CUDA out of memory|OutOfMemoryError", text, re.I):
        return (
            "The GPU ran out of memory.\n"
            "  Fix: lower per_device_train_batch_size or max_seq_length in "
            "train_config.yaml."
        )
    return None


def fetch(api, username: str) -> None:
    out_dir = STAGING / "output"
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)

    print("-> downloading kernel output")
    api.kernels_output(_resolve_kernel_ref(api, username), path=str(out_dir))

    files = sorted(p for p in out_dir.rglob("*") if p.is_file())
    print(f"  {len(files)} file(s) in {out_dir}")

    # Zero files is already conclusive, so say so here rather than falling
    # through to "no RUN_LOG table found", which reads like a parsing problem.
    # A successful run always writes the adapter, the tokenizer and the log.
    #
    # Kaggle reports COMPLETE for this. COMPLETE means "the notebook finished
    # executing", not "it succeeded" — a cell that raises still gets there, and
    # `kernels_status` then shows COMPLETE with an empty failureMessage. The
    # only local evidence of the difference is this empty directory.
    if not files:
        print(
            "\n!! The run produced NO artifacts, so it failed BEFORE saving the\n"
            "   adapter — most likely in an early cell.\n\n"
            "   Note: `--status` says COMPLETE for this. COMPLETE means the\n"
            "   notebook finished executing, NOT that it succeeded.\n\n"
            "   Open the kernel and read the FIRST traceback, not the last:\n"
            "     https://www.kaggle.com/code/"
            f"{_resolve_kernel_ref(api, username)}\n\n"
            "   If it is a config KeyError, the dataset version is stale — push\n"
            "   again (this now correctly versions an existing dataset) and\n"
            "   re-run from the UI with the T4 selected:\n"
            "     python training/kaggle_run.py --push",
            file=sys.stderr,
        )
        return

    # The notebook's last cell prints the RUN_LOG.md table; surface it so the
    # measured values can be transcribed rather than hunted for.
    rows: list[str] = []
    combined = ""
    for path in files:
        if path.suffix.lower() not in {".log", ".txt", ".json", ".ipynb"}:
            continue
        try:
            text = log_text(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        combined += text
        rows += [line for line in text.splitlines() if RUN_LOG_ROW.match(line)]

    # Name a known root cause before the "no table found" line below, which
    # otherwise reads as if the parsing failed rather than the run.
    cause = diagnose(combined)
    if cause:
        print(f"\n!! RUN FAILED: {cause}\n", file=sys.stderr)

    if rows:
        print("\n--- paste into training/RUN_LOG.md ---")
        print("| Field | Value |")
        print("|---|---|")
        for row in dict.fromkeys(rows):
            print(row.strip())
    else:
        print(
            "\n  ! no RUN_LOG table found in the output — open the kernel log "
            "and check whether training actually completed.",
            file=sys.stderr,
        )


def purge(api, username: str, kernel_refs: list[str]) -> None:
    """Delete the artifacts this script created on Kaggle.

    The counterpart to `push`: that call uploads a dataset and a kernel to the
    account, and nothing else here ever removes them, so a "start over" in the
    app would otherwise leave both sitting in Kaggle with the old corpus still
    in them.

    Every deletion is best-effort and reported rather than raised. Kaggle
    answers a missing dataset or kernel with an error, not a no-op, and that
    error means the same thing as success here — the artifact is not there.
    Refusing to finish because one of several kernels was already gone would
    leave the rest behind.

    `kernel_refs` comes from the caller because the pushed slug is not always
    `KERNEL_SLUG`: `--fresh` timestamps a new one, and `_resolve_kernel_ref`
    recovers from a title that slugified differently. The app records what was
    actually pushed on each run row and passes those. With none given, the
    default name is the only thing left to try.
    """
    for ref in kernel_refs or [f"{username}/{KERNEL_SLUG}"]:
        if "/" not in ref:
            ref = f"{username}/{ref}"
        try:
            api.kernels_delete(ref, no_confirm=True)
            print(f"-> deleted kernel {ref}")
        except Exception as exc:
            print(f"  ! kernel {ref} not deleted ({exc})", file=sys.stderr)

    try:
        api.dataset_delete(username, DATASET_SLUG, no_confirm=True)
        print(f"-> deleted dataset {username}/{DATASET_SLUG}")
    except Exception as exc:
        print(
            f"  ! dataset {username}/{DATASET_SLUG} not deleted ({exc})",
            file=sys.stderr,
        )

    # Local staging holds a copy of the uploaded dataset and the downloaded
    # output — by-products of the pushes just undone, and hundreds of megabytes.
    shutil.rmtree(STAGING, ignore_errors=True)
    print(f"-> removed {STAGING}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push", action="store_true", help="Upload data and push the kernel")
    parser.add_argument("--status", action="store_true", help="Poll the kernel once")
    parser.add_argument("--fetch", action="store_true", help="Download output and print the log table")
    parser.add_argument("--all", action="store_true", help="Push, wait for completion, then fetch")
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete the dataset and kernel(s) from Kaggle, and local staging",
    )
    parser.add_argument(
        "--kernel",
        action="append",
        default=[],
        metavar="REF",
        help="Kernel to purge, as owner/slug; repeatable. Defaults to the "
             "standard kernel name when none is given.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Create a NEW kernel rather than updating the existing one "
             "(use when a kernel is stuck without GPU/internet entitlement)",
    )
    args = parser.parse_args()

    if not any((args.push, args.status, args.fetch, args.all, args.purge)):
        parser.error("choose --push, --status, --fetch, --all or --purge")

    if args.fresh:
        use_fresh_kernel_name()

    try:
        username = _credentials()
        api = _api()
    except SetupError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"\nKaggle authentication failed: {exc}\n", file=sys.stderr)
        return 2

    try:
        if args.purge:
            purge(api, username, args.kernel)
        if args.push or args.all:
            push(api, username)
        if args.all:
            state = wait(api, username)
            if state != "complete":
                print(f"\nKernel finished in state '{state}'.", file=sys.stderr)
                fetch(api, username)
                return 1
        if args.status:
            status(api, username)
        if args.fetch or args.all:
            fetch(api, username)
    except SetupError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
