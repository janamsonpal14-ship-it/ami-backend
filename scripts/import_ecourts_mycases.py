"""Import eCourts myCases.txt into AMI database (both Supabase PostgreSQL and local SQLite)."""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import create_app_engine, create_sqlite_engine, SessionLocal
from app.models import (
    Base,
    CalendarEvent,
    Client,
    ClientMatter,
    ECourtsCaseCache,
    Hearing,
    Matter,
    OfficeSettings,
    User,
)
from app.utils.indian_format import ist_now, ist_today
from app.utils.security import normalize_case_number, normalize_name

MYCASES_PATH = Path("/Users/janamsonpal/Downloads/myCases.txt")


def parse_iso_date(d_str: str | None) -> date | None:
    if not d_str or str(d_str).strip() in ("", "null", "None"):
        return None
    try:
        parts = [int(p) for p in str(d_str).strip().split("-")]
        if len(parts) == 3:
            return date(parts[0], parts[1], parts[2])
    except Exception:
        pass
    return None


def format_display_case_no(type_name: str, reg_no: str, reg_year: str) -> str:
    clean_no = reg_no
    # Strip leading court prefixes like '10' or '20' from 6-digit Bombay case numbers
    # e.g. 103418 -> 3418, 201398 -> 1398
    if len(clean_no) == 6 and clean_no.startswith(("10", "20")):
        clean_no = clean_no[2:].lstrip("0")
    elif len(clean_no) == 7 and clean_no.startswith(("100", "200")):
        clean_no = clean_no[3:].lstrip("0")
    return f"{clean_no}/{reg_year}"


