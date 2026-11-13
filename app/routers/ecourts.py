from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth_service import get_current_user
from app.services.ecourts_service import (
    check_causelist_batch,
    get_bulk_refresh_status,
    get_case_detail,
    get_case_provisions,
    get_causelist_available_dates,
    get_court_structure_complexes,
    get_court_structure_courts,
    get_court_structure_districts,
    get_court_structure_states,
    get_facets_full,
    get_order_ai,
    get_order_md,
    get_order_pdf,
    refresh_case,
    search_cases,
    search_causelist,
    search_facets,
    sync_matter_with_ecourts,
)

router = APIRouter(prefix="/api/ecourts", tags=["ecourts"])


class LinkCnrPayload(BaseModel):
    cnr: str
    sync_immediately: bool = True


class BatchCnrPayload(BaseModel):
    cnrs: list[str]


# ==============================================================================
# Search & Discovery
# ==============================================================================

@router.get("/search")
def api_search_cases(
    query: str | None = Query(default=None),
    court_codes: list[str] | None = Query(default=None),
    district_codes: list[str] | None = Query(default=None),
    state_codes: list[str] | None = Query(default=None),
    filing_year: int | None = Query(default=None),
    litigants: list[str] | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return search_cases(
        db,
        query=query,
        court_codes=court_codes,
        district_codes=district_codes,
        state_codes=state_codes,
        filing_year=filing_year,
        litigants=litigants,
        page=page,
        page_size=page_size,
        force_refresh=force_refresh,
    )


@router.get("/facets/full")
def api_get_facets_full(
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_facets_full(db, force_refresh=force_refresh)


@router.get("/facets/search")
def api_search_facets(
    q: str = Query(default=""),
    field: str = Query(default="districtCode"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return search_facets(db, q=q, field=field)


# ==============================================================================
# Case Dossier & Orders
# ==============================================================================

@router.get("/case/{cnr}")
def api_get_case(
    cnr: str,
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_case_detail(db, cnr, force_refresh=force_refresh)


@router.get("/case/{cnr}/provisions")
def api_get_case_provisions(
    cnr: str,
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_case_provisions(db, cnr, force_refresh=force_refresh)


@router.get("/case/{cnr}/orders/{filename}/pdf")
def api_get_order_pdf(
    cnr: str,
    filename: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    pdf_path = get_order_pdf(db, cnr, filename)
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        filename=f"{cnr}_{filename}",
    )


@router.get("/case/{cnr}/orders/{filename}/md")
def api_get_order_md(
    cnr: str,
    filename: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_order_md(db, cnr, filename)


@router.get("/case/{cnr}/orders/{filename}/ai")
def api_get_order_ai(
    cnr: str,
    filename: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_order_ai(db, cnr, filename)


@router.post("/case/{cnr}/refresh")
def api_refresh_case(
    cnr: str,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return refresh_case(db, cnr)


@router.post("/case/bulk-refresh-status")
def api_bulk_refresh_status(
    payload: BatchCnrPayload,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_bulk_refresh_status(db, payload.cnrs)


# ==============================================================================
# Cause Lists & Court Structures
# ==============================================================================

@router.get("/causelist/structure/states")
def api_structure_states(
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_court_structure_states(db, force_refresh=force_refresh)


@router.get("/causelist/structure/states/{state}/districts")
def api_structure_districts(
    state: str,
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_court_structure_districts(db, state, force_refresh=force_refresh)


@router.get("/causelist/structure/states/{state}/districts/{district_code}/complexes")
def api_structure_complexes(
    state: str,
    district_code: str,
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_court_structure_complexes(db, state, district_code, force_refresh=force_refresh)


@router.get("/causelist/structure/complexes/{complex_code}/courts")
def api_structure_courts(
    complex_code: str,
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_court_structure_courts(db, complex_code, force_refresh=force_refresh)


@router.get("/causelist/available-dates")
def api_causelist_dates(
    state: str = Query(default="MH"),
    district_code: str = Query(default="37"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"dates": get_causelist_available_dates(db, state, district_code)}


@router.get("/causelist/search")
def api_search_causelist(
    state: str = Query(default="MH"),
    district_code: str = Query(default="37"),
    court_complex_code: str = Query(...),
    date: str = Query(...),
    force_refresh: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return search_causelist(
        db,
        state=state,
        district_code=district_code,
        court_complex_code=court_complex_code,
        target_date=date,
        force_refresh=force_refresh,
    )


@router.post("/causelist/batch")
def api_causelist_batch(
    payload: BatchCnrPayload,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return check_causelist_batch(db, payload.cnrs)


# ==============================================================================
# Matter Sync Operations
# ==============================================================================

@router.post("/matters/{matter_id}/sync")
def api_sync_matter(
    matter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return sync_matter_with_ecourts(db, matter_id=matter_id, force_refresh=True, actor=user.login_id)


@router.post("/matters/{matter_id}/link-cnr")
def api_link_cnr(
    matter_id: int,
    payload: LinkCnrPayload,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return sync_matter_with_ecourts(
        db,
        matter_id=matter_id,
        cnr=payload.cnr,
        force_refresh=payload.sync_immediately,
        actor=user.login_id,
    )
