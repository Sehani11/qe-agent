"""Drives a fine-tuning run: uploads -> dataset -> Kaggle GPU -> LoRA adapter.

**The backend never imports `training/`.** That directory sits outside the
backend Docker build context on purpose (see `training/README.md`), so the only
way to reach it is as a subprocess. Every stage here is therefore a process
launch against the repo root, and the interface with those scripts is their CLI
and their stdout — not their Python API.

The run outlives its request by tens of minutes, so nothing here is awaited by
the caller. `start_run` returns as soon as the row exists; a detached task then
walks the stages, writing each one to the row. The row is the progress
indicator and the client polls it.

Stage order is not arbitrary. Credentials are checked BEFORE the dataset build,
because the build makes one LLM call per document — discovering a missing Kaggle
key after paying for that is a bill for nothing.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import shutil
import subprocess
import sys
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import async_session_factory
from app.models.training_run import (
    ACTIVE_STATUSES,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_FETCHING,
    STATUS_TRAINING,
    TrainingRun,
)
from app.models.training_run import STATUS_BUILDING as _STATUS_BUILDING

logger = logging.getLogger(__name__)


#: Uploaded files needed before a run can produce anything.
#:
#: TWO, not one, and it is a hard floor rather than a preference. The builder
#: splits train/holdout by ORIGIN FILE — scenarios from one file are
#: near-duplicates, so letting them straddle the boundary makes the holdout
#: score meaningless — and it always reserves at least one origin for the
#: holdout (`max(1, ...)` in `split_by_origin`, there so a run always has an
#: eval loss to report). One uploaded file is one origin however many pairs it
#: holds, so with a single file that origin IS the holdout and train comes out
#: empty. Enforced in the UI so this costs a disabled button rather than a
#: build.
MIN_DATASETS_TO_TRAIN = 2


class TrainingRunError(Exception):
    """A run could not start, or a stage failed. The message is user-facing."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# --- Layout -----------------------------------------------------------------


def repo_root() -> Path:
    """The repository root, which is where the training scripts are rooted.

    Derived from this file's location — backend/app/services/… — rather than
    from the process working directory, which uvicorn's `--reload` and the
    test runner set differently. `TRAINING_REPO_ROOT` overrides it for a
    deployment that mounts the repo somewhere else.
    """
    configured = (settings.training_repo_root or "").strip()
    if configured:
        return Path(configured).resolve()
    return Path(__file__).resolve().parents[3]


def _training_dir() -> Path:
    return repo_root() / "training"


#: Where a finished run's adapter is kept, one directory per run. Beside the
#: manually-run adapters already in `training/outputs/`, because they are the
#: same kind of artifact and `serve/app.py` is pointed at one of them by path.
def _outputs_dir() -> Path:
    return _training_dir() / "outputs"


def training_available() -> str | None:
    """Return None when a run can be started here, or why it cannot.

    The backend image deliberately does NOT ship `training/`, so a deployed
    instance reaches this and says so plainly instead of failing inside a
    subprocess with "No such file or directory".
    """
    for script in ("build_dataset.py", "kaggle_run.py"):
        if not (_training_dir() / script).exists():
            return (
                "Training scripts are not available on this server. The "
                "training/ directory ships only with a full checkout, not with "
                "the backend image."
            )
    return None


# --- Kaggle credentials -----------------------------------------------------

#: Mirrors `kaggle_run.py::_load_dotenv`. Read here as well as there so a
#: missing key is reported before the dataset build spends money, rather than
#: after it.
_KAGGLE_KEY_RE = re.compile(r"[0-9a-f]{32}")


def _kaggle_credentials() -> tuple[str, str]:
    """Return `(username, key)` from the environment or the repo-root .env."""
    values: dict[str, str] = {}
    env_file = repo_root() / ".env"
    if env_file.exists():
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line.startswith("KAGGLE") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
        except OSError:  # pragma: no cover - unreadable .env is not fatal
            pass

    def read(name: str) -> str:
        return (os.environ.get(name) or values.get(name) or "").strip()

    return read("KAGGLE_USERNAME"), (read("KAGGLE_KEY") or read("KAGGLE_API_KEY"))


