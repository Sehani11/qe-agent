"""Manual training-dataset upload API (Story 6.7).

Lets a researcher supply their own `.feature` files, `.jsonl` training pairs, or
a `.csv` authored in a spreadsheet, so a fine-tune can be trained now rather
than waiting for captured corrections to accumulate. CSV is converted to pairs
at upload — see `ValidatedUpload` in the service.

Validation lives in `services/training_data_service.py` — the same module
`training/build_dataset.py` uses — so this endpoint accepts exactly what the
dataset builder keeps. This route handles auth, size limits and HTTP mapping
only.
"""

import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_user
from app.core.database import get_db
from app.models.training_dataset import TrainingDataset
from app.models.training_run import ACTIVE_STATUSES, TrainingRun
from app.schemas.training import (
    RejectedFile,
    ServingReadiness,
    TrainingDatasetResponse,
    TrainingReadiness,
    TrainingRunResponse,
    TrainingUploadResponse,
)
from app.services import (
    model_serving_service,
    training_data_service,
    training_run_service,
)
from app.services.model_serving_service import ModelServingError
from app.services.training_data_service import TrainingDataError
from app.services.training_run_service import TrainingRunError

router = APIRouter()

# Max size per uploaded file (10 MB). A .feature file is capped far lower by
# the parser's own MAX_FILE_CHARS; this bound is really for .jsonl corpora.
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024


