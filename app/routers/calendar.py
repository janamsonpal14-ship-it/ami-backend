from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CourtHoliday, User
from app.schemas import CalendarEventCreate, CalendarEventOut, CalendarEventUpdate, CourtHolidayOut
from app.services.auth_service import get_current_user
from app.services.bhc_pdf_parser import (
    commit_bhc_calendar,
    parse_bhc_pdf_bytes,
    save_uploaded_bhc_pdf,
)
from app.services.calendar_service import (
    create_calendar_event,
    get_events_for_range,
    get_month_view,
    sync_indian_festival_holidays,
    update_calendar_event,
)
from app.utils.indian_format import ist_today

router = APIRouter(prefix="/api/calendar", tags=["calendar"])


@router.get("/month")
def month_view(
    year: int | None = None,
    month: int | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = ist_today()
    return get_month_view(db, year or today.year, month or today.month)


@router.get("/events", response_model=list[CalendarEventOut])
def events_range(
    start_date: date = Query(...),
    end_date: date = Query(...),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_events_for_range(db, start_date, end_date)


@router.post("/events", response_model=CalendarEventOut)
def add_event(
    payload: CalendarEventCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_calendar_event(db, payload, actor=user.login_id)


@router.patch("/events/{event_id}", response_model=CalendarEventOut)
def edit_event(
    event_id: int,
    payload: CalendarEventUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_calendar_event(db, event_id, payload, actor=user.login_id)


@router.get("/holidays", response_model=list[CourtHolidayOut])
def list_holidays(
    year: int | None = None,
    bench: str = "Principal Seat Bombay",
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(CourtHoliday).where(CourtHoliday.bench == bench)
    if year is not None:
        stmt = stmt.where(CourtHoliday.year == year)
    stmt = stmt.order_by(CourtHoliday.calendar_date)
    rows = db.scalars(stmt).all()
    return [
        CourtHolidayOut(
            id=h.id,
            year=h.year,
            calendar_date=h.calendar_date,
            title=h.title,
            category=h.category,
            bench=h.bench,
            is_closed=h.is_closed,
        )
        for h in rows
    ]


class BhcCommitRequest(BaseModel):
    year: int
    bench: str = "Principal Seat Bombay"
    source_file: str = "BHC Calendar PDF"
    entries: list[dict]


@router.post("/bhc-pdf/preview")
async def preview_bhc_pdf(
    file: UploadFile = File(...),
    bench: str = Form(default="Principal Seat Bombay"),
    _user: User = Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".pdf"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload a valid .pdf file.")
    pdf_bytes = await file.read()
    if len(pdf_bytes) > 15 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="PDF exceeds 15 MB limit.")
    saved_path = save_uploaded_bhc_pdf(file.filename or "bhc_calendar.pdf", pdf_bytes)
    preview = parse_bhc_pdf_bytes(pdf_bytes, bench=bench)
    preview["source_file"] = file.filename or saved_path
    return preview


@router.post("/bhc-pdf/commit")
def commit_bhc_pdf(
    payload: BhcCommitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return commit_bhc_calendar(
        db,
        year=payload.year,
        bench=payload.bench,
        source_file=payload.source_file,
        entries=payload.entries,
        actor=user.login_id,
    )


@router.post("/indian-festivals/sync")
def sync_indian_festivals(
    year: int | None = Query(default=None, ge=2020, le=2035),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    target_year = year or ist_today().year
    return sync_indian_festival_holidays(db, target_year, actor=user.login_id)


