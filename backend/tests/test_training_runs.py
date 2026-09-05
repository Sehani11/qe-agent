"""Tests for the sample-dataset download and the fine-tuning run API.

Covers:
- The generated samples pass the uploader's OWN validator (the whole point of
  generating rather than checking them in)
- Readiness reports what blocks a run instead of letting the button fail
- Starting a run refuses without Kaggle credentials, and while one is active
- Run listing/detail scoping: 403 for another user's run, 404 for missing
- The adapter download, including the path-escape guard on `adapter_dir`

Strategy: override get_current_user + get_db (the pattern used by
test_training_datasets.py) and patch the service's own preflight helpers, so
nothing here launches a subprocess or talks to Kaggle.
"""

import asyncio
import uuid
import zipfile
from datetime import UTC, datetime
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.models.training_run import (
    STATUS_COMPLETED,
    STATUS_QUEUED,
    STATUS_TRAINING,
)
from app.services import training_data_service, training_run_service

USER_A = "user-a-id"
USER_B = "user-b-id"


def _make_run(
    user_id: str = USER_A,
    status: str = STATUS_COMPLETED,
    adapter_dir: str | None = "training/outputs/run-abc12345",
) -> MagicMock:
    run = MagicMock()
    run.id = uuid.uuid4()
    run.user_id = user_id
    run.status = status
    run.detail = "Trained."
    run.train_pairs = 40
    run.holdout_pairs = 5
    run.kernel_ref = "someone/bdd-fine-tune"
    run.adapter_dir = adapter_dir
    run.log = "$ build_dataset.py\nWrote 40 train / 5 holdout pairs to training/data/\n"
    run.created_at = datetime(2026, 8, 24, tzinfo=UTC)
    run.completed_at = datetime(2026, 8, 24, tzinfo=UTC)
    return run


def _scalars(items) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars
    result.scalar_one_or_none.return_value = items[0] if items else None
    return result


def _scalar_one(item) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = item
    return r


def _session_factory(db):
    """Stand in for `async_session_factory`, which is used as a context manager."""

    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db

        async def __aexit__(self, *_exc):
            return False

    return _Factory()


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def ready():
    """A server that could start a run: scripts present, credentials set."""
    with (
        patch.object(training_run_service, "training_available", return_value=None),
        patch.object(training_run_service, "kaggle_ready", return_value=None),
    ):
        yield


# ---------------------------------------------------------------------------
# Sample datasets
# ---------------------------------------------------------------------------


def test_sample_datasets_pass_their_own_validator():
    """The samples must be accepted by the uploader they are samples FOR.

    This is why they are generated rather than checked in: a static file would
    drift the moment BDDGenerateResponse or the quality thresholds changed, and
    the app would then ship a sample it rejects.
    """
    kind, count = training_data_service.validate_upload(
        training_data_service.SAMPLE_JSONL_FILENAME,
        training_data_service.build_sample_jsonl().encode(),
    )
    assert kind == "jsonl"
    assert count >= 1

    kind, count = training_data_service.validate_upload(
        training_data_service.SAMPLE_FEATURE_FILENAME,
        training_data_service.build_sample_feature().encode(),
    )
    assert kind == "feature"
    assert count >= 1


def test_sample_jsonl_download_is_an_attachment(client_as_user_a):
    response = client_as_user_a.get("/api/v1/training/sample-dataset")

    assert response.status_code == 200
    assert training_data_service.SAMPLE_JSONL_FILENAME in (
        response.headers["content-disposition"]
    )
    # One JSON object per line, not a JSON array — the format the builder emits.
    assert response.text.strip().splitlines()[0].startswith('{"messages"')


def test_sample_feature_download_is_selectable(client_as_user_a):
    response = client_as_user_a.get(
        "/api/v1/training/sample-dataset", params={"kind": "feature"}
    )

    assert response.status_code == 200
    assert training_data_service.SAMPLE_FEATURE_FILENAME in (
        response.headers["content-disposition"]
    )
    assert "Feature:" in response.text


