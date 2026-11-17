from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BareAct,
    CalendarEvent,
    Client,
    ClientMatter,
    FeeMemo,
    Hearing,
    HearingAdvocate,
    Matter,
    MatterAdvocate,
    Task,
)
from app.services.billing_service import memo_to_out
from app.services.calendar_service import get_events_for_range
from app.services.hearing_service import get_pending_eod_matters
from app.utils.indian_format import format_indian_date, ist_now, ist_today


def _board_for_range(db: Session, start_date: date, end_date: date) -> list[dict]:
    events = get_events_for_range(db, start_date, end_date)
    # Sort chronologically by event_date, then start_time
    events.sort(key=lambda ev: (ev.event_date, ev.start_time or "99:99", ev.id))
    board: list[dict] = []
    for ev in events:
        update_status = None
        if ev.event_type == "Court" and ev.matter_id is not None:
            hearing = db.scalar(
                select(Hearing).where(
                    Hearing.matter_id == ev.matter_id,
                    Hearing.hearing_date == ev.event_date,
                    Hearing.status.in_(["Completed", "Not Taken Up", "Cancelled"]),
                )
            )
            if hearing is not None:
                update_status = "Updated" if hearing.status == "Completed" else hearing.status
            else:
                update_status = "Awaiting hearing update"
        board.append(
            {
                "event_id": ev.id,
                "event_type": ev.event_type,
                "title": ev.title,
                "event_date": str(ev.event_date),
                "event_date_formatted": format_indian_date(ev.event_date),
                "event_day": ev.event_date.strftime("%a, %d %b"),
                "start_time": ev.start_time,
                "end_time": ev.end_time,
                "is_all_day_or_session": ev.is_all_day_or_session,
                "location": ev.location,
                "notes": ev.notes,
                "matter_id": ev.matter_id,
                "advocates": [a.model_dump() for a in ev.advocates],
                "update_status": update_status,
                "clash_warnings": [w.model_dump() for w in ev.clash_warnings],
            }
        )
    return board


def _board_for_date(db: Session, day: date) -> list[dict]:
    return _board_for_range(db, day, day)


def get_home_pinboard(db: Session) -> dict:
    now = ist_now()
    today = now.date()
    tomorrow = today + timedelta(days=1)

    days_until_next_monday = 7 - today.weekday()
    next_week_start = today + timedelta(days=days_until_next_monday)
    next_week_end = next_week_start + timedelta(days=6)

    todays_board = _board_for_date(db, today)
    tomorrows_board = _board_for_date(db, tomorrow)
    next_week_board = _board_for_range(db, next_week_start, next_week_end)
    pending_eod = get_pending_eod_matters(db, today)

    tasks = list(
        db.scalars(
            select(Task)
            .options(selectinload(Task.matter))
            .order_by(Task.status != "Open", Task.due_date.is_(None), Task.due_date, Task.id.desc())
        ).all()
    )
    deadlines = [
        {
            "id": t.id,
            "title": t.title,
            "due_date": str(t.due_date) if t.due_date else None,
            "priority": t.priority,
            "status": t.status,
            "is_overdue": bool(t.status == "Open" and t.due_date and t.due_date < today),
            "matter_id": t.matter_id,
            "matter_title": t.matter.title if t.matter else None,
        }
        for t in tasks
        if t.task_type == "Deadline" and t.status == "Open"
    ]
    todo_list = [
        {
            "id": t.id,
            "title": t.title,
            "task_type": t.task_type,
            "due_date": str(t.due_date) if t.due_date else None,
            "priority": t.priority,
            "status": t.status,
            "matter_id": t.matter_id,
            "matter_title": t.matter.title if t.matter else None,
        }
        for t in tasks
    ]

    memos = list(
        db.scalars(
            select(FeeMemo)
            .options(selectinload(FeeMemo.matter_links), selectinload(FeeMemo.lines), selectinload(FeeMemo.adjustments), selectinload(FeeMemo.payments))
            .where(FeeMemo.status.in_(["Draft", "Issued", "Partially Paid"]))
        ).all()
    )
    pending_memos = [memo_to_out(m).model_dump() for m in memos]

    todays_court_count = sum(1 for item in todays_board if item["event_type"] == "Court")
    clashes_today = [item for item in todays_board if item["clash_warnings"]]

    return {
        "ist_datetime": now.isoformat(),
        "ist_date": str(today),
        "ist_date_formatted": now.strftime("%A, %d %B %Y"),
        "counts": {
            "matters_listed_today": todays_court_count,
            "matters_next_week": sum(1 for item in next_week_board if item["event_type"] == "Court"),
            "deadlines": len(deadlines),
            "open_tasks": sum(1 for t in tasks if t.status == "Open"),
            "pending_eod_updates": len(pending_eod),
            "pending_memos": len(pending_memos),
            "clashes": len(clashes_today),
        },
        "todays_board": todays_board,
        "tomorrows_board": tomorrows_board,
        "next_week_board": next_week_board,
        "next_week_start": str(next_week_start),
        "next_week_end": str(next_week_end),
        "next_week_range_formatted": f"{next_week_start.strftime('%d %b')} - {next_week_end.strftime('%d %b %Y')}",
        "deadlines": deadlines,
        "todo_list": todo_list,
        "pending_eod_updates": pending_eod,
        "pending_memos": pending_memos,
    }


