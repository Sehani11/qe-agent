"""Tests for the traceability report API (Story 5.3).

Covers:
- generated BDD → rows carry the correct ac_clause (joined by scenario title)
- uploaded / malformed BDD → ac_clause is null (no crash)
- no verification results → 200 with empty rows + zeroed summary
- ownership: 403 non-owner, 404 missing session
- assembled from DB only (no LLM / Pinecone touched)

Strategy: override get_current_user + get_db; MagicMock ORM rows.
The endpoint executes 3 queries in order: Session, latest BddFile, VerificationResults.
"""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.database import get_db
from app.main import app

USER_A = "user-a"
USER_B = "user-b"
SESSION_ID = str(uuid.uuid4())


def _make_session(user_id: str = USER_A) -> MagicMock:
    s = MagicMock()
    s.id = SESSION_ID
    s.user_id = user_id
    s.jira_ticket_id = "PROJ-1"
    return s


def _make_bdd(source: str = "generated", content: str = "") -> MagicMock:
    b = MagicMock()
    b.session_id = SESSION_ID
    b.source = source
    b.content = content
    return b


def _make_vr(
    title: str,
    status: str = "pass",
    suggestion: str | None = None,
    rag: list | None = None,
) -> MagicMock:
    v = MagicMock()
    v.scenario_title = title
    v.status = status
    v.justification = "the code handles it"
    v.code_reference = {"file": "auth.py", "line": 42}
    v.github_links = []
    v.implementation_suggestion = suggestion
    v.rag_context = rag
    return v


def _result(items) -> MagicMock:
    r = MagicMock()
    r.scalar_one_or_none.return_value = items[0] if items else None
    sc = MagicMock()
    sc.all.return_value = items
    r.scalars.return_value = sc
    return r


_GENERATED_BDD = json.dumps({
    "scenarios": [
        {
            "scenario": "Login works",
            "source_ac_clause": "AC1: user can log in",
            "feature": "Auth",
            "given": "g",
            "when": "w",
            "then": "t",
        },
        {
            "scenario": "Logout works",
            "source_ac_clause": "AC2: user can log out",
            "feature": "Auth",
            "given": "g",
            "when": "w",
            "then": "t",
        },
    ]
})


@pytest.fixture
def client_as_user_a():
    async def _auth():
        return USER_A

    app.dependency_overrides[get_current_user] = _auth
    yield TestClient(app)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


def _mount_db(*results):
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=list(results))
    app.dependency_overrides[get_db] = lambda: db
    return db


def test_traceability_generated_bdd_maps_ac_clauses(client_as_user_a):
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", _GENERATED_BDD)]),
        _result([
            _make_vr("Login works", "pass"),
            _make_vr("Logout works", "fail", suggestion="add logout route",
                     rag=[{"source": "confluence", "source_id": "1", "snippet": "x"}]),
        ]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    data = resp.json()
    assert data["jira_ticket_id"] == "PROJ-1"
    assert data["summary"] == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "partial": 0,
        "inconclusive": 0,
    }
    assert data["rows"][0]["ac_clause"] == "AC1: user can log in"
    assert data["rows"][0]["scenario_status"] == "pass"
    assert data["rows"][1]["ac_clause"] == "AC2: user can log out"
    assert data["rows"][1]["scenario_status"] == "fail"
    assert data["rows"][1]["implementation_suggestion"] == "add logout route"
    assert data["rows"][1]["rag_context"][0]["source"] == "confluence"


def test_traceability_matches_ac_despite_whitespace_in_title(client_as_user_a):
    """Review M1: a BDD scenario title with surrounding whitespace still joins,
    because verification stores the stripped title."""
    padded_bdd = json.dumps({
        "scenarios": [
            {
                "scenario": "  Login works  ",  # padded in the BDD JSON
                "source_ac_clause": "AC1",
                "feature": "Auth",
                "given": "g",
                "when": "w",
                "then": "t",
            }
        ]
    })
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", padded_bdd)]),
        _result([_make_vr("Login works", "pass")]),  # stored stripped
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    assert resp.json()["rows"][0]["ac_clause"] == "AC1"


def test_traceability_orphan_verdict_gets_null_ac(client_as_user_a):
    """Review L2/Q2: a verdict whose title isn't in the BDD map → null ac_clause."""
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", _GENERATED_BDD)]),  # has Login/Logout
        _result([_make_vr("Some renamed scenario", "pass")]),  # not in the map
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    assert resp.json()["rows"][0]["ac_clause"] is None


