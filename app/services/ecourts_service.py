from __future__ import annotations

import hashlib
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import ECOURTS_API_BASE, ECOURTS_API_KEY, ECOURTS_CACHE_DIR, IST
from app.models import (
    CalendarEvent,
    ECourtsCaseCache,
    ECourtsOrderCache,
    ECourtsQueryCache,
    Hearing,
    Matter,
    ist_now,
)
from app.utils.indian_format import ist_today
from app.services.audit_service import record_audit

logger = logging.getLogger(__name__)


def _get_client(timeout: float = 45.0) -> httpx.Client:
    return httpx.Client(
        base_url=ECOURTS_API_BASE,
        headers={
            "Authorization": f"Bearer {ECOURTS_API_KEY}",
            "Accept": "application/json",
        },
        timeout=timeout,
    )


def _cache_key(prefix: str, params: dict[str, Any]) -> str:
    serialized = json.dumps(params, sort_keys=True, default=str)
    h = hashlib.sha256(serialized.encode("utf-8")).hexdigest()[:24]
    return f"{prefix}_{h}"


def _get_query_cache(db: Session, cache_key: str) -> dict[str, Any] | None:
    row = db.get(ECourtsQueryCache, cache_key)
    if row and row.expires_at:
        exp = row.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=IST)
        if exp > ist_now():
            try:
                return json.loads(row.response_json)
            except Exception:
                return None
    return None


def _set_query_cache(db: Session, cache_key: str, endpoint: str, data: Any, ttl_hours: int = 12) -> None:
    try:
        expires = ist_now() + timedelta(hours=ttl_hours)
        row = db.get(ECourtsQueryCache, cache_key)
        payload_str = json.dumps(data, ensure_ascii=False)
        if row:
            row.response_json = payload_str
            row.expires_at = expires
            row.created_at = ist_now()
        else:
            db.add(
                ECourtsQueryCache(
                    cache_key=cache_key,
                    endpoint=endpoint,
                    response_json=payload_str,
                    expires_at=expires,
                    created_at=ist_now(),
                )
            )
        db.commit()
    except Exception as e:
        logger.warning("Failed to write query cache for %s: %s", cache_key, e)
        db.rollback()


# ==============================================================================
# A. Case Search & Discovery
# ==============================================================================

