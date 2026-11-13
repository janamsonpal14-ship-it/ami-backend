from __future__ import annotations

import io
import json
import platform
import re
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, status
from pypdf import PdfReader
from sqlalchemy import desc, select
from sqlalchemy.orm import Session, selectinload

import app.config as cfg
from app.models import BareAct, BareActSection, ResearchConfig, ResearchQueryLog
from app.schemas import BareActCreate, BareActDetailOut, BareActOut, BareActSectionOut, ResearchAskRequest, ResearchConfigUpdate
from app.services.audit_service import record_audit
from app.utils.indian_format import ist_today

_MAX_ACT_BYTES = 25 * 1024 * 1024  # 25 MB

_VERIFICATION_NOTICE = (
    "VERIFICATION NOTICE: Search results are strictly grounded in your locally uploaded Bare Acts "
    "(zero third-party AI or external search APIs). Always verify against Official Gazette notifications "
    "and current amendments before citing provisions in Court."
)

# --- Roman <-> Arabic conversion for Orders (e.g. Order XXXIX <-> Order 39) ---
_ROMAN_VALS = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
_INT_TO_ROMAN_MAP = [
    (100, "C"),
    (90, "XC"),
    (50, "L"),
    (40, "XL"),
    (10, "X"),
    (9, "IX"),
    (5, "V"),
    (4, "IV"),
    (1, "I"),
]


def _roman_to_int(roman: str) -> int | None:
    s = roman.strip().upper()
    if not s or not re.fullmatch(r"[IVXLCDM]+", s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        val = _ROMAN_VALS[ch]
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total if 1 <= total <= 200 else None


def _int_to_roman(num: int) -> str | None:
    if not (1 <= num <= 200):
        return None
    out: list[str] = []
    n = num
    for val, sym in _INT_TO_ROMAN_MAP:
        while n >= val:
            out.append(sym)
            n -= val
    return "".join(out)


# --- Regexes for India Code OCR & standard Bare Acts ---
_EXPLICIT_SECTION_RE = re.compile(
    r"^(?:Section|Sec\.?|S\.|Article|Art\.?|Order\s+[0-9IVXLCDM]+[A-Z-]*\s+Rule|Rule)\s+([0-9A-Za-z-]+)\s*[.:\-—]+\s*(.+)$",
    re.IGNORECASE,
)

# Matches India Code body headings such as:
#   "1. Short title, commencement and extent. —(1) ..."
#   "2. Definitions.—In this Act..."
#   "2[10A. Power of Court to request any pleader to address it. —The Court..."
#   "1[20A. Service of summons by post. ] Rep. by..."
# Requires an em-dash '—' (or ':—') or explicit Rep./Omitted/Repealed marker so date hyphens like "1-2-1977" never false-match.
_INDIA_CODE_UNIT_RE = re.compile(
    r"^(?:\d{1,2}\s*\[\s*)?"
    r"([0-9]{1,4}[A-Z]{0,2})\.\s+"
    r"([A-Za-z\[][^—\n]{1,180}?)"
    r"(?:\s*(?:\]\s*)?(?:[:.])?\s*—\s*(.*)|"
    r"\s*\]\s*(?:Rep\.|Omitted|Repealed).*|"
    r"\s*\[(?:Omitted|Repealed)\.?\]\.?\s*(?:Rep\.|Omitted|Repealed)?.*)$"
)

_PART_RE = re.compile(r"^(PRELIMINARY|PART\s+[IVXLCDM0-9]+[A-Z]?|CHAPTER\s+[IVXLCDM0-9]+[A-Z]?|THE\s+(?:FIRST|SECOND|THIRD|FOURTH|FIFTH)\s+SCHEDULE|ANNEXURE\s+[IVXLCDM0-9]+)\b(.*)$")
_ORDER_RE = re.compile(r"^ORDER\s+([IVXLCDM]{1,8}(?:-[A-Z0-9]+|[A-Z])?|[0-9]{1,2}[A-Z]?)\s*$")
_APPENDIX_RE = re.compile(r"^APPENDIX\s*[—\-]?\s*([A-Z])\b\.?\s*(.*)$")

_FOOTNOTE_START_RE = re.compile(
    r"^\d{1,2}\.\s+(?:Subs\.|Ins\.|Omitted|Rep\.|Repealed|Added|Clause|Cl\.|Sub-section|Sub-rule|"
    r"Explanation\s+(?:ins\.|subs\.|omitted)|"
    r"The\s+(?:words?|figures?|brackets?|letters?)|For\s+[“\"‘a-z]|See\s+|Now\s+|Cf\.|This\s+Act\s+has\s+been|"
    r"Under\s+s\.|Re-?numbered|Modified|Amended|Substituted|Inserted|In\s+its\s+application|S\.\s*\d+|Sch\.)"
)

_STRUCTURAL_LINE_START_RE = re.compile(
    r"^(?:\d{1,2}\[)?(?:\([0-9]+[A-Z]?\)|\([a-z]{1,3}\)|\([ivxlcdm]+\)|"
    r"Provided\s+that\b|Provided\s+further\b|Provided\s+also\b|"
    r"Explanation(?:\s+[0-9IVX]+)?\s*[.—:-]|"
    r"Illustrations?\b|"
    r"Exception\b|"
    r"Rule\s+[0-9]+)"
)

_TOC_HEADER_RE = re.compile(r"^ARRANGEMENT\s+OF\s+SECTIONS$", re.IGNORECASE)
_LIST_HEADER_RE = re.compile(r"^(?:LIST\s+OF\s+AMENDING\s+ACTS|LIST\s+OF\s+ABBREVIATIONS\s+USED)$", re.IGNORECASE)
_ENACTMENT_START_RE = re.compile(
    r"(?:It\s+is\s+hereby\s+enacted\s+as\s+follows|Be\s+it\s+enacted\s+by)",
    re.IGNORECASE,
)

_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does", "for", "from",
    "how", "in", "into", "is", "it", "of", "on", "or", "shall", "that", "the", "their",
    "this", "to", "under", "what", "when", "where", "which", "who", "with", "would",
    "explain", "give", "principles", "provisions", "provision", "requirements", "about",
}