def test_sample_dataset_rejects_an_unknown_kind(client_as_user_a):
    response = client_as_user_a.get(
        "/api/v1/training/sample-dataset", params={"kind": "csv"}
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


def test_readiness_reports_missing_credentials_rather_than_failing_later(
    client_as_user_a,
):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch.object(training_run_service, "training_available", return_value=None),
        patch.object(
            training_run_service, "kaggle_ready", return_value="KAGGLE_KEY is not set."
        ),
    ):
        response = client_as_user_a.get("/api/v1/training/readiness")

    assert response.status_code == 200
    body = response.json()
    assert body["can_train"] is False
    assert "KAGGLE_KEY" in body["reason"]


def test_readiness_is_false_while_a_run_is_active(client_as_user_a, ready):
    active_id = uuid.uuid4()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(active_id))
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get("/api/v1/training/readiness").json()

    assert body["can_train"] is False
    assert body["reason"] is None  # nothing is misconfigured; one is just running
    assert body["active_run_id"] == str(active_id)


def test_readiness_is_true_when_configured_and_idle(client_as_user_a, ready):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get("/api/v1/training/readiness").json()

    assert body == {
        "can_train": True,
        "reason": None,
        "active_run_id": None,
        "min_datasets": training_run_service.MIN_DATASETS_TO_TRAIN,
    }


def test_readiness_sends_the_file_threshold_rather_than_leaving_it_to_the_client(
    client_as_user_a, ready
):
    """The number follows from how the builder splits, so the server owns it."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get("/api/v1/training/readiness").json()

    # Two, because `split_by_origin` always reserves at least one origin file
    # for the holdout — so one file leaves training empty.
    assert body["min_datasets"] == 2


# ---------------------------------------------------------------------------
# Starting a run
# ---------------------------------------------------------------------------


def test_start_run_creates_a_queued_row_and_detaches_the_worker(client_as_user_a):
    run = _make_run(status=STATUS_QUEUED, adapter_dir=None)
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch.object(
        training_run_service, "start_run", AsyncMock(return_value=run)
    ) as start:
        response = client_as_user_a.post("/api/v1/training/runs")

    # 202: the row exists, but the thing it describes has only just begun.
    assert response.status_code == 202
    assert response.json()["status"] == STATUS_QUEUED
    assert start.await_args.args[0] == USER_A


def test_start_run_is_refused_without_kaggle_credentials(client_as_user_a):
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    with patch.object(
        training_run_service,
        "start_run",
        AsyncMock(
            side_effect=training_run_service.TrainingRunError(
                "Kaggle credentials are not configured on the server."
            )
        ),
    ):
        response = client_as_user_a.post("/api/v1/training/runs")

    # 409, not 400: the request is fine, the server cannot serve it.
    assert response.status_code == 409
    assert "Kaggle" in response.json()["message"]


def test_only_one_run_at_a_time(client_as_user_a, ready):
    """The dataset dir, the Kaggle slug and the kernel are all shared."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(uuid.uuid4()))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.post("/api/v1/training/runs")

    assert response.status_code == 409
    assert "already in progress" in response.json()["message"]
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Surviving a restart
# ---------------------------------------------------------------------------


def test_a_restart_abandons_runs_it_killed_instead_of_wedging_the_queue():
    """Regression: a `--reload` mid-run left a row at "building" forever.

    The worker is a detached task and a subprocess; neither survives the
    process exiting, but the row does. The one-at-a-time guard then refused
    every later run, and nothing in the UI could clear it.
    """
    stuck = _make_run(status=training_run_service._STATUS_BUILDING, adapter_dir=None)
    stuck.log = "$ build_dataset.py\n"
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([stuck]))

    with patch.object(
        training_run_service, "async_session_factory", _session_factory(db)
    ):
        abandoned = asyncio.run(training_run_service.abandon_orphaned_runs())

    assert abandoned == 1
    assert stuck.status == "failed"
    assert "server restarted" in stuck.detail
    # Says so in the log too, where someone reading the output will be looking.
    assert "server restarted here" in stuck.log
    assert stuck.completed_at is not None
    db.commit.assert_awaited_once()


