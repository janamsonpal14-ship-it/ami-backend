from __future__ import annotations

import calendar
import io
import json
import re
from datetime import date, datetime

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CalendarEvent, Client, ClientMatter, Hearing, ImportBatch, Matter
from app.services.audit_service import create_snapshot, record_audit
from app.services.home_service import get_client_wise_report, get_monthly_report
from app.utils.indian_format import ist_today
from app.utils.security import normalize_case_number, normalize_name

_MONTH_ABBR = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10, "october": 10,
    "nov": 11, "november": 11, "dec": 12, "december": 12,
}


def export_monthly_xlsx(db: Session, year: int, month: int, search: str | None = None) -> tuple[bytes, str]:
    """Export Monthly Matters List preserving exact columns & order."""
    rows = get_monthly_report(db, year, month, search)
    wb = Workbook()
    ws = wb.active
    ws.title = f"{calendar.month_abbr[month]} {year}"

    headers = ["Sr. No.", "Matter", "Court", "Present Date", "Next Date", "Business Conducted", "Stage"]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1B2A4A")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="left", vertical="center")

    for r in rows:
        ws.append([
            r["sr_no"],
            r["matter"],
            r["court"],
            r["present_date"],
            r["next_date"],
            r["business_conducted"],
            r["stage"],
        ])

    for col, width in zip(("A", "B", "C", "D", "E", "F", "G"), (8, 38, 22, 15, 18, 42, 20)):
        ws.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Monthly_Matters_{calendar.month_abbr[month]}_{year}.xlsx"


def export_client_wise_xlsx(db: Session, search: str | None = None) -> tuple[bytes, str]:
    """Export Client-Wise Matters List preserving client grouping & exact columns."""
    groups = get_client_wise_report(db, search)
    wb = Workbook()
    ws = wb.active
    ws.title = "Client Wise List"

    headers = ["Sr. No.", "Matter", "Court", "Next Date", "Stage"]
    ws.append(headers)
    header_fill = PatternFill("solid", fgColor="1B2A4A")
    client_fill = PatternFill("solid", fgColor="E9EEF5")
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = header_fill

    for g in groups:
        ws.append([g["client_name"], "", "", "", ""])
        row_idx = ws.max_row
        for c_idx in range(1, 6):
            cell = ws.cell(row=row_idx, column=c_idx)
            cell.font = Font(bold=True, color="1B2A4A")
            cell.fill = client_fill
        for m in g["matters"]:
            ws.append([m["sr_no"], m["matter"], m["court"], m["next_date"], m["stage"]])
        ws.append([])  # blank visual separator

    for col, width in zip(("A", "B", "C", "D", "E"), (10, 42, 24, 18, 22)):
        ws.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), f"Client_Wise_Matters_{ist_today().isoformat()}.xlsx"