def kaggle_ready() -> str | None:
    """Return None when Kaggle credentials are usable, or what is missing."""
    username, key = _kaggle_credentials()
    if not username or not key:
        return (
            "Kaggle credentials are not configured on the server. Training "
            "runs on Kaggle's free GPU, which needs both KAGGLE_USERNAME and "
            "KAGGLE_KEY — download kaggle.json from kaggle.com → Settings → "
            "API → Create New Token and set both."
        )
    if not _KAGGLE_KEY_RE.fullmatch(key):
        return (
            "KAGGLE_KEY does not look like a Kaggle API key (expected 32 hex "
            "characters). Re-copy it from kaggle.json."
        )
    return None


# --- Row updates ------------------------------------------------------------

#: Cap on the stored log. A stuck kernel can print megabytes, and the row is
#: fetched on every poll — the TAIL is kept because that is where a failure is.
_MAX_LOG_CHARS = 200_000


async def _update(
    run_id, *, append: str = "", **fields
) -> None:
    """Write progress to the run row in its own short-lived session.

    Each stage gets its own transaction rather than holding one open across a
    subprocess: a run spends most of its life waiting on Kaggle, and a
    connection held for that long is closed under us by the pooler.
    """
    async with async_session_factory() as db:
        run = await db.get(TrainingRun, run_id)
        if run is None:  # pragma: no cover - the row is created before this
            return
        for key, value in fields.items():
            setattr(run, key, value)
        if append:
            combined = (run.log or "") + append
            run.log = (
                combined
                if len(combined) <= _MAX_LOG_CHARS
                else "…log truncated…\n" + combined[-_MAX_LOG_CHARS:]
            )
        await db.commit()


# --- Subprocess plumbing ----------------------------------------------------


@dataclass
class _Result:
    code: int
    output: str


#: Lines buffered before one UPDATE. A build prints thousands of them and an
#: UPDATE each would swamp the connection pool.
_LOG_BATCH = 25


def _stage_env() -> dict[str, str]:
    env = {
        **os.environ,
        # The scripts print box-drawing characters and arrows; a Windows console
        # defaults to cp1252 and dies on its own progress messages.
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
    }
    username, key = _kaggle_credentials()
    if username:
        env["KAGGLE_USERNAME"] = username
    if key:
        env["KAGGLE_KEY"] = key
    return env


def _pump(args: list[str], sink) -> int:
    """Run one script to completion on a worker thread, feeding `sink` its lines.

    Blocking `subprocess.Popen`, NOT `asyncio.create_subprocess_exec`, and that
    is not a style choice. asyncio can only spawn a process from a Proactor
    event loop on Windows; uvicorn runs a `_WindowsSelectorEventLoop`, where
    every `create_subprocess_exec` raises a bare `NotImplementedError`. Since
    the loop belongs to the server, the process has to be started somewhere
    that does not care what loop is running — a thread.

    stderr is merged into stdout: these scripts write progress to one and
    diagnosis to the other, and reading a failure needs them interleaved in the
    order they actually happened.
    """
    # A GUI-less parent (a service, a packaged app) would otherwise flash a
    # console window for every stage.
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    process = subprocess.Popen(
        [sys.executable, *args],
        cwd=str(repo_root()),
        env=_stage_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,  # line buffered, so progress arrives while the stage runs
        creationflags=creation_flags,
    )
    assert process.stdout is not None
    for line in process.stdout:
        sink(line)
    process.stdout.close()
    return process.wait()


