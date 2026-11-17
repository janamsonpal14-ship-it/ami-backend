from __future__ import annotations

import calendar
import re
from datetime import date, datetime

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

INDIAN_HOLIDAYS_ICS_URL = (
    "https://calendar.google.com/calendar/ical/en.indian%23holiday%40group.v.calendar.google.com/public/basic.ics"
)

from app.models import Advocate, CalendarEvent, CourtHoliday, EventAdvocate, OfficeSettings
from app.schemas import (
    AdvocateOut,
    CalendarEventCreate,
    CalendarEventOut,
    CalendarEventUpdate,
    ClashWarningOut,
    CourtHolidayOut,
)
from app.services.audit_service import record_audit


def _parse_hhmm(value: str | None) -> int | None:
    if not value:
        return None
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid time format '{value}'. Use HH:MM.")
    h, m = int(parts[0]), int(parts[1])
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Time out of range: '{value}'.")
    return h * 60 + m


def _times_overlap(s1: int, e1: int | None, s2: int, e2: int | None) -> bool:
    end1 = e1 if e1 is not None and e1 > s1 else s1 + 60
    end2 = e2 if e2 is not None and e2 > s2 else s2 + 60
    return s1 < end2 and s2 < end1


def compute_clashes_for_day(events: list[CalendarEvent]) -> dict[int, list[ClashWarningOut]]:
    """Compute potential and definite scheduling conflicts among active events on the same day."""
    warnings: dict[int, list[ClashWarningOut]] = {e.id: [] for e in events}
    active = [e for e in events if e.status == "Active"]

    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            adv_a = {link.advocate_id for link in a.advocate_links}
            adv_b = {link.advocate_id for link in b.advocate_links}
            # Clash applies if they share an advocate, or if either has no specific advocate tagged yet
            if adv_a and adv_b and adv_a.isdisjoint(adv_b):
                continue

            if not a.is_all_day_or_session and not b.is_all_day_or_session and a.start_time and b.start_time:
                sa, ea = _parse_hhmm(a.start_time), _parse_hhmm(a.end_time)
                sb, eb = _parse_hhmm(b.start_time), _parse_hhmm(b.end_time)
                assert sa is not None and sb is not None
                if _times_overlap(sa, ea, sb, eb):
                    warnings[a.id].append(
                        ClashWarningOut(
                            severity="Definite Overlap",
                            conflicting_event_id=b.id,
                            conflicting_title=b.title,
                            message=f"Overlaps with '{b.title}' ({b.start_time})",
                        )
                    )
                    warnings[b.id].append(
                        ClashWarningOut(
                            severity="Definite Overlap",
                            conflicting_event_id=a.id,
                            conflicting_title=a.title,
                            message=f"Overlaps with '{a.title}' ({a.start_time})",
                        )
                    )
            else:
                # One or both are court session / untimed listings
                if a.event_type == "Court" or b.event_type == "Court" or not a.is_all_day_or_session or not b.is_all_day_or_session:
                    warnings[a.id].append(
                        ClashWarningOut(
                            severity="Potential Conflict",
                            conflicting_event_id=b.id,
                            conflicting_title=b.title,
                            message=f"Same-day listing/commitment with '{b.title}'",
                        )
                    )
                    warnings[b.id].append(
                        ClashWarningOut(
                            severity="Potential Conflict",
                            conflicting_event_id=a.id,
                            conflicting_title=a.title,
                            message=f"Same-day listing/commitment with '{a.title}'",
                        )
                    )
    return warnings


def event_to_out(event: CalendarEvent, clash_map: dict[int, list[ClashWarningOut]] | None = None) -> CalendarEventOut:
    return CalendarEventOut(
        id=event.id,
        event_type=event.event_type,
        title=event.title,
        event_date=event.event_date,
        start_time=event.start_time,
        end_time=event.end_time,
        is_all_day_or_session=event.is_all_day_or_session,
        location=event.location,
        notes=event.notes,
        matter_id=event.matter_id,
        source_hearing_id=event.source_hearing_id,
        status=event.status,
        advocates=[
            AdvocateOut(id=link.advocate.id, full_name=link.advocate.full_name, is_active=link.advocate.is_active)
            for link in event.advocate_links
            if link.advocate is not None
        ],
        clash_warnings=(clash_map or {}).get(event.id, []),
    )


