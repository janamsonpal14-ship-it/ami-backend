from __future__ import annotations

import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import openpyxl
import pytest
from docx import Document
from fastapi.testclient import TestClient
from PIL import Image
from pypdf import PdfReader
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@pytest.fixture()
def client_and_dirs(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_phase2.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-phase2-secret-key-for-automated-qa-only-987654")

    import importlib
    import app.config as cfg
    import app.database as database
    import app.models as models
    import app.services.audit_service as audit_mod
    import app.services.bhc_pdf_parser as bhc_mod
    import app.services.memo_exporter as memo_exp_mod
    import app.services.excel_service as excel_mod
    import app.main as main_mod

    importlib.reload(cfg)
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(audit_mod)
    importlib.reload(bhc_mod)
    importlib.reload(memo_exp_mod)
    importlib.reload(excel_mod)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        yield client, data_dir


def _auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"login_id": "demo_advocate", "password": "DemoPassword@2026"},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _build_synthetic_bhc_pdf(year: int = 2027) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    _, h = A4

    c.setFont("Helvetica-Bold", 14)
    c.drawString(160, h - 40, f"HIGH COURT OF BOMBAY - Calendar for the year {year}")

    cols_x = [40, 180, 320, 460]
    row_ys = [h - 80, h - 300, h - 520]
    months = [
        ["JANUARY", "FEBRUARY", "MARCH", "APRIL"],
        ["MAY", "JUNE", "JULY", "AUGUST"],
        ["SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"],
    ]
    c.setFont("Helvetica", 9)
    for r_idx, ry in enumerate(row_ys):
        for c_idx, cx in enumerate(cols_x):
            c.drawString(cx, ry, months[r_idx][c_idx])
        c.drawString(40, ry - 90, "SATURDAY")

    c.drawString(42, row_ys[0] - 115, "26 Republic Day")
    c.drawString(182, row_ys[0] - 115, "19 Chhatrapati Shivaji Maharaj Jayanti")
    c.showPage()

    c.setFont("Helvetica", 10)
    c.drawString(40, h - 60, f"HIGH COURT OF BOMBAY {year} - VACATIONS & WORKING SATURDAYS")
    c.drawString(40, h - 100, f"Summer Vacation : From Monday, 10 May, {year} to Friday, 14 May, {year}")
    c.drawString(40, h - 120, f"Diwali Vacation : From Monday, 1 November, {year} to Friday, 5 November, {year}")
    c.drawString(40, h - 160, "1. Court working Saturdays :")
    c.drawString(50, h - 180, f"Principal Seat at Bombay : Saturday, 9 January, {year}")
    c.drawString(40, h - 205, "2. The High Court will remain closed on all Sundays and Second & Fourth Saturdays.")
    c.save()
    return buf.getvalue()


def test_bhc_pdf_preview_and_commit_with_snapshot(client_and_dirs):
    client, data_dir = client_and_dirs
    headers = _auth_headers(client)

    pdf_bytes = _build_synthetic_bhc_pdf(2027)
    prev = client.post(
        "/api/calendar/bhc-pdf/preview",
        headers=headers,
        files={"file": ("BHC_Cal_2027.pdf", pdf_bytes, "application/pdf")},
        data={"bench": "Principal Seat Bombay"},
    )
    assert prev.status_code == 200, prev.text
    pdata = prev.json()
    assert pdata["year"] == 2027
    assert pdata["public_holidays_count"] >= 2
    assert pdata["vacation_days_count"] >= 10
    assert pdata["working_saturdays_count"] >= 1

    entries = pdata["entries"]
    dates_by_key = {(e["calendar_date"], e["is_closed"]): e for e in entries}
    assert ("2027-01-26", True) in dates_by_key
    assert ("2027-01-09", False) in dates_by_key
    assert ("2027-05-10", True) in dates_by_key

    commit_resp = client.post(
        "/api/calendar/bhc-pdf/commit",
        headers=headers,
        json={
            "year": 2027,
            "bench": "Principal Seat Bombay",
            "source_file": pdata["source_file"],
            "entries": entries,
        },
    )
    assert commit_resp.status_code == 200, commit_resp.text
    assert commit_resp.json()["inserted"] > 50

    m_resp = client.get("/api/calendar/month?year=2027&month=1", headers=headers)
    assert m_resp.status_code == 200
    holidays = m_resp.json()["holidays"]
    assert any(h["calendar_date"] == "2027-01-26" and h["is_closed"] is True for h in holidays)
    assert any(h["calendar_date"] == "2027-01-09" and h["is_closed"] is False for h in holidays)

    snapshots = list((data_dir / "snapshots").glob("auto_*bhc_cal_2027*.db"))
    assert len(snapshots) >= 1