def _act_to_out(act: BareAct) -> BareActOut:
    return BareActOut(
        id=act.id,
        title=act.title,
        short_title=act.short_title,
        year=act.year,
        jurisdiction=act.jurisdiction,
        version_date=act.version_date,
        source_note=act.source_note,
        tags=act.tags,
        summary_text=act.summary_text,
        is_demo=act.is_demo,
        has_file=bool(act.file_path),
        page_count=act.page_count,
        section_count=act.section_count,
    )


def _clean_ocr_line(raw: str) -> str:
    """Clean OCR/pypdf artifacts from a single extracted PDF line."""
    line = raw.replace("\xa0", " ").replace("\r", "").strip()
    if not line:
        return ""
    # Collapse excessive multi-space gaps from justified print columns
    line = re.sub(r"[ \t]{2,}", " ", line)
    # Fix OCR space before hyphens in compounds / dates: "sub -section" -> "sub-section", "1 -2-1977" -> "1-2-1977"
    line = re.sub(r"([A-Za-z0-9])\s+-([A-Za-z0-9])", r"\1-\2", line)
    line = re.sub(r"(\d)-\s+(\d)", r"\1-\2", line)
    # Fix spaced brackets & punctuation caused by OCR
    line = re.sub(r"\s+([,.;:?!])", r"\1", line)
    line = line.replace("[ ", "[").replace(" ]", "]")
    # Clean stray space inside "1 [words" or "R U L E S"
    line = re.sub(r"\bR\s*U\s*L\s*E\s*S\b", "RULES", line)
    line = re.sub(r"(\d)\s+\[", r"\1[", line)
    return line.strip()


def _split_page_body_and_footnotes(raw_text: str) -> tuple[list[str], list[str]]:
    """Separate main statutory lines from bottom-of-page amendment footnotes."""
    raw_lines = raw_text.splitlines()
    # Trim leading standalone page number (e.g. "34 ", "  88 ")
    while raw_lines and not raw_lines[0].strip():
        raw_lines.pop(0)
    if raw_lines and re.fullmatch(r"\d{1,4}", raw_lines[0].strip()):
        raw_lines.pop(0)
    # Trim trailing standalone page number if present
    while raw_lines and not raw_lines[-1].strip():
        raw_lines.pop()
    if raw_lines and re.fullmatch(r"\d{1,4}", raw_lines[-1].strip()):
        raw_lines.pop()

    body_lines: list[str] = []
    footnote_lines: list[str] = []
    in_footnotes = False

    for raw in raw_lines:
        cleaned = _clean_ocr_line(raw)
        if not cleaned:
            if not in_footnotes and body_lines and body_lines[-1] != "":
                body_lines.append("")
            continue
        if cleaned in ("________", "______", "_____", "____"):
            continue
        if in_footnotes:
            footnote_lines.append(cleaned)
            continue
        if _FOOTNOTE_START_RE.match(cleaned):
            in_footnotes = True
            footnote_lines.append(cleaned)
            continue
        body_lines.append(cleaned)

    while body_lines and body_lines[-1] == "":
        body_lines.pop()

    return body_lines, footnote_lines


def _join_wrapped_lines(lines: list[str]) -> str:
    """Reflow hard-wrapped OCR lines into clean structured statutory paragraphs."""
    paragraphs: list[str] = []
    current = ""

    _compound_prefixes = {
        "sub", "non", "pre", "ex", "co", "re", "de", "semi", "self", "cross",
        "part", "inter", "intra", "quasi", "counter", "decree", "judgment",
        "subject", "court", "state", "central", "vice", "half", "full",
    }

    for line in lines:
        if not line:
            if current:
                paragraphs.append(current.strip())
                current = ""
            continue

        is_structural = bool(_STRUCTURAL_LINE_START_RE.match(line))
        if is_structural or not current:
            if current:
                paragraphs.append(current.strip())
            current = line
            continue

        # Handle word wrapped across line ending with hyphen
        if current.endswith("-") and not current.endswith("—"):
            last_word = re.split(r"[\s(\[\"]+", current[:-1].lower())[-1]
            if last_word in _compound_prefixes:
                current = current + line
            else:
                current = current[:-1] + line
        else:
            current = f"{current} {line}"

    if current:
        paragraphs.append(current.strip())

    # Format clauses cleanly (indent sub-clauses slightly for visual readability)
    formatted: list[str] = []
    for p in paragraphs:
        if re.match(r"^\([a-z]{1,2}\)\s", p):
            formatted.append(f"  {p}")
        elif re.match(r"^\([ivxlcdm]+\)\s", p):
            formatted.append(f"    {p}")
        else:
            formatted.append(p)
    return "\n\n".join(formatted)


def _format_footnotes(footnotes: list[str]) -> str:
    """Group wrapped footnote lines so each numbered amendment note sits on one line."""
    if not footnotes:
        return ""
    grouped: list[str] = []
    for line in footnotes:
        if re.match(r"^\d{1,2}\.\s+", line) or re.match(r"^\d{0,2}\*\s+\*", line) or not grouped:
            grouped.append(line)
        else:
            if grouped[-1].endswith("-"):
                grouped[-1] = grouped[-1][:-1] + line
            else:
                grouped[-1] = f"{grouped[-1]} {line}"
    return "\n".join(f"• {g}" for g in grouped if g.strip())


