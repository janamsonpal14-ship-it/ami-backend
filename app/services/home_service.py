from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    BareAct,
    BareActSection,
    CalendarEvent,
    Client,
    ClientMatter,
    FeeMemo,
    FeeMemoMatter,
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
    # Pre-fetch matters for efficient lookup
    matter_ids = {ev.matter_id for ev in events if ev.matter_id is not None}
    matter_map = {}
    if matter_ids:
        matters = list(
            db.scalars(
                select(Matter)
                .options(selectinload(Matter.advocate_links))
                .where(Matter.id.in_(matter_ids))
            ).all()
        )
        matter_map = {m.id: m for m in matters}

    for ev in events:
        update_status = None
        m = matter_map.get(ev.matter_id) if ev.matter_id is not None else None
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
                "cnr": m.cnr if m else None,
                "case_number": m.case_number if m else None,
                "court_forum": m.court_forum if (m and m.court_forum) else ev.location,
                "current_stage": m.current_stage if m else None,
                "matter_status": m.status if m else None,
                "default_advocate_ids": [link.advocate_id for link in m.advocate_links] if m else [],
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


STOP_WORDS = {"of", "the", "in", "and", "to", "for", "a", "an", "is", "at", "by", "from", "with", "on", "vs"}

LEGAL_SYNONYMS: dict[str, list[str]] = {
    "anticipatory bail": ["apprehending arrest", "section 482", "section 438", "bail"],
    "cheque bounce": ["dishonour of cheque", "section 138", "cheque", "negotiable instruments"],
    "check bounce": ["dishonour of cheque", "section 138", "cheque", "negotiable instruments"],
    "quashing": ["inherent powers", "section 528", "section 482"],
    "fir": ["first information report"],
    "cpc": ["code of civil procedure", "civil procedure"],
    "crpc": ["code of criminal procedure", "bnss", "criminal procedure"],
    "ipc": ["indian penal code", "bns", "penal code"],
    "ni act": ["negotiable instruments"],
    "posh": ["sexual harassment of women at workplace", "posh"],
    "arbitration": ["arbitration and conciliation"],
    "ibc": ["insolvency and bankruptcy"],
    "sarfaesi": ["securitisation and reconstruction"],
}


def _clean_tokens(s: str) -> list[str]:
    cleaned = re.sub(r"[\s\-_/\\,.:;()\[\]{}]+", " ", str(s).lower()).strip()
    return [t for t in cleaned.split() if t and t not in STOP_WORDS]


def _match_target(query: str, *fields: str | None) -> tuple[bool, int]:
    full_text = " ".join([str(f) for f in fields if f])
    if not full_text:
        return False, 0
    full_lower = full_text.lower()
    full_clean = " " + " ".join(_clean_tokens(full_text)) + " "
    full_compact = re.sub(r"[^a-z0-9]", "", full_lower)

    q_lower = query.strip().lower()
    q_tokens = _clean_tokens(query)
    q_compact = re.sub(r"[^a-z0-9]", "", q_lower)

    if not q_tokens:
        return False, 0

    score = 0
    if q_lower in full_lower:
        score += 100
    elif len(q_compact) >= 3 and q_compact in full_compact:
        score += 80

    matched_tokens = 0
    for qt in q_tokens:
        if (" " + qt) in full_clean or any(tt.startswith(qt) for tt in _clean_tokens(full_text)):
            matched_tokens += 1
            score += 15
        elif len(qt) >= 3 and qt in full_compact:
            matched_tokens += 1
            score += 10

    if matched_tokens == len(q_tokens):
        return True, score

    if q_lower in LEGAL_SYNONYMS:
        for syn in LEGAL_SYNONYMS[q_lower]:
            syn_tokens = _clean_tokens(syn)
            syn_matches = sum(
                1 for st in syn_tokens if (" " + st) in full_clean or (len(st) >= 3 and st in full_compact)
            )
            if syn_matches == len(syn_tokens):
                return True, 75

    return False, 0