async def _run_script(run_id, args: list[str], *, label: str) -> _Result:
    """Run one training script, streaming its output into the run row."""
    await _update(run_id, append=f"\n$ {label}\n")

    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def sink(line: str) -> None:
        # Called from the worker thread, so the queue is fed through the loop
        # rather than touched directly — asyncio.Queue is not thread-safe.
        loop.call_soon_threadsafe(lines.put_nowait, line)

    def pump() -> int:
        try:
            return _pump(args, sink)
        finally:
            # Sentinel, in a finally: a crash inside Popen must still release
            # the reader below, or the run hangs instead of failing.
            loop.call_soon_threadsafe(lines.put_nowait, None)

    worker = asyncio.create_task(asyncio.to_thread(pump))

    chunks: list[str] = []
    pending: list[str] = []
    while True:
        line = await lines.get()
        if line is None:
            break
        chunks.append(line)
        pending.append(line)
        if len(pending) >= _LOG_BATCH:
            await _update(run_id, append="".join(pending))
            pending.clear()
    if pending:
        await _update(run_id, append="".join(pending))

    return _Result(code=await worker, output="".join(chunks))


# --- Protecting the hand-built corpus ---------------------------------------
#
# `training/data` is a SHARED, single directory. `kaggle_run.py` reads
# train.jsonl and holdout.jsonl from exactly there, `train_config.yaml` names
# the same paths, and `app/evaluate_models.py` scores against that holdout — so
# a run has to put its dataset there, and cannot simply build somewhere else.
#
# It is also where a hand-built corpus lives: `--features-dir` over a cloned
# repository, one LLM call per document. That is git-ignored derived data which
# is expensive to rebuild and impossible to recover.
#
# So the directory is borrowed, not taken: moved aside for the length of the run
# and moved back afterwards, success or failure. Safe because only one run
# happens at a time — the guard in `start_run` is what makes this sound.


def _stash_dir() -> Path:
    return _training_dir() / ".data-before-run"


class _BorrowedDataDir:
    """Move `training/data` aside for a run, and put it back afterwards.

    A leftover stash from a process killed mid-run is restored on the next
    entry rather than discarded: the corpus in it is the real one, and the
    half-built dataset that replaced it is not worth preserving.
    """

    def __init__(self) -> None:
        self._data = _training_dir() / "data"
        self._stash = _stash_dir()

    def __enter__(self) -> _BorrowedDataDir:
        self._recover_leftover()
        if self._data.exists():
            shutil.move(str(self._data), str(self._stash))
        self._data.mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *_exc) -> None:
        if not self._stash.exists():
            return
        shutil.rmtree(self._data, ignore_errors=True)
        shutil.move(str(self._stash), str(self._data))

    def _recover_leftover(self) -> None:
        if not self._stash.exists():
            return
        logger.warning("Restoring training/data from a stash left by a killed run")
        shutil.rmtree(self._data, ignore_errors=True)
        shutil.move(str(self._stash), str(self._data))


# --- Stages -----------------------------------------------------------------

#: `build_dataset.py` ends with "Wrote N train / M holdout pairs to …". Read
#: from stdout rather than by counting the JSONL, so the number stored is the
#: one the builder itself claims to have written.
_SPLIT_RE = re.compile(r"Wrote\s+(\d+)\s+train\s*/\s*(\d+)\s+holdout")
#: `kaggle_run.py --status` prints "  status: COMPLETE" (or ERROR, RUNNING…).
_STATUS_RE = re.compile(r"status:\s*([A-Za-z_]+)")

_TERMINAL_KERNEL_STATES = {
    "complete",
    "error",
    "cancelacknowledged",
    "cancelrequested",
    "cancel_acknowledged",
    "cancel_requested",
}


def _split_counts(text: str) -> tuple[int, int]:
    """Read `(train, holdout)` out of the builder's closing line."""
    matches = _SPLIT_RE.findall(text)
    if not matches:
        return 0, 0
    train, holdout = matches[-1]
    return int(train), int(holdout)


