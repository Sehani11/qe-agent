"""Tests for the training-dataset upload API (Story 6.7).

Covers:
- Per-file accept/reject in one multipart request (AC1)
- .feature rejection using the SHARED parser's verdict (AC2)
- .jsonl rejection naming the offending line number (AC3)
- List and delete, including the stored object (AC4)
- User scoping: 403 for another user's row, 404 for missing/malformed (AC6)
- training_opt_in stamped at write time and never exposed (AC7)

Strategy: override get_current_user + get_db (same pattern as
test_knowledge_sources.py); patch the storage singleton the service imports.
"""

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.services.storage_service import StorageServiceError

USER_A = "user-a-id"
USER_B = "user-b-id"

GOOD_FEATURE = b"""Feature: Password reset

  Scenario: A user requests a reset link
    Given a registered user with a verified email address
    When they submit the password reset form
    Then a reset link is emailed to their address
"""

# Parses fine, but has no Then — the exact shape that made the live corpus
# unusable (Story 6.6 finding).
INCOMPLETE_FEATURE = b"""Feature: Partial coverage

  Scenario: Nothing is ever asserted here
    Given a registered user with a verified email address
    When they submit the password reset form
"""


def _pair_line() -> str:
    target = {
        "scenarios": [
            {
                "source_ac_clause": "AC1",
                "feature": "Password reset",
                "scenario": "A user requests a reset link",
                "given": "a registered user with a verified email address",
                "when": "they submit the password reset form",
                "then": "a reset link is emailed to their address",
            }
        ]
    }
    return json.dumps(
        {
            "messages": [
                {"role": "system", "content": "You are a QA engineer."},
                {"role": "user", "content": "AC1: users can reset their password"},
                {"role": "assistant", "content": json.dumps(target)},
            ]
        }
    )


def _make_dataset(
    kind: str = "feature",
    filename: str = "reset.feature",
    user_id: str = USER_A,
) -> MagicMock:
    d = MagicMock()
    d.id = uuid.uuid4()
    d.user_id = user_id
    d.filename = filename
    d.kind = kind
    d.item_count = 3
    d.storage_path = f"{user_id}/training-data/{d.id}/{filename}"
    d.training_opt_in = True
    d.created_at = datetime(2026, 8, 8, tzinfo=UTC)
    return d


def _scalars(items) -> MagicMock:
    scalars = MagicMock()
    scalars.all.return_value = items
    result = MagicMock()
    result.scalars.return_value = scalars
    return result


def _scalar_one(item) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = item
    return r


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def storage():
    """Patch the storage singleton the service module imported."""
    with patch("app.services.training_data_service.storage_service") as s:
        s.upload_file = AsyncMock(
            side_effect=lambda folder, path, file_data, content_type, user_id: (
                f"{user_id}/{folder}/{path}"
            )
        )
        s.delete_file = AsyncMock()
        yield s


def _db_for_write() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    return db


# ---------------------------------------------------------------------------
# Upload (AC1, AC2, AC3)
# ---------------------------------------------------------------------------


def test_uploads_a_valid_feature_file(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("reset.feature", GOOD_FEATURE, "text/plain"))],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["rejected"] == []
    assert len(body["accepted"]) == 1
    accepted = body["accepted"][0]
    assert accepted["filename"] == "reset.feature"
    assert accepted["kind"] == "feature"
    assert accepted["item_count"] == 1
    storage.upload_file.assert_awaited_once()
    assert storage.upload_file.await_args.kwargs["folder"] == "training-data"
    assert storage.upload_file.await_args.kwargs["user_id"] == USER_A
    db.add.assert_called_once()


def test_uploads_a_valid_jsonl_file(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db
    content = "\n".join(_pair_line() for _ in range(4)).encode()

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("pairs.jsonl", content, "application/jsonl"))],
    )

    assert resp.status_code == 200
    accepted = resp.json()["accepted"][0]
    assert accepted["kind"] == "jsonl"
    assert accepted["item_count"] == 4