def get_court_pack(db: Session, target_date: date | None = None) -> dict:
    """Group listed matters by Court/Forum with last 2 hearings & open tasks for offline courtroom use."""
    day = target_date or ist_today()
    board = _board_for_date(db, day)
    matter_ids = [item["matter_id"] for item in board if item["matter_id"] is not None]

    matters = list(
        db.scalars(
            select(Matter)
            .options(
                selectinload(Matter.client_links).selectinload(ClientMatter.client),
                selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate),
                selectinload(Matter.hearings).selectinload(Hearing.advocate_links).selectinload(HearingAdvocate.advocate),
                selectinload(Matter.tasks),
            )
            .where(Matter.id.in_(matter_ids) if matter_ids else Matter.current_next_date == day)
        ).all()
    )
    matter_map = {m.id: m for m in matters}

    groups: dict[str, list[dict]] = {}
    for item in board:
        if item["event_type"] != "Court":
            continue
        m = matter_map.get(item["matter_id"])
        forum = m.court_forum if m else (item["location"] or "Other Forum")
        last_hearings = []
        open_tasks = []
        clients = []
        if m:
            clients = [link.client.display_name for link in m.client_links if link.client]
            confirmed = [h for h in m.hearings if h.hearing_date <= day and h.status in ("Completed", "Not Taken Up")]
            for h in confirmed[:2]:
                last_hearings.append(
                    {
                        "hearing_date": format_indian_date(h.hearing_date),
                        "business_conducted": h.business_conducted,
                        "next_purpose": h.next_purpose,
                    }
                )
            open_tasks = [
                {"title": t.title, "due_date": format_indian_date(t.due_date), "priority": t.priority}
                for t in m.tasks
                if t.status == "Open"
            ]
        groups.setdefault(forum, []).append(
            {
                "matter_id": item["matter_id"],
                "title": m.title if m else item["title"],
                "case_number": m.case_number if m else "",
                "current_stage": m.current_stage if m else "",
                "todays_purpose": item["notes"] or (m.current_next_purpose if m else ""),
                "time_label": item["start_time"] or "Court Session",
                "clients": clients,
                "advocates": [a["full_name"] for a in item["advocates"]],
                "last_hearings": last_hearings,
                "open_tasks": open_tasks,
            }
        )

    return {
        "date": str(day),
        "date_formatted": format_indian_date(day),
        "forums": [{"court_forum": k, "items": v} for k, v in sorted(groups.items())],
    }


