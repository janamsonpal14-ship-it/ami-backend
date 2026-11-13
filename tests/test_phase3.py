from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


@pytest.fixture()
def client_and_dirs(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_phase3.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-phase3-secret-key-for-automated-qa-only-654321")

    import importlib
    import app.config as cfg
    import app.database as database
    import app.models as models
    import app.services.audit_service as audit_mod
    import app.services.backup_service as backup_mod
    import app.main as main_mod

    importlib.reload(cfg)
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(audit_mod)
    importlib.reload(backup_mod)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        yield client, data_dir


def _login_headers(client: TestClient, login_id: str = "demo_advocate", password: str = "DemoPassword@2026") -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"login_id": login_id, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_backup_zip_export_integrity_tamper_guard_and_restore(client_and_dirs):
    client, data_dir = client_and_dirs
    headers = _login_headers(client)

    # Health / integrity check
    integrity = client.get("/api/system/integrity", headers=headers)
    assert integrity.status_code == 200
    assert integrity.json()["ok"] is True

    # Add unique non-demo client/matter + letterhead upload
    img = Image.new("RGB", (200, 50), color=(27, 42, 74))
    img_buf = io.BytesIO()
    img.save(img_buf, format="PNG")
    lh_resp = client.post(
        "/api/letterheads",
        headers=headers,
        files={"file": ("backup_lh.png", img_buf.getvalue(), "image/png")},
        data={"name": "Backup Verification Letterhead", "is_default": "true"},
    )
    assert lh_resp.status_code == 200, lh_resp.text
    lh_id = lh_resp.json()["id"]

    c_resp = client.post("/api/clients", headers=headers, json={"display_name": "Vortex Marine Pvt. Ltd. (Test)"})
    cid = c_resp.json()["id"]
    m_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Vortex Marine vs Customs",
            "case_number": "WP/777/2026",
            "court_forum": "Bombay High Court",
            "clients": [{"client_id": cid, "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200

    # Export full backup bundle
    exp = client.post("/api/backup/export", headers=headers)
    assert exp.status_code == 200
    backup_bytes = exp.content

    with zipfile.ZipFile(io.BytesIO(backup_bytes)) as zf:
        names = set(zf.namelist())
        assert "manifest.json" in names
        assert "amico.db" in names
        assert any(n.startswith("uploads/letterheads/") for n in names)
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["integrity_check"] == "ok"
        assert "amico.db" in manifest["files"]

    # Reject zip-slip traversal
    bad_zip_buf = io.BytesIO()
    with zipfile.ZipFile(bad_zip_buf, "w") as zf:
        zf.writestr("../escape.txt", b"evil")
    bad_resp = client.post(
        "/api/backup/restore",
        headers=headers,
        files={"file": ("evil.zip", bad_zip_buf.getvalue(), "application/zip")},
    )
    assert bad_resp.status_code == 422

    # Reject tampered checksum inside backup
    tampered_buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(backup_bytes)) as src_zf, zipfile.ZipFile(tampered_buf, "w") as dst_zf:
        for item in src_zf.infolist():
            content = src_zf.read(item.filename)
            if item.filename == "amico.db":
                content = b"corrupted-bytes" + content[15:]
            dst_zf.writestr(item.filename, content)
    tamper_resp = client.post(
        "/api/backup/restore",
        headers=headers,
        files={"file": ("tampered.zip", tampered_buf.getvalue(), "application/zip")},
    )
    assert tamper_resp.status_code == 422

    # Wipe / reset to prove restore brings back Vortex Marine + letterhead
    reset_resp = client.post("/api/demo/reset", headers=headers)
    assert reset_resp.status_code == 200
    matters_after_reset = client.get("/api/matters?q=Vortex", headers=headers).json()
    assert len(matters_after_reset) == 0

    # Restore valid backup
    restore_resp = client.post(
        "/api/backup/restore",
        headers=headers,
        files={"file": ("valid_backup.zip", backup_bytes, "application/zip")},
    )
    assert restore_resp.status_code == 200, restore_resp.text
    assert restore_resp.json()["pre_restore_snapshot"] is not None

    restored_matters = client.get("/api/matters?q=Vortex", headers=headers).json()
    assert len(restored_matters) == 1
    assert restored_matters[0]["case_number"] == "WP/777/2026"

    restored_lh_img = client.get(f"/api/letterheads/{lh_id}/image", headers=headers)
    assert restored_lh_img.status_code == 200
    assert restored_lh_img.content[:4] == b"\x89PNG"

    # Also test snapshot restore endpoint back to the pre-restore snapshot (where Vortex wasn't present)
    pre_snap_name = restore_resp.json()["pre_restore_snapshot"]
    snap_restore = client.post(f"/api/snapshots/{pre_snap_name}/restore", headers=headers)
    assert snap_restore.status_code == 200, snap_restore.text
    assert len(client.get("/api/matters?q=Vortex", headers=headers).json()) == 0
    assert len(list((data_dir / "snapshots").glob("auto_*.db"))) >= 2


def test_rbac_password_change_security_readiness_start_fresh_and_audit_logs(client_and_dirs):
    client, _ = client_and_dirs
    admin_headers = _login_headers(client)

    # Admin creates an associate account
    u_resp = client.post(
        "/api/users",
        headers=admin_headers,
        json={
            "login_id": "assoc_meera",
            "full_name": "Adv. Meera Associate",
            "role": "associate",
            "password": "AssocPass@2026",
        },
    )
    assert u_resp.status_code == 200, u_resp.text
    assoc_headers = _login_headers(client, "assoc_meera", "AssocPass@2026")

    # Associate can access regular case endpoints
    assert client.get("/api/matters", headers=assoc_headers).status_code == 200

    # Associate MUST get 403 on admin-only endpoints
    assert client.get("/api/users", headers=assoc_headers).status_code == 403
    assert client.get("/api/audit-logs", headers=assoc_headers).status_code == 403
    assert client.post("/api/backup/export", headers=assoc_headers).status_code == 403
    assert client.post("/api/demo/reset", headers=assoc_headers).status_code == 403

    # Password change works for associate
    cp = client.post(
        "/api/auth/change-password",
        headers=assoc_headers,
        json={"current_password": "AssocPass@2026", "new_password": "NewAssocPass@2026"},
    )
    assert cp.status_code == 200
    assert client.post("/api/auth/login", json={"login_id": "assoc_meera", "password": "AssocPass@2026"}).status_code == 401
    assert client.post("/api/auth/login", json={"login_id": "assoc_meera", "password": "NewAssocPass@2026"}).status_code == 200

    # Security readiness flags demo defaults initially
    sec1 = client.get("/api/system/security-readiness", headers=admin_headers).json()
    assert sec1["production_ready"] is False
    assert sec1["warnings_count"] >= 2

    # Start Fresh requires exact confirmation text
    bad_fresh = client.post(
        "/api/system/start-fresh",
        headers=admin_headers,
        json={"confirmation_text": "clear"},
    )
    assert bad_fresh.status_code == 422

    # Start Fresh with new private admin keeps BHC holidays & clears demo matters
    fresh = client.post(
        "/api/system/start-fresh",
        headers=admin_headers,
        json={
            "confirmation_text": "START FRESH",
            "keep_holidays_and_letterheads": True,
            "new_admin_login_id": "chambers_chief",
            "new_admin_full_name": "Adv. Chambers Chief",
            "new_admin_password": "PrivateChambers@2026",
        },
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["created_admin"] == "chambers_chief"

    # Old demo admin deactivated, new chief logs in cleanly
    assert client.post("/api/auth/login", json={"login_id": "demo_advocate", "password": "DemoPassword@2026"}).status_code == 401
    chief_headers = _login_headers(client, "chambers_chief", "PrivateChambers@2026")

    assert len(client.get("/api/matters", headers=chief_headers).json()) == 0
    assert len(client.get("/api/calendar/holidays?year=2026", headers=chief_headers).json()) > 0

    sec2 = client.get("/api/system/security-readiness", headers=chief_headers).json()
    assert sec2["production_ready"] is True, sec2
    assert sec2["warnings_count"] == 0

    # Audit log captures privileged actions
    logs = client.get("/api/audit-logs?limit=50", headers=chief_headers).json()
    actions = {item["action"] for item in logs}
    assert "clear_demo_data" in actions
    assert "change_password" in actions
    assert "create" in actions


def test_conflict_check_finds_existing_client_and_adverse_party(client_and_dirs):
    client, _ = client_and_dirs
    headers = _login_headers(client)

    cid = client.post("/api/clients", headers=headers, json={"display_name": "Saraswati Builders Ltd. (Test)"}).json()["id"]
    m_resp = client.post(
        "/api/matters",
        headers=headers,
        json={
            "title": "Saraswati Builders Ltd. vs Zenith Infra",
            "case_number": "COMS/112/2026",
            "court_forum": "Bombay High Court",
            "clients": [{"client_id": cid, "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200

    res_client = client.get("/api/matters/conflict-check?q=Saraswati", headers=headers)
    assert res_client.status_code == 200
    assert res_client.json()["has_potential_conflict"] is True
    assert len(res_client.json()["client_matches"]) >= 1

    res_adverse = client.get("/api/matters/conflict-check?q=Zenith Infra", headers=headers)
    assert res_adverse.status_code == 200
    assert res_adverse.json()["has_potential_conflict"] is True
    assert len(res_adverse.json()["matter_matches"]) >= 1

    res_clean = client.get("/api/matters/conflict-check?q=CompletelyUnrelatedParty", headers=headers)
    assert res_clean.status_code == 200
    assert res_clean.json()["has_potential_conflict"] is False

