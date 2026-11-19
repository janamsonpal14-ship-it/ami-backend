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
from app.utils.security import normalize_case_number
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
# Helper: Data Normalization & Status Parsing
# ==============================================================================

def _extract_court_case_data(detail: dict[str, Any]) -> dict[str, Any]:
    """Extract case data whether returned from live eCourts API or cached flat format."""
    if not detail:
        return {}
    if isinstance(detail.get("data"), dict) and "courtCaseData" in detail["data"]:
        return detail["data"]["courtCaseData"]
    if "courtCaseData" in detail and isinstance(detail["courtCaseData"], dict):
        return detail["courtCaseData"]
    return detail


def _parse_ecourts_status(cdata: dict[str, Any]) -> tuple[bool, str, str | None, str | None, str | None]:
    """Parse eCourts caseStatus, disposal nature, decision date, and next hearing date.

    Returns:
        is_disposed (bool): True if case was decided/disposed in court records
        case_status (str): "Disposed" | "Active" | "Reserved"
        disposal_nature (str | None): e.g. "Dismissed", "Allowed", "Settled in Lok Adalat"
        date_of_decision (str | None): e.g. "2026-10-29"
        next_hearing_date (str | None): e.g. "2026-12-02"
    """
    raw_status = str(cdata.get("caseStatus") or cdata.get("status") or "").strip().upper()

    disp_name = (
        cdata.get("natureOfDisposal")
        or cdata.get("disposalNature")
        or cdata.get("disp_name")
        or cdata.get("ldisp_name")
    )
    if disp_name and isinstance(disp_name, str):
        disp_name = disp_name.strip()
        if disp_name.upper() in ("NONE", "NULL", ""):
            disp_name = None

    date_of_decision = (
        cdata.get("dateOfDisposal")
        or cdata.get("decisionDate")
        or cdata.get("date_of_decision")
    )
    if date_of_decision and isinstance(date_of_decision, str):
        date_of_decision = date_of_decision.strip()
        if date_of_decision.upper() in ("NONE", "NULL", ""):
            date_of_decision = None

    is_disposed = (
        raw_status == "DISPOSED"
        or bool(disp_name)
        or bool(date_of_decision)
    )

    if is_disposed:
        return True, "Disposed", disp_name, date_of_decision, None

    stage_upper = str(cdata.get("stageOfCase") or cdata.get("purpose_name") or cdata.get("purpose") or "").strip().upper()
    if raw_status in ("RESERVED", "ORDER RESERVED", "RESERVED FOR ORDERS") or stage_upper in (
        "ORDER RESERVED",
        "RESERVED FOR ORDERS",
        "ORDER",
    ):
        return False, "Reserved", None, None, None

    next_date = cdata.get("nextHearingDate") or cdata.get("date_next_list")
    if next_date and isinstance(next_date, str):
        next_date = next_date.strip()
        if next_date.upper() in ("NONE", "NULL", ""):
            next_date = None

    return False, "Active", None, None, next_date


# ==============================================================================
# Matter Sync ("eCourts API will prevail")
# ==============================================================================

