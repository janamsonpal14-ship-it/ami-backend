from __future__ import annotations

import calendar
import io
import json
import re
from datetime import date, datetime, timedelta

from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.config as cfg
from app.models import CourtHoliday, ImportBatch
from app.services.audit_service import create_snapshot, record_audit

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_DATE_LONG_RE = re.compile(
    r"(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December),?\s+(\d{4})",
    re.IGNORECASE,
)


def _parse_long_date(day_s: str, month_s: str, year_s: str) -> date:
    return date(int(year_s), _MONTH_NAMES[month_s.lower()], int(day_s))


def parse_bhc_pdf_bytes(pdf_bytes: bytes, bench: str = "Principal Seat Bombay") -> dict:
    """Parse a Bombay High Court yearly calendar PDF into previewable holiday/vacation/working-day entries."""
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages_text = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n".join(pages_text)

    year_match = re.search(r"Calendar\s+for\s+the\s+year\s+(\d{4})", full_text, re.IGNORECASE)
    if year_match:
        year = int(year_match.group(1))
    else:
        any_year = re.search(r"\b(20\d{2})\b", full_text)
        year = int(any_year.group(1)) if any_year else date.today().year

    entries: list[dict] = []
    seen_keys: set[tuple[date, str, bool]] = set()
    warnings: list[str] = []

    def add_entry(d: date, title: str, category: str, is_closed: bool) -> None:
        clean_title = " ".join(title.split()).strip(" :-–—")
        if not clean_title:
            clean_title = category
        key = (d, clean_title, is_closed)
        if key not in seen_keys:
            seen_keys.add(key)
            entries.append(
                {
                    "calendar_date": str(d),
                    "year": d.year,
                    "title": clean_title,
                    "category": category,
                    "bench": bench,
                    "is_closed": is_closed,
                }
            )

    # 1. Extract Page 2 Vacations (Summer, Diwali, Christmas)
    vac_pattern = re.compile(
        r"(Summer|Diwali|Christmas)\s+Vacation\s*:\s*From\s+[^,]+,\s*"
        r"(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})\s+to\s+[^,]+,\s*(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})",
        re.IGNORECASE,
    )
    for m in vac_pattern.finditer(full_text):
        vac_name = f"{m.group(1).title()} Vacation"
        d_start_m = _DATE_LONG_RE.search(m.group(2))
        d_end_m = _DATE_LONG_RE.search(m.group(3))
        if d_start_m and d_end_m:
            start_d = _parse_long_date(*d_start_m.groups())
            end_d = _parse_long_date(*d_end_m.groups())
            cur = start_d
            while cur <= end_d:
                add_entry(cur, vac_name, vac_name, True)
                cur += timedelta(days=1)

    # 2. Extract Court Working Saturdays (Note 1 for Principal Seat & all benches)
    working_saturdays: set[date] = set()
    note1_match = re.search(
        r"Court\s+working\s+Saturdays.*?(?=\n\s*2\.\s*The\s+High\s+Court|$)",
        full_text,
        re.IGNORECASE | re.DOTALL,
    )
    if note1_match:
        for dm in _DATE_LONG_RE.finditer(note1_match.group(0)):
            wd = _parse_long_date(*dm.groups())
            working_saturdays.add(wd)
            add_entry(wd, "Court Working Saturday", "Working Saturday", False)

    # Bench-specific overrides if another bench is chosen
    if bench != "Principal Seat Bombay":
        bench_key = bench.split()[0]  # Nagpur, Aurangabad, Kolhapur, Goa
        bench_block = re.search(
            rf"{bench_key}.*?(?=\n\s*\d\.\s*The\s+High\s+Court|\*\*\*\*|$)",
            full_text,
            re.IGNORECASE | re.DOTALL,
        )
        if bench_block:
            for line in bench_block.group(0).splitlines():
                dates_in_line = list(_DATE_LONG_RE.finditer(line))
                if len(dates_in_line) >= 2:
                    work_d = _parse_long_date(*dates_in_line[0].groups())
                    obs_d = _parse_long_date(*dates_in_line[1].groups())
                    working_saturdays.add(work_d)
                    add_entry(work_d, f"Court Working Saturday ({bench})", "Working Saturday", False)
                    add_entry(obs_d, f"Observed Holiday ({bench})", "Public Holiday", True)

    # 3. Parse Page 1 Monthly Holiday Lists (row-by-row under each 4-month block)
    # In BHC PDFs, Page 1 has 3 rows of 4 months:
    # Row 1: JAN, FEB, MAR, APR | Row 2: MAY, JUN, JUL, AUG | Row 3: SEP, OCT, NOV, DEC
    page1 = pages_text[0] if pages_text else full_text
    # Extract known holiday captions after SATURDAY line in each block using date + title tokens
    blocks = re.split(r"SATURDAY[^\n]*\n", page1, flags=re.IGNORECASE)
    month_groups = [[1, 2, 3, 4], [5, 6, 7, 8], [9, 10, 11, 12]]

    # Also build a fallback line-based parser on the text after each SATURDAY block
    if len(blocks) >= 4:
        for idx, months in enumerate(month_groups):
            caption_chunk = blocks[idx + 1]
            # Stop before next year block header if present
            caption_chunk = re.split(r"\b20\d{2}\s+(?:MAY|SEPTEMBER)\b", caption_chunk)[0]
            caption_chunk = caption_chunk.split("..2/-")[0]
            # Check if pypdf preserved column text linearly (column-by-column or interleaved)
            # Extract lines matching 'DD[-DD] Title'
            # Since multi-column PDF text extraction can interleave or concatenate columns,
            # let's extract per-month using word coordinates if available, or robust regex heuristics.
            _extract_holidays_with_layout(reader.pages[0], year, add_entry)
            break
    else:
        _extract_holidays_with_layout(reader.pages[0], year, add_entry)

    # 4. Auto-populate Sundays and 2nd/4th Saturdays for `year` (excluding Working Saturdays)
    d = date(year, 1, 1)
    while d.year == year:
        if d.weekday() == 6:
            add_entry(d, "Sunday", "Sunday", True)
        elif d.weekday() == 5 and d not in working_saturdays:
            nth = (d.day - 1) // 7 + 1
            if nth in (2, 4):
                add_entry(d, f"{nth}th Saturday (Closed)", "Closed Saturday", True)
        d += timedelta(days=1)

    if not any(e["category"] == "Public Holiday" for e in entries):
        warnings.append(
            "No gazetted public holiday captions were automatically extracted from Page 1 text layout; "
            "vacations, working Saturdays, Sundays, and 2nd/4th Saturdays were extracted. You can add/edit holidays below before saving."
        )

    entries.sort(key=lambda e: (e["calendar_date"], e["is_closed"], e["title"]))
    return {
        "year": year,
        "bench": bench,
        "total_entries": len(entries),
        "public_holidays_count": sum(1 for e in entries if e["category"] == "Public Holiday"),
        "vacation_days_count": sum(1 for e in entries if "Vacation" in e["category"]),
        "working_saturdays_count": sum(1 for e in entries if e["category"] == "Working Saturday"),
        "warnings": warnings,
        "entries": entries,
    }