def extract_bare_act_pdf_metadata(pages: list[tuple[int, str]]) -> dict[str, str | int | None]:
    """Detect Act title, year, act number, and 'As on' date from opening PDF pages."""
    front_text = "\n".join(txt for _, txt in pages[:5] if txt)
    cleaned_lines = [_clean_ocr_line(ln) for ln in front_text.splitlines() if _clean_ocr_line(ln)]

    title: str | None = None
    year: int | None = None
    version_date: str | None = None
    act_no: str | None = None

    for ln in cleaned_lines[:30]:
        if re.fullmatch(r"\d{1,4}", ln):
            continue
        if not title and re.search(r"\b(?:Code|Act|Rules|Regulation|Constitution|Bharatiya)\b", ln, re.IGNORECASE):
            if "ARRANGEMENT" not in ln.upper() and "AMENDING" not in ln.upper() and "ABBREVIATION" not in ln.upper():
                candidate = re.sub(r"\s+", " ", ln).strip()
                if len(candidate) >= 6:
                    title = candidate
        m_act = re.search(r"\(?\s*ACT\s+NO\.?\s+([0-9]+)\s+OF\s+(\d{4})\s*\)?", ln, re.IGNORECASE)
        if m_act:
            act_no = f"Act No. {m_act.group(1)} of {m_act.group(2)}"
            year = int(m_act.group(2))
        m_as_on = re.search(r"\[?\s*As\s+on\s+(?:the\s+)?([^\]\n]{4,40})\s*\]?", ln, re.IGNORECASE)
        if m_as_on and not version_date:
            version_date = f"As on {m_as_on.group(1).strip()}"

    if year is None and title:
        m_y = re.search(r"\b(1[89]\d{2}|20\d{2})\b", title)
        if m_y:
            year = int(m_y.group(1))

    short_title: str | None = None
    if title and "code of civil procedure" in title.lower():
        short_title = "CPC"
    elif title and "code of criminal procedure" in title.lower():
        short_title = "CrPC"
    elif title and "indian contract act" in title.lower():
        short_title = "Contract Act"
    elif title and "negotiable instruments" in title.lower():
        short_title = "NI Act"
    elif title and "arbitration and conciliation" in title.lower():
        short_title = "Arbitration Act"

    return {
        "title": title,
        "short_title": short_title,
        "year": year,
        "version_date": version_date,
        "act_no": act_no,
    }