def get_events_for_range(db: Session, start_date: date, end_date: date) -> list[CalendarEventOut]:
    events = list(
        db.scalars(
            select(CalendarEvent)
            .options(selectinload(CalendarEvent.advocate_links).selectinload(EventAdvocate.advocate))
            .where(
                CalendarEvent.event_date >= start_date,
                CalendarEvent.event_date <= end_date,
                CalendarEvent.status == "Active",
            )
            .order_by(CalendarEvent.event_date, CalendarEvent.is_all_day_or_session, CalendarEvent.start_time, CalendarEvent.id)
        ).all()
    )
    by_day: dict[date, list[CalendarEvent]] = {}
    for ev in events:
        by_day.setdefault(ev.event_date, []).append(ev)

    clash_map: dict[int, list[ClashWarningOut]] = {}
    for day_events in by_day.values():
        clash_map.update(compute_clashes_for_day(day_events))
    return [event_to_out(ev, clash_map) for ev in events]


def get_month_view(db: Session, year: int, month: int) -> dict:
    from app.services.hearing_service import sync_all_active_matters_calendar
    sync_all_active_matters_calendar(db)

    _, last_day = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    events = get_events_for_range(db, start_date, end_date)
    settings = db.scalar(select(OfficeSettings))
    bench = settings.default_bhc_bench if settings else "Principal Seat Bombay"

    # Auto-sync Indian festivals for the year in the background if none recorded yet
    has_year_holidays = db.scalar(
        select(CourtHoliday.id).where(CourtHoliday.year == year, CourtHoliday.bench == bench).limit(1)
    )
    if not has_year_holidays:
        try:
            sync_indian_festival_holidays(db, year, bench=bench, actor="auto_sync")
        except Exception:
            pass

    holidays = list(
        db.scalars(
            select(CourtHoliday)
            .where(
                CourtHoliday.calendar_date >= start_date,
                CourtHoliday.calendar_date <= end_date,
                CourtHoliday.bench == bench,
            )
            .order_by(CourtHoliday.calendar_date)
        ).all()
    )

    return {
        "year": year,
        "month": month,
        "bench": bench,
        "events": events,
        "holidays": [
            CourtHolidayOut(
                id=h.id,
                year=h.year,
                calendar_date=h.calendar_date,
                title=h.title,
                category=h.category,
                bench=h.bench,
                is_closed=h.is_closed,
            )
            for h in holidays
        ],
    }


def check_date_holiday_warning(db: Session, target_date: date) -> str | None:
    """Return a clear warning if target_date falls on a Sunday, 2nd/4th Saturday, or BHC holiday/vacation."""
    settings = db.scalar(select(OfficeSettings))
    bench = settings.default_bhc_bench if settings else "Principal Seat Bombay"

    holidays = list(
        db.scalars(
            select(CourtHoliday).where(
                CourtHoliday.calendar_date == target_date,
                CourtHoliday.bench == bench,
            )
        ).all()
    )
    for h in holidays:
        if not h.is_closed:
            # Working Saturday override!
            return None
    if holidays:
        titles = ", ".join(h.title for h in holidays)
        return f"{target_date.strftime('%d %b %Y')} is marked closed ({titles}) for {bench}."

    if target_date.weekday() == 6:
        return f"{target_date.strftime('%d %b %Y')} is a Sunday."
    if target_date.weekday() == 5:
        nth_sat = (target_date.day - 1) // 7 + 1
        if nth_sat in (2, 4):
            return f"{target_date.strftime('%d %b %Y')} is a {nth_sat}th Saturday (typically closed)."
    return None


def create_calendar_event(db: Session, payload: CalendarEventCreate, actor: str) -> CalendarEventOut:
    _parse_hhmm(payload.start_time)
    _parse_hhmm(payload.end_time)
    event = CalendarEvent(
        event_type=payload.event_type,
        title=payload.title.strip(),
        event_date=payload.event_date,
        start_time=payload.start_time if not payload.is_all_day_or_session else None,
        end_time=payload.end_time if not payload.is_all_day_or_session else None,
        is_all_day_or_session=payload.is_all_day_or_session,
        location=payload.location,
        notes=payload.notes,
        matter_id=payload.matter_id,
        status="Active",
    )
    db.add(event)
    db.flush()

    for adv_id in set(payload.advocate_ids):
        if db.get(Advocate, adv_id):
            db.add(EventAdvocate(event_id=event.id, advocate_id=adv_id))

    record_audit(db, actor=actor, entity_type="calendar_event", entity_id=event.id, action="create", after={"title": event.title, "date": str(event.event_date)})
    db.commit()

    day_events = get_events_for_range(db, payload.event_date, payload.event_date)
    return next(e for e in day_events if e.id == event.id)


