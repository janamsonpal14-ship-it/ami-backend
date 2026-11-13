from __future__ import annotations

import io
import os
import re
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Inches, Mm, Pt
from fastapi import HTTPException, status
from PIL import Image
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image as RLImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import app.config as cfg
from app.models import (
    FeeMemo,
    FeeMemoMatter,
    Hearing,
    HearingAdvocate,
    LetterheadTemplate,
    Matter,
    OfficeSettings,
)
from app.services.audit_service import record_audit
from app.utils.indian_format import format_indian_currency, format_indian_date

_ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
_MAX_IMAGE_BYTES = 10 * 1024 * 1024  # 10 MB

_ARIAL_UNICODE_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")
_PDF_FONT_NAME = "Helvetica"
if _ARIAL_UNICODE_PATH.exists():
    try:
        pdfmetrics.registerFont(TTFont("ArialUnicode", str(_ARIAL_UNICODE_PATH)))
        _PDF_FONT_NAME = "ArialUnicode"
    except Exception:
        _PDF_FONT_NAME = "Helvetica"


def letterhead_to_dict(lh: LetterheadTemplate) -> dict:
    return {
        "id": lh.id,
        "name": lh.name,
        "has_image": bool(lh.file_path and Path(lh.file_path).exists()),
        "top_margin_mm": lh.top_margin_mm,
        "bottom_margin_mm": lh.bottom_margin_mm,
        "left_margin_mm": lh.left_margin_mm,
        "right_margin_mm": lh.right_margin_mm,
        "image_width_pct": lh.image_width_pct,
        "is_default": lh.is_default,
    }


def list_letterheads(db: Session) -> list[dict]:
    rows = list(db.scalars(select(LetterheadTemplate).order_by(LetterheadTemplate.is_default.desc(), LetterheadTemplate.id)).all())
    return [letterhead_to_dict(r) for r in rows]


def upload_letterhead(
    db: Session,
    *,
    name: str,
    filename: str,
    image_bytes: bytes,
    top_margin_mm: int = 20,
    bottom_margin_mm: int = 20,
    left_margin_mm: int = 20,
    right_margin_mm: int = 20,
    image_width_pct: int = 100,
    is_default: bool = True,
    actor: str = "demo_advocate",
) -> dict:
    if len(image_bytes) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Letterhead image exceeds 10 MB limit.")
    ext = Path(filename).suffix.lower()
    if ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported image format. Upload PNG, JPEG, or WebP.",
        )

    # Validate image integrity and convert WebP to PNG for universal python-docx & ReportLab compatibility
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img.verify()
        with Image.open(io.BytesIO(image_bytes)) as img:
            out_buf = io.BytesIO()
            save_ext = ".png" if ext == ".webp" else ext
            fmt = "PNG" if save_ext == ".png" else "JPEG"
            if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")
            img.save(out_buf, format=fmt)
            clean_bytes = out_buf.getvalue()
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid or corrupted image file.")

    cfg.ensure_directories()
    safe_base = re.sub(r"[^A-Za-z0-9_-]", "_", Path(filename).stem)[:50]
    target = cfg.LETTERHEADS_DIR / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{safe_base}{save_ext}"
    target.write_bytes(clean_bytes)

    if is_default:
        for old in db.scalars(select(LetterheadTemplate)).all():
            old.is_default = False

    lh = LetterheadTemplate(
        name=name.strip() or safe_base,
        file_path=str(target),
        top_margin_mm=max(5, min(60, top_margin_mm)),
        bottom_margin_mm=max(5, min(60, bottom_margin_mm)),
        left_margin_mm=max(5, min(50, left_margin_mm)),
        right_margin_mm=max(5, min(50, right_margin_mm)),
        image_width_pct=max(20, min(100, image_width_pct)),
        is_default=is_default,
    )
    db.add(lh)
    db.flush()
    record_audit(db, actor=actor, entity_type="letterhead", entity_id=lh.id, action="upload", after={"name": lh.name})
    db.commit()
    db.refresh(lh)
    return letterhead_to_dict(lh)