def sync_matter_with_ecourts(
    db: Session,
    matter_id: int,
    cnr: str | None = None,
    force_refresh: bool = True,
    actor: str = "system",
    auto_commit: bool = True,
) -> dict[str, Any]:
    """Sync a matter with official eCourts records. eCourts data prevails on court docket fields."""
    from app.services.hearing_service import sync_calendar_event_for_matter

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
    cdata = _extract_court_case_data(detail)

    old_state = {
        "court_forum": matter.court_forum,
        "case_number": matter.case_number,
        "current_stage": matter.current_stage,
        "status": matter.status,
        "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
        "current_next_purpose": matter.current_next_purpose,
        "cnr": matter.cnr,
    }

    # 2. eCourts API Prevails on Court Docket Fields
    matter.cnr = target_cnr
    official_court = (
        cdata.get("courtName")
        or cdata.get("courtComplex")
        or cdata.get("court_no_desg_name")
        or cdata.get("establishment_name")
    )
    if official_court and len(str(official_court).strip()) > 2:
        court_str = str(official_court).strip()
        if court_str.upper() in ("HCHC", "HCBM", "HCBM01", "HCBM02"):
            matter.court_forum = "Bombay High Court"
        elif not (matter.court_forum and "High Court" in matter.court_forum and court_str.upper().startswith("HC")):
            matter.court_forum = court_str.title()

    raw_type = cdata.get("caseTypeRaw") or cdata.get("type_name")
    reg_num = cdata.get("registrationNumber") or cdata.get("reg_no")
    if raw_type and reg_num and "/" in str(reg_num):
        official_case_no = f"{raw_type} {reg_num}".strip()
    else:
        official_case_no = (
            cdata.get("registrationNumber")
            or cdata.get("caseNumber")
            or cdata.get("case_no")
        )
    if official_case_no and len(str(official_case_no).strip()) > 0:
        matter.case_number = str(official_case_no).strip()
        matter.normalized_case_number = normalize_case_number(matter.case_number)

    is_disposed, case_status, disp_name, date_of_decision, next_date_str = _parse_ecourts_status(cdata)

    if is_disposed:
        matter.status = "Disposed"
        disp_label = disp_name.strip() if disp_name else "Disposed by Court"
        matter.current_stage = disp_label.title()
        matter.current_next_purpose = f"Disposed: {disp_label}"
        matter.current_next_date = None
        sync_calendar_event_for_matter(db, matter)
    elif case_status == "Reserved":
        matter.status = "Reserved"
        matter.current_stage = "Reserved for Orders"
        matter.current_next_purpose = "Reserved for Orders"
        matter.current_next_date = None
        sync_calendar_event_for_matter(db, matter)
    else:
        if matter.status == "Disposed":
            matter.status = "Active"
        official_stage = cdata.get("stageOfCase") or cdata.get("purpose_name") or cdata.get("purpose")
        if official_stage and len(str(official_stage).strip()) > 0:
            matter.current_stage = str(official_stage).strip().title()

        if next_date_str:
            try:
                parsed_dt = datetime.strptime(next_date_str.strip(), "%Y-%m-%d").date()
                matter.current_next_date = parsed_dt
                sync_calendar_event_for_matter(db, matter)
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
            "status": matter.status,
            "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
            "cnr": matter.cnr,
        },
    )
    if auto_commit:
        db.commit()
    else:
        db.flush()

    return {
        "status": "synced",
        "matter_id": matter.id,
        "cnr": matter.cnr,
        "court_forum": matter.court_forum,
        "case_number": matter.case_number,
        "current_stage": matter.current_stage,
        "case_status": matter.status,
        "matter_status": matter.status,
        "is_disposed": is_disposed,
        "disposal_nature": disp_name,
        "date_of_decision": str(date_of_decision) if date_of_decision else None,
        "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
        "current_next_purpose": matter.current_next_purpose,
        "orders_count": len(cdata.get("orders", []) or cdata.get("judgmentOrders", [])),
        "synced_at": matter.ecourts_synced_at.isoformat(),
        "matter": {
            "id": matter.id,
            "status": matter.status,
            "current_stage": matter.current_stage,
            "current_next_date": matter.current_next_date.isoformat() if matter.current_next_date else None,
            "current_next_purpose": matter.current_next_purpose,
            "court_forum": matter.court_forum,
            "case_number": matter.case_number,
            "cnr": matter.cnr,
        },
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
                cdata = _extract_court_case_data(detail)

                is_disposed, case_status, disp_name, date_of_decision, next_hearing_date = _parse_ecourts_status(cdata)
                ecourts_stage = cdata.get("stageOfCase") or cdata.get("purpose_name")
                ecourts_purpose = cdata.get("purpose") or cdata.get("purpose_name") or cdata.get("stageOfCase")

                if is_disposed:
                    item_copy["ecourts_auto_found"] = True
                    item_copy["ecourts_case_status"] = "Disposed"
                    item_copy["ecourts_disposal_nature"] = disp_name
                    item_copy["ecourts_disposal_date"] = str(date_of_decision) if date_of_decision else None
                    item_copy["ecourts_stage"] = disp_name or "Disposed"
                    disp_str = f" ({disp_name})" if disp_name else ""
                    dt_str = f" on {date_of_decision}" if date_of_decision else ""
                    item_copy["ecourts_proposal"] = f"Court marked DISPOSED{disp_str}{dt_str}."
                elif case_status == "Reserved":
                    item_copy["ecourts_auto_found"] = True
                    item_copy["ecourts_case_status"] = "Reserved"
                    item_copy["ecourts_stage"] = "Reserved for Orders"
                    item_copy["ecourts_proposal"] = "Court reserved order."
                elif next_hearing_date:
                    item_copy["ecourts_auto_found"] = True
                    item_copy["ecourts_case_status"] = "Active"
                    item_copy["ecourts_next_date"] = str(next_hearing_date)
                    item_copy["ecourts_stage"] = ecourts_stage
                    item_copy["ecourts_purpose"] = ecourts_purpose
                    item_copy["ecourts_proposal"] = (
                        f"Court fixed next date: {next_hearing_date} ({ecourts_stage or ecourts_purpose or 'Listed'})."
                    )
                else:
                    item_copy["ecourts_auto_found"] = False
                    item_copy["ecourts_case_status"] = "Active"
                    if ecourts_stage or ecourts_purpose:
                        item_copy["ecourts_stage"] = ecourts_stage
                        item_copy["ecourts_purpose"] = ecourts_purpose
            except Exception as e:
                item_copy["ecourts_auto_found"] = False
                item_copy["ecourts_error"] = str(e)
        else:
            item_copy["cnr"] = None
            item_copy["ecourts_auto_found"] = False

        enriched.append(item_copy)
    return enriched