def search_cases(
    db: Session,
    query: str | None = None,
    court_codes: list[str] | None = None,
    district_codes: list[str] | None = None,
    state_codes: list[str] | None = None,
    filing_year: int | None = None,
    litigants: list[str] | None = None,
    page: int = 1,
    page_size: int = 20,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search cases across India with query caching."""
    params: dict[str, Any] = {
        "page": page,
        "pageSize": min(page_size, 100),
    }
    if query:
        params["query"] = query.strip()
    if court_codes:
        params["courtCodes"] = court_codes
    if district_codes:
        params["districtCodes"] = district_codes
    if state_codes:
        params["stateCodes"] = state_codes
    if filing_year:
        params["filingYear"] = filing_year
    if litigants:
        params["litigants"] = litigants

    ck = _cache_key("search", params)
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached is not None:
            cached["_from_cache"] = True
            return cached

    with _get_client() as client:
        try:
            resp = client.get("/api/partner/v2/search", params=params)
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=resp.status_code,
                    detail=f"eCourts Search error: {resp.text[:300]}",
                )
            data = resp.json()
            _set_query_cache(db, ck, "/api/partner/v2/search", data, ttl_hours=6)
            data["_from_cache"] = False
            return data
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"eCourts service unreachable: {str(e)}",
            )


def get_facets_full(db: Session, force_refresh: bool = False) -> dict[str, Any]:
    """Get the full facet catalog (states, districts, court codes, case statuses)."""
    ck = "facets_full_catalog"
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            cached["_from_cache"] = True
            return cached

    with _get_client(timeout=60.0) as client:
        try:
            resp = client.get("/api/partner/v2/facets/full")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail="Failed to load facets catalog.")
            data = resp.json()
            _set_query_cache(db, ck, "/api/partner/v2/facets/full", data, ttl_hours=72)
            data["_from_cache"] = False
            return data
        except httpx.RequestError as e:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"eCourts unreachable: {e}")


def search_facets(db: Session, q: str, field: str = "districtCode") -> dict[str, Any]:
    """Search facet options (e.g. find Mumbai courts)."""
    ck = _cache_key("facet_search", {"q": q.lower().strip(), "field": field})
    cached = _get_query_cache(db, ck)
    if cached:
        return cached

    with _get_client() as client:
        try:
            resp = client.get("/api/partner/v2/facets/search", params={"q": q, "field": field})
            if resp.status_code == 200:
                data = resp.json()
                _set_query_cache(db, ck, "/api/partner/v2/facets/search", data, ttl_hours=48)
                return data
            return {"groups": []}
        except Exception:
            return {"groups": []}


# ==============================================================================
# B. Case Record & Orders
# ==============================================================================

def get_case_detail(db: Session, cnr: str, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch complete case dossier by 16-character CNR with local caching."""
    clean_cnr = cnr.strip().upper()
    cache_row = db.get(ECourtsCaseCache, clean_cnr)

    # Cache is valid for 24 hours unless force_refresh is requested
    if cache_row and not force_refresh:
        upd = cache_row.updated_at
        if upd and upd.tzinfo is None:
            upd = upd.replace(tzinfo=IST)
        age = (ist_now() - upd) if upd else timedelta(days=99)
        if age < timedelta(hours=24):
            try:
                res = json.loads(cache_row.data_json)
                res["_from_cache"] = True
                res["_cached_at"] = cache_row.updated_at.isoformat()
                return res
            except Exception:
                pass

    with _get_client() as client:
        try:
            resp = client.get(f"/api/partner/case/{clean_cnr}")
            if resp.status_code == 404:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Case with CNR {clean_cnr} not found in eCourts.")
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"eCourts API error: {resp.text[:250]}")

            data = resp.json()
            case_data = data.get("data", {}).get("courtCaseData", {})

            # Upsert into DB cache
            if cache_row:
                cache_row.data_json = json.dumps(data, ensure_ascii=False)
                cache_row.case_number = case_data.get("caseNumber") or case_data.get("registrationNumber")
                cache_row.court_name = case_data.get("courtName") or case_data.get("courtComplex")
                cache_row.stage_of_case = case_data.get("stageOfCase")
                cache_row.next_hearing_date = case_data.get("nextHearingDate")
                cache_row.updated_at = ist_now()
            else:
                db.add(
                    ECourtsCaseCache(
                        cnr=clean_cnr,
                        case_number=case_data.get("caseNumber") or case_data.get("registrationNumber"),
                        court_name=case_data.get("courtName") or case_data.get("courtComplex"),
                        stage_of_case=case_data.get("stageOfCase"),
                        next_hearing_date=case_data.get("nextHearingDate"),
                        data_json=json.dumps(data, ensure_ascii=False),
                        updated_at=ist_now(),
                    )
                )
            db.commit()
            data["_from_cache"] = False
            return data
        except httpx.RequestError as e:
            if cache_row:
                res = json.loads(cache_row.data_json)
                res["_from_cache"] = True
                res["_offline_fallback"] = True
                return res
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"eCourts unreachable: {e}")


def get_case_provisions(db: Session, cnr: str, force_refresh: bool = False) -> dict[str, Any]:
    """Fetch statutory citations and provisions for a case."""
    clean_cnr = cnr.strip().upper()
    cache_row = db.get(ECourtsCaseCache, clean_cnr)

    if cache_row and cache_row.provisions_json and not force_refresh:
        try:
            return json.loads(cache_row.provisions_json)
        except Exception:
            pass

    with _get_client() as client:
        try:
            resp = client.get(f"/api/partner/case/{clean_cnr}/provisions")
            if resp.status_code == 200:
                data = resp.json()
                if cache_row:
                    cache_row.provisions_json = json.dumps(data, ensure_ascii=False)
                    db.commit()
                return data
            return {"data": {"provisions": []}}
        except Exception:
            return {"data": {"provisions": []}}