def _extract_sections_from_pages(pages: list[tuple[int, str]]) -> list[dict]:
    """Parse OCR India Code PDFs & structured Bare Acts cleanly across page boundaries."""
    sections: list[dict] = []

    current_part = ""
    current_order = ""
    current_subhead = ""

    in_front_matter = False
    in_toc = False
    saw_body = False

    front_buffer: list[str] = []
    front_start_page = 1
    toc_buffer: list[str] = []
    toc_start_page = 1

    active: dict | None = None
    active_lines: list[str] = []
    active_footnotes: list[str] = []

    def _flush_front_matter() -> None:
        nonlocal front_buffer
        if not front_buffer:
            return
        body = "\n".join(front_buffer[:120]).strip()
        if body:
            sections.append(
                {
                    "section_no": "Preamble / Index",
                    "heading": "Title, Amending Acts & Abbreviations",
                    "page_number": front_start_page,
                    "body_text": body,
                }
            )
        front_buffer = []

    def _flush_toc() -> None:
        nonlocal toc_buffer
        if not toc_buffer:
            return
        # Summarize TOC into manageable index entry so it stays browseable without drowning section searches
        preview = "\n".join(toc_buffer[:220]).strip()
        if len(toc_buffer) > 220:
            preview += f"\n\n[... {len(toc_buffer) - 220} additional Arrangement of Sections entries omitted; use Section Search to jump directly ...]"
        sections.append(
            {
                "section_no": "TOC",
                "heading": "Arrangement of Sections",
                "page_number": toc_start_page,
                "body_text": preview,
            }
        )
        toc_buffer = []

    def _flush_active() -> None:
        nonlocal active, active_lines, active_footnotes
        if active is None:
            active_lines = []
            active_footnotes = []
            return
        body = _join_wrapped_lines(active_lines)
        fn_block = _format_footnotes(active_footnotes)
        if fn_block:
            body = f"{body}\n\nAmendments / Footnotes:\n{fn_block}" if body else f"Amendments / Footnotes:\n{fn_block}"
        if body.strip() or active["heading"].strip():
            sections.append(
                {
                    "section_no": active["section_no"][:80],
                    "heading": active["heading"][:300],
                    "page_number": active["page_number"],
                    "body_text": body.strip() or active["heading"],
                }
            )
        active = None
        active_lines = []
        active_footnotes = []

    def _format_unit_label(num_token: str) -> str:
        if current_order.upper().startswith("ORDER "):
            ord_suffix = current_order.split(None, 1)[1].upper()
            return f"Order {ord_suffix} Rule {num_token}"
        if current_order.upper().startswith("ANNEXURE"):
            ann_suffix = current_order.split(None, 1)[1].upper() if len(current_order.split()) > 1 else ""
            return f"Annexure {ann_suffix} Para {num_token}".strip()
        if current_part.upper().startswith("APPENDIX"):
            return f"{current_part} Item {num_token}"
        return f"Section {num_token}"

    for page_no, raw_text in pages:
        body_lines, page_footnotes = _split_page_body_and_footnotes(raw_text)
        if not body_lines and not page_footnotes:
            continue

        # Track whether this page contains explicit TOC / Title Preamble / Statutory Body triggers
        for line in body_lines:
            if not line:
                if active is not None and active_lines and active_lines[-1] != "":
                    active_lines.append("")
                continue

            if not saw_body:
                if _LIST_HEADER_RE.match(line):
                    in_front_matter = True
                    in_toc = False
                    front_start_page = front_start_page or page_no
                    front_buffer.append(line)
                    continue
                if _TOC_HEADER_RE.match(line):
                    _flush_front_matter()
                    in_front_matter = False
                    in_toc = True
                    toc_start_page = page_no
                    toc_buffer.append(line)
                    continue
                if in_toc and not _ENACTMENT_START_RE.search(line):
                    if not (_INDIA_CODE_UNIT_RE.match(line) or _EXPLICIT_SECTION_RE.match(line)):
                        toc_buffer.append(line)
                        continue
                # Detect transition from TOC/Title Preamble into actual enacted body
                if _ENACTMENT_START_RE.search(line) or _EXPLICIT_SECTION_RE.match(line) or _INDIA_CODE_UNIT_RE.match(line):
                    _flush_front_matter()
                    _flush_toc()
                    in_front_matter = False
                    in_toc = False
                    saw_body = True
                    current_part = ""
                    current_order = ""
                    current_subhead = ""
                elif in_front_matter or (len(pages) > 3 and page_no <= 2):
                    in_front_matter = True
                    front_start_page = front_start_page or page_no
                    front_buffer.append(line)
                    continue

            # Inside statutory body: track Parts, Schedules, Orders, Appendices
            m_part = _PART_RE.match(line)
            if m_part and len(line) <= 120 and "—" not in line:
                p_name = m_part.group(1).strip()
                if p_name.upper().startswith("THE FIRST SCHEDULE"):
                    current_part = "First Schedule"
                elif p_name.upper().startswith("ANNEXURE"):
                    current_order = p_name.title()
                    current_part = p_name.title()
                else:
                    rest = m_part.group(2).strip(" —-:")
                    current_part = f"{p_name.title()}{': ' + rest if rest else ''}"
                    if not p_name.upper().startswith(("CHAPTER", "PRELIMINARY")):
                        current_order = ""
                if active is None:
                    active = {
                        "section_no": p_name.title(),
                        "heading": current_part,
                        "page_number": page_no,
                    }
                else:
                    active_lines.append("")
                    active_lines.append(line)
                continue

            m_order = _ORDER_RE.match(line)
            if m_order and len(line) <= 90 and "—" not in line:
                ord_tok = m_order.group(1).upper()
                _flush_active()
                current_order = f"ORDER {ord_tok}"
                current_subhead = ""
                active = {
                    "section_no": f"Order {ord_tok}",
                    "heading": f"Order {ord_tok} Overview",
                    "page_number": page_no,
                }
                active_lines = [line]
                continue

            m_app = _APPENDIX_RE.match(line)
            if m_app and len(line) <= 120 and "—" not in line:
                _flush_active()
                app_id = m_app.group(1)
                current_part = f"Appendix {app_id}"
                current_order = ""
                active = {
                    "section_no": f"Appendix {app_id}",
                    "heading": m_app.group(2).strip() or f"Appendix {app_id}",
                    "page_number": page_no,
                }
                active_lines = [line]
                continue

            # Detect new explicit Section/Rule/Order Rule heading
            m_exp = _EXPLICIT_SECTION_RE.match(line)
            if m_exp:
                _flush_active()
                prefix_tok = line.split()[0].lower()
                raw_num = m_exp.group(1).strip()
                rest_text = m_exp.group(2).strip()
                heading_part = rest_text
                body_start = ""
                if "—" in rest_text:
                    h_split = rest_text.split("—", 1)
                    heading_part = h_split[0].strip().rstrip(".")
                    body_start = h_split[1].strip()
                if prefix_tok.startswith("order"):
                    m_o = re.match(r"^Order\s+([0-9IVXLCDM]+[A-Z-]*)\s+Rule\s+([0-9A-Za-z-]+)", line, re.IGNORECASE)
                    sec_label = f"Order {m_o.group(1).upper()} Rule {m_o.group(2)}" if m_o else f"Rule {raw_num}"
                elif prefix_tok.startswith("art"):
                    sec_label = f"Article {raw_num}"
                elif prefix_tok.startswith("rule"):
                    sec_label = _format_unit_label(raw_num) if current_order else f"Rule {raw_num}"
                else:
                    sec_label = f"Section {raw_num}"
                active = {
                    "section_no": sec_label,
                    "heading": heading_part or sec_label,
                    "page_number": page_no,
                }
                if body_start:
                    active_lines = [body_start]
                elif not "—" in rest_text:
                    active_lines = [line]
                else:
                    active_lines = []
                continue

            # Detect India Code numbered section/rule ("149. Power to make up...—Where..." or "2[10A. ...")
            m_ic = _INDIA_CODE_UNIT_RE.match(line)
            if m_ic:
                _flush_active()
                num_tok = m_ic.group(1).strip()
                raw_heading = m_ic.group(2).strip().rstrip(".")
                first_clause = (m_ic.group(3) or "").strip()
                sec_label = _format_unit_label(num_tok)
                # Keep bracketed omission note if whole heading is "[Omitted]" or "Rep. by..."
                if not first_clause and ("Rep." in line or "Omitted" in line or "Repealed" in line):
                    idx_sep = line.find("]")
                    first_clause = line[idx_sep + 1 :].strip() if idx_sep != -1 else line

                ctx_parts = [p for p in (current_part, current_subhead) if p and p.upper() not in sec_label.upper()]
                heading_full = raw_heading.lstrip("[")
                if ctx_parts and current_order:
                    heading_full = f"{heading_full}"
                active = {
                    "section_no": sec_label,
                    "heading": heading_full,
                    "page_number": page_no,
                }
                if ctx_parts:
                    active_lines = [f"[{ ' · '.join(ctx_parts) }]"]
                else:
                    active_lines = []
                if first_clause:
                    active_lines.append(first_clause)
                continue

            # Otherwise append to current active section (or open a page-level fallback chunk)
            if active is None:
                if line in ("THE CODE OF CIVIL PROCEDURE, 1908",) or len(line) < 120 and line.isupper():
                    current_subhead = line.title()
                    continue
                active = {
                    "section_no": current_order.title() if current_order else f"Page {page_no}",
                    "heading": current_subhead or current_part or f"Page {page_no} Text",
                    "page_number": page_no,
                }
                active_lines = [line]
            else:
                # Track if Order title was immediately followed by an ALL-CAPS topic title
                if active["heading"].endswith("Overview") and len(active_lines) <= 2 and line.isupper() and len(line) > 4:
                    active["heading"] = line.title()
                active_lines.append(line)

        if page_footnotes and active is not None:
            active_footnotes.extend(page_footnotes)

    _flush_front_matter()
    _flush_toc()
    _flush_active()

    # Fallback: if text had no recognized structure at all (e.g., short plain-text note)
    if not sections:
        for page_no, raw_text in pages:
            cleaned = "\n".join(_clean_ocr_line(ln) for ln in raw_text.splitlines() if _clean_ocr_line(ln)).strip()
            if cleaned:
                sections.append(
                    {
                        "section_no": f"Page {page_no}",
                        "heading": f"Page {page_no} Text",
                        "page_number": page_no,
                        "body_text": cleaned,
                    }
                )

    return sections


