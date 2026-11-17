from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client_and_db(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_amico.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-secret-key-for-automated-qa-only-123456")

    # Reload config/database/main with isolated test DB path
    import importlib
    import app.config as cfg
    import app.database as database
    import app.models as models
    import app.main as main_mod

    importlib.reload(cfg)
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        yield client, db_file


def _auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"login_id": "demo_advocate", "password": "DemoPassword@2026"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_jwt_authentication_required_and_valid_login(client_and_db):
    client, _ = client_and_db
    # Unauthenticated request must fail
    unauth = client.get("/api/home/pinboard")
    assert unauth.status_code == 401

    # Invalid token must fail
    bad = client.get("/api/home/pinboard", headers={"Authorization": "Bearer invalid.jwt.token"})
    assert bad.status_code == 401

    # Wrong password must fail
    wrong = client.post("/api/auth/login", json={"login_id": "demo_advocate", "password": "wrong"})
    assert wrong.status_code == 401

    # Valid demo login succeeds and accesses protected endpoints
    headers = _auth_headers(client)
    me = client.get("/api/auth/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["login_id"] == "demo_advocate"
    assert me.json()["is_demo"] is True


def test_create_client_matter_and_duplicate_suggestion(client_and_db):
    client, _ = client_and_db
    headers = _auth_headers(client)

    # Create fictional client
    c_resp = client.post(
        "/api/clients",
        headers=headers,
        json={"display_name": "Sunrise Pharma Pvt. Ltd. (Test)", "contact_info": "Mumbai"},
    )
    assert c_resp.status_code == 200
    cid = c_resp.json()["id"]

    # Fuzzy suggestion finds existing client
    sug = client.get("/api/clients?suggest=Sunrise Pharma", headers=headers)
    assert sug.status_code == 200
    assert any(c["id"] == cid for c in sug.json())

    # Create matter
    m_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Sunrise Pharma vs. Delta Distributors",
            "court_forum": "Bombay High Court",
            "case_type": "Commercial Suit",
            "case_number": "COMS/999/2026",
            "current_stage": "Arguments",
            "clients": [{"client_id": cid, "role": "Plaintiff", "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200
    mid = m_resp.json()["id"]

    # Duplicate suggestion returns 409 unless allow_duplicate=True
    dup_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Sunrise Pharma vs. Delta Distributors",
            "court_forum": "Bombay High Court",
            "case_number": "COMS 999 2026",
            "clients": [{"client_id": cid}],
        },
    )
    assert dup_resp.status_code == 409

    # Client-wise report includes the new matter
    cw = client.get("/api/reports/client-wise", headers=headers)
    assert cw.status_code == 200
    groups = cw.json()["clients"]
    target_group = next(g for g in groups if g["client_id"] == cid)
    assert any(m["matter_id"] == mid for m in target_group["matters"])


def test_atomic_hearing_update_calendar_no_duplicates_and_not_reached(client_and_db):
    client, _ = client_and_db
    headers = _auth_headers(client)

    # Create client & matter
    cid = client.post("/api/clients", headers=headers, json={"display_name": "Test Corp"}).json()["id"]
    mid = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Test Corp vs. Beta Ltd.",
            "court_forum": "Bombay High Court",
            "case_number": "WP/501/2026",
            "clients": [{"client_id": cid}],
        },
    ).json()["id"]

    # Add a new advocate
    adv_id = client.post("/api/advocates", headers=headers, json={"full_name": "Adv. New Counsel"}).json()["id"]

    # Completed hearing requires business_conducted and advocates
    bad_h = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={"hearing_date": "2026-11-12", "status": "Completed", "business_conducted": "", "advocate_ids": []},
    )
    assert bad_h.status_code == 422

    # Save valid hearing with next_date on a BHC holiday (2026-11-24 Guru Nanak Jayanti) -> warns, still saves!
    h1 = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={
            "hearing_date": "2026-11-12",
            "status": "Completed",
            "business_conducted": "Arguments partly heard.",
            "stage_at_hearing": "Final Hearing",
            "advocate_ids": [adv_id],
            "next_date": "2026-11-24",
            "next_date_status": "Fixed",
            "next_purpose": "Further arguments",
            "is_billable_candidate": True,
        },
    )
    assert h1.status_code == 200
    h1_data = h1.json()
    assert h1_data["holiday_warning"] is not None

    # Edit same hearing to move next_date to 2026-11-26 -> updates existing calendar event, NO duplicate!
    h1_edit = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={
            "hearing_id": h1_data["id"],
            "hearing_date": "2026-11-12",
            "status": "Completed",
            "business_conducted": "Arguments partly heard (date corrected).",
            "stage_at_hearing": "Final Hearing",
            "advocate_ids": [adv_id],
            "next_date": "2026-11-26",
            "next_date_status": "Fixed",
            "next_purpose": "Further arguments",
            "correction_reason": "Corrected to working day",
        },
    )
    assert h1_edit.status_code == 200

    # Verify Nov 24 has no event for this matter, and Nov 26 has exactly 1
    ev_24 = client.get("/api/calendar/events?start_date=2026-11-24&end_date=2026-11-24", headers=headers).json()
    assert not any(e["matter_id"] == mid for e in ev_24)
    ev_26 = client.get("/api/calendar/events?start_date=2026-11-26&end_date=2026-11-26", headers=headers).json()
    assert sum(1 for e in ev_26 if e["matter_id"] == mid) == 1

    # 1-Click Not Reached on 2026-11-26 preserves purpose & creates second independent monthly row
    nr = client.post(
        f"/api/matters/{mid}/hearings/not-reached",
        headers=headers,
        json={"hearing_date": "2026-11-26", "next_date": "2026-12-03", "advocate_ids": [adv_id]},
    )
    assert nr.status_code == 200
    assert nr.json()["status"] == "Not Taken Up"
    assert nr.json()["next_purpose"] == "Further arguments"
    assert nr.json()["is_billable_candidate"] is False

    # Monthly list has BOTH appearances independently
    monthly = client.get("/api/reports/monthly?year=2026&month=11", headers=headers).json()["rows"]
    matter_rows = [r for r in monthly if r["matter_id"] == mid]
    assert len(matter_rows) == 2

    # Next hearing sets 2026-12-10, then is corrected to Disposed -> future calendar event cancelled
    h_dec = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={
            "hearing_date": "2026-12-03",
            "status": "Completed",
            "business_conducted": "Consent terms tendered.",
            "stage_at_hearing": "Consent Terms",
            "advocate_ids": [adv_id],
            "next_date": "2026-12-10",
            "next_date_status": "Fixed",
            "next_purpose": "Order",
        },
    )
    assert h_dec.status_code == 200
    ev_10 = client.get("/api/calendar/events?start_date=2026-12-10&end_date=2026-12-10", headers=headers).json()
    assert sum(1 for e in ev_10 if e["matter_id"] == mid) == 1

    disp = client.post(
        f"/api/matters/{mid}/hearings",
        headers=headers,
        json={
            "hearing_id": h_dec.json()["id"],
            "hearing_date": "2026-12-03",
            "status": "Completed",
            "business_conducted": "Suit disposed of in terms of consent terms.",
            "stage_at_hearing": "Disposed",
            "advocate_ids": [adv_id],
            "next_date_status": "Disposed",
            "correction_reason": "Disposed same day",
        },
    )
    assert disp.status_code == 200
    ev_dec = client.get("/api/calendar/events?start_date=2026-12-04&end_date=2026-12-31", headers=headers).json()
    assert not any(e["matter_id"] == mid for e in ev_dec)

    # Test deleting hearing record
    del_res = client.delete(f"/api/hearings/{h1_data['id']}", headers=headers)
    assert del_res.status_code == 200
    assert del_res.json()["success"] is True
    h_list = client.get(f"/api/matters/{mid}/hearings", headers=headers).json()
    assert not any(h["id"] == h1_data["id"] for h in h_list)