def test_a_restart_leaves_finished_runs_alone():
    """Only active rows are orphans; a completed run must keep its adapter."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([]))

    with patch.object(
        training_run_service, "async_session_factory", _session_factory(db)
    ):
        assert asyncio.run(training_run_service.abandon_orphaned_runs()) == 0

    db.commit.assert_not_awaited()


def test_the_push_stage_stops_calling_itself_the_build():
    """Regression: the UI showed "Building set" through a multi-minute upload.

    The status moved only after the push returned, so a live push looked like
    a stuck build — which is exactly how it was reported.
    """
    seen: list[dict] = []

    async def record(_run_id, *, append="", **fields):
        if fields:
            seen.append(fields)

    async def run():
        with (
            patch.object(training_run_service, "_update", record),
            patch.object(
                training_run_service,
                "_run_script",
                AsyncMock(return_value=training_run_service._Result(code=0, output="")),
            ),
            patch.object(
                training_run_service,
                "_kaggle_credentials",
                return_value=("someone", "k" * 32),
            ),
        ):
            await training_run_service._push_to_kaggle(uuid.uuid4())

    asyncio.run(run())

    # The very first thing the push does is stop claiming to be building.
    assert seen[0]["status"] == training_run_service.STATUS_TRAINING
    assert "Uploading" in seen[0]["detail"]


# ---------------------------------------------------------------------------
# Listing and fetching runs
# ---------------------------------------------------------------------------


def test_list_runs_is_scoped_to_the_caller(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([_make_run()]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/training/runs")

    assert response.status_code == 200
    [item] = response.json()
    assert item["train_pairs"] == 40
    # The adapter PATH is a server filesystem detail; the client gets a flag.
    assert item["has_model"] is True
    assert "adapter_dir" not in item


def test_get_run_carries_the_log_so_a_failure_is_legible(client_as_user_a):
    run = _make_run(status=STATUS_TRAINING, adapter_dir=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    body = client_as_user_a.get(f"/api/v1/training/runs/{run.id}").json()

    assert body["status"] == STATUS_TRAINING
    assert body["has_model"] is False
    assert "Wrote 40 train" in body["log"]


def test_get_run_returns_403_for_another_users_run(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(_make_run(user_id=USER_B)))
    app.dependency_overrides[get_db] = lambda: db

    run_id = uuid.uuid4()
    response = client_as_user_a.get(f"/api/v1/training/runs/{run_id}")

    assert response.status_code == 403


def test_get_run_returns_404_for_a_malformed_id(client_as_user_a):
    db = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get("/api/v1/training/runs/not-a-uuid")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Downloading the adapter
# ---------------------------------------------------------------------------


def test_download_model_returns_a_zip_of_the_adapter(client_as_user_a, tmp_path):
    adapter = tmp_path / "training" / "outputs" / "run-abc12345"
    adapter.mkdir(parents=True)
    (adapter / "adapter_config.json").write_text('{"r": 16}', encoding="utf-8")
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")

    run = _make_run()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        response = client_as_user_a.get(f"/api/v1/training/runs/{run.id}/model")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert sorted(archive.namelist()) == [
            "adapter_config.json",
            "adapter_model.safetensors",
        ]


def test_download_model_404s_when_the_run_has_no_adapter(client_as_user_a):
    run = _make_run(status=STATUS_TRAINING, adapter_dir=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.get(f"/api/v1/training/runs/{run.id}/model")

    assert response.status_code == 404
    assert "not produced a model" in response.json()["message"]


def test_zip_adapter_refuses_a_path_outside_the_repo(tmp_path):
    """`adapter_dir` is a column, and a column is not a promise."""
    outside = tmp_path.parent / "elsewhere"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("nope", encoding="utf-8")

    with (
        patch.object(training_run_service, "repo_root", return_value=tmp_path),
        pytest.raises(training_run_service.TrainingRunError),
    ):
        training_run_service.zip_adapter("../elsewhere")


def test_zip_adapter_refuses_a_directory_that_is_gone(tmp_path):
    with (
        patch.object(training_run_service, "repo_root", return_value=tmp_path),
        pytest.raises(training_run_service.TrainingRunError),
    ):
        training_run_service.zip_adapter("training/outputs/run-missing")


# ---------------------------------------------------------------------------
# Deleting runs
# ---------------------------------------------------------------------------


def test_deleting_a_run_removes_its_adapter_too(client_as_user_a, tmp_path):
    """The adapter is only on disk, so the row and the directory go together.

    Leaving it behind would accumulate ~80 MB per deleted run with nothing
    pointing at it.
    """
    adapter = tmp_path / "training" / "outputs" / "run-abc12345"
    adapter.mkdir(parents=True)
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")

    run = _make_run()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        response = client_as_user_a.delete(f"/api/v1/training/runs/{run.id}")

    assert response.status_code == 204
    assert not adapter.exists()
    db.delete.assert_awaited_once_with(run)


def test_an_active_run_cannot_be_deleted(client_as_user_a):
    """Removing the row would not stop the subprocess or the Kaggle kernel."""
    run = _make_run(status=STATUS_TRAINING, adapter_dir=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(f"/api/v1/training/runs/{run.id}")

    assert response.status_code == 409
    assert "still going" in response.json()["message"]
    db.delete.assert_not_called()


def test_deleting_another_users_run_is_refused(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(_make_run(user_id=USER_B)))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete(f"/api/v1/training/runs/{uuid.uuid4()}")

    assert response.status_code == 403
    db.delete.assert_not_called()


def test_a_missing_adapter_directory_does_not_block_the_delete(
    client_as_user_a, tmp_path
):
    """The row is the record; the directory is a by-product that may be gone."""
    run = _make_run()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(run))
    app.dependency_overrides[get_db] = lambda: db

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        response = client_as_user_a.delete(f"/api/v1/training/runs/{run.id}")

    assert response.status_code == 204
    db.delete.assert_awaited_once_with(run)


def test_discard_adapter_refuses_a_path_outside_the_repo(tmp_path):
    """A traversal that only the download rejected would be one that deletes."""
    outside = tmp_path.parent / "elsewhere-delete"
    outside.mkdir(exist_ok=True)
    (outside / "keep.txt").write_text("still here", encoding="utf-8")

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        training_run_service.discard_adapter("../elsewhere-delete")

    assert (outside / "keep.txt").exists()


def test_clearing_finished_runs_leaves_the_active_one_alone(client_as_user_a):
    """"Clear the list" is about the finished ones, so a live run must not
    fail the whole call — that would disable the button when the list is
    longest."""
    finished = [_make_run(adapter_dir=None), _make_run(adapter_dir=None)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars(finished))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete("/api/v1/training/runs")

    assert response.status_code == 200
    assert response.json() == {"deleted": 2}
    assert db.delete.await_count == 2


def test_clearing_when_there_is_nothing_finished_is_a_success(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([]))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete("/api/v1/training/runs")

    assert response.status_code == 200
    assert response.json() == {"deleted": 0}
    db.commit.assert_not_awaited()


# ---------------------------------------------------------------------------
# Wiping everything
# ---------------------------------------------------------------------------


def _wipe_db(datasets, runs, evaluation_rowcount=0):
    """A db whose three SELECTs return datasets, runs, then a DELETE result."""
    deleted = MagicMock()
    deleted.rowcount = evaluation_rowcount
    db = AsyncMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_one(None),  # the active-run guard
            _scalars(datasets),
            _scalars(runs),
            deleted,
        ]
    )
    return db


def test_wiping_clears_all_three_stores_and_reports_each(client_as_user_a, tmp_path):
    """One intention, three stores. Clearing them piecemeal leaves states that
    are not a fresh start, so they go together and the counts are reported."""
    outputs = tmp_path / "training" / "outputs"
    adapter = outputs / "run-abc12345"
    adapter.mkdir(parents=True)
    (adapter / "adapter_model.safetensors").write_bytes(b"weights")
    # No row names this one — a model converted or trained off the CLI. It is
    # the same artifact by another route, so a start-over has to take it too.
    stray = outputs / "bdd-lora-1.5b-f16.gguf"
    stray.write_bytes(b"weights")

    dataset = MagicMock()
    dataset.storage_path = "user-a-id/training-data/ds-1/pairs.jsonl"
    db = _wipe_db([dataset], [_make_run()], evaluation_rowcount=6)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch.object(training_run_service, "repo_root", return_value=tmp_path),
        patch(
            "app.services.training_data_service.discard_stored_object",
            AsyncMock(),
        ) as discard,
        patch.object(
            training_run_service, "purge_kaggle_artifacts", AsyncMock()
        ) as purge,
        patch(
            "app.services.fine_tuned_serving.purge_served_model",
            AsyncMock(return_value="bdd-lora-1.5b"),
        ) as unload,
    ):
        response = client_as_user_a.delete("/api/v1/training/all-data")

    assert response.status_code == 200
    assert response.json() == {
        "datasets": 1,
        "runs": 1,
        "evaluation_rows": 6,
        "kernels": 1,
        "served_model": "bdd-lora-1.5b",
    }
    # The copy that outlives every file here: without this the toggle keeps
    # generating from a model the account no longer has.
    unload.assert_awaited_once()
    # The stored object and everything on disk go too, not just the rows.
    discard.assert_awaited_once_with(dataset)
    assert not adapter.exists()
    assert not stray.exists()
    assert outputs.is_dir()  # kept, so the next run has somewhere to collect
    # The kernel a run pushed is recorded on the row and nowhere else, so it
    # has to be read off before the row is deleted.
    purge.assert_awaited_once_with(["someone/bdd-fine-tune"])
    assert db.delete.await_count == 2
    db.commit.assert_awaited_once()


def test_wiping_is_refused_while_a_run_is_in_progress(client_as_user_a):
    """Same reason a single run cannot be deleted: the row is not the run."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(uuid.uuid4()))
    app.dependency_overrides[get_db] = lambda: db

    response = client_as_user_a.delete("/api/v1/training/all-data")

    assert response.status_code == 409
    assert "in progress" in response.json()["message"]
    db.delete.assert_not_called()
    db.commit.assert_not_awaited()