async def _build_dataset(run_id, user_id: str) -> tuple[int, int]:
    """Build train/holdout JSONL from this user's uploaded datasets."""
    await _update(
        run_id,
        status=_STATUS_BUILDING,
        detail="Building the training set from your uploads",
    )
    result = await _run_script(
        run_id,
        [
            "training/build_dataset.py",
            "--from-uploads",
            "--uploads-user",
            user_id,
            "--out",
            "training/data",
        ],
        label="build_dataset.py --from-uploads",
    )
    if result.code != 0:
        raise TrainingRunError(
            "Could not build a training set from your uploads. The log below "
            "says why — the usual cause is that every uploaded file was "
            "filtered out, which leaves nothing to train on."
        )

    train, holdout = _split_counts(result.output)
    if train == 0:
        # Almost always the one-file case, so it is named first. The split is by
        # ORIGIN FILE — scenarios from one file are near-duplicates, and letting
        # them straddle the boundary makes the holdout score a lie — and the
        # builder always reserves at least one origin for the holdout. With a
        # single file that one origin is the whole corpus, so train comes out
        # empty however many pairs the file holds.
        raise TrainingRunError(
            f"The build produced no training pairs (all {holdout} went to the "
            f"holdout). Training needs at least {MIN_DATASETS_TO_TRAIN} separate "
            "uploaded files: the train/holdout split keeps every pair from one "
            "file on the same side, and one file always ends up as the holdout. "
            "Upload another file and start a run again."
        )
    return train, holdout


async def _push_to_kaggle(run_id) -> str:
    """Upload the dataset and push the GPU kernel. Returns the kernel ref."""
    # Status moves HERE, not after the push returns. The upload can take
    # minutes, and leaving it at "building" for that long said the run was
    # doing something it had already finished — which is what made a live push
    # look like a stuck build.
    await _update(
        run_id,
        status=STATUS_TRAINING,
        detail="Uploading the dataset and starting the GPU run",
    )
    result = await _run_script(
        run_id, ["training/kaggle_run.py", "--push"], label="kaggle_run.py --push"
    )
    if result.code != 0:
        raise TrainingRunError(
            "Kaggle rejected the run. A first run also needs the account to be "
            "phone-verified — Kaggle refuses to enable a GPU otherwise."
        )
    username, _ = _kaggle_credentials()
    # Mirrors `kaggle_run.py::KERNEL_SLUG`, and has to be changed with it. The
    # backend cannot import the script (training/ is outside the image), so the
    # name is duplicated rather than shared — the push prints the ref it landed
    # on, and this is only the name it is expected to be.
    return f"{username}/bdd-fine-tune"


#: `kaggle_run.py --fetch` prints a matched root cause as "!! RUN FAILED: …".
#: It knows the signatures that actually happen — no internet on an unverified
#: account, a gated base model, a GPU too old for the installed torch — and
#: each one carries its own fix.
_DIAGNOSIS_RE = re.compile(r"!! RUN FAILED:\s*(.+?)(?:\n\n|\Z)", re.DOTALL)


async def _kernel_failure_reason(run_id) -> str:
    """Pull the kernel's output and turn it into something worth reading.

    Without this a failed run said only "open the kernel and read the first
    traceback", which sends someone out of the app to do work the fetch step
    already knows how to do. The download is the same one the success path
    performs, so it costs nothing extra.
    """
    try:
        result = await _run_script(
            run_id,
            ["training/kaggle_run.py", "--fetch"],
            label="kaggle_run.py --fetch (diagnosing the failure)",
        )
    # Broad on purpose: this runs while reporting a failure, and a diagnosis
    # that raises would replace the real reason with its own.
    except Exception:
        logger.exception("Could not fetch kernel output for run %s", run_id)
        return "Open the kernel and read the FIRST traceback, not the last."

    found = _DIAGNOSIS_RE.search(result.output)
    if found:
        # Collapsed to one line: this lands in `detail`, which the UI renders
        # as a single row. The full text stays in the log below it.
        return " ".join(found.group(1).split())
    return (
        "No known failure signature matched — the full kernel output is in the "
        "log below."
    )