def update_letterhead(db: Session, letterhead_id: int, payload: dict, actor: str) -> dict:
    lh = db.get(LetterheadTemplate, letterhead_id)
    if lh is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letterhead not found.")
    if payload.get("is_default"):
        for old in db.scalars(select(LetterheadTemplate)).all():
            old.is_default = False
    for field in ("name", "top_margin_mm", "bottom_margin_mm", "left_margin_mm", "right_margin_mm", "image_width_pct", "is_default"):
        if field in payload and payload[field] is not None:
            setattr(lh, field, payload[field])
    record_audit(db, actor=actor, entity_type="letterhead", entity_id=lh.id, action="update")
    db.commit()
    db.refresh(lh)
    return letterhead_to_dict(lh)


def format_ordinal_date(val: date | datetime | str | None) -> str:
    """Format a date as '04th November 2026' matching standard legal bills."""
    if val is None:
        return ""
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return ""
        try:
            val = datetime.fromisoformat(val).date()
        except ValueError:
            return val
    elif isinstance(val, datetime):
        val = val.date()

    day = val.day
    if 11 <= day <= 13:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day:02d}{suffix} {val.strftime('%B %Y')}"


def _get_cropped_header_image(file_path: str) -> tuple[str, float, float]:
    """Auto-crop bottom empty whitespace from full A4 letterhead scans so bills print on Page 1."""
    p = Path(file_path)
    if not p.exists():
        return file_path, 0.0, 0.0
    try:
        with Image.open(p) as img:
            w, h = img.size
            gray = img.convert("L")
            dark_rows = [y for y in range(h) if any(gray.getpixel((x, y)) < 240 for x in range(0, w, 5))]
            if dark_rows and max(dark_rows) < (h * 0.70):
                max_y = min(h, max(dark_rows) + 20)
                min_y = max(0, min(dark_rows) - 10)
                cropped_h = max_y - min_y
                cache_dir = cfg.CACHE_DIR / "letterheads"
                cache_dir.mkdir(parents=True, exist_ok=True)
                cached_path = cache_dir / f"hdr_{p.stem}_{w}_{cropped_h}.png"
                if not cached_path.exists():
                    cropped = img.crop((0, min_y, w, max_y))
                    if cropped.mode in ("RGBA", "P"):
                        cropped = cropped.convert("RGB")
                    cropped.save(cached_path, format="PNG")
                return str(cached_path), float(w), float(cropped_h)
            return file_path, float(w), float(h)
    except Exception:
        return file_path, 0.0, 0.0


def _load_memo_for_export(db: Session, memo_id: int) -> tuple[FeeMemo, LetterheadTemplate | None, OfficeSettings | None, list[Hearing]]:
    memo = db.scalar(
        select(FeeMemo)
        .options(
            selectinload(FeeMemo.matter_links).selectinload(FeeMemoMatter.matter),
            selectinload(FeeMemo.lines),
            selectinload(FeeMemo.adjustments),
            selectinload(FeeMemo.payments),
        )
        .where(FeeMemo.id == memo_id)
    )
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")

    lh: LetterheadTemplate | None = None
    if memo.letterhead_id:
        lh = db.get(LetterheadTemplate, memo.letterhead_id)
    if lh is None:
        lh = db.scalar(select(LetterheadTemplate).where(LetterheadTemplate.is_default == True))
    settings = db.scalar(select(OfficeSettings))

    matter_ids = [link.matter_id for link in memo.matter_links]
    appearances: list[Hearing] = []
    if matter_ids:
        appearances = list(
            db.scalars(
                select(Hearing)
                .options(
                    selectinload(Hearing.matter),
                    selectinload(Hearing.advocate_links).selectinload(HearingAdvocate.advocate),
                )
                .where(Hearing.matter_id.in_(matter_ids), Hearing.status.in_(["Completed", "Not Taken Up"]))
                .order_by(Hearing.hearing_date.asc(), Hearing.id.asc())
            ).all()
        )
    return memo, lh, settings, appearances


def _safe_currency(val: Decimal | float | int, use_rs_fallback: bool = False) -> str:
    formatted = format_indian_currency(val)
    if use_rs_fallback:
        return formatted.replace("₹", "Rs. ")
    return formatted


