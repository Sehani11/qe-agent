"""Tests for document (PDF/DOCX) knowledge ingestion (Story 4.6).

Covers:
- _extract_pdf_text / _extract_docx_text
- ingest_document: upserts source="document", records knowledge_sources row,
  rejects unsupported types + empty docs
- POST /api/v1/knowledge/ingest/document: 200 valid, 422 wrong type, 413 oversize

Strategy:
- Build a real DOCX in-memory (python-docx); mock pypdf.PdfReader for the PDF path.
- Mock _embed_chunks + _upsert_to_pinecone; mock the DB session.
"""

import io
from unittest.mock import AsyncMock, MagicMock, patch

import docx
import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app
from app.services import knowledge_service
from app.services.knowledge_service import (
    DocumentIngestError,
    _extract_docx_text,
    _extract_pdf_text,
    ingest_document,
)

USER_A = "user-a-id"

_LONG_TEXT = (
    "This is a project design document describing the authentication service. "
    "It uses JWT with RS256 and rotates keys every 90 days. "
) * 5


def _make_docx_bytes(text: str) -> bytes:
    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _make_db(existing_source=None) -> MagicMock:
    """Mock AsyncSession; `existing_source` is what the dedupe lookup finds."""
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_source
    db.execute = AsyncMock(return_value=result)
    return db


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------


class TestExtraction:
    def test_extract_docx_text(self):
        data = _make_docx_bytes("Hello DOCX world")
        assert "Hello DOCX world" in _extract_docx_text(data)

    def test_extract_docx_raises_on_corrupt(self):
        with pytest.raises(DocumentIngestError):
            _extract_docx_text(b"not a real docx")

    def test_extract_pdf_text_joins_pages(self):
        page1 = MagicMock()
        page1.extract_text.return_value = "Page one text"
        page2 = MagicMock()
        page2.extract_text.return_value = "Page two text"
        reader = MagicMock()
        reader.pages = [page1, page2]

        with patch("pypdf.PdfReader", return_value=reader):
            out = _extract_pdf_text(b"%PDF-fake")

        assert "Page one text" in out
        assert "Page two text" in out

    def test_extract_pdf_raises_on_corrupt(self):
        with (
            patch("pypdf.PdfReader", side_effect=ValueError("bad")),
            pytest.raises(DocumentIngestError),
        ):
            _extract_pdf_text(b"garbage")


# ---------------------------------------------------------------------------
# ingest_document service
# ---------------------------------------------------------------------------