async def _await_kernel(run_id) -> None:
    """Poll the kernel until it reaches a terminal state."""
    deadline = asyncio.get_running_loop().time() + settings.training_timeout_seconds
    while True:
        result = await _run_script(
            run_id,
            ["training/kaggle_run.py", "--status"],
            label="kaggle_run.py --status",
        )
        states = _STATUS_RE.findall(result.output)
        state = (states[-1] if states else "unknown").lower()

        if state in _TERMINAL_KERNEL_STATES:
            if state != "complete":
                raise TrainingRunError(
                    f"The Kaggle run ended as {state.upper()} rather than "
                    f"completing. {await _kernel_failure_reason(run_id)}"
                )
            return

        await _update(run_id, detail=f"Training on Kaggle (kernel {state})")
        if asyncio.get_running_loop().time() > deadline:
            raise TrainingRunError(
                "Timed out waiting for the Kaggle run. It may still finish — "
                "check the kernel, then start a new run to collect the result."
            )
        await asyncio.sleep(settings.training_poll_seconds)


def _find_adapter(search_root: Path) -> Path | None:
    """Locate the adapter directory inside downloaded kernel output.

    Identified by `adapter_config.json` rather than by the configured output
    path: `train_config.yaml` can name any `adapter_dir`, and Kaggle nests its
    output under a directory of its own choosing.
    """
    for marker in sorted(search_root.rglob("adapter_config.json")):
        return marker.parent
    return None


async def _collect_adapter(run_id) -> str:
    """Download kernel output and copy the adapter somewhere permanent."""
    await _update(
        run_id, status=STATUS_FETCHING, detail="Downloading the trained adapter"
    )
    result = await _run_script(
        run_id, ["training/kaggle_run.py", "--fetch"], label="kaggle_run.py --fetch"
    )
    if result.code != 0:
        raise TrainingRunError("Could not download the run's output from Kaggle.")

    staging = _training_dir() / ".kaggle-staging" / "output"
    source = _find_adapter(staging) if staging.exists() else None
    if source is None:
        # Kaggle reports COMPLETE for a notebook that raised, so "finished" and
        # "succeeded" are different questions and this is where they diverge.
        raise TrainingRunError(
            "The run finished but produced no adapter, so it failed before "
            "saving one — Kaggle reports COMPLETE for that. Open the kernel "
            "and read the FIRST traceback, not the last."
        )

    destination = _outputs_dir() / f"run-{str(run_id)[:8]}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(destination, ignore_errors=True)
    shutil.copytree(source, destination)

    return str(destination.relative_to(repo_root())).replace("\\", "/")


# --- Orchestration ----------------------------------------------------------


async def _execute(run_id, user_id: str) -> None:
    """Walk every stage, recording the outcome on the row either way.

    The whole run happens inside `_BorrowedDataDir`: the build writes into
    `training/data`, which is also where a hand-built corpus lives, and that
    corpus must survive a run it had nothing to do with.
    """
    try:
        with _BorrowedDataDir():
            train, holdout = await _build_dataset(run_id, user_id)
            await _update(run_id, train_pairs=train, holdout_pairs=holdout)

            kernel_ref = await _push_to_kaggle(run_id)
            await _update(run_id, kernel_ref=kernel_ref)

            # Held for the wait too: `--fetch` reads the kernel it pushed, and
            # the dataset on disk is what identifies that run.
            await _await_kernel(run_id)
            adapter_dir = await _collect_adapter(run_id)

        await _update(
            run_id,
            status=STATUS_COMPLETED,
            detail="Trained. The adapter is ready to download.",
            adapter_dir=adapter_dir,
            completed_at=datetime.now(UTC),
        )
    except TrainingRunError as exc:
        await _update(
            run_id,
            status=STATUS_FAILED,
            detail=exc.message,
            completed_at=datetime.now(UTC),
        )
    # Broad on purpose: this task is detached, so an escaping exception would be
    # swallowed by asyncio and leave the row stuck in an active status forever,
    # blocking every later run against the one-at-a-time guard.
    except Exception as exc:
        logger.exception("Training run %s crashed", run_id)
        await _update(
            run_id,
            status=STATUS_FAILED,
            detail=f"The run stopped unexpectedly: {type(exc).__name__}.",
            completed_at=datetime.now(UTC),
        )