def _extract_holidays_with_layout(page, year: int, add_entry) -> None:
    """Use pypdf visitor_text to map x/y coordinates of holiday captions directly to the 4 month columns."""
    fragments: list[tuple[float, float, str]] = []

    def visitor(text, _cm, tm, _font_dict, _font_size):
        cleaned = text.strip()
        if cleaned:
            x = float(tm[4])
            y = float(tm[5])
            fragments.append((x, y, cleaned))

    try:
        page.extract_text(visitor_text=visitor)
    except Exception:
        return

    # Locate the 12 month headers (JANUARY .. DECEMBER) on Page 1 to know each column's (x, y)
    month_positions: list[tuple[int, float, float]] = []
    for x, y, txt in fragments:
        upper = txt.upper()
        for m_name, m_num in _MONTH_NAMES.items():
            if upper == m_name.upper():
                month_positions.append((m_num, x, y))

    if len(month_positions) < 12:
        return

    # Group fragments by y-row bands (months 1-4, 5-8, 9-12)
    # Sort month_positions by month number
    month_positions.sort(key=lambda t: t[0])
    # Compute x midpoints between the 4 columns using months 1..4
    col_xs = sorted([p[1] for p in month_positions[:4]])
    col_bounds = [
        (col_xs[0] - 80, (col_xs[0] + col_xs[1]) / 2),
        ((col_xs[0] + col_xs[1]) / 2, (col_xs[1] + col_xs[2]) / 2),
        ((col_xs[1] + col_xs[2]) / 2, (col_xs[2] + col_xs[3]) / 2),
        ((col_xs[2] + col_xs[3]) / 2, col_xs[3] + 200),
    ]

    # Row top y bounds: each 4-month row starts at month header y; holidays sit below SATURDAY row down to next row header
    row_y_tops = [
        max(p[2] for p in month_positions[0:4]),
        max(p[2] for p in month_positions[4:8]),
        max(p[2] for p in month_positions[8:12]),
    ]

    # Find "SATURDAY" y-positions for the 3 rows to isolate ONLY the holiday text below each grid
    sat_ys = sorted([y for _, y, t in fragments if t.upper() == "SATURDAY"], reverse=True)
    if len(sat_ys) < 3:
        return

    row_bands = [
        (row_y_tops[1] + 5, sat_ys[0] - 2),
        (row_y_tops[2] + 5, sat_ys[1] - 2),
        (0.0, sat_ys[2] - 2),
    ]

    for row_idx, (y_min, y_max) in enumerate(row_bands):
        for col_idx, (x_min, x_max) in enumerate(col_bounds):
            month_num = row_idx * 4 + col_idx + 1
            cell_frags = [
                (y, x, t)
                for (x, y, t) in fragments
                if x_min <= x < x_max and y_min <= y <= y_max and "..2/-" not in t
            ]
            if not cell_frags:
                continue
            # Sort top-to-bottom (descending y), then left-to-right (ascending x)
            cell_frags.sort(key=lambda item: (-round(item[0], 1), item[1]))
            cell_text = " ".join(item[2] for item in cell_frags)
            # Parse patterns like "26 Republic Day", "08-13 Diwali Holidays", "01 Maharashtra Day & Buddha Pournima"
            matches = list(re.finditer(r"(?<!\d)(\d{1,2})(?:-(\d{1,2}))?\s+([A-Za-z][^\d]{1,80}?)(?=(?:\s+\d{1,2}(?:-\d{1,2})?\s+[A-Za-z])|$)", cell_text))
            _, max_day = calendar.monthrange(year, month_num)
            for hm in matches:
                d1 = int(hm.group(1))
                d2 = int(hm.group(2)) if hm.group(2) else d1
                title = " ".join(hm.group(3).split())
                if not (1 <= d1 <= max_day and 1 <= d2 <= max_day and d1 <= d2):
                    continue
                if "diwali holiday" in title.lower():
                    cat = "Diwali Vacation"
                else:
                    cat = "Public Holiday"
                for day in range(d1, d2 + 1):
                    add_entry(date(year, month_num, day), title, cat, True)