def test_wiping_an_already_empty_account_is_a_success(client_as_user_a, tmp_path):
    db = _wipe_db([], [], evaluation_rowcount=0)
    app.dependency_overrides[get_db] = lambda: db

    with (
        patch.object(training_run_service, "repo_root", return_value=tmp_path),
        patch.object(
            training_run_service, "purge_kaggle_artifacts", AsyncMock()
        ) as purge,
        patch(
            "app.services.fine_tuned_serving.purge_served_model",
            AsyncMock(return_value=None),
        ),
    ):
        response = client_as_user_a.delete("/api/v1/training/all-data")

    assert response.status_code == 200
    assert response.json() == {
        "datasets": 0,
        "runs": 0,
        "evaluation_rows": 0,
        "kernels": 0,
        "served_model": None,
    }
    # Still attempted with no refs: a kernel can outlive the row that pushed it
    # (an abandoned run, a row deleted singly), and the default name covers it.
    purge.assert_awaited_once_with([])


class TestPurgingKaggle:
    """The Kaggle side of a wipe: what runs leave in the user's account."""

    async def test_each_recorded_kernel_is_passed_to_the_script(self, tmp_path):
        """Not the default name: `--fresh` pushes a kernel this cannot guess."""
        with (
            patch.object(training_run_service, "training_available", return_value=None),
            patch.object(training_run_service, "kaggle_ready", return_value=None),
            patch.object(training_run_service, "_pump", return_value=0) as pump,
        ):
            await training_run_service.purge_kaggle_artifacts(
                ["me/bdd-fine-tune", "me/bdd-fine-tune-0824-1130"]
            )

        args = pump.call_args.args[0]
        assert args == [
            "training/kaggle_run.py",
            "--purge",
            "--kernel",
            "me/bdd-fine-tune",
            "--kernel",
            "me/bdd-fine-tune-0824-1130",
        ]

    async def test_missing_credentials_skip_the_purge_rather_than_fail_it(self):
        """The rows are already gone by here; a 500 now would misreport a wipe
        that did happen."""
        with (
            patch.object(training_run_service, "training_available", return_value=None),
            patch.object(
                training_run_service, "kaggle_ready", return_value="no KAGGLE_KEY"
            ),
            patch.object(training_run_service, "_pump") as pump,
        ):
            await training_run_service.purge_kaggle_artifacts(["me/kernel"])

        pump.assert_not_called()

    async def test_a_failing_script_does_not_raise(self):
        """Kaggle being down must not turn a completed wipe into an error."""
        with (
            patch.object(training_run_service, "training_available", return_value=None),
            patch.object(training_run_service, "kaggle_ready", return_value=None),
            patch.object(training_run_service, "_pump", return_value=2),
        ):
            await training_run_service.purge_kaggle_artifacts(["me/kernel"])