#: Detached tasks are kept referenced for their lifetime. asyncio holds only a
#: weak reference to a running task, so without this the garbage collector is
#: free to cancel a run mid-flight.
_running: set[asyncio.Task] = set()


async def abandon_orphaned_runs() -> int:
    """Fail any run left active by a process that is no longer here.

    A run lives in a detached task and a subprocess, neither of which survives
    the process exiting — a restart, a `--reload` on a saved file, a deploy, a
    crash. The ROW survives all of them, so without this it sits in an active
    status forever and the one-at-a-time guard in `start_run` then refuses
    every future run, with nothing in the UI able to clear it.

    Resuming is not an option: the subprocess is gone and its output with it,
    and a Kaggle kernel that was mid-push cannot be picked up from a row. So
    they are failed honestly, saying what happened.

    Called at startup, which is the only moment we can be certain no worker of
    ours is running — this process has not started one yet.
    """
    async with async_session_factory() as db:
        result = await db.execute(
            select(TrainingRun).where(TrainingRun.status.in_(ACTIVE_STATUSES))
        )
        orphaned = result.scalars().all()
        for run in orphaned:
            run.status = STATUS_FAILED
            run.detail = (
                "The server restarted while this run was in progress, so it "
                "was abandoned. Nothing was corrupted — start a new run."
            )
            run.completed_at = datetime.now(UTC)
            run.log = (run.log or "") + (
                "\n!! The server restarted here. The run's worker and its "
                "subprocess did not survive, so the run was abandoned.\n"
            )
        if orphaned:
            await db.commit()
            logger.warning("Abandoned %d orphaned training run(s)", len(orphaned))
    return len(orphaned)