def update_calendar_event(db: Session, event_id: int, payload: CalendarEventUpdate, actor: str) -> CalendarEventOut:
    event = db.scalar(
        select(CalendarEvent)
        .options(selectinload(CalendarEvent.advocate_links))
        .where(CalendarEvent.id == event_id)
    )
    if event is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Calendar event not found.")

    before = {"title": event.title, "event_date": str(event.event_date), "status": event.status}
    data = payload.model_dump(exclude_unset=True)
    if "start_time" in data:
        _parse_hhmm(data["start_time"])
    if "end_time" in data:
        _parse_hhmm(data["end_time"])

    advocate_ids = data.pop("advocate_ids", None)
    for k, v in data.items():
        setattr(event, k, v)
    if event.is_all_day_or_session:
        event.start_time = None
        event.end_time = None

    if advocate_ids is not None:
        event.advocate_links.clear()
        db.flush()
        for adv_id in set(advocate_ids):
            if db.get(Advocate, adv_id):
                db.add(EventAdvocate(event_id=event.id, advocate_id=adv_id))

    record_audit(db, actor=actor, entity_type="calendar_event", entity_id=event.id, action="update", before=before, after={"title": event.title, "event_date": str(event.event_date), "status": event.status})
    db.commit()

    refreshed = db.scalar(
        select(CalendarEvent)
        .options(selectinload(CalendarEvent.advocate_links).selectinload(EventAdvocate.advocate))
        .where(CalendarEvent.id == event.id)
    )
    assert refreshed is not None
    day_events = get_events_for_range(db, refreshed.event_date, refreshed.event_date)
    for item in day_events:
        if item.id == refreshed.id:
            return item
    return event_to_out(refreshed)


def parse_indian_holidays_ics(ics_text: str, year: int) -> list[dict]:
    """Parse VEVENT entries for a given year from Google's public Indian Holidays .ics feed."""
    unfolded = re.sub(r"\r?\n[ \t]", "", ics_text)
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", unfolded, re.S)
    results: list[dict] = []
    seen: set[tuple[date, str]] = set()

    for block in blocks:
        dt_match = re.search(r"DTSTART[^:\r\n]*:(\d{8})", block)
        sum_match = re.search(r"SUMMARY:([^\r\n]+)", block)
        if not dt_match or not sum_match:
            continue
        raw_date = dt_match.group(1)
        try:
            hol_date = datetime.strptime(raw_date, "%Y%m%d").date()
        except ValueError:
            continue
        if hol_date.year != year:
            continue

        title = sum_match.group(1).replace(r"\,", ",").replace(r"\;", ";").replace(r"\n", " ").strip()
        if not title:
            continue
        key = (hol_date, title.lower())
        if key in seen:
            continue
        seen.add(key)

        desc_match = re.search(r"DESCRIPTION:([^\r\n]+)", block)
        is_public = bool(desc_match and "Public holiday" in desc_match.group(1))
        results.append(
            {
                "calendar_date": hol_date,
                "title": title,
                "category": "Public Holiday" if is_public else "Indian Festival",
            }
        )

    results.sort(key=lambda item: (item["calendar_date"], item["title"]))
    return results


def sync_indian_festival_holidays(
    db: Session,
    year: int,
    *,
    bench: str | None = None,
    actor: str = "system",
    ics_text: str | None = None,
) -> dict:
    """Fetch free public Indian festival & national holidays and store them as Red calendar holidays."""
    settings = db.scalar(select(OfficeSettings))
    target_bench = bench or (settings.default_bhc_bench if settings else "Principal Seat Bombay")

    raw_ics = ics_text
    if raw_ics is None:
        try:
            resp = httpx.get(INDIAN_HOLIDAYS_ICS_URL, timeout=8.0)
            resp.raise_for_status()
            raw_ics = resp.text
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not fetch free Indian Holidays feed: {exc}",
            )

    parsed = parse_indian_holidays_ics(raw_ics, year)
    existing_rows = list(
        db.scalars(
            select(CourtHoliday).where(
                CourtHoliday.year == year,
                CourtHoliday.bench == target_bench,
            )
        ).all()
    )
    working_sat_dates = {row.calendar_date for row in existing_rows if not row.is_closed}
    existing_pairs = {(row.calendar_date, row.title.strip().lower()) for row in existing_rows}

    added = 0
    for item in parsed:
        cal_date: date = item["calendar_date"]
        title: str = item["title"]
        if cal_date in working_sat_dates:
            continue
        if (cal_date, title.lower()) in existing_pairs:
            continue
        existing_pairs.add((cal_date, title.lower()))
        db.add(
            CourtHoliday(
                year=year,
                calendar_date=cal_date,
                title=title,
                category=item["category"],
                bench=target_bench,
                is_closed=True,
                source_file="Free Public Indian Holidays ICS API",
            )
        )
        added += 1

    if added > 0:
        record_audit(
            db,
            actor=actor,
            entity_type="court_holiday",
            entity_id=f"indian_festivals_{year}",
            action="sync_indian_festivals",
            after={"year": year, "bench": target_bench, "added": added},
        )
    db.commit()
    return {
        "year": year,
        "bench": target_bench,
        "fetched": len(parsed),
        "added": added,
        "source": "Google Public Indian Holidays ICS (Free)",
    }