# ---------------------------------------------------------------------------
# Stage plumbing
# ---------------------------------------------------------------------------


def test_split_counts_are_read_from_the_builders_own_summary():
    """The stored counts are what build_dataset.py says it wrote, not a guess."""
    output = (
        "Corpus scan\n  seen 12\n"
        "\nWrote 182 train / 20 holdout pairs to training/data/\n"
    )
    assert training_run_service._split_counts(output) == (182, 20)


def test_split_counts_are_zero_when_the_builder_wrote_nothing():
    assert training_run_service._split_counts("no usable feature documents") == (0, 0)


def test_a_single_uploaded_file_fails_with_the_reason_it_actually_failed():
    """Regression: "0 train / 2 holdout" from one uploaded file.

    The message has to name the real constraint. "Upload more files" was true
    but not useful — it did not say that the split is by FILE, so a user could
    reasonably upload one bigger file and fail again for the same reason.
    """
    output = "\nWrote 0 train / 2 holdout pairs to training/data/\n"

    async def run():
        with patch.object(
            training_run_service, "_run_script", AsyncMock(return_value=
                training_run_service._Result(code=0, output=output)
            )
        ), patch.object(training_run_service, "_update", AsyncMock()):
            await training_run_service._build_dataset(uuid.uuid4(), USER_A)

    with pytest.raises(training_run_service.TrainingRunError) as caught:
        asyncio.run(run())

    message = caught.value.message
    assert "2" in message  # the pairs that went to the holdout
    assert "separate uploaded files" in message
    # The mechanism, so a second attempt is not the same mistake bigger.
    assert "same side" in message