def list_bare_acts(db: Session, query: str | None = None) -> list[BareActOut]:
    acts = list(db.scalars(select(BareAct).order_by(BareAct.is_demo, BareAct.title)).all())
    if query and query.strip():
        needle = query.lower().strip()
        acts = [
            a
            for a in acts
            if needle in f"{a.title} {a.short_title or ''} {a.tags or ''} {a.summary_text or ''} {a.year or ''}".lower()
        ]
    return [_act_to_out(a) for a in acts]


def create_bare_act_from_text(db: Session, payload: BareActCreate, actor: str) -> BareActDetailOut:
    act = BareAct(
        title=payload.title.strip(),
        short_title=payload.short_title.strip() if payload.short_title else None,
        year=payload.year,
        jurisdiction=payload.jurisdiction.strip() or "India",
        version_date=payload.version_date,
        source_note=payload.source_note or "Curated local text entry",
        tags=payload.tags,
        summary_text=payload.summary_text,
        is_demo=False,
    )
    db.add(act)
    db.flush()

    sections_data = _extract_sections_from_pages([(1, payload.full_text or payload.summary_text or "")])
    db.add_all([BareActSection(act_id=act.id, **s) for s in sections_data])
    act.page_count = 1 if (payload.full_text or payload.summary_text) else 0
    act.section_count = len(sections_data)

    record_audit(db, actor=actor, entity_type="bare_act", entity_id=act.id, action="create", after={"title": act.title})
    db.commit()
    return get_bare_act_detail(db, act.id)


def upload_bare_act_file(
    db: Session,
    *,
    title: str,
    short_title: str | None,
    year: int | None,
    jurisdiction: str,
    version_date: str | None,
    source_note: str | None,
    tags: str | None,
    filename: str,
    file_bytes: bytes,
    actor: str,
) -> BareActDetailOut:
    if len(file_bytes) > _MAX_ACT_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Bare Act file exceeds 25 MB limit.")

    ext = Path(filename).suffix.lower()
    if ext not in (".pdf", ".txt"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Please upload a .pdf or .txt Bare Act file.",
        )

    pages: list[tuple[int, str]] = []
    if ext == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(file_bytes))
            for i, page in enumerate(reader.pages, start=1):
                pages.append((i, page.extract_text() or ""))
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Could not read PDF file.")
    else:
        text = file_bytes.decode("utf-8", errors="replace")
        pages.append((1, text))

    sections_data = _extract_sections_from_pages(pages)
    if not sections_data:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No searchable text could be extracted from the uploaded file. Ensure the PDF has an OCR text layer.",
        )

    detected = extract_bare_act_pdf_metadata(pages) if ext == ".pdf" else {}
    raw_title = title.strip()
    stem = Path(filename).stem
    # If title omitted or left as an opaque India Code filename (e.g. A190805), prefer parsed official Act title
    if (not raw_title or (raw_title == stem and re.fullmatch(r"[A-Za-z]\d{4,}", raw_title))) and detected.get("title"):
        final_title = str(detected["title"])
    else:
        final_title = raw_title or str(detected.get("title") or stem)

    final_short = (short_title.strip() if short_title else None) or (str(detected["short_title"]) if detected.get("short_title") else None)
    final_year = year if year is not None else (int(detected["year"]) if detected.get("year") else None)
    final_version = (
        version_date.strip()
        if version_date and version_date.strip() and "2026 Verified Local Upload" not in version_date
        else (str(detected["version_date"]) if detected.get("version_date") else (version_date or "Uploaded PDF/Text"))
    )

    cfg.ensure_directories()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:80]
    target = cfg.BARE_ACTS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_name}"
    target.write_bytes(file_bytes)

    first_substantive = next(
        (s for s in sections_data if s["section_no"] not in ("Preamble / Index", "TOC")),
        sections_data[0],
    )
    preview_excerpt = f"{first_substantive['section_no']} — {first_substantive['body_text'][:220].replace(chr(10), ' ').strip()}"

    act = BareAct(
        title=final_title,
        short_title=final_short,
        year=final_year,
        jurisdiction=jurisdiction.strip() or "India",
        version_date=final_version,
        source_note=source_note or (f"{detected.get('act_no')} • {Path(filename).name}" if detected.get("act_no") else f"Uploaded local file: {Path(filename).name}"),
        tags=tags,
        summary_text=preview_excerpt,
        file_path=str(target),
        page_count=len(pages),
        section_count=len(sections_data),
        is_demo=False,
    )
    db.add(act)
    db.flush()

    db.add_all([BareActSection(act_id=act.id, **s) for s in sections_data])

    record_audit(
        db,
        actor=actor,
        entity_type="bare_act",
        entity_id=act.id,
        action="upload",
        after={"title": act.title, "pages": act.page_count, "sections": act.section_count},
    )
    db.commit()
    return get_bare_act_detail(db, act.id)


