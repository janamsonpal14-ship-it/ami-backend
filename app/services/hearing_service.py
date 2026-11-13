from __future__ import annotations

import json
from datetime import date

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Advocate,
    CalendarEvent,
    EventAdvocate,
    Hearing,
    HearingAdvocate,
    HearingRevision,
    Matter,
    MatterAdvocate,
)
from app.schemas import (
    AdvocateOut,
    BatchEodRequest,
    HearingOut,
    HearingSaveRequest,
    NotReachedRequest,
)
from app.services.audit_service import record_audit
from app.services.calendar_service import check_date_holiday_warning
from app.utils.indian_format import ist_today


def hearing_to_out(hearing: Hearing, holiday_warning: str | None = None) -> HearingOut:
    return HearingOut(
        id=hearing.id,
        matter_id=hearing.matter_id,
        matter_title=hearing.matter.title if hearing.matter else "",
        court_forum=hearing.matter.court_forum if hearing.matter else "",
        hearing_date=hearing.hearing_date,
        session_time=hearing.session_time,
        status=hearing.status,
        business_conducted=hearing.business_conducted,
        exception_reason=hearing.exception_reason,
        stage_at_hearing=hearing.stage_at_hearing,
        next_date=hearing.next_date,
        next_date_status=hearing.next_date_status,
        next_purpose=hearing.next_purpose,
        is_billable_candidate=hearing.is_billable_candidate,
        correction_reason=hearing.correction_reason,
        revision_no=hearing.revision_no,
        advocates=[
            AdvocateOut(id=link.advocate.id, full_name=link.advocate.full_name, is_active=link.advocate.is_active)
            for link in hearing.advocate_links
            if link.advocate is not None
        ],
        holiday_warning=holiday_warning,
    )


def _sync_matter_from_latest_hearing(db: Session, matter: Matter) -> None:
    """Derive current matter stage, next date, and status from latest confirmed saved hearing."""
    hearings = list(
        db.scalars(
            select(Hearing)
            .where(Hearing.matter_id == matter.id, Hearing.status.in_(["Completed", "Not Taken Up"]))
            .order_by(Hearing.hearing_date.desc(), Hearing.id.desc())
        ).all()
    )
    if not hearings:
        return
    latest = hearings[0]
    if latest.stage_at_hearing:
        matter.current_stage = latest.stage_at_hearing
    if latest.next_date_status == "Fixed":
        matter.current_next_date = latest.next_date
        matter.current_next_purpose = latest.next_purpose
        if matter.status in ("Disposed", "Reserved"):
            matter.status = "Active"
    elif latest.next_date_status == "Disposed":
        matter.current_next_date = None
        matter.current_next_purpose = "Disposed"
        matter.status = "Disposed"
    elif latest.next_date_status == "Reserved for Orders":
        matter.current_next_date = None
        matter.current_next_purpose = latest.next_purpose or "Reserved for Orders"
        matter.status = "Reserved"
    else:
        matter.current_next_date = None
        matter.current_next_purpose = latest.next_purpose or latest.next_date_status


def _sync_calendar_for_hearing(db: Session, hearing: Hearing, matter: Matter, advocate_ids: list[int]) -> None:
    """Create, update, or cancel the future calendar event linked to this hearing without duplicates."""
    existing_event = db.scalar(
        select(CalendarEvent)
        .options(selectinload(CalendarEvent.advocate_links))
        .where(CalendarEvent.source_hearing_id == hearing.id)
    )

    if hearing.status == "Draft" or hearing.status == "Cancelled":
        if existing_event is not None:
            existing_event.status = "Cancelled"
        return

    if hearing.next_date_status == "Fixed" and hearing.next_date is not None:
        # Supersede any *earlier* hearing's future auto-generated event on this matter beyond hearing_date
        prior_future_events = list(
            db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.matter_id == matter.id,
                    CalendarEvent.source_hearing_id.is_not(None),
                    CalendarEvent.source_hearing_id != hearing.id,
                    CalendarEvent.event_date > hearing.hearing_date,
                    CalendarEvent.status == "Active",
                )
            ).all()
        )
        for prior in prior_future_events:
            prior.status = "Cancelled"

        title = f"{matter.title} ({matter.court_forum})"
        notes = hearing.next_purpose or matter.current_next_purpose or "Listed hearing"
        if existing_event is None:
            existing_event = CalendarEvent(
                event_type="Court",
                title=title,
                event_date=hearing.next_date,
                is_all_day_or_session=True,
                location=matter.court_forum,
                notes=notes,
                matter_id=matter.id,
                source_hearing_id=hearing.id,
                status="Active",
            )
            db.add(existing_event)
            db.flush()
        else:
            existing_event.title = title
            existing_event.event_date = hearing.next_date
            existing_event.location = matter.court_forum
            existing_event.notes = notes
            existing_event.status = "Active"
            existing_event.advocate_links.clear()
            db.flush()

        for adv_id in set(advocate_ids):
            if db.get(Advocate, adv_id):
                db.add(EventAdvocate(event_id=existing_event.id, advocate_id=adv_id))
    else:
        # Not fixed / Reserved / Disposed -> cancel generated future event if any
        if existing_event is not None:
            existing_event.status = "Cancelled"
        prior_future_events = list(
            db.scalars(
                select(CalendarEvent).where(
                    CalendarEvent.matter_id == matter.id,
                    CalendarEvent.source_hearing_id.is_not(None),
                    CalendarEvent.event_date > hearing.hearing_date,
                    CalendarEvent.status == "Active",
                )
            ).all()
        )
        for prior in prior_future_events:
            prior.status = "Cancelled"