def test_a_failed_kernel_reports_the_cause_the_fetch_step_already_knows():
    """Regression: a failed run said only "open the kernel and read the first
    traceback", sending the user out of the app to do work `--fetch` does.

    The real case was a P100 allocated to a torch build needing sm_70+.
    """
    fetch_output = (
        "-> downloading kernel output\n"
        "!! RUN FAILED: Kaggle allocated a GPU too old for the installed "
        "PyTorch.\n  A P100 is sm_60; current torch wheels build for sm_70 "
        "and up.\n\n"
        "  ! no RUN_LOG table found in the output\n"
    )

    async def run():
        with (
            patch.object(training_run_service, "_update", AsyncMock()),
            patch.object(
                training_run_service,
                "_run_script",
                AsyncMock(
                    side_effect=[
                        training_run_service._Result(
                            code=0, output="  status: ERROR\n"
                        ),
                        training_run_service._Result(code=0, output=fetch_output),
                    ]
                ),
            ),
        ):
            await training_run_service._await_kernel(uuid.uuid4())

    with pytest.raises(training_run_service.TrainingRunError) as caught:
        asyncio.run(run())

    message = caught.value.message
    assert "ERROR" in message
    assert "GPU too old" in message
    # Flattened to one line: this lands in `detail`, rendered as a single row.
    assert "\n" not in message


