from __future__ import annotations

import io
import platform
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas


@pytest.fixture()
def client_and_dirs(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_phase4.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-phase4-secret-key-for-automated-qa-only-987654")

    import importlib
    import app.config as cfg
    import app.database as database
    import app.models as models
    import app.services.audit_service as audit_mod
    import app.services.law_service as law_mod
    import app.main as main_mod

    importlib.reload(cfg)
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(audit_mod)
    importlib.reload(law_mod)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        yield client, data_dir, law_mod


def _login_headers(client: TestClient, login_id: str = "demo_advocate", password: str = "DemoPassword@2026") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_india_code_ocr_pdf_bytes() -> bytes:
    """Create a realistic India Code OCR style Bare Act PDF (front matter, TOC, sections, Schedule/Order, footnotes)."""
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    def draw_page(lines: list[str]) -> None:
        c.setFont("Helvetica", 10.5)
        y = 795
        for line in lines:
            c.drawString(48, y, line)
            y -= 17
        c.showPage()

    # Page 1: Title + Arrangement of Sections (TOC)
    draw_page([
        "1",
        "The Sample Civil Code, 1908",
        "(ACT NO. 5 OF 1908)",
        "[As on the 10th January, 2026]",
        "ARRANGEMENT OF SECTIONS",
        "1. Short title and commencement.",
        "9. Courts to try all civil suits unless barred.",
        "ORDER XXXIX",
        "1. Cases in which temporary injunction may be granted.",
    ])

    # Page 2: Enacted Statutory Body + Order XXXIX + Footnotes
    draw_page([
        "2",
        "THE SAMPLE CIVIL CODE, 1908",
        "It is hereby enacted as follows:—",
        "PRELIMINARY",
        "1. Short title and commencement. —(1) This Act may be cited as the Sample Civil Code, 1908.",
        "(2) It extends to the whole of India.",
        "9. Courts to try all civil suits unless barred. —The Courts shall have jurisdic-",
        "tion to try all suits of a civil nature except suits expressly barred.",
        "THE FIRST SCHEDULE",
        "ORDER XXXIX",
        "TEMPORARY INJUNCTIONS",
        "1. Cases in which temporary injunction may be granted. —Where in any suit it is proved",
        "that any property in dispute is in danger of being wasted, damaged or alienated,",
        "(a) the Court may by order grant a temporary injunction to restrain such act.",
        "1. Subs. by Act 104 of 1976, s. 2 (w.e.f. 1-2-1977).",
    ])

    c.save()
    return buf.getvalue()


def test_bare_act_india_code_ocr_pdf_upload_search_original_pdf_and_permissions(client_and_dirs):
    client, data_dir, _ = client_and_dirs
    admin_headers = _login_headers(client, "demo_advocate", "DemoPassword@2026")

    create_assoc = client.post(
        "/api/users",
        json={
            "login_id": "jr_associate",
            "full_name": "Junior Associate",
            "password": "AssocPassword@2026",
            "role": "associate",
        },
        headers=admin_headers,
    )
    assert create_assoc.status_code == 200, create_assoc.text
    assoc_headers = _login_headers(client, "jr_associate", "AssocPassword@2026")

    # 1) Upload India Code style OCR PDF with blank title -> auto-detect metadata
    pdf_bytes = _make_india_code_ocr_pdf_bytes()
    up_pdf = client.post(
        "/api/bare-acts/upload",
        data={
            "title": "",
            "jurisdiction": "India",
        },
        files={"file": ("A190805.pdf", pdf_bytes, "application/pdf")},
        headers=admin_headers,
    )
    assert up_pdf.status_code == 201, up_pdf.text
    pdf_act = up_pdf.json()
    assert pdf_act["title"] == "The Sample Civil Code, 1908"
    assert pdf_act["year"] == 1908
    assert "10th January, 2026" in (pdf_act["version_date"] or "")
    assert pdf_act["has_file"] is True
    assert pdf_act["page_count"] == 2
    assert any(s["section_no"] == "Section 9" for s in pdf_act["sections"])
    assert any(s["section_no"] == "Order XXXIX Rule 1" for s in pdf_act["sections"])
    # Verify broken OCR hyphenation ("jurisdic-\ntion") & footnotes separated cleanly
    sec9 = next(s for s in pdf_act["sections"] if s["section_no"] == "Section 9")
    assert "jurisdiction" in sec9["body_text"]
    ord39 = next(s for s in pdf_act["sections"] if s["section_no"] == "Order XXXIX Rule 1")
    assert "Amendments / Footnotes" in ord39["body_text"]
    assert any((data_dir / "uploads" / "bare_acts").iterdir())

    # 2) Serve original uploaded PDF + verbatim page text + rendered page image
    raw_file = client.get(f"/api/bare-acts/{pdf_act['id']}/file", headers=assoc_headers)
    assert raw_file.status_code == 200
    assert raw_file.headers["content-type"].startswith("application/pdf")
    assert raw_file.content.startswith(b"%PDF")

    page_txt = client.get(f"/api/bare-acts/{pdf_act['id']}/page-text/2", headers=assoc_headers)
    assert page_txt.status_code == 200
    assert "ORDER XXXIX" in page_txt.json()["text"]

    if platform.system() == "Darwin":
        page_img = client.get(f"/api/bare-acts/{pdf_act['id']}/page/2", headers=assoc_headers)
        assert page_img.status_code == 200
        assert page_img.headers["content-type"] == "image/png"
        assert page_img.content.startswith(b"\x89PNG")

    # 3) Roman <-> Arabic search accuracy ("Order 39 Rule 1" finds "Order XXXIX Rule 1" at top)
    search = client.get("/api/bare-acts/search", params={"q": "Order 39 Rule 1 temporary injunction"}, headers=assoc_headers)
    assert search.status_code == 200, search.text
    hits = search.json()["hits"]
    assert len(hits) >= 1
    assert hits[0]["act_id"] == pdf_act["id"]
    assert hits[0]["section_no"] == "Order XXXIX Rule 1"

    # 4) Upload TXT Bare Act & verify associate cannot delete while admin can
    txt_content = (
        "Section 11. Competency to contract\n"
        "Every person is competent to contract who is of the age of majority according to the law.\n"
    ).encode("utf-8")
    up_txt = client.post(
        "/api/bare-acts/upload",
        data={"title": "Indian Contract Act Excerpt", "short_title": "ICA", "year": "1872"},
        files={"file": ("contract_act.txt", txt_content, "text/plain")},
        headers=admin_headers,
    )
    assert up_txt.status_code == 201, up_txt.text
    txt_act = up_txt.json()

    assert client.delete(f"/api/bare-acts/{txt_act['id']}", headers=assoc_headers).status_code == 403
    assert client.delete(f"/api/bare-acts/{txt_act['id']}", headers=admin_headers).status_code == 200
    assert client.get(f"/api/bare-acts/{txt_act['id']}", headers=admin_headers).status_code == 404


def test_legal_search_strictly_local_bare_acts_only_no_external_ai(client_and_dirs):
    client, _, law_mod = client_and_dirs
    admin_headers = _login_headers(client, "demo_advocate", "DemoPassword@2026")

    act_resp = client.post(
        "/api/bare-acts",
        json={
            "title": "Code of Civil Procedure Excerpt",
            "short_title": "CPC Test",
            "year": 1908,
            "tags": "injunction,cpc",
            "full_text": (
                "Section 9. Courts to try all civil suits unless barred\n"
                "The Courts shall have jurisdiction to try all suits of a civil nature excepting suits of which cognizance is barred.\n"
                "Order 39 Rule 1. Cases in which temporary injunction may be granted\n"
                "Where in any suit it is proved by affidavit that any property in dispute is in danger of being wasted or alienated."
            ),
        },
        headers=admin_headers,
    )
    assert act_resp.status_code == 201, act_resp.text
    act_id = act_resp.json()["id"]

    # Default status confirms strictly local Bare Acts search & zero external AI
    st = client.get("/api/research/status", headers=admin_headers)
    assert st.status_code == 200
    assert st.json()["enabled"] is False
    assert st.json()["ai_enabled"] is False
    assert st.json()["provider"] == "local_bare_acts"

    # Third-party AI enablement & external AI queries are strictly disabled (403)
    blocked_cfg = client.patch(
        "/api/research/config",
        json={"ai_enabled": True, "provider": "openai_compatible"},
        headers=admin_headers,
    )
    assert blocked_cfg.status_code == 403

    blocked_ai = client.post(
        "/api/research/ask",
        json={"question": "Temporary injunction test", "use_external_ai": True},
        headers=admin_headers,
    )
    assert blocked_ai.status_code == 403

    # Strict local Bare Acts Legal Search returns formatted statutory excerpts & citations
    local_resp = client.post(
        "/api/research/ask",
        json={"question": "temporary injunction property danger", "act_ids": [act_id], "use_external_ai": False},
        headers=admin_headers,
    )
    assert local_resp.status_code == 200, local_resp.text
    data = local_resp.json()
    assert data["mode"] == "local_bare_acts"
    assert len(data["citations"]) >= 1
    assert "wasted or alienated" in data["answer"]
    assert "VERIFICATION NOTICE" in data["verification_notice"]

    hist = client.get("/api/research/history", headers=admin_headers)
    assert hist.status_code == 200
    assert len(hist.json()) >= 1

    # Strict architectural isolation assertion on law_service.py source
    law_source = Path(law_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("Client", "Matter", "Hearing", "FeeMemo", "CalendarEvent", "Task"):
        assert forbidden not in law_source, f"Isolation violation: {forbidden} referenced in law_service.py"


def test_three_point_statutory_reference_and_caching(client_and_dirs):
    """
    Verifies the Three-Point statutory reference format:
    1. Statutory Provision & Original PDF Reference (from local DB)
    2. Interpretation (AI / SQLite Cache)
    3. Characteristics of the Provision (AI / SQLite Cache)
    Also verifies SQLite caching (second call costs ₹0.00) and Pay-As-You-Go accounting.
    """
    client, _, _ = client_and_dirs
    admin_headers = _login_headers(client, "demo_advocate", "DemoPassword@2026")

    # Upload test act
    act_resp = client.post(
        "/api/bare-acts",
        json={
            "title": "Commercial Arbitration Act",
            "short_title": "Arbitration Act",
            "year": 1996,
            "tags": "arbitration,interim",
            "full_text": (
                "Section 9. Interim measures, etc., by Court\n"
                "A party may, before or during arbitral proceedings or at any time after the making of the arbitral award "
                "but before it is enforced in accordance with section 36, apply to a court for an interim measure of protection."
            ),
        },
        headers=admin_headers,
    )
    assert act_resp.status_code == 201
    act_data = act_resp.json()
    act_id = act_data["id"]
    assert len(act_data["sections"]) >= 1
    sec_id = act_data["sections"][0]["id"]

    # 1. Fetch Three-Point Analysis (First Call - generated or fallback)
    resp1 = client.get(
        f"/api/bare-acts/{act_id}/sections/{sec_id}/three-point",
        headers=admin_headers,
    )
    assert resp1.status_code == 200, resp1.text
    data1 = resp1.json()

    # Point 1 assertions: Exact local DB statutory text & PDF metadata
    assert data1["act_id"] == act_id
    assert data1["section_no"] == "Section 9"
    assert "Interim measures" in data1["heading"]
    assert "interim measure of protection" in data1["statutory_text"]
    assert data1["page_number"] == 1
    assert data1["is_cached"] is False

    # Point 2 assertions: Interpretation present and legally grounded
    assert len(data1["interpretation"]) > 20
    assert "Section 9" in data1["interpretation"] or "Arbitration" in data1["interpretation"]

    # Point 3 assertions: Characteristics list present
    assert isinstance(data1["characteristics"], list)
    assert len(data1["characteristics"]) >= 3

    # 2. Fetch Three-Point Analysis (Second Call - MUST be SQLite cache hit at ₹0.00)
    resp2 = client.get(
        f"/api/bare-acts/{act_id}/sections/{sec_id}/three-point",
        headers=admin_headers,
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["is_cached"] is True
    assert data2["ai_status"] == "cached"
    assert data2["cost_inr"] == 0.0
    assert data2["interpretation"] == data1["interpretation"]
    assert data2["characteristics"] == data1["characteristics"]

    # 3. Check Pay-As-You-Go AI Usage Ledger
    usage_resp = client.get("/api/law/ai-usage", headers=admin_headers)
    assert usage_resp.status_code == 200
    usage = usage_resp.json()
    assert usage["total_queries"] >= 1
    assert len(usage["recent_logs"]) >= 1
    assert usage["recent_logs"][0]["module"] == "law"

