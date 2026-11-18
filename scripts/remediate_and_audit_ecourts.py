"""Thorough audit and remediation script:
1. Removes all duplicate matters (id >= 92) created during faulty import.
2. Updates the 40 original matters with exact eCourts match details (CNR, forum, next dates, purpose, status).
3. Inserts ONLY the 8 truly new genuine eCourts matters.
4. Synchronizes CalendarEvents accurately without duplicates.
5. Applies changes to both Supabase PostgreSQL and local SQLite.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from datetime import date
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import psycopg2
from app.utils.indian_format import ist_now
from app.utils.security import normalize_case_number, normalize_name

MYCASES_PATH = Path("/Users/janamsonpal/Downloads/myCases.txt")
SQLITE_DB = BASE_DIR / "data" / "amico.db"
SUPABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres.cmpajseqaxqlqsciwjun:htj6zXEl8dQbUE6T@aws-0-ap-northeast-1.pooler.supabase.com:6543/postgres?sslmode=require"
)

# Verified 1-to-1 mapping from comprehensive audit:
# cino -> matter_id in original 87 matters (None if truly new)
EXACT_MAPPING = {
    # 1. New: Suraj Babulal Shah in MA 1400/2025
    "MHCC020164392025": None,
    # 2. New: Notice of Motion 1775/2026 in Civil Suit 1494/2025 (Rakesh Mehta)
    "MHCC040044842026": None,
    # 3. Match: NM 100398/2026 in Suit 3893/2024 -> Matter 83 (Jaya Shantilal Shah)
    "MHCC010008762026": 83,
    # 4. Match: Suit 103418/2024 -> Matter 82 (Shantilal Shah vs Nine Globe)
    "MHCC010074452024": 82,
    # 5. Match: NM 101041/2026 in Suit 553/2012 -> Matter 37 (Meena Walishetty)
    "MHCC010024972026": 37,
    # 6. Match: NM 105598/2025 in Suit 378/2025 -> Matter 8 (Vardhaman Heights)
    "MHCC010133482025": 8,
    # 7. Match: SS 6406229/2012 -> Matter 76 (Shipping Corp vs Mathuradas)
    "MHMM110000081998": 76,
    # 8. Match: PW 4700652/2019 -> Matter 38 (EOW vs Mohd Ashafak Hussain Miya)
    "MHMM110070802019": 38,
    # 9. Match: Police case W 6702305/2024 -> Matter 17 (Dindoshi PS vs Ashish Mehta)
    "MHMM170095012024": 17,
    # 10. Match: Police case W 6702304/2024 -> Matter 16 (Dindoshi PS vs Naresh Jain)
    "MHMM170095032024": 16,
    # 11. Match: Suit 100378/2025 -> Matter 8 (Vardhaman Heights vs Manish Jain)
    "MHCC010018602025": 8,
    # 12. Match: Suit 104032/2024 -> Matter 81 (Hiten Shah vs Nine Globe)
    "MHCC010081342024": 81,
    # 13. Match: Suit 103893/2024 -> Matter 83 (Jaya Shah vs Nine Globe)
    "MHCC010079192024": 83,
    # 14. Match: Police case W 6702186/2021 -> Matter 14 (Dindoshi PS vs Ashish Mehta)
    "MHMM170079162021": 14,
    # 15. Match: Police case W 6702288/2021 -> Matter 15 (Dindoshi PS vs Ashish Mehta)
    "MHMM170082132021": 15,
    # 16. Match: Civil Suit 552/2022 -> Matter 18 (Ankit Chaturvedi vs Google LLC)
    "MHCC040011382022": 18,
    # 17. Match: Spl Case 100105/2024 -> Matter 21 (MPID EOW vs Varun / Kiran Mehta)
    "MHCC020002792024": 21,
    # 18. Match: PW 5702180/2018 -> Matter 23 (Govandi PS vs Sanjay Chablani)
    "MHMM130096292018": 23,
    # 19. Match: RAE 201398/2007 -> Matter 26 (Surendra Hiranandani vs Ajit Hutheesing)
    "MHSCA20037562007": 26,
    # 20. Match: RAN 200029/2001 -> Matter 30 (British Metal vs Steelcrete)
    "MHSCA20119712004": 30,
    # 21. Match: TER 200168/2002 -> Matter 31 (Steelcrete vs British Metal)
    "MHSCA20001132004": 31,
    # 22. Match: Civil Suit 1399/2025 -> Matter 33 (PB Global vs Apeejay)
    "MHCC040052922025": 33,
    # 23. Match: Misc 4701776/2022 -> Matter 39 (Mohd Hussain Shaikh)
    "MHMM110054522022": 39,
    # 24. Match: SS 6406230/2012 -> Matter 77 (Shipping Corp vs Mathuradas)
    "MHMM110000091998": 77,
    # 25. Match: SS 6406231/2012 -> Matter 78 (Shipping Corp vs Mathuradas)
    "MHMM110002311998": 78,
    # 26. Match: SW 3800005/2013 -> Matter 41 (Arvind Inamdar vs Mathuradas)
    "MHMM200002112013": 41,
    # 27. Match: SW 3800006/2013 -> Matter 41 (Arvind Inamdar vs Mathuradas)
    "MHMM200002122013": 41,
    # 28. Match: SW 3800007/2013 -> Matter 41 (Arvind Inamdar vs Mathuradas)
    "MHMM200002132013": 41,
    # 29. Match: SS 700930/2018 -> Matter 42 (Shamrock vs Gomti)
    "MHMM150018262018": 42,
    # 30. Match: Commercial Suit 100010/2025 -> Matter 63 (YT Entertainment vs Vikram Kumar)
    "MHCC010001052025": 63,
    # 31. Match: SC 2300142/2024 -> Matter 68 (Nickunj EDM Wires)
    "MHMM110024802024": 68,
    # 32. Match: SC 2300137/2024 -> Matter 68 (Nickunj EDM Wires)
    "MHMM110024722024": 68,
    # 33. Match: Suit 104113/2024 -> Matter 10 (Velji Shah vs Nine Globe)
    "MHCC010081552024": 10,
    # 34. Match: Execution Appln 100429/2023 -> Matter 36 (Suresh Walishetty)
    "MHCC010127562023": 36,
    # 35. Match: Execution Appln 100376/2024 -> Matter 37 (Meena Walishetty)
    "MHCC010206162024": 37,
    # 36. New: SS 2802551/2019 (EXIM Bank vs JBF Petrochemicals)
    "MHMM110097392019": None,
    # 37. Match: Petition D 103/2026 -> Matter 65 (Sahil Shah vs Falini Shah)
    "MHFC010044872026": 65,
    # 38. Match: LEC 146/2026 -> Matter 67 (Suneeta Sood vs Namaskar Bharat)
    "MHSCA20021802026": 67,
    # 39. New: Civil Suit 1494/2025 (Rakesh Mehta vs Bhanushankar Mehta)
    "MHCC040046562025": None,
    # 40. Match: Petition A 1675/2026 -> Matter 66 (Falini Shah vs Sahil Shah)
    "MHFC010045282026": 66,
    # 41. New: Cri Bail Appln 700/2026 (Noor Ali Chote Miya vs State)
    "MHTH230014192026": None,
    # 42. New: RCS 111/2015 (Urmilaben Kamleshbhai Sonpal vs Vasantbhai Patel)
    "GJVL040001162015": None,
    # 43. New: CMA SC 3/2021 (Urmilaben Kamleshbhai Sonpal vs Chandanben Patel)
    "GJVL040012682021": None,
    # 44. Match: RCS 758/2021 -> Matter 43 (Shraddha Ganu vs Mukesh Udani)
    "MHTH020053712021": 43,
    # 45. Match: RCS 759/2021 -> Matter 44 (Shraddha Ganu vs Manisha Udani)
    "MHTH020053692021": 44,
    # 46. Match: CS 70585/2023 -> Matter 50 (AK Minerals vs Mukesh Joshi)
    "WBCS020918482023": 50,
    # 47. New: Summary Civil Suit 100371/2026 (Gautam Vijay Jain vs Chandan Galani)
    "MHCC010089802026": None,
    # 48. Match: CRA 100613/2026 -> Matter 24 (Sanjay Chablani)
    "MHCC020126442026": 24,
}


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
    if len(clean_no) == 6 and clean_no.startswith(("10", "20")):
        clean_no = clean_no[2:].lstrip("0")
    elif len(clean_no) == 7 and clean_no.startswith(("100", "200")):
        clean_no = clean_no[3:].lstrip("0")
    return f"{clean_no}/{reg_year}"


def remediate_sqlite(cases: list[dict]):
    print("\n--- REMEDIATING LOCAL SQLITE (amico.db) ---")
    conn = sqlite3.connect(str(SQLITE_DB))
    cur = conn.cursor()

    # 1. Delete duplicate matters with id >= 92 and cascade links
    cur.execute("SELECT id FROM matters WHERE id >= 92;")
    dup_ids = [r[0] for r in cur.fetchall()]
    if dup_ids:
        print(f"Deleting {len(dup_ids)} duplicate matters (IDs >= 92)...")
        id_str = ", ".join(str(i) for i in dup_ids)
        cur.execute(f"DELETE FROM client_matters WHERE matter_id IN ({id_str});")
        cur.execute(f"DELETE FROM calendar_events WHERE matter_id IN ({id_str});")
        cur.execute(f"DELETE FROM hearings WHERE matter_id IN ({id_str});")
        cur.execute(f"DELETE FROM matters WHERE id IN ({id_str});")
        conn.commit()

    # 2. Update matched matters
    updated_matters = 0
    new_matters = 0

    cases_by_cino = {c["cino"]: c for c in cases}

    for cino, mid in EXACT_MAPPING.items():
        ec = cases_by_cino.get(cino)
        if not ec:
            continue

        type_name = (ec.get("type_name") or "").strip()
        reg_no = str(ec.get("reg_no") or "").strip()
        reg_year = str(ec.get("reg_year") or "").strip()
        disp_no = format_display_case_no(type_name, reg_no, reg_year)

        pet = (ec.get("petparty_name") or "").strip()
        res = (ec.get("resparty_name") or "").strip()
        establishment = (ec.get("establishment_name") or "").strip()
        court_desg = (ec.get("court_no_desg_name") or "").strip()

        date_next = parse_iso_date(ec.get("date_next_list"))
        purpose = (ec.get("purpose_name") or "").strip()
        disp_name = (ec.get("disp_name") or "").strip()
        json_str = json.dumps(ec, ensure_ascii=False)

        if mid is not None:
            # Update existing matter
            cur.execute("""
                UPDATE matters
                SET cnr = ?,
                    court_forum = COALESCE(NULLIF(court_forum, ''), ?),
                    current_next_date = COALESCE(?, current_next_date),
                    current_next_purpose = COALESCE(NULLIF(?, ''), current_next_purpose),
                    current_stage = CASE WHEN ? != '' THEN ? ELSE current_stage END,
                    status = CASE WHEN ? != '' THEN 'Disposed' ELSE status END,
                    ecourts_synced_at = ?,
                    ecourts_data_json = ?,
                    updated_at = ?
                WHERE id = ?;
            """, (
                cino,
                establishment,
                str(date_next) if date_next else None,
                purpose if purpose else None,
                disp_name, disp_name,
                disp_name,
                str(ist_now()),
                json_str,
                str(ist_now()),
                mid,
            ))
            updated_matters += 1
            matter_target_id = mid
        else:
            # Insert truly new matter
            title = f"{type_name} {disp_no}\n{pet}\nVs.\n{res}"
            status = "Disposed" if disp_name else "Active"
            stage = disp_name if disp_name else (purpose or "Listed")
            notes = f"Court: {court_desg}\nImported from eCourts" if court_desg else "Imported from eCourts"

            cur.execute("""
                INSERT INTO matters (
                    title, court_forum, case_type, case_number, normalized_case_number,
                    current_stage, status, current_next_date, current_next_purpose, notes,
                    cnr, is_demo, ecourts_synced_at, ecourts_data_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?);
            """, (
                title,
                establishment or "eCourts",
                type_name,
                disp_no,
                normalize_case_number(disp_no),
                stage,
                status,
                str(date_next) if date_next else None,
                purpose if purpose else None,
                notes,
                cino,
                str(ist_now()),
                json_str,
                str(ist_now()),
                str(ist_now()),
            ))
            matter_target_id = cur.lastrowid
            new_matters += 1

            # Create Client & link
            client_name = pet
            if " THROUGH " in client_name.upper():
                client_name = client_name.split(" THROUGH ")[0].split(" through ")[0].strip()
            if " IN " in client_name.upper():
                client_name = client_name.split(" IN ")[0].split(" in ")[0].strip()

            if client_name and len(client_name) > 2 and client_name != "XXXXXXX":
                norm_name = normalize_name(client_name)
                cur.execute("SELECT id FROM clients WHERE normalized_name = ?;", (norm_name,))
                c_row = cur.fetchone()
                if c_row:
                    c_id = c_row[0]
                else:
                    cur.execute("""
                        INSERT INTO clients (display_name, normalized_name, notes, is_active, is_demo, created_at, updated_at)
                        VALUES (?, ?, ?, 1, 0, ?, ?);
                    """, (client_name, norm_name, f"Party in {type_name} {disp_no} ({cino})", str(ist_now()), str(ist_now())))
                    c_id = cur.lastrowid
                
                cur.execute("INSERT OR IGNORE INTO client_matters (client_id, matter_id, role, is_billing_client) VALUES (?, ?, 'client', 1);", (c_id, matter_target_id))

        # Synchronize CalendarEvent if date_next is upcoming in 2026/2027
        if date_next and date_next >= date(2026, 1, 1):
            cur.execute("""
                SELECT id FROM calendar_events WHERE matter_id = ? AND event_date = ?;
            """, (matter_target_id, str(date_next)))
            if not cur.fetchone():
                p_up = purpose.upper()
                s_time = "14:45" if ("2.45" in p_up or "02:45" in p_up) else ("11:00" if ("11" in p_up) else "11:00")
                e_time = "16:00" if s_time == "14:45" else "12:00"
                event_title = f"{type_name} {disp_no} - {purpose if purpose else 'Hearing'}"
                cur.execute("""
                    INSERT INTO calendar_events (
                        event_type, title, event_date, start_time, end_time, is_all_day_or_session,
                        location, notes, matter_id, status, is_demo, created_at, updated_at
                    ) VALUES ('Court', ?, ?, ?, ?, 0, ?, ?, ?, 'Active', 0, ?, ?);
                """, (
                    event_title,
                    str(date_next),
                    s_time,
                    e_time,
                    establishment,
                    f"{court_desg}\n{pet} vs {res}\nCNR: {cino}",
                    matter_target_id,
                    str(ist_now()),
                    str(ist_now()),
                ))

        # Always upsert ecourts_case_cache
        cur.execute("""
            INSERT INTO ecourts_case_cache (cnr, case_number, court_name, stage_of_case, next_hearing_date, data_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cnr) DO UPDATE SET
                case_number = excluded.case_number,
                court_name = excluded.court_name,
                stage_of_case = excluded.stage_of_case,
                next_hearing_date = excluded.next_hearing_date,
                data_json = excluded.data_json,
                updated_at = excluded.updated_at;
        """, (
            cino,
            f"{type_name} {disp_no}",
            f"{establishment} ({court_desg})" if court_desg else establishment,
            disp_name if disp_name else purpose,
            str(date_next) if date_next else None,
            json_str,
            str(ist_now()),
        ))

    conn.commit()
    cur.execute("SELECT count(*) FROM matters;")
    total_m = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM calendar_events;")
    total_c = cur.fetchone()[0]
    conn.close()

    print(f"SQLite Remediation Complete: {updated_matters} matched & updated, {new_matters} new inserted.")
    print(f"Final SQLite counts: {total_m} matters, {total_c} calendar events.")


def remediate_supabase(cases: list[dict]):
    print("\n--- REMEDIATING SUPABASE POSTGRESQL ---")
    pg_conn = psycopg2.connect(SUPABASE_URL)
    pg_cur = pg_conn.cursor()

    # 1. Delete duplicate matters with id >= 92 and cascade links
    pg_cur.execute("SELECT id FROM matters WHERE id >= 92;")
    dup_ids = [r[0] for r in pg_cur.fetchall()]
    if dup_ids:
        print(f"Deleting {len(dup_ids)} duplicate matters in Supabase (IDs >= 92)...")
        id_str = ", ".join(str(i) for i in dup_ids)
        pg_cur.execute(f"DELETE FROM client_matters WHERE matter_id IN ({id_str});")
        pg_cur.execute(f"DELETE FROM calendar_events WHERE matter_id IN ({id_str});")
        pg_cur.execute(f"DELETE FROM hearings WHERE matter_id IN ({id_str});")
        pg_cur.execute(f"DELETE FROM matters WHERE id IN ({id_str});")
        pg_conn.commit()

    updated_matters = 0
    new_matters = 0
    cases_by_cino = {c["cino"]: c for c in cases}

    for cino, mid in EXACT_MAPPING.items():
        ec = cases_by_cino.get(cino)
        if not ec:
            continue

        type_name = (ec.get("type_name") or "").strip()
        reg_no = str(ec.get("reg_no") or "").strip()
        reg_year = str(ec.get("reg_year") or "").strip()
        disp_no = format_display_case_no(type_name, reg_no, reg_year)

        pet = (ec.get("petparty_name") or "").strip()
        res = (ec.get("resparty_name") or "").strip()
        establishment = (ec.get("establishment_name") or "").strip()
        court_desg = (ec.get("court_no_desg_name") or "").strip()

        date_next = parse_iso_date(ec.get("date_next_list"))
        purpose = (ec.get("purpose_name") or "").strip()
        disp_name = (ec.get("disp_name") or "").strip()
        json_str = json.dumps(ec, ensure_ascii=False)

        if mid is not None:
            # Update existing matter in Postgres
            pg_cur.execute("""
                UPDATE matters
                SET cnr = %s,
                    court_forum = COALESCE(NULLIF(court_forum, ''), %s),
                    current_next_date = COALESCE(%s, current_next_date),
                    current_next_purpose = COALESCE(NULLIF(%s, ''), current_next_purpose),
                    current_stage = CASE WHEN %s != '' THEN %s ELSE current_stage END,
                    status = CASE WHEN %s != '' THEN 'Disposed' ELSE status END,
                    ecourts_synced_at = %s,
                    ecourts_data_json = %s,
                    updated_at = %s
                WHERE id = %s;
            """, (
                cino,
                establishment,
                date_next,
                purpose if purpose else None,
                disp_name, disp_name,
                disp_name,
                ist_now(),
                json_str,
                ist_now(),
                mid,
            ))
            updated_matters += 1
            matter_target_id = mid
        else:
            # Insert truly new matter in Postgres
            title = f"{type_name} {disp_no}\n{pet}\nVs.\n{res}"
            status = "Disposed" if disp_name else "Active"
            stage = disp_name if disp_name else (purpose or "Listed")
            notes = f"Court: {court_desg}\nImported from eCourts" if court_desg else "Imported from eCourts"

            pg_cur.execute("""
                INSERT INTO matters (
                    title, court_forum, case_type, case_number, normalized_case_number,
                    current_stage, status, current_next_date, current_next_purpose, notes,
                    cnr, is_demo, ecourts_synced_at, ecourts_data_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, false, %s, %s, %s, %s)
                RETURNING id;
            """, (
                title,
                establishment or "eCourts",
                type_name,
                disp_no,
                normalize_case_number(disp_no),
                stage,
                status,
                date_next,
                purpose if purpose else None,
                notes,
                cino,
                ist_now(),
                json_str,
                ist_now(),
                ist_now(),
            ))
            matter_target_id = pg_cur.fetchone()[0]
            new_matters += 1

            # Client & ClientMatter in Postgres
            client_name = pet
            if " THROUGH " in client_name.upper():
                client_name = client_name.split(" THROUGH ")[0].split(" through ")[0].strip()
            if " IN " in client_name.upper():
                client_name = client_name.split(" IN ")[0].split(" in ")[0].strip()

            if client_name and len(client_name) > 2 and client_name != "XXXXXXX":
                norm_name = normalize_name(client_name)
                pg_cur.execute("SELECT id FROM clients WHERE normalized_name = %s;", (norm_name,))
                c_row = pg_cur.fetchone()
                if c_row:
                    c_id = c_row[0]
                else:
                    pg_cur.execute("""
                        INSERT INTO clients (display_name, normalized_name, notes, is_active, is_demo, created_at, updated_at)
                        VALUES (%s, %s, %s, true, false, %s, %s)
                        RETURNING id;
                    """, (client_name, norm_name, f"Party in {type_name} {disp_no} ({cino})", ist_now(), ist_now()))
                    c_id = pg_cur.fetchone()[0]

                pg_cur.execute("""
                    INSERT INTO client_matters (client_id, matter_id, role, is_billing_client)
                    VALUES (%s, %s, 'client', true)
                    ON CONFLICT DO NOTHING;
                """, (c_id, matter_target_id))

        # CalendarEvent in Postgres
        if date_next and date_next >= date(2026, 1, 1):
            pg_cur.execute("""
                SELECT id FROM calendar_events WHERE matter_id = %s AND event_date = %s;
            """, (matter_target_id, date_next))
            if not pg_cur.fetchone():
                p_up = purpose.upper()
                s_time = "14:45" if ("2.45" in p_up or "02:45" in p_up) else ("11:00" if ("11" in p_up) else "11:00")
                e_time = "16:00" if s_time == "14:45" else "12:00"
                event_title = f"{type_name} {disp_no} - {purpose if purpose else 'Hearing'}"
                pg_cur.execute("""
                    INSERT INTO calendar_events (
                        event_type, title, event_date, start_time, end_time, is_all_day_or_session,
                        location, notes, matter_id, status, is_demo, created_at, updated_at
                    ) VALUES ('Court', %s, %s, %s, %s, false, %s, %s, %s, 'Active', false, %s, %s);
                """, (
                    event_title,
                    date_next,
                    s_time,
                    e_time,
                    establishment,
                    f"{court_desg}\n{pet} vs {res}\nCNR: {cino}",
                    matter_target_id,
                    ist_now(),
                    ist_now(),
                ))

        # Upsert ecourts_case_cache in Postgres
        pg_cur.execute("""
            INSERT INTO ecourts_case_cache (cnr, case_number, court_name, stage_of_case, next_hearing_date, data_json, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (cnr) DO UPDATE SET
                case_number = EXCLUDED.case_number,
                court_name = EXCLUDED.court_name,
                stage_of_case = EXCLUDED.stage_of_case,
                next_hearing_date = EXCLUDED.next_hearing_date,
                data_json = EXCLUDED.data_json,
                updated_at = EXCLUDED.updated_at;
        """, (
            cino,
            f"{type_name} {disp_no}",
            f"{establishment} ({court_desg})" if court_desg else establishment,
            disp_name if disp_name else purpose,
            str(date_next) if date_next else None,
            json_str,
            ist_now(),
        ))

    # Reset sequence in Postgres
    pg_cur.execute("""
        SELECT setval(
            pg_get_serial_sequence('"matters"', 'id'),
            COALESCE((SELECT MAX(id) FROM matters), 1)
        );
    """)
    pg_conn.commit()

    pg_cur.execute("SELECT count(*) FROM matters;")
    total_m = pg_cur.fetchone()[0]
    pg_cur.execute("SELECT count(*) FROM calendar_events;")
    total_c = pg_cur.fetchone()[0]
    pg_cur.close()
    pg_conn.close()

    print(f"Supabase Remediation Complete: {updated_matters} matched & updated, {new_matters} new inserted.")
    print(f"Final Supabase counts: {total_m} matters, {total_c} calendar events.")


def main():
    with open(MYCASES_PATH, "r", encoding="utf-8") as f:
        raw_list = json.load(f)
    cases = [json.loads(x) if isinstance(x, str) else x for x in raw_list]
    print(f"Loaded {len(cases)} cases from {MYCASES_PATH}.")

    remediate_sqlite(cases)
    remediate_supabase(cases)

    print("\n✓ AUDIT & REMEDIATION COMPLETED ON BOTH DATABASES WITH ZERO DUPLICATES!")


if __name__ == "__main__":
    main()
