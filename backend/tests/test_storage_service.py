"""Tests for the StorageService wrapper around Supabase Storage SDK."""

from unittest.mock import MagicMock

import pytest

from app.services.storage_service import (
    FOLDER_ARTIFACTS,
    FOLDER_FEATURE_FILES,
    FOLDER_REPORTS,
    StorageService,
    StorageServiceError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_with_mock_client(
    bucket: str = "qe-agent",
) -> tuple[StorageService, MagicMock]:
    """Construct a StorageService with a mocked Supabase client injected."""
    service = StorageService.__new__(StorageService)
    service._supabase_url = "https://test.supabase.co"
    service._service_role_key = "test-service-key"
    service._bucket = bucket
    mock_client = MagicMock()
    service._client = mock_client
    return service, mock_client


def _make_unconfigured_service() -> StorageService:
    """Construct a StorageService with no client (simulates missing env vars)."""
    service = StorageService.__new__(StorageService)
    service._client = None
    service._bucket = "qe-agent"
    return service


# ---------------------------------------------------------------------------
# is_available property
# ---------------------------------------------------------------------------


def test_is_available_true_when_client_set() -> None:
    service, _ = _make_service_with_mock_client()
    assert service.is_available is True


def test_is_available_false_when_client_none() -> None:
    service = _make_unconfigured_service()
    assert service.is_available is False


# ---------------------------------------------------------------------------
# upload_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_file_success() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().upload.return_value = {}

    result = await service.upload_file(
        folder=FOLDER_FEATURE_FILES,
        path="session-abc/test.feature",
        file_data=b"Feature: test",
        content_type="text/plain",
        user_id="user-123",
    )

    assert result == "user-123/feature-files/session-abc/test.feature"


@pytest.mark.asyncio
async def test_upload_scopes_path_to_user_id_and_folder() -> None:
    """Object path is {user_id}/{folder}/{path} so RLS user-isolation holds."""
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().upload.return_value = {}

    result = await service.upload_file(
        folder=FOLDER_REPORTS,
        path="my-report.pdf",
        file_data=b"%PDF",
        content_type="application/pdf",
        user_id="user-xyz",
    )

    assert result == "user-xyz/reports/my-report.pdf"
    upload_call_args = mock_client.storage.from_().upload.call_args
    assert upload_call_args[0][0] == "user-xyz/reports/my-report.pdf"


@pytest.mark.asyncio
async def test_upload_file_no_user_id_uses_folder_path() -> None:
    """When user_id is empty, path is folder/{path} without a user prefix."""
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().upload.return_value = {}

    result = await service.upload_file(
        folder=FOLDER_ARTIFACTS,
        path="artifact.zip",
        file_data=b"data",
    )

    assert result == "artifacts/artifact.zip"


@pytest.mark.asyncio
async def test_upload_file_not_configured() -> None:
    service = _make_unconfigured_service()

    with pytest.raises(StorageServiceError) as exc_info:
        await service.upload_file(
            folder=FOLDER_FEATURE_FILES,
            path="test.feature",
            file_data=b"Feature: test",
            user_id="user-123",
        )

    assert exc_info.value.code == "STORAGE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_upload_file_sdk_error_raises_storage_error() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().upload.side_effect = Exception("network error")

    with pytest.raises(StorageServiceError) as exc_info:
        await service.upload_file(
            folder=FOLDER_FEATURE_FILES,
            path="fail.feature",
            file_data=b"data",
            user_id="user-123",
        )

    assert "network error" in exc_info.value.message


# ---------------------------------------------------------------------------
# download_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_download_file_success() -> None:
    service, mock_client = _make_service_with_mock_client()
    expected_bytes = b"Feature: downloaded content"
    mock_client.storage.from_().download.return_value = expected_bytes

    result = await service.download_file(
        folder=FOLDER_FEATURE_FILES,
        path="session-abc/test.feature",
        user_id="user-123",
    )

    assert result == expected_bytes


@pytest.mark.asyncio
async def test_download_file_scopes_path() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().download.return_value = b"data"

    await service.download_file(
        folder=FOLDER_FEATURE_FILES,
        path="session-abc/test.feature",
        user_id="user-123",
    )

    mock_client.storage.from_().download.assert_called_once_with(
        "user-123/feature-files/session-abc/test.feature"
    )


@pytest.mark.asyncio
async def test_download_file_not_configured() -> None:
    service = _make_unconfigured_service()

    with pytest.raises(StorageServiceError) as exc_info:
        await service.download_file(
            folder=FOLDER_FEATURE_FILES,
            path="test.feature",
            user_id="user-123",
        )

    assert exc_info.value.code == "STORAGE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_download_file_sdk_error_raises_storage_error() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().download.side_effect = Exception("not found")

    with pytest.raises(StorageServiceError) as exc_info:
        await service.download_file(
            folder=FOLDER_FEATURE_FILES,
            path="missing.feature",
            user_id="user-123",
        )

    assert "not found" in exc_info.value.message


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_file_success() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().remove.return_value = []

    await service.delete_file(
        folder=FOLDER_FEATURE_FILES,
        path="session-abc/test.feature",
        user_id="user-123",
    )

    mock_client.storage.from_().remove.assert_called_once_with(
        ["user-123/feature-files/session-abc/test.feature"]
    )


@pytest.mark.asyncio
async def test_delete_file_not_configured() -> None:
    service = _make_unconfigured_service()

    with pytest.raises(StorageServiceError) as exc_info:
        await service.delete_file(
            folder=FOLDER_FEATURE_FILES,
            path="test.feature",
            user_id="user-123",
        )

    assert exc_info.value.code == "STORAGE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_delete_file_sdk_error_raises_storage_error() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().remove.side_effect = Exception("permission denied")

    with pytest.raises(StorageServiceError) as exc_info:
        await service.delete_file(
            folder=FOLDER_FEATURE_FILES,
            path="locked.feature",
            user_id="user-123",
        )

    assert "permission denied" in exc_info.value.message


# ---------------------------------------------------------------------------
# list_files
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_files_success() -> None:
    service, mock_client = _make_service_with_mock_client()
    expected = [{"name": "user-123/feature-files/session-abc/test.feature", "id": "abc"}]
    mock_client.storage.from_().list.return_value = expected

    result = await service.list_files(
        folder=FOLDER_FEATURE_FILES,
        prefix="session-abc",
        user_id="user-123",
    )

    assert result == expected
    mock_client.storage.from_().list.assert_called_once_with(
        "user-123/feature-files/session-abc"
    )


@pytest.mark.asyncio
async def test_list_files_empty_prefix() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().list.return_value = []

    await service.list_files(
        folder=FOLDER_FEATURE_FILES,
        user_id="user-123",
    )

    # Empty prefix → lists the user's whole folder, no trailing slash
    mock_client.storage.from_().list.assert_called_once_with("user-123/feature-files")


@pytest.mark.asyncio
async def test_list_files_not_configured() -> None:
    service = _make_unconfigured_service()

    with pytest.raises(StorageServiceError) as exc_info:
        await service.list_files(
            folder=FOLDER_FEATURE_FILES,
            user_id="user-123",
        )

    assert exc_info.value.code == "STORAGE_NOT_CONFIGURED"


@pytest.mark.asyncio
async def test_list_files_sdk_error_raises_storage_error() -> None:
    service, mock_client = _make_service_with_mock_client()
    mock_client.storage.from_().list.side_effect = Exception("bucket not found")

    with pytest.raises(StorageServiceError) as exc_info:
        await service.list_files(
            folder=FOLDER_FEATURE_FILES,
            user_id="user-123",
        )

    assert "bucket not found" in exc_info.value.message


# ---------------------------------------------------------------------------
# Folder constants
# ---------------------------------------------------------------------------


def test_folder_constants_are_correct() -> None:
    assert FOLDER_REPORTS == "reports"
    assert FOLDER_FEATURE_FILES == "feature-files"
    assert FOLDER_ARTIFACTS == "artifacts"


# ---------------------------------------------------------------------------
# StorageServiceError
# ---------------------------------------------------------------------------


def test_storage_service_error_default_code() -> None:
    err = StorageServiceError("something failed")
    assert err.message == "something failed"
    assert err.code == "STORAGE_ERROR"


def test_storage_service_error_custom_code() -> None:
    err = StorageServiceError("not configured", code="STORAGE_NOT_CONFIGURED")
    assert err.code == "STORAGE_NOT_CONFIGURED"