def _format_amt(val: Decimal | float | int) -> str:
    """Format numeric amount as Indian comma grouped digits without repeating currency symbol in table cells."""
    v = float(val)
    # Format with Indian numbering format (e.g. 1,20,000.00 or 1,20,000 if integer)
    is_neg = v < 0
    abs_v = abs(v)
    int_part = int(abs_v)
    dec_part = abs_v - int_part
    s = str(int_part)
    if len(s) > 3:
        last3 = s[-3:]
        other = s[:-3]
        groups = []
        while len(other) > 2:
            groups.insert(0, other[-2:])
            other = other[:-2]
        if other:
            groups.insert(0, other)
        groups.append(last3)
        formatted = ",".join(groups)
    else:
        formatted = s

    if dec_part > 0.001:
        formatted += f"{dec_part:.2f}"[1:]
    if is_neg:
        return f"({formatted})"
    return formatted


def export_memo_docx(db: Session, memo_id: int, include_annexure: bool = False) -> tuple[bytes, str]:
    """Generate a formal Proforma Word (.docx) Memo of Fees exactly matching chambers format."""
    memo, lh, settings, appearances = _load_memo_for_export(db, memo_id)
    doc = Document()

    section = doc.sections[0]
    section.page_width = Mm(210)
    section.page_height = Mm(297)
    top_mm = lh.top_margin_mm if lh else 15
    bottom_mm = lh.bottom_margin_mm if lh else 15
    left_mm = lh.left_margin_mm if lh else 18
    right_mm = lh.right_margin_mm if lh else 18
    section.top_margin = Mm(top_mm)
    section.bottom_margin = Mm(bottom_mm)
    section.left_margin = Mm(left_mm)
    section.right_margin = Mm(right_mm)

    usable_width_mm = 210 - left_mm - right_mm

    # Letterhead header
    if lh and lh.file_path and Path(lh.file_path).exists():
        cropped_file, img_w, img_h = _get_cropped_header_image(lh.file_path)
        img_p = doc.add_paragraph()
        img_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        width_mm = usable_width_mm * (lh.image_width_pct / 100.0)
        img_p.add_run().add_picture(cropped_file, width=Mm(width_mm))
    else:
        # Default chambers typography header
        hdr_table = doc.add_table(rows=1, cols=2)
        hdr_table.autofit = True
        c0 = hdr_table.rows[0].cells[0]
        p0 = c0.paragraphs[0]
        r0 = p0.add_run("Janam Sonpal\n")
        r0.bold = True
        r0.font.size = Pt(15)
        r0_sub = p0.add_run("Advocate")
        r0_sub.font.size = Pt(11)

        c1 = hdr_table.rows[0].cells[1]
        p1 = c1.paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r1 = p1.add_run(
            "601, Jolly Bhavan No. 1, 10 New Marine Lines,\n"
            "Churchgate Mumbai - 400020\n"
            "janamsonpal14@gmail.com | +91 7878955633"
        )
        r1.font.size = Pt(9.5)

        dash_p = doc.add_paragraph()
        dash_p.add_run("-" * 75).bold = True

    # Bill No. (Left) & Date (Right)
    meta = doc.add_table(rows=1, cols=2)
    meta.autofit = True
    p_left = meta.rows[0].cells[0].paragraphs[0]
    r_b = p_left.add_run("Bill No.: ")
    r_b.bold = True
    p_left.add_run(memo.bill_number)

    p_right = meta.rows[0].cells[1].paragraphs[0]
    p_right.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r_dt = p_right.add_run(format_ordinal_date(memo.bill_date))
    r_dt.bold = True

    to_p = doc.add_paragraph()
    to_p.add_run("To,\n").bold = True
    addr_lines = memo.addressee_name
    if memo.addressee_address:
        addr_lines += f"\n{memo.addressee_address}"
    to_p.add_run(addr_lines)

    if memo.matter_links:
        ref = doc.add_paragraph()
        ref.add_run("Ref.:\n").bold = True
        for idx, link in enumerate(memo.matter_links, 1):
            if link.matter:
                ref.add_run(f"   {idx}. {link.matter.title} ({link.matter.case_number}, {link.matter.court_forum})\n")

    if memo.subject_line:
        subj = doc.add_paragraph()
        subj.add_run("Sub: ").bold = True
        subj.add_run(memo.subject_line)

    title_p = doc.add_paragraph()
    title_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    tr = title_p.add_run("MEMO OF FEES")
    tr.bold = True
    tr.underline = True
    tr.font.size = Pt(12)

    # 3-column Table: Sr. No. | Particulars | Amt (₹)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = "Sr. No."
    hdr_cells[1].text = "Particulars"
    hdr_cells[2].text = "Amt (₹)"
    for c in hdr_cells:
        for p in c.paragraphs:
            for r in p.runs:
                r.bold = True
    hdr_cells[0].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
    hdr_cells[2].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT

    for idx, line in enumerate(memo.lines, 1):
        row = table.add_row().cells
        p_sr = row[0].paragraphs[0]
        p_sr.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p_sr.text = str(idx)

        qty_str = f"{line.quantity:g}"
        part_text = f"{line.category}: {line.description}"
        if line.quantity != 1 or line.unit_rate > 0:
            part_text += f"\n(Qty: {qty_str} × Rs. {_format_amt(line.unit_rate)})"
        row[1].text = part_text

        p_amt = row[2].paragraphs[0]
        p_amt.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p_amt.text = _format_amt(line.line_amount)

    # Gross Total / Adjustments / Tax / Net Total
    def add_docx_total_row(label: str, value: str, bold: bool = False):
        row = table.add_row().cells
        p1 = row[1].paragraphs[0]
        p1.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r1 = p1.add_run(label)
        r1.bold = bold
        p2 = row[2].paragraphs[0]
        p2.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        r2 = p2.add_run(value)
        r2.bold = bold

    add_docx_total_row("Gross Total", _format_amt(memo.gross_total))
    for adj in memo.adjustments:
        add_docx_total_row(f"(-) towards {adj.reason}", f"({_format_amt(abs(adj.signed_amount))})")
    if memo.tax_percent and memo.tax_percent > 0:
        add_docx_total_row(f"Tax ({memo.tax_percent:g}%)", _format_amt(memo.tax_amount))
    add_docx_total_row("Net Total", _format_amt(memo.net_total), bold=True)

    words_p = doc.add_paragraph()
    words_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    wr = words_p.add_run(f"({memo.amount_in_words})")
    wr.italic = True
    wr.bold = True

    note_p = doc.add_paragraph()
    note_p.add_run("\nKindly do the needful at the earliest. Should you require any further detail, do let me know.\n")
    note_p.add_run("Best Regards,\n\n").bold = True
    note_p.add_run("Sd/-\n")
    signatory = settings.advocate_signatory if settings else "Janam Sonpal"
    note_p.add_run(f"{signatory}, Advocate\n").bold = True

    bank_text = memo.footer_bank_details or (settings.default_bank_details if settings else None)
    if not bank_text or "Replace before live billing" in bank_text:
        bank_text = "Indusind Bank\nA/c No.: 187878955633\nIFSC: INDB0000092\nUPI ID: janamsonpal@indie"
    bp = doc.add_paragraph()
    bp.add_run(bank_text)

    if include_annexure and appearances:
        doc.add_page_break()
        ap = doc.add_paragraph()
        ap.alignment = WD_ALIGN_PARAGRAPH.CENTER
        ar = ap.add_run(f"ANNEXURE — SCHEDULE OF APPEARANCES ({memo.bill_number})")
        ar.bold = True
        at = doc.add_table(rows=1, cols=4)
        at.style = "Table Grid"
        for i, h in enumerate(("Date", "Matter", "Forum", "Advocates & Business Conducted")):
            at.rows[0].cells[i].text = h
        for h in appearances:
            rc = at.add_row().cells
            rc[0].text = format_indian_date(h.hearing_date)
            rc[1].text = h.matter.title if h.matter else ""
            rc[2].text = h.matter.court_forum if h.matter else ""
            advs = ", ".join(link.advocate.full_name for link in h.advocate_links if link.advocate)
            rc[3].text = f"[{advs}] {h.business_conducted}" if advs else h.business_conducted

    out = io.BytesIO()
    doc.save(out)
    safe_bill = re.sub(r"[^A-Za-z0-9_-]", "_", memo.bill_number)
    return out.getvalue(), f"FeeMemo_{safe_bill}.docx"