def test_traceability_uploaded_bdd_has_null_ac_clause(client_as_user_a):
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("uploaded", "Feature: X\n  Scenario: Login works\n")]),
        _result([_make_vr("Login works", "pass")]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    assert resp.json()["rows"][0]["ac_clause"] is None


def test_traceability_malformed_bdd_json_yields_null_ac(client_as_user_a):
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", "{not valid json")]),
        _result([_make_vr("Login works", "pass")]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    assert resp.json()["rows"][0]["ac_clause"] is None


def test_traceability_no_results_returns_empty_report(client_as_user_a):
    _mount_db(
        _result([_make_session()]),
        _result([]),  # no bdd file
        _result([]),  # no verification results
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")

    assert resp.status_code == 200
    data = resp.json()
    assert data["rows"] == []
    assert data["summary"] == {
        "total": 0,
        "passed": 0,
        "failed": 0,
        "partial": 0,
        "inconclusive": 0,
    }


def test_traceability_403_for_non_owner(client_as_user_a):
    _mount_db(_result([_make_session(USER_B)]))
    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability")
    assert resp.status_code == 403


def test_traceability_404_for_missing_session(client_as_user_a):
    _mount_db(_result([]))
    resp = client_as_user_a.get(f"/api/v1/reports/{uuid.uuid4()!s}/traceability")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Story 5.4 — PDF / CSV export
# ---------------------------------------------------------------------------

from app.services.storage_service import StorageServiceError  # noqa: E402


def _fake_storage(monkeypatch, *, available=True, upload_side_effect=None):
    """Replace the storage_service singleton used by the reports route."""
    fake = MagicMock()
    fake.is_available = available
    fake.upload_file = AsyncMock(side_effect=upload_side_effect)
    monkeypatch.setattr("app.api.v1.reports.storage_service", fake)
    return fake


def _mount_export_db():
    """Session + generated BDD (Login/Logout) + two verdicts (one pass, one fail)."""
    return _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", _GENERATED_BDD)]),
        _result([
            _make_vr("Login works", "pass"),
            _make_vr(
                "Logout works",
                "fail",
                suggestion="add logout route",
                rag=[{
                    "source": "confluence",
                    "source_id": "42",
                    "snippet": "logout flow docs",
                    "title": "Auth Guide",
                }],
            ),
        ]),
    )


def test_export_pdf_returns_pdf_bytes(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_export_db()

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content.startswith(b"%PDF-")
    assert 'filename="PROJ-1_report.pdf"' in resp.headers["content-disposition"]


def test_export_pdf_persists_to_storage(client_as_user_a, monkeypatch):
    fake = _fake_storage(monkeypatch, available=True)
    _mount_export_db()

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")

    assert resp.status_code == 200
    fake.upload_file.assert_awaited_once()
    kwargs = fake.upload_file.await_args.kwargs
    assert kwargs["folder"] == "reports"
    assert kwargs["path"] == f"{SESSION_ID}/PROJ-1_report.pdf"
    assert kwargs["content_type"] == "application/pdf"
    assert kwargs["user_id"] == USER_A


def test_export_pdf_survives_storage_error(client_as_user_a, monkeypatch):
    """AC3: a storage failure must not break the download."""
    _fake_storage(
        monkeypatch,
        available=True,
        upload_side_effect=StorageServiceError("boom"),
    )
    _mount_export_db()

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def test_export_csv_header_and_rows(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_export_db()

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert 'filename="PROJ-1_report.csv"' in resp.headers["content-disposition"]

    lines = resp.content.decode("utf-8").splitlines()
    assert lines[0] == (
        "AC Clause,Scenario,Status,Justification,Code Reference,"
        "Implementation Suggestion,RAG Context"
    )
    # header + 2 data rows (may wrap if a cell had newlines — here it won't)
    import csv as _csv
    rows = list(_csv.reader(resp.content.decode("utf-8").splitlines()))
    assert len(rows) == 3
    assert rows[1][0] == "AC1: user can log in"  # ac_clause joined by title
    assert rows[1][1] == "Login works"
    assert rows[1][2] == "pass"


def test_export_csv_serializes_dict_and_list_fields(client_as_user_a, monkeypatch):
    """code_reference (dict) and rag_context (list) must be readable, not repr."""
    _fake_storage(monkeypatch, available=False)
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("generated", _GENERATED_BDD)]),
        _result([
            _make_vr(
                "Logout works",
                "fail",
                suggestion="add logout route",
                rag=[{
                    "source": "confluence",
                    "source_id": "42",
                    "snippet": "docs",
                    "title": "Auth Guide",
                }],
            ),
        ]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    import csv as _csv
    rows = list(_csv.reader(resp.content.decode("utf-8").splitlines()))
    data = rows[1]
    # _make_vr uses code_reference={"file": "auth.py", "line": 42}
    assert data[4] == "auth.py:42"
    assert data[5] == "add logout route"
    assert data[6] == "confluence: Auth Guide — docs"
    assert "{" not in data[6] and "}" not in data[6]  # not a python-repr dict


def test_export_csv_none_fields_render_empty(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("uploaded", "Feature: X\n  Scenario: Login works\n")]),
        _result([_make_vr("Login works", "pass")]),  # no suggestion, no rag
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    import csv as _csv
    rows = list(_csv.reader(resp.content.decode("utf-8").splitlines()))
    data = rows[1]
    assert data[0] == ""  # ac_clause null (uploaded BDD)
    assert data[5] == ""  # implementation_suggestion null
    assert data[6] == ""  # rag_context null


def test_export_empty_report_csv_header_only(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_db(
        _result([_make_session()]),
        _result([]),  # no bdd
        _result([]),  # no verification results
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    assert resp.status_code == 200
    lines = resp.content.decode("utf-8").splitlines()
    assert len(lines) == 1  # header only


def test_export_empty_report_pdf_still_valid(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_db(
        _result([_make_session()]),
        _result([]),
        _result([]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")


def test_export_pdf_403_for_non_owner(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_db(_result([_make_session(USER_B)]))
    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")
    assert resp.status_code == 403


def test_export_csv_404_for_missing_session(client_as_user_a, monkeypatch):
    _fake_storage(monkeypatch, available=False)
    _mount_db(_result([]))
    resp = client_as_user_a.get(f"/api/v1/reports/{uuid.uuid4()!s}/export/csv")
    assert resp.status_code == 404


# ---- Review fixes: content safety (H1 / M1 / L1) --------------------------


def test_export_pdf_survives_markup_content(client_as_user_a, monkeypatch):
    """H1: content with angle brackets must not crash the PDF nor be dropped.

    Reportlab parses Paragraph text as mini-XML; without escaping, unbalanced
    tags crash the build and '<...>' spans get silently swallowed.
    """
    import io as _io

    from pypdf import PdfReader

    _fake_storage(monkeypatch, available=False)
    vr = _make_vr("Handles foo<T>(x)", "fail", suggestion="wrap <Component> safely")
    vr.justification = "unbalanced <b>bold and a < b && c > d"
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("uploaded", "")]),
        _result([vr]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/pdf")

    assert resp.status_code == 200
    assert resp.content.startswith(b"%PDF-")
    text = PdfReader(_io.BytesIO(resp.content)).pages[0].extract_text()
    assert "foo<T>(x)" in text  # angle-bracket content preserved, not swallowed


def test_export_csv_neutralizes_formula_injection(client_as_user_a, monkeypatch):
    """M1: a cell beginning with '=' is prefixed with ' so it can't execute."""
    import csv as _csv

    _fake_storage(monkeypatch, available=False)
    vr = _make_vr("Login works", "pass")
    vr.justification = "=1+2 dangerous"
    _mount_db(
        _result([_make_session()]),
        _result([_make_bdd("uploaded", "")]),
        _result([vr]),
    )

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    rows = list(_csv.reader(resp.content.decode("utf-8").splitlines()))
    assert rows[1][3] == "'=1+2 dangerous"  # Justification column, guarded


def test_export_filename_sanitized_for_unsafe_ticket_id(client_as_user_a, monkeypatch):
    """L1: a ticket id with quotes/spaces can't break the Content-Disposition."""
    _fake_storage(monkeypatch, available=False)
    session = _make_session()
    session.jira_ticket_id = 'PROJ-1"; evil'
    _mount_db(_result([session]), _result([]), _result([]))

    resp = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/export/csv")

    assert resp.status_code == 200
    assert (
        'filename="PROJ-1___evil_report.csv"'
        in resp.headers["content-disposition"]
    )


# ---------------------------------------------------------------------------
# Summary bucketing (Findings 3 and 4)
# ---------------------------------------------------------------------------


def test_summary_keeps_inconclusive_separate_from_failed(client_as_user_a):
    """An inconclusive verdict asks the reader to verify again; a failure asks
    them to write code. Counting the first as the second is the false negative
    the verification prompt exists to prevent, and the report used to
    reintroduce it at the last step.
    """
    _mount_db(
        _result([_make_session()]),
        _result([]),
        _result(
            [
                _make_vr("A", status="pass"),
                _make_vr("B", status="fail", suggestion="write it"),
                _make_vr("C", status="inconclusive", suggestion="read the router"),
                _make_vr("D", status="inconclusive", suggestion="read the router"),
            ]
        ),
    )

    data = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability").json()

    assert data["summary"] == {
        "total": 4,
        "passed": 1,
        "failed": 1,
        "partial": 0,
        "inconclusive": 2,
    }


def test_summary_counts_partial_separately(client_as_user_a):
    _mount_db(
        _result([_make_session()]),
        _result([]),
        _result(
            [
                _make_vr("A", status="pass"),
                _make_vr("B", status="partial", suggestion="wire it up"),
                _make_vr("C", status="fail", suggestion="write it"),
            ]
        ),
    )

    data = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability").json()

    assert data["summary"] == {
        "total": 3,
        "passed": 1,
        "failed": 1,
        "partial": 1,
        "inconclusive": 0,
    }


def test_summary_buckets_always_add_up_to_total(client_as_user_a):
    """An unrecognised status must land somewhere rather than vanish - a
    summary whose parts do not sum to `total` is worse than a wrong bucket.
    """
    _mount_db(
        _result([_make_session()]),
        _result([]),
        _result(
            [
                _make_vr("A", status="pass"),
                _make_vr("B", status="something-new"),
            ]
        ),
    )

    s = client_as_user_a.get(f"/api/v1/reports/{SESSION_ID}/traceability").json()[
        "summary"
    ]

    assert s["total"] == 2
    assert s["passed"] + s["failed"] + s["partial"] + s["inconclusive"] == s["total"]