@router.get("/datasets", response_model=list[TrainingDatasetResponse])
async def list_datasets(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[TrainingDatasetResponse]:
    """Return the current user's uploaded training datasets, newest first.

    Scoped to ``current_user`` so no cross-user rows are ever returned (NFR-S8).
    """
    result = await db.execute(
        select(TrainingDataset)
        .where(TrainingDataset.user_id == current_user)
        .order_by(TrainingDataset.created_at.desc())
    )
    rows = result.scalars().all()
    return [TrainingDatasetResponse.model_validate(r) for r in rows]


@router.post("/datasets", response_model=TrainingUploadResponse)
async def upload_datasets(
    files: list[UploadFile] = File(...),  # noqa: B008
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Upload one or more `.feature` / `.jsonl` / `.csv` training files.

    Each file is validated, stored and recorded independently: the response
    reports per-file outcomes rather than failing the whole batch. Returns 422
    only when nothing at all was accepted.
    """
    accepted: list[TrainingDatasetResponse] = []
    rejected: list[RejectedFile] = []
    max_mb = _MAX_UPLOAD_BYTES // (1024 * 1024)

    for upload in files:
        filename = upload.filename or "(unnamed)"

        # Reject oversize uploads from the reported size before buffering the
        # whole body, then re-check the actual bytes as defense-in-depth.
        if upload.size is not None and upload.size > _MAX_UPLOAD_BYTES:
            rejected.append(
                RejectedFile(
                    filename=filename,
                    reason=f"File is too large. Maximum size is {max_mb} MB.",
                )
            )
            continue

        data = await upload.read()
        if len(data) > _MAX_UPLOAD_BYTES:
            rejected.append(
                RejectedFile(
                    filename=filename,
                    reason=f"File is too large. Maximum size is {max_mb} MB.",
                )
            )
            continue

        try:
            dataset = await training_data_service.store_dataset(
                user_id=current_user,
                filename=filename,
                data=data,
                db=db,
            )
        except TrainingDataError as exc:
            rejected.append(RejectedFile(filename=filename, reason=exc.message))
            continue

        accepted.append(TrainingDatasetResponse.model_validate(dataset))

    payload = TrainingUploadResponse(accepted=accepted, rejected=rejected)
    if not accepted:
        # Nothing usable arrived — surface it as a client error, while still
        # returning the per-file reasons so the user knows what to fix.
        return JSONResponse(status_code=422, content=payload.model_dump(mode="json"))
    return payload


@router.delete("/datasets/{dataset_id}", status_code=204)
async def delete_dataset(
    dataset_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Delete an uploaded dataset's stored object and its row.

    404 if it does not exist, 403 if it belongs to another user (NFR-S8).
    """
    # A malformed (non-UUID) id can't match any row — treat as not found rather
    # than letting the DB driver raise a cast error (500).
    try:
        uuid.UUID(dataset_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=404, detail="Training dataset not found."
        ) from exc

    result = await db.execute(
        select(TrainingDataset).where(TrainingDataset.id == dataset_id)
    )
    dataset = result.scalar_one_or_none()
    if dataset is None:
        raise HTTPException(status_code=404, detail="Training dataset not found.")
    if dataset.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")

    await training_data_service.delete_dataset(dataset, db)
    return Response(status_code=204)


# --- Sample datasets --------------------------------------------------------


@router.get("/sample-dataset")
async def download_sample_dataset(
    kind: str = Query(
        "jsonl",
        pattern="^(jsonl|feature|csv)$",
        description=(
            "'csv' for the spreadsheet authoring format, 'jsonl' for ready-made "
            "pairs, 'feature' for Gherkin."
        ),
    ),
    current_user: str = Depends(get_current_user),
) -> Response:
    """Download a valid example of an accepted training file.

    Generated from the same constants the uploader validates against, so the
    sample can never drift into a shape this endpoint's sibling rejects.

    Authenticated like the rest of the router. The content is not secret, but
    an unauthenticated route here would be the only one in the file, and the
    exception is not worth the reader's time.
    """
    # text/csv for the CSV sample so a double-click opens it in a spreadsheet,
    # which is the entire reason that format exists here.
    media_type = "application/octet-stream"
    if kind == "feature":
        body = training_data_service.build_sample_feature()
        filename = training_data_service.SAMPLE_FEATURE_FILENAME
    elif kind == "csv":
        body = training_data_service.build_sample_csv()
        filename = training_data_service.SAMPLE_CSV_FILENAME
        media_type = "text/csv"
    else:
        body = training_data_service.build_sample_jsonl()
        filename = training_data_service.SAMPLE_JSONL_FILENAME

    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Fine-tuning runs -------------------------------------------------------


def _to_run_response(run: TrainingRun) -> TrainingRunResponse:
    """Map a row to the client view, reducing the adapter path to a flag."""
    return TrainingRunResponse(
        id=run.id,
        status=run.status,
        detail=run.detail,
        train_pairs=run.train_pairs,
        holdout_pairs=run.holdout_pairs,
        kernel_ref=run.kernel_ref,
        has_model=bool(run.adapter_dir),
        serve_status=run.serve_status,
        serve_detail=run.serve_detail,
        served_model=run.served_model,
        log=run.log or "",
        created_at=run.created_at,
        completed_at=run.completed_at,
    )


@router.get("/readiness", response_model=TrainingReadiness)
async def training_readiness(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TrainingReadiness:
    """Whether a run can be started here, and what blocks it if not.

    Both blockers — no `training/` directory, no Kaggle credentials — are
    operator configuration nobody can fix from the UI, so the client asks first
    and explains, rather than offering a button that always fails.
    """
    reason = (
        training_run_service.training_available()
        or training_run_service.kaggle_ready()
    )

    active = await db.execute(
        select(TrainingRun.id).where(TrainingRun.status.in_(ACTIVE_STATUSES)).limit(1)
    )
    active_id = active.scalar_one_or_none()

    return TrainingReadiness(
        can_train=reason is None and active_id is None,
        reason=reason,
        active_run_id=active_id,
        min_datasets=training_run_service.MIN_DATASETS_TO_TRAIN,
    )


@router.get("/serving-readiness", response_model=ServingReadiness)
async def serving_readiness(
    current_user: str = Depends(get_current_user),
) -> ServingReadiness:
    """Whether a finished adapter can be published into a runtime from here.

    Asked before the button is offered, for the same reason
    `/readiness` is: the blockers are all operator setup nobody can fix from the
    UI, and a button that always fails is worse than one that explains.
    """
    reason = await model_serving_service.serving_ready()
    return ServingReadiness(can_serve=reason is None, reason=reason)


@router.get("/runs", response_model=list[TrainingRunResponse])
async def list_runs(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> list[TrainingRunResponse]:
    """The current user's fine-tuning runs, newest first."""
    result = await db.execute(
        select(TrainingRun)
        .where(TrainingRun.user_id == current_user)
        .order_by(TrainingRun.created_at.desc())
        .limit(20)
    )
    return [_to_run_response(r) for r in result.scalars().all()]


@router.post("/runs", response_model=TrainingRunResponse, status_code=202)
async def start_run(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TrainingRunResponse:
    """Start a fine-tuning run over this user's uploaded datasets.

    202, not 201: the row exists, but the thing it describes has only just
    begun. The client polls `GET /training/runs/{id}` from here.
    """
    try:
        run = await training_run_service.start_run(current_user, db)
    except TrainingRunError as exc:
        # 409, not 400: nothing about the request is wrong — the server is in a
        # state that cannot serve it (no credentials, or a run already going).
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return _to_run_response(run)


async def _owned_run(run_id: str, current_user: str, db: AsyncSession) -> TrainingRun:
    """Fetch a run the caller owns, or raise the right HTTP error."""
    try:
        uuid.UUID(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Training run not found.") from exc

    result = await db.execute(select(TrainingRun).where(TrainingRun.id == run_id))
    run = result.scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="Training run not found.")
    if run.user_id != current_user:
        raise HTTPException(status_code=403, detail="Access denied.")
    return run


@router.get("/runs/{run_id}", response_model=TrainingRunResponse)
async def get_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TrainingRunResponse:
    """One run, including its accumulated log. This is the polling target."""
    return _to_run_response(await _owned_run(run_id, current_user, db))


@router.get("/runs/{run_id}/model")
async def download_run_model(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Download this run's LoRA adapter as a zip.

    The adapter, not a merged model: it is tens of megabytes rather than
    gigabytes, and it is exactly what `training/serve/app.py` is pointed at.
    """
    run = await _owned_run(run_id, current_user, db)
    if not run.adapter_dir:
        raise HTTPException(
            status_code=404,
            detail="This run has not produced a model yet.",
        )

    try:
        payload, filename = training_run_service.zip_adapter(run.adapter_dir)
    except TrainingRunError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc

    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/runs/{run_id}/recheck",
    response_model=TrainingRunResponse,
    status_code=202,
)
async def recheck_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TrainingRunResponse:
    """Ask Kaggle what a dropped run's kernel is doing, and pick up from there.

    The watcher is what fails on a long run - a DNS blip, or the wall-clock
    timeout - while the kernel itself carries on. This re-attaches to it
    WITHOUT pushing, which is the difference between recovering a run and
    destroying it.
    """
    run = await _owned_run(run_id, current_user, db)
    try:
        run = await training_run_service.recheck_run(run, db)
    except TrainingRunError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return _to_run_response(run)


@router.post(
    "/runs/{run_id}/serve",
    response_model=TrainingRunResponse,
    status_code=202,
)
async def serve_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> TrainingRunResponse:
    """Publish this run's adapter into the local model runtime.

    202, like starting a run: converting the adapter and registering it takes
    longer than a request, so the row carries the progress and the client polls
    `GET /training/runs/{id}` for `serve_status`.
    """
    run = await _owned_run(run_id, current_user, db)
    try:
        run = await model_serving_service.start_publish(run, db)
    except ModelServingError as exc:
        # 409 for the same reason starting a run uses it: the request is fine,
        # the server is not in a state to serve it.
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return _to_run_response(run)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> Response:
    """Delete one run, its log and the adapter it produced.

    404 if it does not exist, 403 if it belongs to another user, 409 while it
    is still running — the row is not what a run is made of, so removing it
    would not stop the work.
    """
    run = await _owned_run(run_id, current_user, db)
    try:
        await training_run_service.delete_run(run, db)
    except TrainingRunError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return Response(status_code=204)


@router.delete("/runs")
async def delete_runs(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> JSONResponse:
    """Delete every one of this user's finished runs, and their adapters.

    Anything still running is left alone rather than refused: this is a request
    about the finished ones, and failing the whole call because something is
    training would disable the button exactly when the list is longest.

    Returns ``{"deleted": N}``. Zero is a success, not a 404.
    """
    deleted = await training_run_service.delete_finished_runs(current_user, db)
    return JSONResponse(status_code=200, content={"deleted": deleted})


@router.delete("/all-data")
async def wipe_all_training_data(
    db: AsyncSession = Depends(get_db),  # noqa: B008
    current_user: str = Depends(get_current_user),
) -> JSONResponse:
    """Delete everything fine-tuning has produced, here and on Kaggle.

    The fine-tune page start-over button. Each of the three stores can already
    be cleared on its own; this exists because clearing them one at a time
    leaves states that are not a fresh start — runs citing datasets that are
    gone, evaluation rows scoring a model that no longer exists.

    It also reaches past the database, which the per-store buttons do not: the
    trained models in `training/outputs`, and the dataset and kernels pushed to
    the user's Kaggle account. Those are the fine-tune's real output, and a
    "start over" that left them would be one in name only.

    409 while a run is in progress. Returns per-store counts.
    """
    try:
        counts = await training_run_service.wipe_all_training_data(current_user, db)
    except TrainingRunError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc
    return JSONResponse(status_code=200, content=counts)