def export_memo_pdf(db: Session, memo_id: int, include_annexure: bool = False) -> tuple[bytes, str]:
    """Generate a formal A4 Proforma PDF Memo of Fees printed cleanly on letterhead matching chambers format."""
    memo, lh, settings, appearances = _load_memo_for_export(db, memo_id)
    # Always use Amt (Rs.) in standard PDF typography to prevent missing font glyph boxes
    amt_header = "Amt (Rs.)"

    top_mm = lh.top_margin_mm if lh else 15
    bottom_mm = lh.bottom_margin_mm if lh else 15
    left_mm = lh.left_margin_mm if lh else 18
    right_mm = lh.right_margin_mm if lh else 18

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        topMargin=top_mm * mm,
        bottomMargin=bottom_mm * mm,
        leftMargin=left_mm * mm,
        rightMargin=right_mm * mm,
    )

    styles = getSampleStyleSheet()
    base_style = ParagraphStyle("AdvBase", parent=styles["Normal"], fontName=_PDF_FONT_NAME, fontSize=9.5, leading=13.5)
    center_style = ParagraphStyle("AdvCenter", parent=base_style, alignment=1)
    right_style = ParagraphStyle("AdvRight", parent=base_style, alignment=2)
    center_title = ParagraphStyle("AdvTitle", parent=base_style, fontSize=12, leading=16, alignment=1, spaceAfter=6)

    story = []
    usable_width = (210 - left_mm - right_mm) * mm

    # Letterhead header
    if lh and lh.file_path and Path(lh.file_path).exists():
        cropped_file, img_w, img_h = _get_cropped_header_image(lh.file_path)
        target_w = usable_width * (lh.image_width_pct / 100.0)
        target_h = target_w * (img_h / max(1.0, img_w))
        story.append(RLImage(cropped_file, width=target_w, height=target_h))
        story.append(Spacer(1, 3 * mm))
    else:
        # Default chambers typography header matching JS_24_Suraj Babulal Shah
        hdr_left = Paragraph("<b><font size=15>Janam Sonpal</font></b><br/><font size=11>Advocate</font>", base_style)
        hdr_right = Paragraph(
            "601, Jolly Bhavan No. 1, 10 New Marine Lines,<br/>"
            "Churchgate Mumbai - 400020<br/>"
            "janamsonpal14@gmail.com | +91 7878955633",
            right_style,
        )
        hdr_table = Table([[hdr_left, hdr_right]], colWidths=[usable_width * 0.45, usable_width * 0.55])
        hdr_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
        story.append(hdr_table)
        story.append(Spacer(1, 1.5 * mm))
        dash_p = Paragraph("-" * 80, center_style)
        story.append(dash_p)
        story.append(Spacer(1, 2 * mm))

    # Bill No. (Left) & Date (Right)
    meta_table = Table(
        [
            [
                Paragraph(f"<b>Bill No.:</b> {memo.bill_number}", base_style),
                Paragraph(f"<b>{format_ordinal_date(memo.bill_date)}</b>", right_style),
            ]
        ],
        colWidths=[usable_width * 0.55, usable_width * 0.45],
    )
    meta_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(meta_table)
    story.append(Spacer(1, 2.5 * mm))

    addr_html = f"<b>To,</b><br/>{memo.addressee_name}"
    if memo.addressee_address:
        addr_html += "<br/>" + memo.addressee_address.replace("\n", "<br/>")
    story.append(Paragraph(addr_html, base_style))
    story.append(Spacer(1, 2.5 * mm))

    if memo.matter_links:
        ref_lines = ["<b>Ref.:</b>"]
        for idx, link in enumerate(memo.matter_links, 1):
            if link.matter:
                ref_lines.append(f"&nbsp;&nbsp;{idx}. {link.matter.title} ({link.matter.case_number}, {link.matter.court_forum})")
        story.append(Paragraph("<br/>".join(ref_lines), base_style))
        story.append(Spacer(1, 2.5 * mm))

    if memo.subject_line:
        story.append(Paragraph(f"<b>Sub:</b> {memo.subject_line}", base_style))
        story.append(Spacer(1, 2.5 * mm))

    story.append(Paragraph("<b><u>MEMO OF FEES</u></b>", center_title))
    story.append(Spacer(1, 1.5 * mm))

    # 3-column Table: Sr. No. | Particulars | Amt (₹)
    sr_w = usable_width * 0.09
    amt_w = usable_width * 0.23
    part_w = usable_width - sr_w - amt_w

    rows = [
        [
            Paragraph("<b>Sr.<br/>No.</b>", center_style),
            Paragraph("<b>Particulars</b>", base_style),
            Paragraph(f"<b>{amt_header}</b>", right_style),
        ]
    ]

    for idx, line in enumerate(memo.lines, 1):
        qty_str = f"{line.quantity:g}"
        desc = f"<b>{line.category}:</b> {line.description}"
        if line.quantity != 1 or line.unit_rate > 0:
            desc += f"<br/>(Qty: {qty_str} × Rs. {_format_amt(line.unit_rate)})"

        rows.append(
            [
                Paragraph(str(idx), center_style),
                Paragraph(desc, base_style),
                Paragraph(_format_amt(line.line_amount), right_style),
            ]
        )

    # Totals rows
    rows.append(
        [
            Paragraph("", base_style),
            Paragraph("<b>Gross Total</b>", right_style),
            Paragraph(f"<b>{_format_amt(memo.gross_total)}</b>", right_style),
        ]
    )
    for adj in memo.adjustments:
        rows.append(
            [
                Paragraph("", base_style),
                Paragraph(f"(-) towards {adj.reason}", right_style),
                Paragraph(f"({_format_amt(abs(adj.signed_amount))})", right_style),
            ]
        )
    if memo.tax_percent and memo.tax_percent > 0:
        rows.append(
            [
                Paragraph("", base_style),
                Paragraph(f"Tax ({memo.tax_percent:g}%)", right_style),
                Paragraph(_format_amt(memo.tax_amount), right_style),
            ]
        )
    rows.append(
        [
            Paragraph("", base_style),
            Paragraph("<b>Net Total</b>", right_style),
            Paragraph(f"<b>{_format_amt(memo.net_total)}</b>", right_style),
        ]
    )

    fee_table = Table(rows, colWidths=[sr_w, part_w, amt_w], repeatRows=1)
    fee_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, colors.Color(0.2, 0.2, 0.2)),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.95, 0.95, 0.95)),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(fee_table)
    story.append(Spacer(1, 2 * mm))

    # Amount in words right aligned
    story.append(Paragraph(f"<i><b>({memo.amount_in_words})</b></i>", right_style))
    story.append(Spacer(1, 3.5 * mm))

    # Closing sign-off & Bank particulars
    signatory = settings.advocate_signatory if settings else "Janam Sonpal"
    bank_text = memo.footer_bank_details or (settings.default_bank_details if settings else None)
    if not bank_text or "Replace before live billing" in bank_text:
        bank_text = "Indusind Bank\nA/c No.: 187878955633\nIFSC: INDB0000092\nUPI ID: janamsonpal@indie"

    closing_text = (
        "Kindly do the needful at the earliest. Should you require any further detail, do let me know.<br/><br/>"
        "<b>Best Regards,</b><br/><br/>"
        "Sd/-<br/>"
        f"<b>{signatory}, Advocate</b>"
    )
    story.append(Paragraph(closing_text, base_style))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"<b>Payment / Bank Particulars:</b><br/>{bank_text.replace(chr(10), '<br/>')}", base_style))

    if include_annexure and appearances:
        story.append(PageBreak())
        story.append(Paragraph(f"<b>ANNEXURE — SCHEDULE OF APPEARANCES ({memo.bill_number})</b>", center_title))
        ann_rows = [
            [
                Paragraph("<b>Date</b>", base_style),
                Paragraph("<b>Matter</b>", base_style),
                Paragraph("<b>Forum</b>", base_style),
                Paragraph("<b>Advocates & Business Conducted</b>", base_style),
            ]
        ]
        for h in appearances:
            advs = ", ".join(link.advocate.full_name for link in h.advocate_links if link.advocate)
            biz = f"[{advs}] {h.business_conducted}" if advs else h.business_conducted
            ann_rows.append(
                [
                    Paragraph(format_indian_date(h.hearing_date), base_style),
                    Paragraph(h.matter.title if h.matter else "", base_style),
                    Paragraph(h.matter.court_forum if h.matter else "", base_style),
                    Paragraph(biz, base_style),
                ]
            )
        ann_table = Table(
            ann_rows,
            colWidths=[usable_width * 0.16, usable_width * 0.28, usable_width * 0.20, usable_width * 0.36],
            repeatRows=1,
        )
        ann_table.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.92, 0.94, 0.97)),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ]
            )
        )
        story.append(ann_table)

    doc.build(story)
    safe_bill = re.sub(r"[^A-Za-z0-9_-]", "_", memo.bill_number)
    return buf.getvalue(), f"FeeMemo_{safe_bill}.pdf"