def _parse_excel_date_cell(val) -> tuple[date | None, str | None, bool]:
    """
    Parse a date cell conservatively.
    Returns (parsed_date, flag_message, is_non_date_status).
    Never guesses silently on ambiguous DD.MM.YY like '06.01.26'.
    """
    if val is None:
        return None, None, False
    if isinstance(val, datetime):
        return val.date(), None, False
    if isinstance(val, date):
        return val, None, False

    raw = str(val).strip()
    if not raw:
        return None, None, False

    lower = raw.lower()
    if lower in ("-", "—", "–", "nil", "none", "n/a") or any(
        tok in lower for tok in ("not fixed", "reserved", "disposed", "awaiting", "sine die", "due course")
    ):
        return None, None, True

    # ISO YYYY-MM-DD
    m_iso = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    if m_iso:
        try:
            return date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3))), None, False
        except ValueError:
            return None, f"Invalid calendar date '{raw}'", False

    # DD/MM/YYYY or DD.MM.YYYY or DD-MM-YYYY or 2-digit year
    m_dmy = re.fullmatch(r"(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})", raw)
    if m_dmy:
        d_i, m_i, y_str = int(m_dmy.group(1)), int(m_dmy.group(2)), m_dmy.group(3)
        y_i = 2000 + int(y_str) if len(y_str) == 2 else int(y_str)
        try:
            parsed = date(y_i, m_i, d_i)
        except ValueError:
            return None, f"Invalid calendar date '{raw}'", False
        # Flag ambiguous DD/MM vs MM/DD when both <= 12 and 2-digit year or dot format
        if d_i <= 12 and m_i <= 12 and d_i != m_i and len(y_str) == 2:
            return parsed, f"Ambiguous date '{raw}' (interpreted as {parsed.strftime('%d %b %Y')} — verify DD/MM)", False
        return parsed, None, False

    # Named months like "12 Nov 2026" or "12-Nov-26"
    m_named = re.fullmatch(r"(\d{1,2})[\s./-]+([A-Za-z]+)[\s.,/-]+(\d{2,4})", raw)
    if m_named:
        m_num = _MONTH_ABBR.get(m_named.group(2).lower())
        if m_num:
            y_s = m_named.group(3)
            y_i = 2000 + int(y_s) if len(y_s) == 2 else int(y_s)
            try:
                return date(y_i, m_num, int(m_named.group(1))), None, False
            except ValueError:
                return None, f"Invalid calendar date '{raw}'", False

    return None, f"Unparseable date '{raw}' — left unscheduled for review", False


def _detect_sheet_type(headers: list[str]) -> str:
    joined = " ".join(h.lower() for h in headers if h)
    if "present date" in joined or "business" in joined:
        return "monthly"
    return "client_wise"