def test_letterhead_upload_and_memo_docx_pdf_export(client_and_dirs):
    client, _ = client_and_dirs
    headers = _auth_headers(client)

    bad_lh = client.post(
        "/api/letterheads",
        headers=headers,
        files={"file": ("not_image.txt", b"hello", "text/plain")},
        data={"name": "Bad"},
    )
    assert bad_lh.status_code == 422

    img = Image.new("RGB", (600, 120), color=(15, 23, 42))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    up_lh = client.post(
        "/api/letterheads",
        headers=headers,
        files={"file": ("chambers_lh.png", img_buf.getvalue(), "image/png")},
        data={
            "name": "Mumbai Chambers Letterhead",
            "top_margin_mm": "22",
            "bottom_margin_mm": "18",
            "left_margin_mm": "20",
            "right_margin_mm": "20",
            "image_width_pct": "95",
            "is_default": "true",
        },
    )
    assert up_lh.status_code == 200, up_lh.text
    lh_id = up_lh.json()["id"]

    patch_lh = client.patch(
        f"/api/letterheads/{lh_id}",
        headers=headers,
        json={"top_margin_mm": 25},
    )
    assert patch_lh.status_code == 200
    assert patch_lh.json()["top_margin_mm"] == 25

    img_resp = client.get(f"/api/letterheads/{lh_id}/image", headers=headers)
    assert img_resp.status_code == 200
    assert img_resp.content[:4] == b"\x89PNG"

    c_resp = client.post(
        "/api/clients",
        headers=headers,
        json={"display_name": "Meridian Shipping Co. (Test)", "address": "Nariman Point, Mumbai"},
    )
    assert c_resp.status_code == 200, c_resp.text
    cid = c_resp.json()["id"]

    m_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Meridian Shipping vs Port Authority",
            "case_number": "COMSL/402/2026",
            "court_forum": "Bombay High Court",
            "clients": [{"client_id": cid, "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200, m_resp.text
    mid = m_resp.json()["id"]

    adv_id = client.post("/api/advocates", headers=headers, json={"full_name": "Adv. Test Counsel"}).json()["id"]
    h_resp = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={
            "hearing_date": str(date.today()),
            "status": "Completed",
            "business_conducted": "Argued ad-interim relief",
            "advocate_ids": [adv_id],
            "next_date_status": "Fixed",
            "next_date": str(date.today() + timedelta(days=10)),
        },
    )
    assert h_resp.status_code == 200, h_resp.text

    memo_resp = client.post(
        "/api/memos",
        headers=headers,
        json={
            "bill_number": "TEST/2026/01",
            "bill_date": str(date.today()),
            "addressee_client_id": cid,
            "addressee_name": "Meridian Shipping Co. (Test)",
            "addressee_address": "Nariman Point, Mumbai",
            "matter_ids": [mid],
            "letterhead_id": lh_id,
            "subject_line": "Professional Fee Memo for Appearance & Drafting",
            "lines": [
                {"category": "Drafting", "description": "Plaint & Affidavit in Support", "quantity": 1, "unit_rate": 35000},
                {"category": "Appearance", "description": "Hearing before Single Judge", "quantity": 2, "unit_rate": 15000},
            ],
            "adjustments": [{"reason": "Less Advance", "signed_amount": -10000}],
            "tax_percent": 0,
            "footer_bank_details": "Chambers Bank Account - NEFT/RTGS Only",
        },
    )
    assert memo_resp.status_code == 200, memo_resp.text
    memo_id = memo_resp.json()["id"]

    docx_resp = client.get(f"/api/memos/{memo_id}/export.docx?include_annexure=true", headers=headers)
    assert docx_resp.status_code == 200
    assert docx_resp.content[:2] == b"PK"
    with zipfile.ZipFile(io.BytesIO(docx_resp.content)) as zf:
        assert "word/document.xml" in zf.namelist()
    doc = Document(io.BytesIO(docx_resp.content))
    full_doc_text = "\n".join(p.text for p in doc.paragraphs)
    assert "TEST/2026/01" in full_doc_text
    assert "ANNEXURE" in full_doc_text
    assert "Chambers Bank Account" in full_doc_text

    pdf_resp = client.get(f"/api/memos/{memo_id}/export.pdf?include_annexure=true", headers=headers)
    assert pdf_resp.status_code == 200
    assert pdf_resp.content[:5] == b"%PDF-"
    reader = PdfReader(io.BytesIO(pdf_resp.content))
    assert len(reader.pages) == 2


def test_excel_preview_import_and_bidirectional_exports(client_and_dirs):
    client, _ = client_and_dirs
    headers = _auth_headers(client)

    c_resp = client.post("/api/clients", headers=headers, json={"display_name": "Beacon Textiles (Test)"})
    cid = c_resp.json()["id"]
    m_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Beacon Textiles vs State",
            "case_number": "WP/88/2026",
            "court_forum": "Bombay High Court",
            "clients": [{"client_id": cid, "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200, m_resp.text

    wb = openpyxl.Workbook()
    ws_m = wb.active
    ws_m.title = "Monthly Matters"
    ws_m.append(["Present Date", "Court", "Case Name", "Business Conducted", "Stage", "Next Date"])
    ws_m.append(["14-01-2027", "Bombay High Court", "Beacon Textiles vs State", "Notice issued", "Notice", "06.01.27"])
    ws_m.append([None, None, None, None, None, None])
    ws_m.append(["15-01-2027", "Bombay High Court", "Apex Foods vs Municipal Corp", "Part heard", "Hearing", "TBD"])

    ws_c = wb.create_sheet("Client-Wise List")
    ws_c.append(["Sr. No.", "Matter", "Court", "Next Date", "Stage"])
    ws_c.append(["Orbit Logistics Pvt. Ltd. (Test)"])
    ws_c.append([1, "Orbit Logistics vs Customs", "Bombay High Court", date(2027, 3, 18), "Arguments"])
    ws_c.append([2, "Orbit Logistics vs Port Trust", "City Civil Court", "Due Course", "Pleadings"])

    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)

    prev = client.post(
        "/api/import/excel/preview",
        headers=headers,
        files={"file": ("Practice_Import.xlsx", xlsx_buf.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert prev.status_code == 200, prev.text
    pdata = prev.json()
    assert pdata["total_rows"] == 4
    assert pdata["flagged_count"] >= 2

    rows = pdata["rows"]
    for row in rows:
        if row["matter_title"] == "Beacon Textiles vs State":
            row["next_date"] = "2027-02-06"
        if row["matter_title"] == "Apex Foods vs Municipal Corp":
            row["next_date"] = "2027-02-20"
            row["client_name"] = "Apex Foods Ltd. (Test)"

    commit = client.post(
        "/api/import/excel/commit",
        headers=headers,
        json={"filename": pdata["filename"], "rows": rows},
    )
    assert commit.status_code == 200, commit.text
    cdata = commit.json()
    assert cdata["added_clients"] >= 2
    assert cdata["added_matters"] >= 3
    assert cdata["matched_matters"] >= 1
    assert cdata["added_hearings"] >= 2

    exp_m = client.get("/api/reports/monthly/export.xlsx?year=2027&month=1", headers=headers)
    assert exp_m.status_code == 200
    wb_out_m = openpyxl.load_workbook(io.BytesIO(exp_m.content))
    ws_out_m = wb_out_m.active
    assert ws_out_m.cell(row=1, column=1).value == "Sr. No."
    assert ws_out_m.cell(row=1, column=2).value == "Matter"
    assert ws_out_m.max_row >= 3

    exp_c = client.get("/api/reports/client-wise/export.xlsx", headers=headers)
    assert exp_c.status_code == 200
    wb_out_c = openpyxl.load_workbook(io.BytesIO(exp_c.content))
    ws_out_c = wb_out_c.active
    all_values = [cell.value for row in ws_out_c.iter_rows(values_only=False) for cell in row if cell.value]
    assert any("Orbit Logistics" in str(v) for v in all_values)


def test_sync_indian_festival_holidays_from_free_ics_feed(client_and_dirs, monkeypatch):
    client, _ = client_and_dirs
    headers = _auth_headers(client)

    sample_ics = """BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART;VALUE=DATE:20260304
DESCRIPTION:Public holiday
SUMMARY:Holi
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261111
DESCRIPTION:Observance
SUMMARY:Bhai Duj
END:VEVENT
BEGIN:VEVENT
DTSTART;VALUE=DATE:20261115
DESCRIPTION:Observance
SUMMARY:Chhat Puja (Pratihar Sashthi\\, Surya Sashthi)
END:VEVENT
END:VCALENDAR
"""

    class _FakeResp:
        text = sample_ics

        def raise_for_status(self):
            return None

    import app.services.calendar_service as cal_mod

    monkeypatch.setattr(cal_mod.httpx, "get", lambda *args, **kwargs: _FakeResp())

    r1 = client.post("/api/calendar/indian-festivals/sync?year=2026", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["fetched"] == 3
    assert r1.json()["added"] == 3

    # Re-sync skips duplicates idempotently
    r2 = client.post("/api/calendar/indian-festivals/sync?year=2026", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["added"] == 0

    nov = client.get("/api/calendar/month?year=2026&month=11", headers=headers).json()
    titles = [h["title"] for h in nov["holidays"]]
    assert "Bhai Duj" in titles
    assert "Chhat Puja (Pratihar Sashthi, Surya Sashthi)" in titles