def _resolve_stored_act_file(act: BareAct) -> Path:
    if not act.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Original PDF/text file is not attached to this entry.")
    p = Path(act.file_path)
    if not p.exists():
        p = cfg.BARE_ACTS_DIR / p.name
    if not p.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stored Bare Act file could not be found on disk.")
    return p


def get_bare_act_file(db: Session, act_id: int) -> tuple[Path, str, str]:
    act = db.get(BareAct, act_id)
    if act is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bare Act not found.")
    path = _resolve_stored_act_file(act)
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
    return path, media_type, path.name


def get_bare_act_page_text(db: Session, act_id: int, page_number: int) -> dict:
    act = db.get(BareAct, act_id)
    if act is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bare Act not found.")
    path = _resolve_stored_act_file(act)
    if path.suffix.lower() != ".pdf":
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"act_id": act.id, "page_number": 1, "total_pages": 1, "text": text}

    reader = PdfReader(str(path))
    total = len(reader.pages)
    if page_number < 1 or page_number > total:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Page {page_number} out of range (1..{total}).")
    raw_text = reader.pages[page_number - 1].extract_text() or ""
    return {"act_id": act.id, "page_number": page_number, "total_pages": total, "text": raw_text}


_SWIFT_RENDER_SOURCE = r"""
import Foundation
import PDFKit
import AppKit

let args = CommandLine.arguments
guard args.count >= 4,
      let pageNum = Int(args[2]), pageNum >= 1 else {
    fputs("Usage: pdf_page_render <pdf> <page_1_based> <out_png>\n", stderr)
    exit(1)
}
let url = URL(fileURLWithPath: args[1])
guard let doc = PDFDocument(url: url), pageNum <= doc.pageCount,
      let page = doc.page(at: pageNum - 1) else {
    fputs("Page not found\n", stderr)
    exit(2)
}
let bounds = page.bounds(for: .mediaBox)
let scale: CGFloat = 1.75
let size = NSSize(width: max(1, bounds.width * scale), height: max(1, bounds.height * scale))
let img = NSImage(size: size)
img.lockFocus()
if let ctx = NSGraphicsContext.current?.cgContext {
    ctx.setFillColor(NSColor.white.cgColor)
    ctx.fill(CGRect(origin: .zero, size: size))
    ctx.scaleBy(x: scale, y: scale)
    page.draw(with: .mediaBox, to: ctx)
}
img.unlockFocus()
guard let tiff = img.tiffRepresentation,
      let rep = NSBitmapImageRep(data: tiff),
      let png = rep.representation(using: .png, properties: [:]) else {
    exit(3)
}
try png.write(to: URL(fileURLWithPath: args[3]))
"""


def _ensure_pdf_renderer_binary() -> Path | None:
    if platform.system() != "Darwin":
        return None
    cfg.ensure_directories()
    bin_path = cfg.CACHE_DIR / "pdf_page_render"
    if bin_path.exists():
        return bin_path
    src_path = cfg.CACHE_DIR / "pdf_page_render.swift"
    src_path.write_text(_SWIFT_RENDER_SOURCE, encoding="utf-8")
    try:
        subprocess.run(
            ["swiftc", "-O", str(src_path), "-o", str(bin_path)],
            check=True,
            capture_output=True,
            timeout=30,
        )
        return bin_path
    except Exception:
        return None


def render_bare_act_pdf_page(db: Session, act_id: int, page_number: int) -> bytes:
    """Render an exact original PDF page to PNG (cached locally) for side-by-side verification."""
    act = db.get(BareAct, act_id)
    if act is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bare Act not found.")
    path = _resolve_stored_act_file(act)
    if path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Page image rendering is only supported for PDF Bare Acts.")
    if page_number < 1 or (act.page_count > 0 and page_number > act.page_count):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Page {page_number} does not exist.")

    pages_cache = cfg.CACHE_DIR / "pdf_pages"
    pages_cache.mkdir(parents=True, exist_ok=True)
    cache_key = f"act_{act.id}_{path.stat().st_mtime_ns}_p{page_number}.png"
    cached_png = pages_cache / cache_key
    if cached_png.exists():
        return cached_png.read_bytes()

    renderer = _ensure_pdf_renderer_binary()
    if renderer is None:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="PDF image renderer unavailable on this host; download Original PDF instead.",
        )
    try:
        subprocess.run(
            [str(renderer), str(path), str(page_number), str(cached_png)],
            check=True,
            capture_output=True,
            timeout=15,
        )
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Could not render PDF page {page_number}: {exc}")

    return cached_png.read_bytes()


def get_bare_act_detail(db: Session, act_id: int) -> BareActDetailOut:
    act = db.scalar(select(BareAct).options(selectinload(BareAct.sections)).where(BareAct.id == act_id))
    if act is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bare Act not found.")
    base = _act_to_out(act)
    return BareActDetailOut(
        **base.model_dump(),
        sections=[
            BareActSectionOut(
                id=s.id,
                act_id=s.act_id,
                section_no=s.section_no,
                heading=s.heading,
                page_number=s.page_number,
                body_text=s.body_text,
            )
            for s in act.sections
        ],
    )