async def start_run(user_id: str, db: AsyncSession) -> TrainingRun:
    """Create a run row and detach a worker to execute it.

    Refuses while any run is active — globally, not per user. `training/data`,
    the Kaggle dataset slug and the kernel are all single shared resources, so
    a second concurrent run would overwrite the first one's dataset between its
    push and its GPU start, and train it on someone else's corpus.
    """
    unavailable = training_available()
    if unavailable:
        raise TrainingRunError(unavailable)

    # Checked BEFORE the build, which costs one LLM call per document.
    missing = kaggle_ready()
    if missing:
        raise TrainingRunError(missing)

    active = await db.execute(
        select(TrainingRun.id).where(TrainingRun.status.in_(ACTIVE_STATUSES)).limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise TrainingRunError(
            "A training run is already in progress. Only one can run at a "
            "time — they share the same dataset and GPU kernel."
        )

    run = TrainingRun(user_id=user_id, detail="Queued")
    db.add(run)
    await db.commit()
    await db.refresh(run)

    task = asyncio.create_task(_execute(run.id, user_id))
    _running.add(task)
    task.add_done_callback(_running.discard)
    return run


# --- Adapter download -------------------------------------------------------

#: An adapter directory is a handful of small files plus one safetensors blob;
#: this bound exists so a mistargeted path cannot stream a whole corpus.
_MAX_ZIP_BYTES = 500 * 1024 * 1024


def _resolved_adapter(adapter_dir: str) -> Path | None:
    """Resolve `adapter_dir` under the repo root, or None if it is not there.

    The path comes from a database column, but a column is not a promise —
    anything that ever writes it must not be able to reach outside the repo.
    Shared by download and delete: a traversal that only one of them rejected
    would be a traversal that deletes.
    """
    root = repo_root()
    resolved = (root / adapter_dir).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_dir():
        return None
    return resolved


def zip_adapter(adapter_dir: str) -> tuple[bytes, str]:
    """Zip an adapter directory, returning `(bytes, filename)`.

    Built in memory rather than streamed from disk: a LoRA adapter is tens of
    megabytes, and a temporary file would need cleaning up on a path where the
    client can disconnect mid-download.
    """
    resolved = _resolved_adapter(adapter_dir)
    if resolved is None:
        raise TrainingRunError("The trained model is no longer on this server.")

    files = [p for p in sorted(resolved.rglob("*")) if p.is_file()]
    if not files:
        raise TrainingRunError("The trained model is no longer on this server.")
    if sum(p.stat().st_size for p in files) > _MAX_ZIP_BYTES:
        raise TrainingRunError("The trained model is too large to download.")

    buffer = io.BytesIO()
    # ZIP_STORED, not DEFLATE: safetensors weights are already dense, so
    # compressing them burns CPU for a percent or two.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path in files:
            archive.write(path, arcname=str(path.relative_to(resolved)))
    return buffer.getvalue(), f"{resolved.name}.zip"


# --- Deleting runs ----------------------------------------------------------


def discard_adapter(adapter_dir: str | None) -> None:
    """Remove a run's adapter directory, if it is still there.

    Best-effort by design: the row is the record and the directory is a
    by-product, so a file the process cannot remove must not prevent the run
    from being deleted. The alternative — refusing — leaves a row the user
    cannot get rid of because of a permission problem they cannot see.
    """
    if not adapter_dir:
        return
    resolved = _resolved_adapter(adapter_dir)
    if resolved is None:
        return
    try:
        shutil.rmtree(resolved)
    except OSError:
        logger.warning("Could not remove adapter directory %s", resolved)


def purge_outputs_dir() -> None:
    """Empty `training/outputs`, best-effort.

    A run's own adapter is removed by `discard_adapter`, which only touches the
    directory a row names. This clears what no row names: adapters from runs
    driven by hand off the CLI, converted `.gguf` weights, Modelfiles and stray
    kernel logs. They are the same fine-tuned models by another route, and a
    "start over" that left 200 MB of them behind is not one.

    The directory itself is kept so the next run has somewhere to collect into.
    Best-effort for the same reason as `discard_adapter`: a file the process
    cannot remove must not fail an operation whose real record is the database.
    """
    outputs = _outputs_dir()
    if not outputs.is_dir():
        return
    for entry in outputs.iterdir():
        try:
            if entry.is_dir():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            logger.warning("Could not remove training output %s", entry)


async def purge_kaggle_artifacts(kernel_refs: Sequence[str]) -> None:
    """Delete the dataset and kernels this app pushed to the user's Kaggle.

    A run leaves as much behind on Kaggle as it does here — a private dataset
    holding the training pairs and a kernel holding the notebook and its
    output. Nothing else in this codebase removes them, so without this a wipe
    would clear the local side and leave the account holding the old corpus.

    Best-effort, and deliberately not part of the transaction: the rows are the
    record and are already gone by the time this runs. A Kaggle outage, missing
    credentials or a deployment without `training/` must not turn a completed
    wipe into a 500 — the script reports what it could not delete, and that
    lands in the server log.
    """
    if training_available() or kaggle_ready():
        logger.info("Kaggle purge skipped: training scripts or credentials absent")
        return

    args = ["training/kaggle_run.py", "--purge"]
    for ref in kernel_refs:
        args += ["--kernel", ref]

    lines: list[str] = []
    code = await asyncio.to_thread(_pump, args, lines.append)
    if code != 0:
        logger.warning(
            "Kaggle purge exited %s; artifacts may remain: %s",
            code,
            "".join(lines),
        )


async def delete_run(run: TrainingRun, db: AsyncSession) -> None:
    """Delete one run and the adapter it produced.

    Refuses while the run is active. Its worker is a detached task holding a
    subprocess and a Kaggle kernel, and removing the row would neither stop
    them nor be noticed by them — the run would carry on writing to a row that
    no longer exists. A restart is what ends an active run (see
    `abandon_orphaned_runs`), and it can be deleted once it has.
    """
    if run.status in ACTIVE_STATUSES:
        raise TrainingRunError(
            "This run is still going, so it cannot be deleted yet. Deleting the "
            "row would not stop the work it is doing."
        )

    # Directory first: if the delete fails afterwards the row is still there to
    # try again, whereas an orphaned 80 MB adapter has nothing pointing at it.
    discard_adapter(run.adapter_dir)
    await db.delete(run)
    await db.commit()


async def delete_finished_runs(user_id: str, db: AsyncSession) -> int:
    """Delete every one of this user's runs that is no longer active.

    Active runs are skipped rather than refused: "clear the list" is a request
    about the finished ones, and failing the whole call because something is
    training would make the button unusable exactly when the list is longest.
    """
    result = await db.execute(
        select(TrainingRun).where(
            TrainingRun.user_id == user_id,
            TrainingRun.status.notin_(ACTIVE_STATUSES),
        )
    )
    runs = result.scalars().all()
    for run in runs:
        discard_adapter(run.adapter_dir)
        await db.delete(run)
    if runs:
        await db.commit()
    return len(runs)


async def wipe_all_training_data(
    user_id: str, db: AsyncSession
) -> dict[str, object]:
    """Remove everything the fine-tune page accumulates, for a fresh start.

    Three separate stores, cleared together because "start over" is one
    intention and clearing them one at a time leaves the page in states that
    are not a fresh start — runs referring to datasets that are gone, or
    evaluation rows scoring a model that no longer exists.

    Lives here rather than in `training_data_service` because it is the run
    side that owns the destructive operations (adapters on disk), and this is
    the only caller that needs all three.

    Past the database it also removes what the runs put outside it: the models
    in `training/outputs`, the dataset and kernels on Kaggle, and the model
    loaded into the serving runtime. Each of those is a copy that outlives the
    rows, and a "start over" that left any of them is one in name only.

    Refuses while a run is active, for the same reason `delete_run` does:
    deleting the row would not stop the subprocess or the Kaggle kernel, and
    the run would carry on writing to rows that no longer exist.

    Returns per-store counts so the caller can say exactly what went, plus the
    name of the served model if one was removed.
    """
    active = await db.execute(
        select(TrainingRun.id)
        .where(TrainingRun.user_id == user_id, TrainingRun.status.in_(ACTIVE_STATUSES))
        .limit(1)
    )
    if active.scalar_one_or_none() is not None:
        raise TrainingRunError(
            "A training run is in progress, so there is nothing safe to wipe "
            "yet. Wait for it to finish, then try again."
        )

    # Imported here, not at module scope: the wipe is the only thing in this
    # module that touches the other stores, and a module-level import would tie
    # every training run to them.
    from app.models.evaluation_result import EvaluationResult
    from app.models.training_dataset import TrainingDataset
    from app.services import fine_tuned_serving, training_data_service

    datasets_result = await db.execute(
        select(TrainingDataset).where(TrainingDataset.user_id == user_id)
    )
    datasets = datasets_result.scalars().all()
    for dataset in datasets:
        await training_data_service.discard_stored_object(dataset)
        await db.delete(dataset)

    runs_result = await db.execute(
        select(TrainingRun).where(TrainingRun.user_id == user_id)
    )
    runs = runs_result.scalars().all()
    # Read off the rows before they go: the kernel a run pushed is recorded
    # nowhere else, and `--fresh` means it cannot be reconstructed from the
    # default name afterwards.
    kernel_refs = sorted({run.kernel_ref for run in runs if run.kernel_ref})
    for run in runs:
        discard_adapter(run.adapter_dir)
        await db.delete(run)

    evaluations = await db.execute(
        delete(EvaluationResult).where(EvaluationResult.user_id == user_id)
    )

    await db.commit()

    # After the commit, both best-effort: the rows are the record and they are
    # already gone. Neither a locked file nor a Kaggle outage should report a
    # wipe that did happen as a failure.
    purge_outputs_dir()
    await purge_kaggle_artifacts(kernel_refs)
    # The last copy of a fine-tune is the one loaded into the serving runtime,
    # not the GGUF it was imported from. Leaving it is what lets a wiped
    # account still generate from a model it no longer has.
    served_model = await fine_tuned_serving.purge_served_model()

    return {
        "datasets": len(datasets),
        "runs": len(runs),
        "evaluation_rows": evaluations.rowcount or 0,
        "kernels": len(kernel_refs),
        "served_model": served_model,
    }