def test_a_failure_with_no_known_signature_still_points_at_the_log():
    async def run():
        with (
            patch.object(training_run_service, "_update", AsyncMock()),
            patch.object(
                training_run_service,
                "_run_script",
                AsyncMock(
                    side_effect=[
                        training_run_service._Result(
                            code=0, output="  status: ERROR\n"
                        ),
                        training_run_service._Result(code=0, output="nothing useful"),
                    ]
                ),
            ),
        ):
            await training_run_service._await_kernel(uuid.uuid4())

    with pytest.raises(training_run_service.TrainingRunError) as caught:
        asyncio.run(run())

    assert "log below" in caught.value.message


def test_diagnosis_failing_does_not_replace_the_real_failure():
    """A raising diagnosis must not become the reported cause."""

    async def run():
        with (
            patch.object(training_run_service, "_update", AsyncMock()),
            patch.object(
                training_run_service,
                "_run_script",
                AsyncMock(
                    side_effect=[
                        training_run_service._Result(
                            code=0, output="  status: ERROR\n"
                        ),
                        OSError("kaggle unreachable"),
                    ]
                ),
            ),
        ):
            await training_run_service._await_kernel(uuid.uuid4())

    with pytest.raises(training_run_service.TrainingRunError) as caught:
        asyncio.run(run())

    assert "ERROR" in caught.value.message


def test_find_adapter_locates_the_dir_by_its_config_not_by_a_fixed_path(tmp_path):
    """Kaggle nests output under a directory of its own choosing."""
    nested = tmp_path / "kernel" / "outputs" / "bdd-lora"
    nested.mkdir(parents=True)
    (nested / "adapter_config.json").write_text("{}", encoding="utf-8")

    assert training_run_service._find_adapter(tmp_path) == nested


def test_find_adapter_returns_none_when_the_run_saved_nothing(tmp_path):
    """Kaggle reports COMPLETE for a notebook that raised; this is the tell."""
    (tmp_path / "empty").mkdir()
    assert training_run_service._find_adapter(tmp_path) is None


# ---------------------------------------------------------------------------
# The hand-built corpus must survive a run
# ---------------------------------------------------------------------------


def _corpus(tmp_path):
    """A training/data holding a hand-built corpus, as a checkout would have."""
    data = tmp_path / "training" / "data"
    data.mkdir(parents=True)
    (data / "train.jsonl").write_text("hand-built train\n", encoding="utf-8")
    (data / "holdout.jsonl").write_text("hand-built holdout\n", encoding="utf-8")
    return data


def test_a_run_gives_back_the_corpus_it_borrowed(tmp_path):
    """Regression: a run replaced a 19-item corpus with 2 uploaded pairs.

    `training/data` is shared — kaggle_run.py and train_config.yaml both name
    it — so a run has to build there. But it is also where a corpus built from
    a cloned repository lives, at one LLM call per document, git-ignored and
    unrecoverable. So the directory is borrowed and given back.
    """
    data = _corpus(tmp_path)

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        with training_run_service._BorrowedDataDir():
            # Inside the run the builder sees an empty directory to write into.
            assert list(data.iterdir()) == []
            (data / "train.jsonl").write_text("from uploads\n", encoding="utf-8")

        restored = (data / "train.jsonl").read_text(encoding="utf-8")
        assert restored == "hand-built train\n"
        assert (data / "holdout.jsonl").exists()