def get_monthly_report(
    db: Session,
    year: int,
    month: int,
    search: str | None = None,
    filter_mode: str = "month",
) -> list[dict]:
    """
    Chronological appearance register. Fetches from:
    1. Recorded hearings in the target month (completed or not taken up).
    2. Scheduled matters whose current_next_date falls in the target month.
    3. Calendar court events falling in the target month.
    Supports filter_mode="month" (default), filter_mode="all" (all active feeded matters),
    and filter_mode="no_date" (matters awaiting next court date).
    Never fabricates rows.
    """
    _, last_day = calendar.monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    matter_map = {
        m.id: m
        for m in db.scalars(
            select(Matter).options(
                selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate)
            )
        ).all()
    }

    q = search.lower().strip() if search else ""

    if filter_mode == "all":
        active_matters = [m for m in matter_map.values() if m.status != "Archived"]
        sorted_matters = sorted(
            active_matters,
            key=lambda m: (
                0 if m.current_next_date else 1,
                m.current_next_date or date.max,
                m.id,
            ),
        )
        rows: list[dict] = []
        sr = 1
        for m in sorted_matters:
            p_date_str = (
                format_indian_date(m.current_next_date)
                if m.current_next_date
                else "Date Awaited"
            )
            n_date_str = (
                format_indian_date(m.current_next_date)
                if m.current_next_date
                else m.status
            )
            biz = m.current_next_purpose or m.current_stage
            advs = [al.advocate.full_name for al in m.advocate_links if al.advocate]
            if q:
                hay = f"{m.title} {m.court_forum} {m.case_number} {biz} {m.current_stage} {p_date_str} {n_date_str}".lower()
                if q not in hay:
                    continue
            rows.append(
                {
                    "sr_no": sr,
                    "hearing_id": None,
                    "matter_id": m.id,
                    "matter": f"{m.title} ({m.case_number})" if m.case_number else m.title,
                    "court": m.court_forum,
                    "present_date": p_date_str,
                    "present_date_iso": str(m.current_next_date) if m.current_next_date else None,
                    "next_date": n_date_str,
                    "next_date_iso": str(m.current_next_date) if m.current_next_date else None,
                    "business_conducted": biz,
                    "stage": m.current_stage,
                    "advocates": advs,
                }
            )
            sr += 1
        return rows

    if filter_mode == "no_date":
        no_date_matters = [
            m for m in matter_map.values()
            if m.status != "Archived" and m.current_next_date is None
        ]
        rows = []
        sr = 1
        for m in sorted(no_date_matters, key=lambda m: m.id):
            biz = m.current_next_purpose or m.current_stage
            advs = [al.advocate.full_name for al in m.advocate_links if al.advocate]
            if q:
                hay = f"{m.title} {m.court_forum} {m.case_number} {biz} {m.current_stage}".lower()
                if q not in hay:
                    continue
            rows.append(
                {
                    "sr_no": sr,
                    "hearing_id": None,
                    "matter_id": m.id,
                    "matter": f"{m.title} ({m.case_number})" if m.case_number else m.title,
                    "court": m.court_forum,
                    "present_date": "Date Awaited",
                    "present_date_iso": None,
                    "next_date": m.status,
                    "next_date_iso": None,
                    "business_conducted": biz,
                    "stage": m.current_stage,
                    "advocates": advs,
                }
            )
            sr += 1
        return rows

    # filter_mode == "month"
    # 1. Hearings
    hearings = list(
        db.scalars(
            select(Hearing)
            .options(
                selectinload(Hearing.matter).selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate),
                selectinload(Hearing.advocate_links).selectinload(HearingAdvocate.advocate),
            )
            .where(
                Hearing.hearing_date >= start_date,
                Hearing.hearing_date <= end_date,
                Hearing.status.in_(["Completed", "Not Taken Up"]),
            )
            .order_by(Hearing.hearing_date.asc(), Hearing.id.asc())
        ).all()
    )

    # 2. Matters with current_next_date in month
    matters = list(
        db.scalars(
            select(Matter)
            .options(selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate))
            .where(
                Matter.status != "Archived",
                Matter.current_next_date >= start_date,
                Matter.current_next_date <= end_date,
            )
            .order_by(Matter.current_next_date.asc(), Matter.id.asc())
        ).all()
    )

    # 3. Calendar events in month
    events = list(
        db.scalars(
            select(CalendarEvent)
            .where(
                CalendarEvent.matter_id.isnot(None),
                CalendarEvent.event_date >= start_date,
                CalendarEvent.event_date <= end_date,
                CalendarEvent.status == "Active",
            )
            .order_by(CalendarEvent.event_date.asc(), CalendarEvent.id.asc())
        ).all()
    )

    raw_rows: list[dict] = []
    recorded_hearing_dates: set[tuple[int, date]] = set()

    for h in hearings:
        if not h.matter:
            continue
        recorded_hearing_dates.add((h.matter_id, h.hearing_date))
        advs = [al.advocate.full_name for al in h.advocate_links if al.advocate] or [
            al.advocate.full_name for al in h.matter.advocate_links if al.advocate
        ]
        raw_rows.append(
            {
                "hearing_id": h.id,
                "matter_id": h.matter_id,
                "matter": f"{h.matter.title} ({h.matter.case_number})" if h.matter.case_number else h.matter.title,
                "court": h.matter.court_forum,
                "present_date": format_indian_date(h.hearing_date),
                "present_date_iso": str(h.hearing_date),
                "next_date": format_indian_date(h.next_date) if h.next_date else h.next_date_status,
                "next_date_iso": str(h.next_date) if h.next_date else None,
                "business_conducted": h.business_conducted or h.matter.current_next_purpose or h.matter.current_stage,
                "stage": h.stage_at_hearing or h.matter.current_stage,
                "advocates": advs,
            }
        )

    # Add Calendar events that do not have a recorded hearing on that date
    covered_event_dates: set[tuple[int, date]] = set()
    for ev in events:
        m = matter_map.get(ev.matter_id)
        if not m or m.status == "Archived":
            continue
        key = (ev.matter_id, ev.event_date)
        if key in recorded_hearing_dates or key in covered_event_dates:
            continue
        covered_event_dates.add(key)
        advs = [al.advocate.full_name for al in m.advocate_links if al.advocate]
        raw_rows.append(
            {
                "hearing_id": None,
                "matter_id": m.id,
                "matter": f"{m.title} ({m.case_number})" if m.case_number else m.title,
                "court": m.court_forum,
                "present_date": format_indian_date(ev.event_date),
                "present_date_iso": str(ev.event_date),
                "next_date": (
                    format_indian_date(m.current_next_date)
                    if (m.current_next_date and m.current_next_date > ev.event_date)
                    else (format_indian_date(m.current_next_date) if m.current_next_date else m.status)
                ),
                "next_date_iso": str(m.current_next_date) if m.current_next_date else None,
                "business_conducted": ev.notes or m.current_next_purpose or m.current_stage,
                "stage": m.current_stage,
                "advocates": advs,
            }
        )

    # Add active matters scheduled in this month if not covered yet
    for m in matters:
        if not m.current_next_date:
            continue
        key = (m.id, m.current_next_date)
        if key in recorded_hearing_dates or key in covered_event_dates:
            continue
        covered_event_dates.add(key)
        advs = [al.advocate.full_name for al in m.advocate_links if al.advocate]
        raw_rows.append(
            {
                "hearing_id": None,
                "matter_id": m.id,
                "matter": f"{m.title} ({m.case_number})" if m.case_number else m.title,
                "court": m.court_forum,
                "present_date": format_indian_date(m.current_next_date),
                "present_date_iso": str(m.current_next_date),
                "next_date": format_indian_date(m.current_next_date),
                "next_date_iso": str(m.current_next_date),
                "business_conducted": m.current_next_purpose or m.current_stage,
                "stage": m.current_stage,
                "advocates": advs,
            }
        )

    # Sort chronologically by present date, then matter id
    raw_rows.sort(key=lambda r: (r["present_date_iso"] or "", r["matter_id"]))

    rows = []
    sr = 1
    for r in raw_rows:
        if q:
            hay = f"{r['matter']} {r['court']} {r['business_conducted']} {r['stage']} {r['present_date']} {r['next_date']}".lower()
            if q not in hay:
                continue
        r["sr_no"] = sr
        rows.append(r)
        sr += 1
    return rows