def delete_bare_act(db: Session, act_id: int, actor: str) -> dict:
    act = db.get(BareAct, act_id)
    if act is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bare Act not found.")
    if act.file_path:
        p = Path(act.file_path)
        if not p.exists():
            p = cfg.BARE_ACTS_DIR / p.name
        p.unlink(missing_ok=True)
    title = act.title
    db.delete(act)
    record_audit(db, actor=actor, entity_type="bare_act", entity_id=act_id, action="delete", before={"title": title})
    db.commit()
    return {"status": "ok", "deleted_id": act_id}


def _make_snippet(body: str, tokens: list[str], max_len: int = 340) -> str:
    clean = " ".join(body.split())
    if len(clean) <= max_len:
        return clean
    lower = clean.lower()
    pos = -1
    for tok in tokens:
        if len(tok) < 3:
            continue
        idx = lower.find(tok)
        if idx != -1:
            pos = idx
            break
    if pos == -1:
        return clean[:max_len] + "..."
    start = max(0, pos - 70)
    end = min(len(clean), start + max_len)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return f"{prefix}{clean[start:end]}{suffix}"


def _extract_query_refs_and_tokens(raw: str) -> tuple[list[str], set[str], set[str]]:
    """Extract normalized statutory section/order/rule targets and meaningful search keywords."""
    lower = raw.lower()
    order_targets: set[str] = set()
    rule_targets: set[str] = set()
    section_targets: set[str] = set()

    for m in re.finditer(r"\border\s+([0-9]+[a-z]?|[ivxlcdm]+)\b", lower):
        tok = m.group(1)
        if tok.isdigit():
            rom = _int_to_roman(int(tok))
            order_targets.add(f"order {tok}")
            if rom:
                order_targets.add(f"order {rom.lower()}")
        else:
            val = _roman_to_int(tok)
            order_targets.add(f"order {tok}")
            if val:
                order_targets.add(f"order {val}")

    for m in re.finditer(r"\brule\s+([0-9]+[a-z]?)\b", lower):
        rule_targets.add(f"rule {m.group(1)}")

    for m in re.finditer(r"\b(?:section|sec\.?|s\.|article|art\.?)\s*([0-9]+[a-z]?)\b", lower):
        section_targets.add(m.group(1))

    # Standalone numeric tokens like "138" or "148a"
    for m in re.finditer(r"\b([0-9]{1,4}[a-z]?)\b", lower):
        tok = m.group(1)
        if tok not in ("1908", "1881", "1872", "1996", "2026"):
            section_targets.add(tok)

    raw_tokens = [t.strip(".,;:?!()[]{}\"'") for t in re.split(r"\s+", lower)]
    tokens = [t for t in raw_tokens if t and t not in _STOPWORDS and (len(t) >= 2 or t.isdigit())]
    if not tokens:
        tokens = [lower.strip()]

    target_phrases = set(order_targets) | set(rule_targets)
    for sec_tok in section_targets:
        target_phrases.add(f"section {sec_tok}")
    return tokens, target_phrases, section_targets