def test_fee_memo_indian_words_adjustments_and_partial_payments(client_and_db):
    client, _ = client_and_db
    headers = _auth_headers(client)

    matters = client.get("/api/matters", headers=headers).json()
    m_ids = [matters[0]["id"], matters[1]["id"]]

    # Proforma example: 14 x 5,000 + 4 x 25,000 = 1,70,000; adjustment -50,000 => Net 1,20,000
    memo_resp = client.post(
        "/api/memos",
        headers=headers,
        json={
            "bill_date": "2026-11-12",
            "due_date": "2026-11-26",
            "addressee_name": "Vardhman Textiles Pvt. Ltd. (Test)",
            "matter_ids": m_ids,
            "lines": [
                {"category": "Appearances", "description": "Court appearances", "quantity": 14, "unit_rate": 5000},
                {"category": "Drafting", "description": "Drafting pleadings", "quantity": 4, "unit_rate": 25000},
            ],
            "adjustments": [{"reason": "Agreed credit", "signed_amount": -50000}],
        },
    )
    assert memo_resp.status_code == 200
    memo = memo_resp.json()
    assert memo["gross_total"] == 170000.0
    assert memo["adjustment_total"] == -50000.0
    assert memo["net_total"] == 120000.0
    assert memo["net_total_formatted"] == "₹1,20,000"
    assert memo["amount_in_words"] == "Rupees One Lakh Twenty Thousand Only"

    memo_id = memo["id"]
    # Cannot pay a Draft memo before issuing
    early_pay = client.post(
        f"/api/memos/{memo_id}/payments",
        headers=headers,
        json={"payment_date": "2026-11-12", "amount": 20000, "method": "UPI"},
    )
    assert early_pay.status_code == 409

    # Issue memo -> locks financial lines
    issued = client.post(f"/api/memos/{memo_id}/issue", headers=headers)
    assert issued.status_code == 200
    assert issued.json()["status"] == "Issued"

    locked_edit = client.patch(f"/api/memos/{memo_id}", headers=headers, json={"notes": "Tamper"})
    assert locked_edit.status_code == 409

    # Partial payment 50,000 -> balance 70,000, Partially Paid
    p1 = client.post(
        f"/api/memos/{memo_id}/payments",
        headers=headers,
        json={"payment_date": "2026-11-12", "amount": 50000, "method": "NEFT/RTGS"},
    )
    assert p1.status_code == 200
    assert p1.json()["status"] == "Partially Paid"
    assert p1.json()["balance_due"] == 70000.0
    assert p1.json()["gross_total"] == 170000.0

    # Overpayment 80,000 rejected
    over = client.post(
        f"/api/memos/{memo_id}/payments",
        headers=headers,
        json={"payment_date": "2026-11-12", "amount": 80000, "method": "UPI"},
    )
    assert over.status_code == 422

    # Remaining 70,000 settles memo -> Paid
    p2 = client.post(
        f"/api/memos/{memo_id}/payments",
        headers=headers,
        json={"payment_date": "2026-11-12", "amount": 70000, "method": "NEFT/RTGS"},
    )
    assert p2.status_code == 200
    assert p2.json()["status"] == "Paid"
    assert p2.json()["balance_due"] == 0.0

    # Quick-status toggle buttons: Pending -> Partially Paid -> Paid
    q_pending = client.post(
        f"/api/memos/{memo_id}/quick-status",
        headers=headers,
        json={"status": "Pending"},
    )
    assert q_pending.status_code == 200
    assert q_pending.json()["status"] == "Issued"
    assert q_pending.json()["balance_due"] == 120000.0

    q_part = client.post(
        f"/api/memos/{memo_id}/quick-status",
        headers=headers,
        json={"status": "Partially Paid", "amount": 40000, "method": "UPI"},
    )
    assert q_part.status_code == 200
    assert q_part.json()["status"] == "Partially Paid"
    assert q_part.json()["balance_due"] == 80000.0

    q_paid = client.post(
        f"/api/memos/{memo_id}/quick-status",
        headers=headers,
        json={"status": "Paid", "method": "NEFT/RTGS"},
    )
    assert q_paid.status_code == 200
    assert q_paid.json()["status"] == "Paid"
    assert q_paid.json()["balance_due"] == 0.0