def get_client_wise_report(db: Session, search: str | None = None) -> list[dict]:
    """Client-grouped current status report preserving exact columns."""
    clients = list(
        db.scalars(
            select(Client)
            .options(
                selectinload(Client.matter_links)
                .selectinload(ClientMatter.matter)
            )
            .where(Client.is_active == True)
            .order_by(Client.display_name)
        ).all()
    )
    q = search.lower().strip() if search else ""
    groups: list[dict] = []
    for c in clients:
        rows: list[dict] = []
        sr = 1
        for link in c.matter_links:
            m = link.matter
            if m is None or m.status == "Archived":
                continue
            if q:
                hay = f"{c.display_name} {m.title} {m.court_forum} {m.case_number} {m.current_stage}".lower()
                if q not in hay:
                    continue
            rows.append(
                {
                    "sr_no": sr,
                    "matter_id": m.id,
                    "matter": f"{m.title} ({m.case_number})" if m.case_number else m.title,
                    "court": m.court_forum,
                    "next_date": format_indian_date(m.current_next_date) if m.current_next_date else m.status,
                    "next_date_iso": str(m.current_next_date) if m.current_next_date else None,
                    "stage": m.current_stage,
                    "status": m.status,
                }
            )
            sr += 1
        if rows or (q and q in c.display_name.lower()):
            groups.append(
                {
                    "client_id": c.id,
                    "client_name": c.display_name,
                    "matters": rows,
                }
            )
    return groups