def preview_excel_import(db: Session, filename: str, file_bytes: bytes) -> dict:
    """Parse an uploaded Excel workbook into reviewable rows with anomaly & duplicate flags."""
    wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    existing_clients = list(db.scalars(select(Client)).all())
    existing_matters = list(db.scalars(select(Matter)).all())

    rows_out: list[dict] = []
    row_counter = 1

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        all_rows = list(ws.iter_rows(values_only=True))
        if not all_rows:
            continue

        # Locate header row within first 5 rows
        header_idx = 0
        headers: list[str] = []
        for idx, r in enumerate(all_rows[:5]):
            vals = [str(c).strip() if c is not None else "" for c in r]
            joined = " ".join(vals).lower()
            if ("matter" in joined or "case" in joined) and ("court" in joined or "stage" in joined):
                header_idx = idx
                headers = vals
                break
        if not headers:
            headers = [str(c).strip() if c is not None else "" for c in all_rows[0]]

        sheet_type = _detect_sheet_type(headers)
        col_map = {h.lower(): i for i, h in enumerate(headers) if h}

        def get_col(row_vals: tuple, *names: str) -> object:
            for name in names:
                for k, idx in col_map.items():
                    if name in k and idx < len(row_vals):
                        return row_vals[idx]
            return None

        current_client_header: str | None = None

        for excel_row_num, raw_row in enumerate(all_rows[header_idx + 1 :], start=header_idx + 2):
            vals = [c for c in raw_row]
            if not any(v is not None and str(v).strip() != "" for v in vals):
                continue  # Skip empty separator rows

            sr_val = get_col(raw_row, "sr", "s.no")
            matter_val = get_col(raw_row, "matter", "case")
            court_val = get_col(raw_row, "court")
            present_val = get_col(raw_row, "present date")
            next_val = get_col(raw_row, "next date")
            biz_val = get_col(raw_row, "business")
            stage_val = get_col(raw_row, "stage")

            # In Client-Wise sheets, detect client header rows (text in col A or B, empty court/next/stage)
            non_empty = [str(v).strip() for v in raw_row if v is not None and str(v).strip() != ""]
            if sheet_type == "client_wise" and len(non_empty) == 1 and not court_val and not stage_val:
                current_client_header = non_empty[0]
                continue

            matter_title = str(matter_val).strip() if matter_val is not None else ""
            if not matter_title and sr_val is not None and len(non_empty) == 1:
                current_client_header = str(sr_val).strip()
                continue

            flags: list[str] = []
            if not matter_title:
                flags.append("Missing matter title")

            court_str = str(court_val).strip() if court_val is not None else ""
            if not court_str:
                flags.append("Missing court/forum")

            present_date, present_flag, _ = _parse_excel_date_cell(present_val)
            if present_flag:
                flags.append(f"Present Date: {present_flag}")
            if sheet_type == "monthly" and present_date is None and present_val is not None and str(present_val).strip():
                flags.append("Present Date could not be confirmed")

            next_date, next_flag, is_non_date_status = _parse_excel_date_cell(next_val)
            if next_flag:
                flags.append(f"Next Date: {next_flag}")

            # Match existing client & matter without auto-merging
            matched_client_id = None
            if current_client_header:
                norm_c = normalize_name(current_client_header)
                for c in existing_clients:
                    if c.normalized_name == norm_c:
                        matched_client_id = c.id
                        break
            elif sheet_type == "monthly":
                flags.append("No client column in Monthly sheet — review/assign client or import as unassigned")

            matched_matter_id = None
            norm_m = normalize_name(matter_title)
            for m in existing_matters:
                if norm_m and (normalize_name(m.title) == norm_m or (m.case_number and normalize_case_number(m.case_number) in normalize_case_number(matter_title))):
                    matched_matter_id = m.id
                    flags.append(f"Possible match with existing matter: '{m.title}'")
                    break

            status_label = "flagged" if any("Unparseable" in f or "Invalid" in f or "Ambiguous" in f or "Missing" in f for f in flags) else (
                "duplicate_match" if matched_matter_id else "ready"
            )

            rows_out.append(
                {
                    "row_id": row_counter,
                    "source_sheet": sheet_name,
                    "source_row": excel_row_num,
                    "sheet_type": sheet_type,
                    "client_name": current_client_header or "",
                    "matched_client_id": matched_client_id,
                    "matter_title": matter_title,
                    "court_forum": court_str,
                    "present_date": str(present_date) if present_date else None,
                    "present_date_raw": str(present_val).strip() if present_val is not None else "",
                    "next_date": str(next_date) if next_date else None,
                    "next_date_raw": str(next_val).strip() if next_val is not None else "",
                    "next_date_status": (str(next_val).strip() if is_non_date_status else ("Fixed" if next_date else "Not Fixed")),
                    "business_conducted": str(biz_val).strip() if biz_val is not None else "",
                    "stage": str(stage_val).strip() if stage_val is not None else "Fresh / Notice",
                    "matched_matter_id": matched_matter_id,
                    "flags": flags,
                    "status": status_label,
                    "include": bool(matter_title),
                }
            )
            row_counter += 1

    return {
        "filename": filename,
        "total_rows": len(rows_out),
        "ready_count": sum(1 for r in rows_out if r["status"] == "ready"),
        "flagged_count": sum(1 for r in rows_out if r["status"] == "flagged"),
        "duplicate_count": sum(1 for r in rows_out if r["status"] == "duplicate_match"),
        "rows": rows_out,
    }