def test_feature_without_a_complete_scenario_is_rejected(client_as_user_a, storage):
    """AC2 — the shared parser's verdict, not a looser UI-only check."""
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("partial.feature", INCOMPLETE_FEATURE, "text/plain"))],
    )

    assert resp.status_code == 422
    body = resp.json()
    assert body["accepted"] == []
    assert body["rejected"][0]["filename"] == "partial.feature"
    assert "Given, When and Then" in body["rejected"][0]["reason"]
    # Nothing was stored or recorded for a rejected file.
    storage.upload_file.assert_not_awaited()
    db.add.assert_not_called()


def test_jsonl_rejection_names_the_offending_line(client_as_user_a, storage):
    """AC3 — the first bad line number, not a generic failure."""
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db
    content = "\n".join([_pair_line(), _pair_line(), "{ broken"]).encode()

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("pairs.jsonl", content, "application/jsonl"))],
    )

    assert resp.status_code == 422
    assert "line 3" in resp.json()["rejected"][0]["reason"].lower()


def test_mixed_batch_keeps_the_good_files(client_as_user_a, storage):
    """AC1 — one bad file must not cost the user the good ones."""
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[
            ("files", ("good.feature", GOOD_FEATURE, "text/plain")),
            ("files", ("partial.feature", INCOMPLETE_FEATURE, "text/plain")),
            ("files", ("pairs.jsonl", _pair_line().encode(), "application/jsonl")),
        ],
    )

    assert resp.status_code == 200
    body = resp.json()
    assert [a["filename"] for a in body["accepted"]] == ["good.feature", "pairs.jsonl"]
    assert [r["filename"] for r in body["rejected"]] == ["partial.feature"]


def test_unsupported_extension_is_rejected(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )

    assert resp.status_code == 422
    assert ".feature" in resp.json()["rejected"][0]["reason"]


def test_oversize_file_is_rejected_and_never_stored(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db
    oversize = b"x" * (10 * 1024 * 1024 + 10)

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("big.jsonl", oversize, "application/jsonl"))],
    )

    assert resp.status_code == 422
    assert "too large" in resp.json()["rejected"][0]["reason"].lower()
    storage.upload_file.assert_not_awaited()
    db.add.assert_not_called()


# ---------------------------------------------------------------------------
# Filename safety (AC6) — the filename becomes a storage object path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "evil",
    [
        "../../../victim-user/owned.feature",
        "../owned.feature",
        "sub/dir/owned.feature",
        "..\\..\\victim\\owned.feature",
        "dir\\owned.feature",
    ],
)
def test_a_filename_with_a_path_component_is_rejected(client_as_user_a, storage, evil):
    """A crafted filename must not escape {user_id}/training-data/.

    The storage RLS policy keys off the FIRST path segment being the owner's id,
    so a traversing name would write outside the user's namespace — and would
    later let that user delete another user's object through their own row.
    """
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", (evil, GOOD_FEATURE, "text/plain"))],
    )

    assert resp.status_code == 422
    assert "path separators" in resp.json()["rejected"][0]["reason"]
    storage.upload_file.assert_not_awaited()
    db.add.assert_not_called()


def test_stored_path_stays_inside_the_users_namespace(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("reset.feature", GOOD_FEATURE, "text/plain"))],
    )

    assert resp.status_code == 200
    path = resp.json()["accepted"][0]["storage_path"]
    assert path.startswith(f"{USER_A}/training-data/")
    assert ".." not in path


def test_storage_failure_fails_the_file_and_writes_no_row(client_as_user_a, storage):
    """Storage holds the only copy — a row pointing at nothing is worse."""
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db
    storage.upload_file = AsyncMock(
        side_effect=StorageServiceError("bucket unreachable")
    )

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("reset.feature", GOOD_FEATURE, "text/plain"))],
    )

    assert resp.status_code == 422
    assert "store" in resp.json()["rejected"][0]["reason"].lower()
    db.add.assert_not_called()