def global_search(db: Session, query: str) -> dict:
    """Cmd+K global search across clients, matters, hearings, memos, tasks, and bare acts."""
    q = query.strip().lower()
    if not q:
        return {"clients": [], "matters": [], "hearings": [], "memos": [], "tasks": [], "bare_acts": []}

    clients = [
        {"id": c.id, "title": c.display_name, "subtitle": c.contact_info or "Client"}
        for c in db.scalars(select(Client)).all()
        if q in c.display_name.lower() or (c.notes and q in c.notes.lower())
    ][:8]

    matters = [
        {"id": m.id, "title": m.title, "subtitle": f"{m.court_forum} • {m.case_number}"}
        for m in db.scalars(select(Matter)).all()
        if q in f"{m.title} {m.case_number} {m.court_forum} {m.current_stage}".lower()
    ][:10]

    hearings = [
        {
            "id": h.id,
            "matter_id": h.matter_id,
            "title": h.matter.title if h.matter else f"Matter #{h.matter_id}",
            "subtitle": f"{format_indian_date(h.hearing_date)} — {h.business_conducted[:90]}",
        }
        for h in db.scalars(select(Hearing).options(selectinload(Hearing.matter))).all()
        if q in h.business_conducted.lower() or (h.next_purpose and q in h.next_purpose.lower())
    ][:8]

    memos = [
        {"id": m.id, "title": f"{m.bill_number} — {m.addressee_name}", "subtitle": f"{m.status}"}
        for m in db.scalars(select(FeeMemo)).all()
        if q in f"{m.bill_number} {m.addressee_name} {m.subject_line or ''}".lower()
    ][:8]

    tasks = [
        {"id": t.id, "title": t.title, "subtitle": f"{t.task_type} • {t.status}"}
        for t in db.scalars(select(Task)).all()
        if q in t.title.lower() or (t.notes and q in t.notes.lower())
    ][:8]

    bare_acts = [
        {"id": b.id, "title": b.title, "subtitle": f"{b.year or ''} • {b.tags or ''}"}
        for b in db.scalars(select(BareAct)).all()
        if q in f"{b.title} {b.short_title or ''} {b.tags or ''} {b.summary_text or ''}".lower()
    ][:8]

    return {
        "clients": clients,
        "matters": matters,
        "hearings": hearings,
        "memos": memos,
        "tasks": tasks,
        "bare_acts": bare_acts,
    }