def get_order_pdf(db: Session, cnr: str, filename: str) -> Path:
    """Download and return local path to certified court order PDF with permanent caching."""
    clean_cnr = cnr.strip().upper()
    ECOURTS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    local_pdf = ECOURTS_CACHE_DIR / f"{clean_cnr}_{filename}"
    if local_pdf.exists() and local_pdf.stat().st_size > 0:
        return local_pdf

    order_row = db.scalar(
        select(ECourtsOrderCache).where(
            ECourtsOrderCache.cnr == clean_cnr,
            ECourtsOrderCache.filename == filename,
        )
    )
    if order_row and order_row.pdf_path and Path(order_row.pdf_path).exists():
        return Path(order_row.pdf_path)

    with _get_client(timeout=120.0) as client:
        resp = client.get(f"/api/partner/case/{clean_cnr}/order/{filename}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Court order PDF not available.")
        local_pdf.write_bytes(resp.content)

        if order_row:
            order_row.pdf_path = str(local_pdf)
        else:
            db.add(
                ECourtsOrderCache(
                    cnr=clean_cnr,
                    filename=filename,
                    pdf_path=str(local_pdf),
                    created_at=ist_now(),
                )
            )
        db.commit()
        return local_pdf


def get_order_md(db: Session, cnr: str, filename: str) -> dict[str, Any]:
    """Get extracted markdown text of a court order."""
    clean_cnr = cnr.strip().upper()
    order_row = db.scalar(
        select(ECourtsOrderCache).where(
            ECourtsOrderCache.cnr == clean_cnr,
            ECourtsOrderCache.filename == filename,
        )
    )
    if order_row and order_row.markdown_content:
        return {
            "cnr": clean_cnr,
            "filename": filename,
            "markdownContent": order_row.markdown_content,
            "_from_cache": True,
        }

    with _get_client(timeout=120.0) as client:
        resp = client.get(f"/api/partner/case/{clean_cnr}/order-md/{filename}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Order markdown extraction failed.")
        data = resp.json().get("data", {})
        md_text = data.get("markdownContent", "")

        if order_row:
            order_row.markdown_content = md_text
        else:
            db.add(
                ECourtsOrderCache(
                    cnr=clean_cnr,
                    filename=filename,
                    markdown_content=md_text,
                    created_at=ist_now(),
                )
            )
        db.commit()
        data["_from_cache"] = False
        return data


def get_order_ai(db: Session, cnr: str, filename: str) -> dict[str, Any]:
    """Get server-side AI summary and precedent analysis for a court order."""
    clean_cnr = cnr.strip().upper()
    order_row = db.scalar(
        select(ECourtsOrderCache).where(
            ECourtsOrderCache.cnr == clean_cnr,
            ECourtsOrderCache.filename == filename,
        )
    )
    if order_row and order_row.ai_analysis_json:
        try:
            res = json.loads(order_row.ai_analysis_json)
            res["_from_cache"] = True
            return res
        except Exception:
            pass

    with _get_client(timeout=180.0) as client:
        resp = client.get(f"/api/partner/case/{clean_cnr}/order-ai/{filename}")
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="AI order analysis failed.")
        data = resp.json().get("data", {})
        ai_obj = data.get("aiAnalysis", {})

        if order_row:
            order_row.ai_analysis_json = json.dumps(data, ensure_ascii=False)
        else:
            db.add(
                ECourtsOrderCache(
                    cnr=clean_cnr,
                    filename=filename,
                    ai_analysis_json=json.dumps(data, ensure_ascii=False),
                    created_at=ist_now(),
                )
            )
        db.commit()
        data["_from_cache"] = False
        return data


def refresh_case(db: Session, cnr: str) -> dict[str, Any]:
    """Trigger an asynchronous re-scrape of the case from official court servers."""
    clean_cnr = cnr.strip().upper()
    with _get_client() as client:
        resp = client.post(f"/api/partner/case/{clean_cnr}/refresh")
        if resp.status_code in (200, 202):
            return resp.json()
        raise HTTPException(status_code=resp.status_code, detail="Failed to queue case refresh.")


def get_bulk_refresh_status(db: Session, cnrs: list[str]) -> dict[str, Any]:
    """Check status of previously queued case refreshes (Free endpoint)."""
    with _get_client() as client:
        resp = client.post("/api/partner/case/bulk-refresh-status", json={"cnrs": cnrs})
        if resp.status_code == 200:
            return resp.json()
        return {"data": []}


# ==============================================================================
# C. Cause Lists & Court Structures
# ==============================================================================

def get_court_structure_states(db: Session, force_refresh: bool = False) -> list[dict[str, Any]]:
    """List all states and Supreme Court."""
    ck = "court_struct_states"
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            return cached

    with _get_client() as client:
        resp = client.get("/api/partner/causelist/court-structure/states")
        if resp.status_code == 200:
            data = resp.json()
            _set_query_cache(db, ck, "/court-structure/states", data, ttl_hours=168)
            return data
        return []