def global_search(db: Session, query: str) -> dict:
    """Cmd+K global search across clients, matters, hearings, memos, tasks, bare acts, and statutory sections."""
    q = query.strip()
    if not q:
        return {
            "clients": [],
            "matters": [],
            "hearings": [],
            "memos": [],
            "tasks": [],
            "bare_acts": [],
            "sections": [],
        }

    # 1. Clients
    client_candidates = db.scalars(
        select(Client).options(selectinload(Client.matter_links))
    ).all()
    clients = []
    for c in client_candidates:
        m, sc = _match_target(q, c.display_name, c.normalized_name, c.contact_info, c.notes)
        if m:
            clients.append(
                (
                    sc,
                    {
                        "id": c.id,
                        "title": c.display_name,
                        "subtitle": f"{c.contact_info or 'Client'} • {len(c.matter_links)} matter(s)",
                    },
                )
            )
    clients.sort(key=lambda x: x[0], reverse=True)
    clients = [x[1] for x in clients[:8]]

    # 2. Matters
    matter_candidates = db.scalars(
        select(Matter).options(
            selectinload(Matter.client_links).selectinload(ClientMatter.client),
            selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate),
        )
    ).all()
    matters = []
    for m in matter_candidates:
        c_names = " ".join([cl.client.display_name for cl in m.client_links if cl.client])
        adv_names = " ".join([al.advocate.full_name for al in m.advocate_links if al.advocate])
        matched, sc = _match_target(
            q,
            m.title,
            m.case_number,
            m.normalized_case_number,
            m.court_forum,
            m.current_stage,
            m.cnr,
            m.connected_cases,
            m.notes,
            c_names,
            adv_names,
        )
        if matched:
            display_title = m.title.split("\n")[0] if "\n" in m.title else m.title
            subtitle_parts = [m.court_forum]
            if m.case_number:
                subtitle_parts.append(m.case_number)
            if m.current_stage:
                subtitle_parts.append(m.current_stage)
            matters.append(
                (
                    sc,
                    {
                        "id": m.id,
                        "title": display_title,
                        "subtitle": " • ".join(subtitle_parts),
                        "case_number": m.case_number,
                        "court_forum": m.court_forum,
                    },
                )
            )
    matters.sort(key=lambda x: x[0], reverse=True)
    matters = [x[1] for x in matters[:12]]

    # 3. Bare Acts
    all_acts = {a.id: a for a in db.scalars(select(BareAct)).all()}
    bare_acts = []
    for a in all_acts.values():
        matched, sc = _match_target(
            q, a.title, a.short_title, a.tags, a.summary_text, a.category_slug
        )
        if matched:
            subtitle_parts = []
            if a.year:
                subtitle_parts.append(str(a.year))
            if a.short_title and a.short_title != a.title:
                subtitle_parts.append(a.short_title)
            elif a.tags:
                subtitle_parts.append(a.tags.replace("_", " "))
            bare_acts.append(
                (
                    sc,
                    {
                        "id": a.id,
                        "title": a.title,
                        "subtitle": " • ".join(subtitle_parts) if subtitle_parts else "Bare Act",
                        "short_title": a.short_title,
                    },
                )
            )
    bare_acts.sort(key=lambda x: x[0], reverse=True)
    bare_acts = [x[1] for x in bare_acts[:6]]

    # 4. Statutory Sections
    all_sections = db.scalars(select(BareActSection)).all()
    sections = []
    for s in all_sections:
        act = all_acts.get(s.act_id)
        act_title = act.short_title or act.title if act else ""
        matched, sc = _match_target(
            q, s.section_no, s.heading, act_title, s.body_text[:200]
        )
        if matched:
            sections.append(
                (
                    sc,
                    {
                        "id": s.id,
                        "act_id": s.act_id,
                        "title": f"{act_title} — {s.section_no}",
                        "subtitle": s.heading or (s.body_text[:120] if s.body_text else ""),
                        "section_no": s.section_no,
                        "act_title": act.title if act else "",
                    },
                )
            )
    sections.sort(key=lambda x: x[0], reverse=True)
    sections = [x[1] for x in sections[:8]]

    # 5. Hearings
    hearings_candidates = db.scalars(
        select(Hearing).options(selectinload(Hearing.matter))
    ).all()
    hearings = []
    for h in hearings_candidates:
        m_title = h.matter.title if h.matter else ""
        m_case = h.matter.case_number if h.matter else ""
        m_court = h.matter.court_forum if h.matter else ""
        date_str = format_indian_date(h.hearing_date)
        matched, sc = _match_target(
            q,
            h.business_conducted,
            h.next_purpose,
            h.exception_reason,
            h.stage_at_hearing,
            date_str,
            str(h.hearing_date),
            m_title,
            m_case,
            m_court,
        )
        if matched:
            display_title = (
                h.matter.title.split("\n")[0]
                if h.matter and "\n" in h.matter.title
                else (h.matter.title if h.matter else f"Matter #{h.matter_id}")
            )
            note_snip = (h.business_conducted or h.next_purpose or "Hearing entry")[:90]
            hearings.append(
                (
                    sc,
                    {
                        "id": h.id,
                        "matter_id": h.matter_id,
                        "title": display_title,
                        "subtitle": f"{date_str} — {note_snip}",
                    },
                )
            )
    hearings.sort(key=lambda x: x[0], reverse=True)
    hearings = [x[1] for x in hearings[:8]]

    # 6. Fee Memos
    memo_candidates = db.scalars(
        select(FeeMemo).options(
            selectinload(FeeMemo.matter_links).selectinload(FeeMemoMatter.matter)
        )
    ).all()
    memos = []
    for fm in memo_candidates:
        linked_matters = " ".join(
            [fmm.matter.title for fmm in fm.matter_links if fmm.matter]
        )
        matched, sc = _match_target(
            q,
            fm.bill_number,
            fm.addressee_name,
            fm.subject_line,
            fm.status,
            fm.notes,
            linked_matters,
        )
        if matched:
            memos.append(
                (
                    sc,
                    {
                        "id": fm.id,
                        "title": f"{fm.bill_number} — {fm.addressee_name}",
                        "subtitle": f"Status: {fm.status} • ₹{fm.net_total:,.2f}",
                    },
                )
            )
    memos.sort(key=lambda x: x[0], reverse=True)
    memos = [x[1] for x in memos[:8]]

    # 7. Tasks
    task_candidates = db.scalars(
        select(Task).options(selectinload(Task.matter))
    ).all()
    tasks = []
    for t in task_candidates:
        m_title = t.matter.title if t.matter else ""
        matched, sc = _match_target(
            q,
            t.title,
            t.task_type,
            t.priority,
            t.status,
            t.notes,
            m_title,
        )
        if matched:
            tasks.append(
                (
                    sc,
                    {
                        "id": t.id,
                        "title": t.title,
                        "subtitle": f"{t.task_type} • {t.priority} • {t.status}",
                        "matter_id": t.matter_id,
                    },
                )
            )
    tasks.sort(key=lambda x: x[0], reverse=True)
    tasks = [x[1] for x in tasks[:8]]

    return {
        "clients": clients,
        "matters": matters,
        "sections": sections,
        "bare_acts": bare_acts,
        "hearings": hearings,
        "memos": memos,
        "tasks": tasks,
    }