def search_bare_acts_and_sections(
    db: Session,
    query: str,
    *,
    act_ids: list[int] | None = None,
    limit: int = 20,
) -> dict:
    raw = query.strip()
    if not raw:
        return {"query": "", "hits": []}

    tokens, target_phrases, section_targets = _extract_query_refs_and_tokens(raw)

    stmt = select(BareActSection).join(BareAct).options(selectinload(BareActSection.act))
    if act_ids:
        stmt = stmt.where(BareActSection.act_id.in_(act_ids))
    all_sections = list(db.scalars(stmt).all())

    scored: list[tuple[int, BareActSection]] = []
    for sec in all_sections:
        sec_no_l = sec.section_no.lower()
        head_l = sec.heading.lower()
        body_l = sec.body_text.lower()
        hay_sec = f"{sec_no_l} {head_l} {body_l}"
        hay_act = f"{sec.act.title} {sec.act.short_title or ''} {sec.act.tags or ''}".lower()

        score = 0
        if raw.lower() in hay_sec:
            score += 24
        if raw.lower() in sec_no_l or raw.lower() in head_l:
            score += 20

        # Direct statutory citation match (e.g. "order 39" <-> "order xxxix", "section 138", "rule 1")
        for phrase in target_phrases:
            if phrase == sec_no_l or f"{phrase} " in f"{sec_no_l} ":
                score += 40
            elif phrase in sec_no_l or phrase in head_l:
                score += 28

        # Order + Rule combined match (e.g. "order xxxix rule 1")
        for ord_tok in [p for p in target_phrases if p.startswith("order ")]:
            for rule_tok in [p for p in target_phrases if p.startswith("rule ")]:
                if ord_tok in sec_no_l and re.search(rf"\b{re.escape(rule_tok)}\b", sec_no_l):
                    score += 65

        for sec_tok in section_targets:
            if sec_no_l in (sec_tok, f"section {sec_tok}", f"article {sec_tok}", f"rule {sec_tok}"):
                score += 45

        matched_kw = 0
        for tok in tokens:
            if tok in sec_no_l or tok in head_l:
                score += 10
                matched_kw += 1
            elif tok in body_l:
                score += 5
                matched_kw += 1
            elif tok in hay_act:
                score += 2

        if len(tokens) > 1 and matched_kw == len(tokens):
            score += 15

        if score <= 0:
            continue

        # Demote Preamble / Index & Arrangement of Sections (TOC) below actual substantive statutory provisions
        if sec_no_l in ("toc", "preamble / index"):
            score = max(1, score // 4)

        scored.append((score, sec))

    scored.sort(key=lambda item: (-item[0], item[1].act.title, item[1].page_number, item[1].id))
    hits = [
        {
            "act_id": sec.act_id,
            "act_title": sec.act.title,
            "short_title": sec.act.short_title,
            "year": sec.act.year,
            "version_date": sec.act.version_date,
            "source_note": sec.act.source_note,
            "section_id": sec.id,
            "section_no": sec.section_no,
            "heading": sec.heading,
            "page_number": sec.page_number,
            "snippet": _make_snippet(sec.body_text, tokens),
            "body_text": sec.body_text,
            "score": sc,
        }
        for sc, sec in scored[:limit]
    ]
    return {"query": raw, "hits": hits}


def _get_or_create_research_config(db: Session) -> ResearchConfig:
    cfg_row = db.scalar(select(ResearchConfig))
    if cfg_row is None:
        cfg_row = ResearchConfig()
        db.add(cfg_row)
        db.flush()
    today = ist_today()
    if cfg_row.last_query_date != today:
        cfg_row.queries_today = 0
        cfg_row.last_query_date = today
        db.flush()
    return cfg_row


def get_research_status(db: Session) -> dict:
    c = _get_or_create_research_config(db)
    db.commit()
    return {
        "enabled": False,
        "ai_enabled": False,
        "provider": "local_bare_acts",
        "model_name": None,
        "endpoint_url": None,
        "has_api_key": False,
        "api_key_masked": "",
        "max_daily_queries": c.max_daily_queries,
        "queries_today": c.queries_today,
        "disclaimer_accepted": False,
        "mode": "Strict Uploaded Bare Acts Search",
        "message": (
            "AMICO Legal Search operates strictly offline on your uploaded local Bare Acts PDFs & text entries. "
            "No third-party AI or external search APIs are used."
        ),
        "confidentiality_guarantee": (
            "Strict Bare Acts Only & DB Isolation: Legal Search queries ONLY BareAct & BareActSection tables. "
            "Zero third-party AI, external web search, or client/matter/billing records are ever accessed."
        ),
    }


def update_research_config(db: Session, payload: ResearchConfigUpdate, actor: str) -> dict:
    """Reject third-party AI enablement: AMICO Legal Search strictly uses uploaded Bare Acts only."""
    if payload.ai_enabled or (payload.provider and payload.provider != "local_bare_acts"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Third-party AI providers and external search APIs are disabled. Search operates strictly on uploaded Bare Acts.",
        )
    c = _get_or_create_research_config(db)
    c.ai_enabled = False
    c.provider = "local_bare_acts"
    record_audit(db, actor=actor, entity_type="research_config", entity_id=c.id, action="update", after={"provider": "local_bare_acts"})
    db.commit()
    return get_research_status(db)


def ask_legal_research(
    db: Session,
    payload: ResearchAskRequest,
    *,
    actor: str,
) -> dict:
    """Answer statutory queries strictly from uploaded/local Bare Acts (zero third-party AI or search API)."""
    if payload.use_external_ai:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Third-party AI and external search APIs are disabled. Legal Search strictly returns provisions from uploaded Bare Acts.",
        )

    c = _get_or_create_research_config(db)
    search_res = search_bare_acts_and_sections(
        db,
        payload.question,
        act_ids=payload.act_ids or None,
        limit=6,
    )
    hits = search_res["hits"]
    citations = [
        {
            "act_id": h["act_id"],
            "act_title": h["act_title"],
            "section_no": h["section_no"],
            "heading": h["heading"],
            "page_number": h["page_number"],
            "snippet": h["snippet"],
            "body_text": h["body_text"],
            "version_date": h["version_date"],
        }
        for h in hits
    ]

    if hits:
        blocks = [f"Results found strictly across {len(hits)} matching statutory provision(s) in your uploaded Bare Acts:"]
        for idx, h in enumerate(hits, start=1):
            heading_txt = f" — {h['heading']}" if h["heading"] else ""
            year_txt = f" ({h['year']})" if h.get("year") else ""
            excerpt = h["body_text"].strip()
            if len(excerpt) > 1200:
                excerpt = excerpt[:1200].rstrip() + "..."
            blocks.append(
                f"[{idx}] {h['act_title']}{year_txt}\n"
                f"    {h['section_no']}{heading_txt}  |  Original PDF Page {h['page_number']}\n"
                f"    {excerpt.replace(chr(10), chr(10) + '    ')}"
            )
        answer = "\n\n".join(blocks)
    else:
        answer = (
            "No matching statutory provisions were found in your uploaded Bare Acts for this query. "
            "Upload the relevant Bare Act PDF in the Bare Acts tab (or clear Act scope filters) and search again."
        )

    mode = "local_bare_acts"
    log = ResearchQueryLog(
        actor=actor,
        mode=mode,
        question=payload.question,
        answer_text=answer,
        citations_json=json.dumps(citations),
    )
    db.add(log)
    record_audit(db, actor=actor, entity_type="research", entity_id="query", action="ask", after={"mode": mode, "hits": len(hits)})
    db.commit()

    return {
        "mode": mode,
        "question": payload.question,
        "answer": answer,
        "citations": citations,
        "verification_notice": _VERIFICATION_NOTICE,
        "queries_today": c.queries_today,
        "max_daily_queries": c.max_daily_queries,
    }


def list_research_history(db: Session, limit: int = 25) -> list[dict]:
    rows = list(db.scalars(select(ResearchQueryLog).order_by(desc(ResearchQueryLog.id)).limit(limit)).all())
    return [
        {
            "id": r.id,
            "actor": r.actor,
            "mode": r.mode,
            "question": r.question,
            "answer": r.answer_text,
            "citations": json.loads(r.citations_json or "[]"),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]