class TestIngestDocument:
    @pytest.mark.asyncio
    async def test_docx_ingest_upserts_as_document(self):
        db = _make_db()
        data = _make_docx_bytes(_LONG_TEXT)

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(return_value=[[0.1] * 1536]),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ) as mock_upsert,
        ):
            result = await ingest_document(USER_A, "design.docx", data, db)

        assert result["ingested_count"] == 1
        assert result["chunk_count"] >= 1
        assert result["title"] == "design.docx"
        # Upserted under the document source
        assert mock_upsert.call_args.kwargs["source"] == "document"
        assert mock_upsert.call_args.kwargs["title"] == "design.docx"
        # knowledge_sources row recorded as document/completed
        added = db.add.call_args[0][0]
        assert added.source_type == "document"
        assert added.ingestion_status == "completed"
        assert added.source_url is None

    @pytest.mark.asyncio
    async def test_large_document_embeds_in_batches(self):
        """>_EMBED_BATCH_SIZE chunks are embedded across multiple calls (M1)."""
        db = _make_db()
        data = _make_docx_bytes(_LONG_TEXT)

        embed_calls = []

        async def _fake_embed(batch, *args, **kwargs):
            embed_calls.append(len(batch))
            return [[0.1] * 1536 for _ in batch]

        with (
            patch(
                "app.services.knowledge_service.chunk_text",
                return_value=[f"chunk {i}" for i in range(250)],
            ),
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=_fake_embed,
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ),
        ):
            result = await ingest_document(USER_A, "big.docx", data, db)

        # 250 chunks / batch size 100 => 3 batches (100, 100, 50)
        assert len(embed_calls) == 3
        assert embed_calls == [100, 100, 50]
        assert result["chunk_count"] == 250

    @pytest.mark.asyncio
    async def test_embed_failure_raises_clean_error_and_records_failed(self):
        """An embedding/upsert failure becomes a DocumentIngestError, not a 500 (M1)."""
        db = _make_db()
        data = _make_docx_bytes(_LONG_TEXT)

        with (
            patch(
                "app.services.knowledge_service._embed_chunks",
                new=AsyncMock(side_effect=RuntimeError("OpenAI 400: too many inputs")),
            ),
            patch(
                "app.services.knowledge_service._upsert_to_pinecone",
                new=AsyncMock(),
            ),
            pytest.raises(DocumentIngestError) as exc,
        ):
            await ingest_document(USER_A, "big.docx", data, db)

        assert exc.value.code == "DOCUMENT_INDEX_FAILED"
        added = db.add.call_args[0][0]
        assert added.ingestion_status == "failed"

    @pytest.mark.asyncio
    async def test_unsupported_type_raises(self):
        db = _make_db()
        with pytest.raises(DocumentIngestError) as exc:
            await ingest_document(USER_A, "notes.txt", b"hello", db)
        assert exc.value.code == "UNSUPPORTED_FILE_TYPE"

    @pytest.mark.asyncio
    async def test_empty_document_raises_and_records_failed(self):
        db = _make_db()
        data = _make_docx_bytes("")  # no extractable text

        with pytest.raises(DocumentIngestError) as exc:
            await ingest_document(USER_A, "blank.docx", data, db)

        assert exc.value.code == "NO_EXTRACTABLE_TEXT"
        added = db.add.call_args[0][0]
        assert added.ingestion_status == "failed"
        assert added.page_count == 0


# ---------------------------------------------------------------------------
# POST /api/v1/knowledge/ingest/document
# ---------------------------------------------------------------------------


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    app.dependency_overrides[get_db] = lambda: _make_db()
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def test_endpoint_accepts_valid_pdf(client_as_user_a):
    """A valid upload returns 200 with the ingest result."""
    _ok = {"ingested_count": 1, "chunk_count": 3, "title": "d.pdf"}
    with patch.object(
        knowledge_service,
        "ingest_document",
        new=AsyncMock(return_value=_ok),
    ):
        resp = client_as_user_a.post(
            "/api/v1/knowledge/ingest/document",
            files={"file": ("d.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["chunk_count"] == 3
    assert body["title"] == "d.pdf"


def test_endpoint_rejects_unsupported_extension(client_as_user_a):
    """A .txt upload is rejected with 422 before hitting the service."""
    resp = client_as_user_a.post(
        "/api/v1/knowledge/ingest/document",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 422
    msg = resp.json()["message"].lower()
    assert ".pdf" in msg or "accepted" in msg


def test_endpoint_rejects_oversize(client_as_user_a, monkeypatch):
    """An oversize upload is rejected with 413."""
    monkeypatch.setattr("app.api.v1.knowledge._MAX_DOC_BYTES", 4)
    resp = client_as_user_a.post(
        "/api/v1/knowledge/ingest/document",
        files={"file": ("big.pdf", b"%PDF too big", "application/pdf")},
    )
    assert resp.status_code == 413


def test_endpoint_maps_ingest_error_to_422(client_as_user_a):
    """A DocumentIngestError from the service becomes a 422."""
    with patch.object(
        knowledge_service,
        "ingest_document",
        new=AsyncMock(side_effect=DocumentIngestError("No extractable text.")),
    ):
        resp = client_as_user_a.post(
            "/api/v1/knowledge/ingest/document",
            files={"file": ("scan.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert resp.status_code == 422
    assert "no extractable text" in resp.json()["message"].lower()
