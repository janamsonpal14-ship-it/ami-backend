from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, create_sqlite_engine, ensure_additive_schema
from app.models import Client, Matter, User
from app.services.auth_service import hash_password
from app.services.seed_service import ensure_seed_data


@pytest.fixture()
def client_and_db(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_ecourts.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-secret-key-for-automated-qa-only-123456")

    import app.config as cfg
    import app.database as database
    import app.main as main_mod

    cfg.DB_PATH = db_file
    cfg.DATA_DIR = data_dir
    cfg.ensure_directories()

    test_engine = create_sqlite_engine(db_file)
    database.engine = test_engine
    database.SessionLocal = database.sessionmaker(bind=test_engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=test_engine)
    ensure_additive_schema(test_engine)

    with database.SessionLocal() as db:
        ensure_seed_data(db)

    with TestClient(main_mod.app) as c:
        resp = c.post("/api/auth/login", json={"login_id": "demo_advocate", "password": "DemoPassword@2026"})
        assert resp.status_code == 200, resp.text
        token = resp.json()["access_token"]
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c, database.SessionLocal


def test_matter_crud_create_update_delete(client_and_db):
    client, session_factory = client_and_db

    # 1. Create Client
    c_resp = client.post("/api/clients", json={"display_name": "Test Client Pvt Ltd"})
    assert c_resp.status_code == 200
    client_id = c_resp.json()["id"]

    # 2. Create Matter with CNR
    m_resp = client.post(
        "/api/matters",
        json={
            "title": "Hiten Shah vs Nine Globe",
            "court_forum": "City Civil Court",
            "case_type": "Short Causes Suit",
            "case_number": "4032/2024",
            "current_stage": "Fresh",
            "status": "Active",
            "cnr": "MHCC010081342024",
            "clients": [{"client_id": client_id, "role": "Plaintiff", "is_billing_client": True}],
        },
    )
    assert m_resp.status_code == 200
    m_data = m_resp.json()
    matter_id = m_data["id"]
    assert m_data["cnr"] == "MHCC010081342024"

    # 3. Update Matter
    u_resp = client.patch(
        f"/api/matters/{matter_id}",
        json={
            "title": "Hiten Shantilal Shah vs Nine Globe Builders",
            "current_stage": "Rejoinder Filed",
            "notes": "Updated during office review",
        },
    )
    assert u_resp.status_code == 200
    u_data = u_resp.json()
    assert u_data["title"] == "Hiten Shantilal Shah vs Nine Globe Builders"
    assert u_data["current_stage"] == "Rejoinder Filed"
    assert u_data["notes"] == "Updated during office review"

    # 4. Delete Matter
    d_resp = client.delete(f"/api/matters/{matter_id}")
    assert d_resp.status_code == 200
    assert d_resp.json()["status"] == "success"

    # Verify deleted
    g_resp = client.get(f"/api/matters/{matter_id}")
    assert g_resp.status_code == 404


def test_ecourts_sync_matter_and_caching(client_and_db):
    client, session_factory = client_and_db

    # Create a client and matter
    c_resp = client.post("/api/clients", json={"display_name": "Velji Shah"})
    client_id = c_resp.json()["id"]

    m_resp = client.post(
        "/api/matters",
        json={
            "title": "Velji Shah vs Nine Globe",
            "court_forum": "Old Court Name",
            "case_number": "4113/2024",
            "current_stage": "Unknown",
            "cnr": "MHCC010081552024",
            "clients": [{"client_id": client_id, "role": "Plaintiff", "is_billing_client": True}],
        },
    )
    matter_id = m_resp.json()["id"]

    # Mock eCourts API response for sync
    mock_case_payload = {
        "data": {
            "courtCaseData": {
                "cnr": "MHCC010081552024",
                "courtName": "City Civil Court, Mumbai",
                "caseNumber": "104113/2024",
                "registrationNumber": "104113/2024",
                "stageOfCase": "REJOINDER_FILED",
                "nextHearingDate": "2026-11-26",
                "orders": [],
            }
        }
    }

    mock_external_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_case_payload
    mock_external_client.get.return_value = mock_resp
    mock_external_client.__enter__.return_value = mock_external_client
    mock_external_client.__exit__.return_value = None

    with patch("app.services.ecourts_service._get_client", return_value=mock_external_client):
        # Trigger sync
        sync_resp = client.post(f"/api/ecourts/matters/{matter_id}/sync")
        assert sync_resp.status_code == 200
        s_data = sync_resp.json()
        assert s_data["status"] == "synced"
        assert s_data["court_forum"] == "City Civil Court, Mumbai"
        assert s_data["current_stage"] == "Rejoinder_Filed"
        assert s_data["current_next_date"] == "2026-11-26"

    # Verify eCourts API prevailed on the matter
    m_after_resp = client.get(f"/api/matters/{matter_id}")
    assert m_after_resp.status_code == 200
    m_after = m_after_resp.json()
    assert m_after["court_forum"] == "City Civil Court, Mumbai"
    assert m_after["current_stage"] == "Rejoinder_Filed"
    assert m_after["current_next_date"] == "2026-11-26"


def test_ecourts_search_and_court_structure_endpoints(client_and_db):
    client, session_factory = client_and_db

    mock_search_payload = {
        "data": {
            "results": [
                {
                    "cnr": "MHCC010081342024",
                    "caseType": "CS",
                    "registrationNumber": "104032/2024",
                    "courtCode": "MHCC01",
                    "petitioners": ["Hiten Shantilal Shah"],
                    "respondents": ["Nine Globe Builders"],
                }
            ],
            "totalHits": 1,
            "page": 1,
            "pageSize": 20,
        }
    }

    mock_external_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_search_payload
    mock_external_client.get.return_value = mock_resp
    mock_external_client.__enter__.return_value = mock_external_client
    mock_external_client.__exit__.return_value = None

    with patch("app.services.ecourts_service._get_client", return_value=mock_external_client):
        # First call fetches and caches
        res1 = client.get("/api/ecourts/search", params={"query": "Hiten Shah"})
        assert res1.status_code == 200
        assert res1.json()["_from_cache"] is False

        # Second identical call serves from cache (0 external API charge)
        res2 = client.get("/api/ecourts/search", params={"query": "Hiten Shah"})
        assert res2.status_code == 200
        assert res2.json()["_from_cache"] is True