def get_court_structure_districts(db: Session, state: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """List districts under a state."""
    clean_state = state.strip().upper()
    ck = f"court_struct_districts_{clean_state}"
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            return cached

    with _get_client() as client:
        resp = client.get(f"/api/partner/causelist/court-structure/states/{clean_state}/districts")
        if resp.status_code == 200:
            data = resp.json()
            _set_query_cache(db, ck, "/court-structure/districts", data, ttl_hours=168)
            return data
        return []


def get_court_structure_complexes(db: Session, state: str, district_code: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """List court complexes under a district."""
    clean_state = state.strip().upper()
    ck = f"court_struct_complexes_{clean_state}_{district_code}"
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            return cached

    with _get_client() as client:
        resp = client.get(f"/api/partner/causelist/court-structure/states/{clean_state}/districts/{district_code}/complexes")
        if resp.status_code == 200:
            data = resp.json()
            _set_query_cache(db, ck, "/court-structure/complexes", data, ttl_hours=168)
            return data
        return []


def get_court_structure_courts(db: Session, complex_code: str, force_refresh: bool = False) -> list[dict[str, Any]]:
    """List individual courtrooms and judges in a court complex."""
    ck = f"court_struct_courts_{complex_code}"
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            return cached

    with _get_client() as client:
        resp = client.get(f"/api/partner/causelist/court-structure/complexes/{complex_code}/courts")
        if resp.status_code == 200:
            data = resp.json()
            _set_query_cache(db, ck, "/court-structure/courts", data, ttl_hours=168)
            return data
        return []


def get_causelist_available_dates(db: Session, state: str, district_code: str) -> list[str]:
    """Free endpoint returning dates with available cause lists."""
    ck = f"causelist_dates_{state}_{district_code}"
    cached = _get_query_cache(db, ck)
    if cached:
        return cached

    with _get_client() as client:
        resp = client.get("/api/partner/causelist/available-dates", params={"state": state, "districtCode": district_code})
        if resp.status_code == 200:
            data = resp.json()
            dates = data.get("data", {}).get("dates", [])
            _set_query_cache(db, ck, "/causelist/available-dates", dates, ttl_hours=6)
            return dates
        return []


def search_causelist(
    db: Session,
    state: str,
    district_code: str,
    court_complex_code: str,
    target_date: str,
    force_refresh: bool = False,
) -> dict[str, Any]:
    """Search cause lists for a given court complex and date with caching."""
    params = {
        "state": state,
        "districtCode": district_code,
        "courtComplexCode": court_complex_code,
        "date": target_date,
    }
    ck = _cache_key("causelist_search", params)
    if not force_refresh:
        cached = _get_query_cache(db, ck)
        if cached:
            cached["_from_cache"] = True
            return cached

    with _get_client() as client:
        resp = client.get("/api/partner/causelist/search", params=params)
        if resp.status_code != 200:
            raise HTTPException(status_code=resp.status_code, detail="Cause list search failed.")
        data = resp.json()
        _set_query_cache(db, ck, "/api/partner/causelist/search", data, ttl_hours=12)
        data["_from_cache"] = False
        return data


def check_causelist_batch(db: Session, cnrs: list[str]) -> dict[str, Any]:
    """Check upcoming listings for a batch of CNRs."""
    with _get_client() as client:
        resp = client.post("/api/partner/causelist/cnr/batch", json={"cnrs": cnrs})
        if resp.status_code == 200:
            return resp.json()
        return {"data": {}}


# ==============================================================================
# Matter Sync ("eCourts API will prevail")
# ==============================================================================

def sync_matter_with_ecourts(
    db: Session,
    matter_id: int,
    cnr: str | None = None,
    force_refresh: bool = True,
    actor: str = "system",
) -> dict[str, Any]:
    """Sync a matter with official eCourts records. eCourts data prevails on court docket fields."""
    matter = db.get(Matter, matter_id)
    if not matter:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    target_cnr = cnr.strip().upper() if cnr else (matter.cnr.strip().upper() if matter.cnr else None)
    if not target_cnr:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Matter does not have a linked CNR. Provide a CNR to sync.",
        )

    # 1. Fetch case details from eCourts
    detail = get_case_detail(db, target_cnr, force_refresh=force_refresh)
    cdata = detail.get("data", {}).get("courtCaseData", {})

    old_state = {
        "court_forum": matter.court_forum,
        "case_number": matter.case_number,
        "current_stage": matter.current_stage,
        "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
        "current_next_purpose": matter.current_next_purpose,
        "cnr": matter.cnr,
    }

    # 2. eCourts API Prevails on Court Docket Fields
    matter.cnr = target_cnr
    official_court = cdata.get("courtName") or cdata.get("courtComplex")
    if official_court and len(official_court.strip()) > 2:
        matter.court_forum = official_court.strip()

    official_case_no = cdata.get("registrationNumber") or cdata.get("caseNumber")
    if official_case_no and len(official_case_no.strip()) > 0:
        matter.case_number = official_case_no.strip()

    official_stage = cdata.get("stageOfCase")
    if official_stage and len(official_stage.strip()) > 0:
        matter.current_stage = official_stage.strip().title()

    next_dt_str = cdata.get("nextHearingDate")
    if next_dt_str:
        try:
            parsed_dt = datetime.strptime(next_dt_str.strip(), "%Y-%m-%d").date()
            matter.current_next_date = parsed_dt

            # Sync or create Calendar Event for the upcoming hearing
            existing_event = db.scalar(
                select(CalendarEvent).where(
                    CalendarEvent.matter_id == matter.id,
                    CalendarEvent.event_date == parsed_dt,
                    CalendarEvent.event_type == "Court",
                )
            )
            if not existing_event:
                db.add(
                    CalendarEvent(
                        title=f"{matter.title} ({matter.court_forum})",
                        event_date=parsed_dt,
                        event_type="Court",
                        court_forum=matter.court_forum,
                        matter_id=matter.id,
                        notes=f"Auto-synced from eCourts (Stage: {matter.current_stage})",
                        status="Active",
                        is_demo=False,
                    )
                )
        except Exception:
            pass

    matter.ecourts_synced_at = ist_now()
    matter.ecourts_data_json = json.dumps(detail, ensure_ascii=False)

    record_audit(
        db,
        actor=actor,
        entity_type="matter",
        entity_id=matter.id,
        action="ecourts_sync",
        before=old_state,
        after={
            "court_forum": matter.court_forum,
            "case_number": matter.case_number,
            "current_stage": matter.current_stage,
            "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
            "cnr": matter.cnr,
        },
    )
    db.commit()

    return {
        "status": "synced",
        "matter_id": matter.id,
        "cnr": matter.cnr,
        "court_forum": matter.court_forum,
        "case_number": matter.case_number,
        "current_stage": matter.current_stage,
        "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
        "orders_count": len(cdata.get("orders", []) or cdata.get("judgmentOrders", [])),
        "synced_at": matter.ecourts_synced_at.isoformat(),
    }


# ==============================================================================
# EOD 60-Sec Auto-Update
# ==============================================================================

def enrich_pending_eod_with_ecourts(db: Session, pending_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Enrich pending EOD triage matters with automatic eCourts status and proposed next dates."""
    enriched = []
    for item in pending_items:
        mid = item.get("matter_id")
        matter = db.get(Matter, mid) if mid else None
        item_copy = dict(item)

        if matter and matter.cnr:
            item_copy["cnr"] = matter.cnr
            try:
                # Check cache first to avoid unnecessary charge; if expired or empty, fetch
                detail = get_case_detail(db, matter.cnr, force_refresh=False)
                cdata = detail.get("data", {}).get("courtCaseData", {})
                ecourts_next_date = cdata.get("nextHearingDate")
                ecourts_stage = cdata.get("stageOfCase")
                ecourts_purpose = cdata.get("stageOfCase")

                if ecourts_next_date:
                    item_copy["ecourts_auto_found"] = True
                    item_copy["ecourts_next_date"] = ecourts_next_date
                    item_copy["ecourts_stage"] = ecourts_stage
                    item_copy["ecourts_proposal"] = (
                        f"Court fixed next date: {ecourts_next_date} ({ecourts_stage or 'Listed'})."
                    )
                else:
                    item_copy["ecourts_auto_found"] = False
            except Exception as e:
                item_copy["ecourts_auto_found"] = False
                item_copy["ecourts_error"] = str(e)
        else:
            item_copy["cnr"] = None
            item_copy["ecourts_auto_found"] = False

        enriched.append(item_copy)
    return enriched