def import_into_db(session: Session, raw_cases: list[dict], db_name: str) -> dict:
    print(f"\n[{db_name}] Starting eCourts import of {len(raw_cases)} cases...")
    
    updated_count = 0
    created_count = 0
    calendar_count = 0

    # Cache existing clients and matters for fast matching
    all_matters = session.scalars(select(Matter)).all()
    all_clients = {c.normalized_name: c for c in session.scalars(select(Client)).all()}

    for c in raw_cases:
        cino = (c.get("cino") or "").strip().upper()
        if not cino:
            continue

        type_name = (c.get("type_name") or "").strip()
        reg_no = str(c.get("reg_no") or "").strip()
        reg_year = str(c.get("reg_year") or "").strip()
        disp_no = format_display_case_no(type_name, reg_no, reg_year)
        
        pet_party = (c.get("petparty_name") or "").strip()
        res_party = (c.get("resparty_name") or "").strip()
        establishment = (c.get("establishment_name") or "").strip()
        court_desg = (c.get("court_no_desg_name") or "").strip()
        
        date_next = parse_iso_date(c.get("date_next_list"))
        purpose = (c.get("purpose_name") or "").strip()
        disp_name = (c.get("disp_name") or "").strip()
        date_decision = parse_iso_date(c.get("date_of_decision"))

        json_str = json.dumps(c, ensure_ascii=False)

        # 1. Update or create ECourtsCaseCache
        cache_entry = session.scalar(select(ECourtsCaseCache).where(ECourtsCaseCache.cnr == cino))
        if cache_entry:
            cache_entry.case_number = f"{type_name} {disp_no}"
            cache_entry.court_name = f"{establishment} ({court_desg})" if court_desg else establishment
            cache_entry.stage_of_case = disp_name if disp_name else purpose
            cache_entry.next_hearing_date = c.get("date_next_list")
            cache_entry.data_json = json_str
            cache_entry.updated_at = ist_now()
        else:
            session.add(ECourtsCaseCache(
                cnr=cino,
                case_number=f"{type_name} {disp_no}",
                court_name=f"{establishment} ({court_desg})" if court_desg else establishment,
                stage_of_case=disp_name if disp_name else purpose,
                next_hearing_date=c.get("date_next_list"),
                data_json=json_str,
                updated_at=ist_now(),
            ))

        # 2. Match with existing matter
        matched_matter: Matter | None = None
        for m in all_matters:
            if m.cnr and m.cnr.strip().upper() == cino:
                matched_matter = m
                break
            # Match by case number or reg number and year
            if reg_no and reg_year and (
                f"{reg_no}/{reg_year}" in m.title
                or (m.case_number and f"{reg_no}/{reg_year}" in m.case_number)
                or f"{disp_no}" in m.title
                or (m.case_number and f"{disp_no}" in m.case_number)
            ):
                matched_matter = m
                break

        if matched_matter:
            # Update existing matter with live eCourts details
            matched_matter.cnr = cino
            if establishment and (not matched_matter.court_forum or matched_matter.court_forum == "eCourts"):
                matched_matter.court_forum = establishment
            if date_next:
                matched_matter.current_next_date = date_next
            if purpose:
                matched_matter.current_next_purpose = purpose
            if disp_name:
                matched_matter.status = "Disposed"
                matched_matter.current_stage = disp_name
            elif purpose and not matched_matter.current_stage:
                matched_matter.current_stage = purpose
            
            matched_matter.ecourts_synced_at = ist_now()
            matched_matter.ecourts_data_json = json_str
            matched_matter.updated_at = ist_now()
            target_matter = matched_matter
            updated_count += 1
        else:
            # Create new matter
            title = f"{type_name} {disp_no}\n{pet_party}\nVs.\n{res_party}"
            status = "Disposed" if disp_name else "Active"
            stage = disp_name if disp_name else (purpose or "Listed")
            notes = f"Court: {court_desg}\nImported from eCourts" if court_desg else "Imported from eCourts"
            
            new_matter = Matter(
                title=title,
                court_forum=establishment or (court_desg or "eCourts"),
                case_type=type_name,
                case_number=disp_no,
                normalized_case_number=normalize_case_number(disp_no),
                current_stage=stage,
                status=status,
                current_next_date=date_next,
                current_next_purpose=purpose if purpose else None,
                notes=notes,
                cnr=cino,
                is_demo=False,
                ecourts_synced_at=ist_now(),
                ecourts_data_json=json_str,
                created_at=ist_now(),
                updated_at=ist_now(),
            )
            session.add(new_matter)
            session.flush()  # assign new_matter.id
            all_matters.append(new_matter)
            target_matter = new_matter
            created_count += 1

            # Check or create Client from petitioner
            client_name = pet_party
            # Clean up long names like '... IN MA 1400/2025' or '... THROUGH SECRETARY ...'
            if " THROUGH " in client_name.upper():
                client_name = client_name.split(" THROUGH ")[0].split(" through ")[0].strip()
            if " IN " in client_name.upper():
                client_name = client_name.split(" IN ")[0].split(" in ")[0].strip()
            
            if client_name and len(client_name) > 2 and client_name != "XXXXXXX":
                norm_cname = normalize_name(client_name)
                client = all_clients.get(norm_cname)
                if not client:
                    client = Client(
                        display_name=client_name,
                        normalized_name=norm_cname,
                        notes=f"Party in {type_name} {disp_no} ({cino})",
                        is_active=True,
                        is_demo=False,
                        created_at=ist_now(),
                        updated_at=ist_now(),
                    )
                    session.add(client)
                    session.flush()
                    all_clients[norm_cname] = client
                
                # Link client to matter
                session.add(ClientMatter(client_id=client.id, matter_id=new_matter.id))

        # 3. Create CalendarEvent if next date is valid
        if date_next and date_next >= date(2026, 1, 1):
            existing_event = session.scalar(
                select(CalendarEvent).where(
                    CalendarEvent.matter_id == target_matter.id,
                    CalendarEvent.event_date == date_next,
                )
            )
            if not existing_event:
                # Time detection
                p_upper = purpose.upper()
                if "2.45" in p_upper or "02:45" in p_upper:
                    s_time = "14:45"
                    e_time = "16:00"
                elif "11" in p_upper or "11:00" in p_upper:
                    s_time = "11:00"
                    e_time = "13:00"
                else:
                    s_time = "11:00"
                    e_time = "12:00"

                event_title = f"{type_name} {disp_no} - {purpose if purpose else 'Hearing'}"
                session.add(CalendarEvent(
                    event_type="Court",
                    title=event_title,
                    event_date=date_next,
                    start_time=s_time,
                    end_time=e_time,
                    is_all_day_or_session=False,
                    location=establishment,
                    notes=f"{court_desg}\n{pet_party} vs {res_party}\nCNR: {cino}",
                    matter_id=target_matter.id,
                    status="Active",
                    is_demo=False,
                    created_at=ist_now(),
                    updated_at=ist_now(),
                ))
                calendar_count += 1

    session.commit()
    print(f"[{db_name}] Import complete: {updated_count} updated, {created_count} created, {calendar_count} calendar events scheduled.")
    return {
        "updated": updated_count,
        "created": created_count,
        "calendar_events": calendar_count,
    }


def main():
    if not MYCASES_PATH.exists():
        print(f"File not found: {MYCASES_PATH}")
        sys.exit(1)

    print(f"Loading eCourts export from {MYCASES_PATH}...")
    with open(MYCASES_PATH, "r", encoding="utf-8") as f:
        raw_list = json.load(f)
    cases = [json.loads(x) if isinstance(x, str) else x for x in raw_list]
    print(f"Parsed {len(cases)} valid eCourts cases.")

    # 1. Import into Supabase PostgreSQL
    supabase_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://postgres.cmpajseqaxqlqsciwjun:htj6zXEl8dQbUE6T@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
    )
    os.environ["DATABASE_URL"] = supabase_url
    print("\nConnecting to Supabase PostgreSQL...")
    pg_engine = create_app_engine()
    with Session(pg_engine) as pg_session:
        pg_res = import_into_db(pg_session, cases, "Supabase PostgreSQL")

    # 2. Also import into local SQLite (data/amico.db) for local offline copy
    sqlite_engine = create_sqlite_engine()
    with Session(sqlite_engine) as sq_session:
        sq_res = import_into_db(sq_session, cases, "Local SQLite amico.db")

    print("\n" + "=" * 50)
    print("ALL CASES SUCCESSFULLY IMPORTED INTO BOTH DATABASES!")
    print(f"Supabase PostgreSQL: {pg_res}")
    print(f"Local SQLite:       {sq_res}")
    print("=" * 50)


if __name__ == "__main__":
    main()