def test_non_utf8_content_is_rejected(client_as_user_a, storage):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("bad.feature", b"\xff\xfe\x00binary", "text/plain"))],
    )

    assert resp.status_code == 422
    assert "UTF-8" in resp.json()["rejected"][0]["reason"]


# ---------------------------------------------------------------------------
# training_opt_in (AC7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("opt_in", [True, False])
def test_opt_in_flag_is_stamped_from_the_setting(client_as_user_a, storage, opt_in):
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    with patch(
        "app.services.training_data_service.settings.training_data_opt_in", opt_in
    ):
        resp = client_as_user_a.post(
            "/api/v1/training/datasets",
            files=[("files", ("reset.feature", GOOD_FEATURE, "text/plain"))],
        )

    assert resp.status_code == 200
    persisted = db.add.call_args.args[0]
    assert persisted.training_opt_in is opt_in


def test_opt_in_flag_never_appears_in_a_response(client_as_user_a, storage):
    """Operator configuration, not user data (Story 6.6 precedent)."""
    db = _db_for_write()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.post(
        "/api/v1/training/datasets",
        files=[("files", ("reset.feature", GOOD_FEATURE, "text/plain"))],
    )

    assert "training_opt_in" not in resp.text


# ---------------------------------------------------------------------------
# List (AC4, AC6)
# ---------------------------------------------------------------------------


def test_list_returns_the_users_datasets(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([_make_dataset()]))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.get("/api/v1/training/datasets")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["filename"] == "reset.feature"
    assert data[0]["kind"] == "feature"
    assert data[0]["item_count"] == 3
    assert "training_opt_in" not in data[0]


def test_list_is_empty_when_nothing_uploaded(client_as_user_a):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars([]))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.get("/api/v1/training/datasets")

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_query_is_scoped_to_the_current_user(client_as_user_a):
    captured = {}

    async def _capture(statement, *args, **kwargs):
        captured["sql"] = str(statement.compile())
        return _scalars([])

    db = AsyncMock()
    db.execute = _capture
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.get("/api/v1/training/datasets")

    assert resp.status_code == 200
    assert "training_datasets.user_id" in captured["sql"]


# ---------------------------------------------------------------------------
# Delete (AC4, AC6)
# ---------------------------------------------------------------------------


def test_delete_removes_the_object_and_the_row(client_as_user_a, storage):
    dataset = _make_dataset()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(dataset))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/training/datasets/{dataset.id}")

    assert resp.status_code == 204
    storage.delete_file.assert_awaited_once()
    db.delete.assert_awaited_once_with(dataset)
    db.commit.assert_awaited()


def test_delete_still_removes_the_row_when_storage_fails(client_as_user_a, storage):
    """Best-effort object removal — a user must be able to clear a broken entry."""
    dataset = _make_dataset()
    storage.delete_file = AsyncMock(side_effect=StorageServiceError("gone"))
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(dataset))
    db.delete = AsyncMock()
    db.commit = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/training/datasets/{dataset.id}")

    assert resp.status_code == 204
    db.delete.assert_awaited_once_with(dataset)


def test_delete_404_when_missing(client_as_user_a, storage):
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(None))
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/training/datasets/{uuid.uuid4()!s}")

    assert resp.status_code == 404


def test_delete_403_for_another_users_dataset(client_as_user_a, storage):
    dataset = _make_dataset(user_id=USER_B)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalar_one(dataset))
    db.delete = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete(f"/api/v1/training/datasets/{dataset.id}")

    assert resp.status_code == 403
    db.delete.assert_not_awaited()
    storage.delete_file.assert_not_awaited()


def test_delete_404_for_a_malformed_id(client_as_user_a, storage):
    """A non-UUID must not reach the driver and surface as a 500."""
    db = AsyncMock()
    db.execute = AsyncMock()
    app.dependency_overrides[get_db] = lambda: db

    resp = client_as_user_a.delete("/api/v1/training/datasets/not-a-uuid")

    assert resp.status_code == 404
    db.execute.assert_not_awaited()
