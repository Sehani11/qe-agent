"""Supabase Storage service wrapper.

All file operations (upload, download, delete, list) in the application
MUST go through this service — never call Supabase Storage SDK directly
in route handlers or other services.

Architecture rule #10: All file operations go through StorageService.

A single Supabase bucket (settings.supabase_bucket / SUPABASE_BUCKET env)
holds everything; per-feature data is namespaced by subfolder:
  - reports/        — generated PDF/CSV reports
  - feature-files/  — uploaded .feature files
  - artifacts/      — test artifacts produced during verification
  - training-data/  — manually uploaded .feature / .jsonl training corpora

Object paths land at {user_id}/{folder}/{path} so the user_id stays the
first path segment, keeping the RLS policy in
backend/supabase/migrations/002_storage_bucket_policies.sql intact.
"""

import asyncio
import logging
from typing import BinaryIO

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageServiceError(Exception):
    """Raised when a storage operation fails."""

    def __init__(self, message: str, code: str = "STORAGE_ERROR"):
        self.message = message
        self.code = code
        super().__init__(self.message)


# Subfolder names inside the shared Supabase bucket.
FOLDER_REPORTS = "reports"
FOLDER_FEATURE_FILES = "feature-files"
FOLDER_ARTIFACTS = "artifacts"
FOLDER_TRAINING_DATA = "training-data"


def _scope(user_id: str, folder: str, path: str) -> str:
    """Build the final object path: {user_id}/{folder}/{path} with empties skipped."""
    parts = [p for p in (user_id, folder, path) if p]
    return "/".join(parts)


class StorageService:
    """Wrapper around Supabase Storage for all file operations.

    Instantiated once as a module-level singleton (``storage_service``).
    If ``SUPABASE_URL`` or ``SUPABASE_SERVICE_ROLE_KEY`` env vars are
    absent, the client is unavailable and every operation raises
    ``StorageServiceError`` with code ``STORAGE_NOT_CONFIGURED``.

    All operations target a single bucket (``settings.supabase_bucket``);
    callers pass a ``folder`` (use ``FOLDER_*`` constants) which becomes
    a subfolder under each user's namespace.
    """

    def __init__(self) -> None:
        self._supabase_url = settings.supabase_url
        self._service_role_key = settings.supabase_service_role_key
        self._bucket = settings.supabase_bucket
        self._client = None

        if self._supabase_url and self._service_role_key:
            try:
                from supabase import create_client
                self._client = create_client(
                    self._supabase_url, self._service_role_key
                )
            except Exception as e:
                logger.warning(
                    "Failed to initialize Supabase storage client: %s. "
                    "Storage operations will be unavailable.",
                    e,
                )

    @property
    def is_available(self) -> bool:
        """Check if the storage service is configured and available."""
        return self._client is not None

    @property
    def bucket(self) -> str:
        """The Supabase bucket name this service writes to."""
        return self._bucket

    async def upload_file(
        self,
        folder: str,
        path: str,
        file_data: bytes | BinaryIO,
        content_type: str = "application/octet-stream",
        user_id: str = "",
    ) -> str:
        """Upload a file under {user_id}/{folder}/{path} in the shared bucket.

        Args:
            folder: Subfolder name (use FOLDER_* constants).
            path: The path under the folder (typically session-id / filename).
            file_data: The file content as bytes or file-like object.
            content_type: MIME type of the file.
            user_id: The user ID for scoping the file path.

        Returns:
            The final object path written to the bucket.

        Raises:
            StorageServiceError: If the upload fails.
        """
        if not self._client:
            raise StorageServiceError(
                "Storage service is not configured.",
                code="STORAGE_NOT_CONFIGURED",
            )

        scoped_path = _scope(user_id, folder, path)

        try:
            await asyncio.to_thread(
                self._client.storage.from_(self._bucket).upload,
                scoped_path,
                file_data,
                {"content-type": content_type, "upsert": "true"},
            )
            return scoped_path
        except Exception as e:
            raise StorageServiceError(
                f"Failed to upload file to {self._bucket}/{scoped_path}: {e!s}"
            ) from e

    async def download_file(
        self,
        folder: str,
        path: str,
        user_id: str = "",
    ) -> bytes:
        """Download a file from {user_id}/{folder}/{path}.

        Raises:
            StorageServiceError: If the download fails.
        """
        if not self._client:
            raise StorageServiceError(
                "Storage service is not configured.",
                code="STORAGE_NOT_CONFIGURED",
            )

        scoped_path = _scope(user_id, folder, path)

        try:
            result = await asyncio.to_thread(
                self._client.storage.from_(self._bucket).download, scoped_path
            )
            return result
        except Exception as e:
            raise StorageServiceError(
                f"Failed to download file from {self._bucket}/{scoped_path}: {e!s}"
            ) from e

    async def delete_file(
        self,
        folder: str,
        path: str,
        user_id: str = "",
    ) -> None:
        """Delete a file at {user_id}/{folder}/{path}.

        Raises:
            StorageServiceError: If the deletion fails.
        """
        if not self._client:
            raise StorageServiceError(
                "Storage service is not configured.",
                code="STORAGE_NOT_CONFIGURED",
            )

        scoped_path = _scope(user_id, folder, path)

        try:
            await asyncio.to_thread(
                self._client.storage.from_(self._bucket).remove, [scoped_path]
            )
        except Exception as e:
            raise StorageServiceError(
                f"Failed to delete file from {self._bucket}/{scoped_path}: {e!s}"
            ) from e

    async def list_files(
        self,
        folder: str,
        prefix: str = "",
        user_id: str = "",
    ) -> list[dict[str, object]]:
        """List files under {user_id}/{folder}/{prefix}.

        Raises:
            StorageServiceError: If the listing fails.
        """
        if not self._client:
            raise StorageServiceError(
                "Storage service is not configured.",
                code="STORAGE_NOT_CONFIGURED",
            )

        scoped_prefix = _scope(user_id, folder, prefix)

        try:
            result = await asyncio.to_thread(
                self._client.storage.from_(self._bucket).list, scoped_prefix
            )
            return result
        except Exception as e:
            raise StorageServiceError(
                f"Failed to list files in {self._bucket}/{scoped_prefix}: {e!s}"
            ) from e


# Singleton instance — import this in route handlers and services
storage_service = StorageService()