def save_hearing(db: Session, matter_id: int, payload: HearingSaveRequest, actor: str) -> HearingOut:
    matter = db.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    if payload.status == "Completed":
        if not payload.business_conducted.strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Please describe what happened in the matter today before completing the update.",
            )
        if not payload.advocate_ids and not (payload.exception_reason and payload.exception_reason.strip()):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Select at least one appearing advocate (or specify an exception reason).",
            )

    if payload.next_date_status == "Fixed" and payload.next_date is None and payload.status == "Completed":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide the next hearing date or mark next date as Not Fixed / Reserved / Disposed.",
        )

    if payload.next_date is not None and payload.next_date < payload.hearing_date and not (
        payload.correction_reason and payload.correction_reason.strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Next date is earlier than the hearing date. Provide a correction_reason to record a historic correction.",
        )

    holiday_warning: str | None = None
    if payload.next_date_status == "Fixed" and payload.next_date is not None:
        holiday_warning = check_date_holiday_warning(db, payload.next_date)
        if payload.next_date_status != "Fixed":
            payload.next_date = None
    else:
        payload.next_date = None

    hearing: Hearing | None = None
    if payload.hearing_id is not None:
        hearing = db.scalar(
            select(Hearing)
            .options(selectinload(Hearing.advocate_links))
            .where(Hearing.id == payload.hearing_id, Hearing.matter_id == matter_id)
        )
        if hearing is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Hearing record not found.")

        snapshot = {
            "hearing_date": str(hearing.hearing_date),
            "status": hearing.status,
            "business_conducted": hearing.business_conducted,
            "next_date": str(hearing.next_date) if hearing.next_date else None,
            "next_date_status": hearing.next_date_status,
            "next_purpose": hearing.next_purpose,
        }
        db.add(
            HearingRevision(
                hearing_id=hearing.id,
                revision_no=hearing.revision_no,
                snapshot_json=json.dumps(snapshot),
                reason=payload.correction_reason or "Updated hearing entry",
                changed_by=actor,
            )
        )
        hearing.revision_no += 1
        hearing.hearing_date = payload.hearing_date
        hearing.session_time = payload.session_time
        hearing.status = payload.status
        hearing.business_conducted = payload.business_conducted.strip()
        hearing.exception_reason = payload.exception_reason
        hearing.stage_at_hearing = payload.stage_at_hearing or matter.current_stage
        hearing.next_date = payload.next_date
        hearing.next_date_status = payload.next_date_status
        hearing.next_purpose = payload.next_purpose
        hearing.is_billable_candidate = payload.is_billable_candidate
        hearing.correction_reason = payload.correction_reason
        hearing.entered_by = actor
        hearing.advocate_links.clear()
        db.flush()
    else:
        # Also guard against accidental double-submit of identical hearing on same date/matter if status Pending Update exists
        hearing = Hearing(
            matter_id=matter_id,
            hearing_date=payload.hearing_date,
            session_time=payload.session_time,
            status=payload.status,
            business_conducted=payload.business_conducted.strip(),
            exception_reason=payload.exception_reason,
            stage_at_hearing=payload.stage_at_hearing or matter.current_stage,
            next_date=payload.next_date,
            next_date_status=payload.next_date_status,
            next_purpose=payload.next_purpose,
            is_billable_candidate=payload.is_billable_candidate,
            correction_reason=payload.correction_reason,
            entered_by=actor,
        )
        db.add(hearing)
        db.flush()

    for adv_id in set(payload.advocate_ids):
        if db.get(Advocate, adv_id):
            db.add(HearingAdvocate(hearing_id=hearing.id, advocate_id=adv_id))

    _sync_calendar_for_hearing(db, hearing, matter, payload.advocate_ids)
    _sync_matter_from_latest_hearing(db, matter)

    record_audit(
        db,
        actor=actor,
        entity_type="hearing",
        entity_id=hearing.id,
        action="save",
        after={"matter_id": matter_id, "hearing_date": str(hearing.hearing_date), "status": hearing.status, "next_date": str(hearing.next_date)},
    )
    db.commit()

    refreshed = db.scalar(
        select(Hearing)
        .options(
            selectinload(Hearing.matter),
            selectinload(Hearing.advocate_links).selectinload(HearingAdvocate.advocate),
        )
        .where(Hearing.id == hearing.id)
    )
    assert refreshed is not None
    return hearing_to_out(refreshed, holiday_warning=holiday_warning)