def test_clash_detection_court_pack_and_global_search(client_and_db):
    client, _ = client_and_db
    headers = _auth_headers(client)

    # Pinboard & Court Pack
    pin = client.get("/api/home/pinboard", headers=headers)
    assert pin.status_code == 200
    assert pin.json()["counts"]["clashes"] >= 1

    pack = client.get("/api/home/court-pack", headers=headers)
    assert pack.status_code == 200
    assert len(pack.json()["forums"]) >= 1

    # Global search Cmd+K
    srch = client.get("/api/search?q=Vardhman", headers=headers)
    assert srch.status_code == 200
    assert len(srch.json()["clients"]) >= 1
    assert len(srch.json()["matters"]) >= 1


def test_persistence_across_restart(client_and_db):
    client, db_file = client_and_db
    headers = _auth_headers(client)
    created = client.post(
        "/api/clients",
        headers=headers,
        json={"display_name": "Persistent Client XYZ (Test)"},
    )
    assert created.status_code == 200
    cid = created.json()["id"]

    # Simulate server restart by opening a fresh TestClient against same SQLite DB
    import app.main as main_mod
    with TestClient(main_mod.app) as new_client:
        new_headers = _auth_headers(new_client)
        clients = new_client.get("/api/clients", headers=new_headers).json()
        assert any(c["id"] == cid and c["display_name"] == "Persistent Client XYZ (Test)" for c in clients)
        assert db_file.exists()