def test_the_corpus_comes_back_even_when_the_run_fails(tmp_path):
    data = _corpus(tmp_path)

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        with pytest.raises(RuntimeError), training_run_service._BorrowedDataDir():
            (data / "train.jsonl").write_text("half a build\n", encoding="utf-8")
            raise RuntimeError("kaggle said no")

        restored = (data / "train.jsonl").read_text(encoding="utf-8")
        assert restored == "hand-built train\n"


def test_a_stash_left_by_a_killed_run_is_recovered_on_the_next_one(tmp_path):
    """A process killed mid-run leaves the corpus in the stash, not in place."""
    data = tmp_path / "training" / "data"
    data.mkdir(parents=True)
    (data / "train.jsonl").write_text("half-built leftovers\n", encoding="utf-8")

    stash = tmp_path / "training" / ".data-before-run"
    stash.mkdir(parents=True)
    (stash / "train.jsonl").write_text("hand-built train\n", encoding="utf-8")

    with (
        patch.object(training_run_service, "repo_root", return_value=tmp_path),
        training_run_service._BorrowedDataDir(),
    ):
        pass

    assert (data / "train.jsonl").read_text(encoding="utf-8") == "hand-built train\n"
    assert not stash.exists()


def test_borrowing_works_when_there_is_no_corpus_to_begin_with(tmp_path):
    (tmp_path / "training").mkdir()

    with patch.object(training_run_service, "repo_root", return_value=tmp_path):
        with training_run_service._BorrowedDataDir():
            (tmp_path / "training" / "data" / "train.jsonl").write_text(
                "from uploads\n", encoding="utf-8"
            )

        # Nothing was borrowed, so the run's own output is left in place.
        assert (
            tmp_path / "training" / "data" / "train.jsonl"
        ).read_text(encoding="utf-8") == "from uploads\n"


def test_a_stage_runs_on_the_event_loop_uvicorn_actually_uses():
    """Regression: the first real run died before printing a single line.

    `asyncio.create_subprocess_exec` raises a bare `NotImplementedError` on a
    selector event loop, and a selector loop is exactly what uvicorn runs on
    Windows — so every stage failed at the spawn. The loop belongs to the
    server, so the fix was to start the process somewhere that does not care
    which loop is running: a blocking `Popen` on a worker thread.

    Constructed explicitly rather than left to the ambient policy, because the
    default on this platform is a Proactor loop, where the bug does not
    reproduce and the test would pass without proving anything.
    """
    loop = asyncio.SelectorEventLoop()
    try:
        with patch.object(training_run_service, "_update", AsyncMock()):
            result = loop.run_until_complete(
                training_run_service._run_script(
                    uuid.uuid4(),
                    [
                        "-c",
                        "import sys; print('from stdout'); "
                        "print('from stderr', file=sys.stderr); sys.exit(2)",
                    ],
                    label="smoke",
                )
            )
    finally:
        loop.close()

    # The exit code decides whether a stage failed, so it has to survive.
    assert result.code == 2
    assert "from stdout" in result.output
    # Merged, not dropped: diagnosis goes to stderr and is the half that
    # explains a failure.
    assert "from stderr" in result.output


def test_a_crash_inside_the_stage_fails_the_run_rather_than_hanging_it():
    """The reader waits on a sentinel, so the sentinel must survive a crash.

    Without it a `Popen` that raises (a missing interpreter, a bad cwd) would
    leave the run parked in an active status forever, which also blocks every
    later run through the one-at-a-time guard.
    """
    loop = asyncio.SelectorEventLoop()
    try:
        with (
            patch.object(training_run_service, "_update", AsyncMock()),
            patch.object(
                training_run_service, "_pump", side_effect=OSError("no such file")
            ),
            pytest.raises(OSError, match="no such file"),
        ):
            loop.run_until_complete(
                training_run_service._run_script(
                    uuid.uuid4(), ["-c", "pass"], label="smoke"
                )
            )
    finally:
        loop.close()
