"""Tests for AMI Automated Legal Library & Statutory Intelligence System (Pilot & Versioning)."""

from __future__ import annotations

import importlib
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BareAct, BareActSection, BareActVersion, LegislativeAmendment


@pytest.fixture()
def client_and_db(tmp_path: Path, monkeypatch):
    db_file = tmp_path / "test_statutory.db"
    data_dir = tmp_path / "data"
    monkeypatch.setenv("AMICO_DATA_DIR", str(data_dir))
    monkeypatch.setenv("AMICO_DB_PATH", str(db_file))
    monkeypatch.setenv("AMICO_JWT_SECRET", "test-secret-key-for-automated-qa-only-123456")

    import app.config as cfg
    import app.database as database
    import app.models as models
    import app.main as main_mod

    importlib.reload(cfg)
    importlib.reload(database)
    importlib.reload(models)
    importlib.reload(main_mod)

    with TestClient(main_mod.app) as client:
        yield client, database.SessionLocal


def _auth_headers(client: TestClient) -> dict[str, str]:
    resp = client.post(
        "/api/auth/login",
        json={"login_id": "demo_advocate", "password": "DemoPassword@2026"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_demo_bare_acts_purged_and_pilot_acts_present(client_and_db):
    client, session_factory = client_and_db
    headers = _auth_headers(client)

    # Ingest a clean pilot act
    with session_factory() as db:
        demo_acts = db.scalars(select(BareAct).where(BareAct.is_demo == True)).all()
        assert len(demo_acts) == 0, "No demo Bare Acts should remain in database."

        # Add pilot act record
        pilot_act = BareAct(
            title="The Code of Civil Procedure, 1908",
            short_title="CPC, 1908",
            act_no="Act No. 5 of 1908",
            year=1908,
            jurisdiction="Central",
            category_slug="civil_procedure_and_civil_laws",
            commencement_date=date(1909, 1, 1),
            page_count=347,
            section_count=975,
            is_demo=False,
            sha256_hash="8849fc32fdca764fb0280ebdb1206c49a6fe4022",
        )
        db.add(pilot_act)
        db.commit()

    # Verify via API
    resp = client.get("/api/bare-acts", headers=headers)
    assert resp.status_code == 200
    acts = resp.json()
    assert len(acts) >= 1
    assert any(a["title"] == "The Code of Civil Procedure, 1908" for a in acts)
    assert all(a["is_demo"] is False for a in acts)


def test_statutory_version_control_and_applicable_law_on_date(client_and_db):
    client, session_factory = client_and_db
    headers = _auth_headers(client)

    with session_factory() as db:
        test_act = BareAct(
            title="The Arbitration and Conciliation Act, 1996",
            short_title="Arbitration Act, 1996",
            act_no="Act No. 26 of 1996",
            year=1996,
            jurisdiction="Central",
            category_slug="arbitration_and_conciliation",
            commencement_date=date(1996, 8, 22),
            page_count=53,
            section_count=110,
            is_demo=False,
            sha256_hash="6fb32e5ffe2581740345d948c1e362bfbb5018ca7958932cde8ca92436485b6c",
        )
        db.add(test_act)
        db.flush()

        db.add(
            BareActSection(
                act_id=test_act.id,
                section_no="Section 29A",
                heading="Time limit for arbitral award",
                page_number=19,
                body_text="An award in matters other than international commercial arbitration shall be made within twelve months.",
            )
        )

        v_old = BareActVersion(
            act_id=test_act.id,
            version_tag="pre_2015",
            version_title="Pre-2015 Version",
            effective_from=date(1996, 8, 22),
            effective_to=date(2015, 10, 22),
            is_current=False,
            notes="Prior to Act 3 of 2016",
        )
        v_current = BareActVersion(
            act_id=test_act.id,
            version_tag="current",
            version_title="Current Version",
            effective_from=date(2015, 10, 23),
            effective_to=None,
            is_current=True,
            notes="Includes 2015 & 2019 amendments",
        )
        db.add_all([v_old, v_current])

        am1 = LegislativeAmendment(
            act_id=test_act.id,
            amending_act_title="Arbitration and Conciliation (Amendment) Act, 2015",
            amending_act_no="Act No. 3 of 2016",
            commencement_date=date(2015, 10, 23),
            sections_affected="Section 29A, Section 34",
            status="incorporated",
        )
        am2 = LegislativeAmendment(
            act_id=test_act.id,
            amending_act_title="Arbitration and Conciliation (Amendment) Act, 2019",
            amending_act_no="Act No. 33 of 2019",
            commencement_date=date(2019, 8, 9),
            sections_affected="Section 29A",
            status="incorporated",
        )
        db.add_all([am1, am2])
        db.commit()
        act_id = test_act.id

    # 1. Query past date prior to 2015 amendment
    r_past = client.get(
        f"/api/bare-acts/{act_id}/applicable-law",
        params={"as_of_date": "2014-05-01", "section_no": "29A"},
        headers=headers,
    )
    assert r_past.status_code == 200
    d_past = r_past.json()
    assert d_past["applicable_version"]["version_tag"] == "pre_2015"
    assert d_past["amendments_in_effect_count"] == 0
    assert d_past["subsequent_amendments_count"] == 2
    assert d_past["matching_section"]["heading"] == "Time limit for arbitral award"

    # 2. Query mid date between 2015 and 2019 amendments
    r_mid = client.get(
        f"/api/bare-acts/{act_id}/applicable-law",
        params={"as_of_date": "2018-01-01", "section_no": "29A"},
        headers=headers,
    )
    assert r_mid.status_code == 200
    d_mid = r_mid.json()
    assert d_mid["applicable_version"]["version_tag"] == "current"
    assert d_mid["amendments_in_effect_count"] == 1
    assert d_mid["subsequent_amendments_count"] == 1

    # 3. Test versions endpoint
    r_ver = client.get(f"/api/bare-acts/{act_id}/versions", headers=headers)
    assert r_ver.status_code == 200
    assert len(r_ver.json()) == 2

    # 4. Test amendments endpoint
    r_am = client.get(f"/api/bare-acts/{act_id}/amendments", headers=headers)
    assert r_am.status_code == 200
    assert len(r_am.json()) == 2


def test_mobile_db_download_endpoint(client_and_db):
    client, _ = client_and_db
    headers = _auth_headers(client)

    resp = client.get("/api/system/download-mobile-db", headers=headers)
    assert resp.status_code == 200
    assert resp.headers.get("content-type") == "application/x-sqlite3"
    assert "amico.db" in resp.headers.get("content-disposition", "")
    assert resp.content.startswith(b"SQLite format 3")