def mark_not_reached(db: Session, matter_id: int, payload: NotReachedRequest, actor: str) -> HearingOut:
    """1-click Not Reached / Left Over: logs Monthly row, carries forward purpose, defaults non-billable."""
    matter = db.get(Matter, matter_id)
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    carried_purpose = payload.next_purpose or matter.current_next_purpose or "Same purpose carried forward"
    req = HearingSaveRequest(
        hearing_id=payload.hearing_id,
        hearing_date=payload.hearing_date,
        status="Not Taken Up",
        business_conducted="Matter not reached / left over for the day.",
        exception_reason="Matter not reached",
        stage_at_hearing=matter.current_stage,
        advocate_ids=payload.advocate_ids,
        next_date=payload.next_date,
        next_date_status="Fixed" if payload.next_date else "Awaiting Notification",
        next_purpose=carried_purpose,
        is_billable_candidate=payload.is_billable_candidate,
    )
    return save_hearing(db, matter_id, req, actor)


def list_matter_hearings(db: Session, matter_id: int) -> list[HearingOut]:
    hearings = list(
        db.scalars(
            select(Hearing)
            .options(
                selectinload(Hearing.matter),
                selectinload(Hearing.advocate_links).selectinload(HearingAdvocate.advocate),
            )
            .where(Hearing.matter_id == matter_id)
            .order_by(Hearing.hearing_date.desc(), Hearing.id.desc())
        ).all()
    )
    return [hearing_to_out(h) for h in hearings]


def get_pending_eod_matters(db: Session, target_date: date | None = None) -> list[dict]:
    """Return matters listed on target_date (default IST today) still awaiting an EOD hearing update."""
    day = target_date or ist_today()
    court_events = list(
        db.scalars(
            select(CalendarEvent).where(
                CalendarEvent.event_date == day,
                CalendarEvent.event_type == "Court",
                CalendarEvent.status == "Active",
                CalendarEvent.matter_id.is_not(None),
            )
        ).all()
    )
    matter_ids_from_events = {e.matter_id for e in court_events if e.matter_id is not None}

    listed_matters = list(
        db.scalars(
            select(Matter)
            .options(selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate))
            .where(
                (Matter.current_next_date == day) | (Matter.id.in_(matter_ids_from_events) if matter_ids_from_events else False)
            )
            .order_by(Matter.court_forum, Matter.title)
        ).all()
    )

    pending: list[dict] = []
    for m in listed_matters:
        existing_today = db.scalar(
            select(Hearing).where(
                Hearing.matter_id == m.id,
                Hearing.hearing_date == day,
                Hearing.status.in_(["Completed", "Not Taken Up", "Cancelled"]),
            )
        )
        if existing_today is None:
            draft_today = db.scalar(
                select(Hearing).where(
                    Hearing.matter_id == m.id,
                    Hearing.hearing_date == day,
                    Hearing.status == "Draft",
                )
            )
            pending.append(
                {
                    "matter_id": m.id,
                    "title": m.title,
                    "court_forum": m.court_forum,
                    "case_number": m.case_number,
                    "current_stage": m.current_stage,
                    "listed_purpose": m.current_next_purpose,
                    "draft_hearing_id": draft_today.id if draft_today else None,
                    "default_advocate_ids": [link.advocate_id for link in m.advocate_links],
                }
            )
    from app.services.ecourts_service import enrich_pending_eod_with_ecourts
    return enrich_pending_eod_with_ecourts(db, pending)


def batch_save_eod(db: Session, payload: BatchEodRequest, actor: str) -> list[HearingOut]:
    """Process 60-second EOD Batch Triage items."""
    results: list[HearingOut] = []
    for item in payload.items:
        if item.action == "not_reached":
            res = mark_not_reached(
                db,
                item.matter_id,
                NotReachedRequest(
                    hearing_id=item.payload.hearing_id,
                    hearing_date=item.payload.hearing_date,
                    next_date=item.payload.next_date,
                    next_purpose=item.payload.next_purpose,
                    advocate_ids=item.payload.advocate_ids,
                    is_billable_candidate=item.payload.is_billable_candidate,
                ),
                actor,
            )
        else:
            if item.action == "draft":
                item.payload.status = "Draft"
            res = save_hearing(db, item.matter_id, item.payload, actor)
        results.append(res)
    return results