def commit_excel_import(db: Session, *, filename: str, rows: list[dict], actor: str) -> dict:
    """Snapshot DB and commit user-reviewed Excel rows with full source-row traceability."""
    snap = create_snapshot("excel_import")

    added_clients = 0
    added_matters = 0
    matched_matters = 0
    added_hearings = 0
    skipped = 0

    client_cache: dict[str, Client] = {c.normalized_name: c for c in db.scalars(select(Client)).all()}
    matter_cache: dict[str, Matter] = {normalize_name(m.title): m for m in db.scalars(select(Matter)).all()}

    for row in rows:
        if not row.get("include", True):
            skipped += 1
            continue
        title = (row.get("matter_title") or "").strip()
        if not title:
            skipped += 1
            continue

        client_obj: Client | None = None
        if row.get("matched_client_id"):
            client_obj = db.get(Client, int(row["matched_client_id"]))
        elif row.get("client_name"):
            cname = str(row["client_name"]).strip()
            nc = normalize_name(cname)
            client_obj = client_cache.get(nc)
            if client_obj is None:
                client_obj = Client(display_name=cname, normalized_name=nc, notes=f"Imported from {filename}")
                db.add(client_obj)
                db.flush()
                client_cache[nc] = client_obj
                added_clients += 1

        matter_obj: Matter | None = None
        if row.get("matched_matter_id") and not row.get("create_new_matter"):
            matter_obj = db.get(Matter, int(row["matched_matter_id"]))
            if matter_obj is not None:
                matched_matters += 1
        if matter_obj is None:
            nm = normalize_name(title)
            if not row.get("create_new_matter") and nm in matter_cache:
                matter_obj = matter_cache[nm]
                matched_matters += 1
            else:
                next_d = date.fromisoformat(row["next_date"]) if row.get("next_date") else None
                matter_obj = Matter(
                    title=title,
                    court_forum=(row.get("court_forum") or "Bombay High Court").strip(),
                    current_stage=(row.get("stage") or "Fresh / Notice").strip(),
                    status="Active",
                    current_next_date=next_d,
                    notes=f"Imported from {filename} ({row.get('source_sheet')} row {row.get('source_row')})",
                )
                db.add(matter_obj)
                db.flush()
                matter_cache[nm] = matter_obj
                added_matters += 1
                if client_obj is not None:
                    db.add(ClientMatter(client_id=client_obj.id, matter_id=matter_obj.id, role="Client", is_billing_client=True))

        # If Monthly row has a present_date, record an immutable historical hearing row
        if row.get("present_date"):
            p_date = date.fromisoformat(row["present_date"])
            n_date = date.fromisoformat(row["next_date"]) if row.get("next_date") else None
            h = Hearing(
                matter_id=matter_obj.id,
                hearing_date=p_date,
                status="Completed",
                business_conducted=(row.get("business_conducted") or "Imported historical appearance").strip(),
                stage_at_hearing=(row.get("stage") or matter_obj.current_stage).strip(),
                next_date=n_date,
                next_date_status="Fixed" if n_date else "Not Fixed",
                entered_by=actor,
            )
            db.add(h)
            db.flush()
            added_hearings += 1
            if n_date and n_date >= ist_today():
                db.add(
                    CalendarEvent(
                        event_type="Court",
                        title=f"{matter_obj.title} ({matter_obj.court_forum})",
                        event_date=n_date,
                        is_all_day_or_session=True,
                        location=matter_obj.court_forum,
                        matter_id=matter_obj.id,
                        source_hearing_id=h.id,
                        status="Active",
                    )
                )
        elif row.get("next_date"):
            # Client-wise future date -> add calendar event if none exists for that date/matter
            n_date = date.fromisoformat(row["next_date"])
            matter_obj.current_next_date = n_date
            if n_date >= ist_today():
                exists_ev = db.scalar(
                    select(CalendarEvent).where(
                        CalendarEvent.matter_id == matter_obj.id,
                        CalendarEvent.event_date == n_date,
                        CalendarEvent.status == "Active",
                    )
                )
                if exists_ev is None:
                    db.add(
                        CalendarEvent(
                            event_type="Court",
                            title=f"{matter_obj.title} ({matter_obj.court_forum})",
                            event_date=n_date,
                            is_all_day_or_session=True,
                            location=matter_obj.court_forum,
                            matter_id=matter_obj.id,
                            status="Active",
                        )
                    )

    summary = {
        "added_clients": added_clients,
        "added_matters": added_matters,
        "matched_matters": matched_matters,
        "added_hearings": added_hearings,
        "skipped_rows": skipped,
    }
    db.add(
        ImportBatch(
            import_type="excel",
            filename=filename,
            summary_json=json.dumps(summary),
            snapshot_path=str(snap) if snap else None,
            imported_by=actor,
        )
    )
    record_audit(db, actor=actor, entity_type="import", entity_id=filename, action="commit_excel", after=summary)
    db.commit()
    return summary