def save_uploaded_bhc_pdf(filename: str, pdf_bytes: bytes) -> str:
    cfg.ensure_directories()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)
    target = cfg.BHC_CALENDARS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.write_bytes(pdf_bytes)
    return str(target)


def commit_bhc_calendar(
    db: Session,
    *,
    year: int,
    bench: str,
    source_file: str,
    entries: list[dict],
    actor: str,
) -> dict:
    """Snapshot DB, replace holidays for (year, bench), and record import batch."""
    snap = create_snapshot(f"bhc_cal_{year}")
    existing = list(
        db.scalars(
            select(CourtHoliday).where(CourtHoliday.year == year, CourtHoliday.bench == bench)
        ).all()
    )
    for row in existing:
        db.delete(row)
    db.flush()

    seen: set[tuple[date, str]] = set()
    inserted = 0
    for item in entries:
        d = date.fromisoformat(item["calendar_date"])
        title = item["title"].strip()[:200]
        if (d, title) in seen:
            continue
        seen.add((d, title))
        db.add(
            CourtHoliday(
                year=d.year,
                calendar_date=d,
                title=title,
                category=item["category"],
                bench=bench,
                is_closed=bool(item["is_closed"]),
                source_file=source_file,
            )
        )
        inserted += 1

    summary = {"year": year, "bench": bench, "inserted": inserted}
    db.add(
        ImportBatch(
            import_type="bhc_pdf",
            filename=source_file,
            summary_json=json.dumps(summary),
            snapshot_path=str(snap) if snap else None,
            imported_by=actor,
        )
    )
    record_audit(db, actor=actor, entity_type="court_holiday", entity_id=f"{year}:{bench}", action="import_bhc_pdf", after=summary)
    db.commit()
    return summary

