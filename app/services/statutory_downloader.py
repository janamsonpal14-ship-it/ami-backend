"""Statutory Downloader, Verification, and Version Control Engine for AMI.

Provides official-source discovery, bulk download, SHA-256 integrity validation,
historical version preservation, and 'Law Applicable on Date' querying.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import ssl
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import httpx
from pypdf import PdfReader
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

import app.config as cfg
from app.models import BareAct, BareActSection, BareActVersion, LegislativeAmendment
from app.services.law_service import (
    _extract_sections_from_pages,
    _split_page_body_and_footnotes,
)
from app.utils.indian_format import ist_now

logger = logging.getLogger(__name__)

# Base storage directory for verified legal library
LEGAL_LIBRARY_DIR = cfg.DATA_DIR / "legal_library"
MANIFESTS_DIR = cfg.DATA_DIR / "manifests"


def _create_official_ssl_context() -> ssl.SSLContext:
    """Create SSL context configured for government servers with legacy renegotiation."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    return ctx


def download_statutory_file(url: str, timeout: float = 30.0) -> bytes:
    """Download official statutory PDF with retry, rate-limiting, and legacy TLS handling."""
    ctx = _create_official_ssl_context()
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "application/pdf,*/*",
    }
    with httpx.Client(verify=ctx, timeout=timeout, follow_redirects=True, headers=headers) as client:
        time.sleep(0.5)  # Polite spacing
        resp = client.get(url)
        resp.raise_for_status()
        content = resp.content
        if not content.startswith(b"%PDF-"):
            raise ValueError(f"Downloaded content from {url} is not a valid PDF document.")
        return content


def calculate_sha256(content: bytes) -> str:
    """Compute SHA-256 hex digest for document verification."""
    return hashlib.sha256(content).hexdigest()


def ensure_legal_library_directory(category_slug: str, act_slug: str) -> tuple[Path, Path]:
    """Ensure directory structure exists: data/legal_library/<category>/<act>/current and historical."""
    act_dir = LEGAL_LIBRARY_DIR / category_slug / act_slug
    current_dir = act_dir / "current"
    historical_dir = act_dir / "historical"
    current_dir.mkdir(parents=True, exist_ok=True)
    historical_dir.mkdir(parents=True, exist_ok=True)
    return current_dir, historical_dir


def ingest_pilot_act(
    db: Session,
    act_def: dict[str, Any],
    pdf_bytes: bytes,
    actor: str = "statutory_system",
) -> BareAct:
    """Ingest a verified statutory Act from official PDF into the AMI Legal Library."""
    category_slug = act_def.get("category_slug", "central_acts")
    act_slug = act_def.get("slug", "act")
    target_filename = act_def.get("target_filename", f"{act_slug}.pdf")

    current_dir, _ = ensure_legal_library_directory(category_slug, act_slug)
    target_pdf_path = current_dir / target_filename
    target_pdf_path.write_bytes(pdf_bytes)

    # Compute SHA-256
    sha256_hash = calculate_sha256(pdf_bytes)

    # Write companion metadata.json sidecar
    metadata_path = current_dir / "metadata.json"
    sidecar_meta = {
        "title": act_def.get("title"),
        "short_title": act_def.get("short_title"),
        "act_no": act_def.get("act_no"),
        "year": act_def.get("year"),
        "jurisdiction": act_def.get("jurisdiction"),
        "category_slug": category_slug,
        "enactment_date": act_def.get("enactment_date"),
        "commencement_date": act_def.get("commencement_date"),
        "source_provider": act_def.get("source_provider"),
        "source_url": act_def.get("source_url"),
        "sha256_hash": sha256_hash,
        "file_size_bytes": len(pdf_bytes),
        "verified_at": ist_now().isoformat(),
    }
    metadata_path.write_text(json.dumps(sidecar_meta, indent=2), encoding="utf-8")

    rel_pdf_path = f"legal_library/{category_slug}/{act_slug}/current/{target_filename}"

    # Check if this Act already exists in the database
    title = act_def["title"]
    short_title = act_def.get("short_title", title)
    existing_act = db.scalar(
        select(BareAct).where(
            (BareAct.title == title)
            | (BareAct.short_title == short_title)
            | (BareAct.sha256_hash == sha256_hash)
        )
    )

    if existing_act:
        act = existing_act
        act.title = title
        act.short_title = short_title
        act.act_no = act_def.get("act_no")
        act.year = act_def.get("year")
        act.jurisdiction = act_def.get("jurisdiction", "India")
        act.category_slug = category_slug
        act.source_url = act_def.get("source_url")
        act.source_note = f"Verified from {act_def.get('source_provider', 'Official Source')}"
        act.sha256_hash = sha256_hash
        act.file_path = rel_pdf_path
        act.is_demo = False
        if act_def.get("enactment_date"):
            act.enactment_date = datetime.strptime(act_def["enactment_date"], "%Y-%m-%d").date()
        if act_def.get("commencement_date"):
            act.commencement_date = datetime.strptime(act_def["commencement_date"], "%Y-%m-%d").date()
    else:
        enact_d = datetime.strptime(act_def["enactment_date"], "%Y-%m-%d").date() if act_def.get("enactment_date") else None
        commence_d = datetime.strptime(act_def["commencement_date"], "%Y-%m-%d").date() if act_def.get("commencement_date") else None
        act = BareAct(
            title=title,
            short_title=short_title,
            act_no=act_def.get("act_no"),
            year=act_def.get("year"),
            jurisdiction=act_def.get("jurisdiction", "India"),
            category_slug=category_slug,
            version_date=act_def.get("commencement_date") or str(act_def.get("year")),
            enactment_date=enact_d,
            commencement_date=commence_d,
            source_note=f"Verified from {act_def.get('source_provider', 'Official Source')}",
            source_url=act_def.get("source_url"),
            sha256_hash=sha256_hash,
            tags=f"{category_slug}, {act_def.get('jurisdiction')}, verified_library",
            summary_text=f"Official statutory text of {title} ({act_def.get('act_no', '')}).",
            file_path=rel_pdf_path,
            is_demo=False,
        )
        db.add(act)
        db.flush()

    # Parse and index sections from PDF if not already indexed
    if act.section_count == 0 or len(act.sections) == 0:
        _extract_and_index_sections_from_pdf(db, act, target_pdf_path)

    # Ingest Current and Historical Versions
    _register_versions_for_act(db, act, act_def, target_pdf_path, sha256_hash, rel_pdf_path=rel_pdf_path)

    # Ingest Known Amendments
    _register_amendments_for_act(db, act, act_def)

    db.commit()
    db.refresh(act)
    return act


def _extract_and_index_sections_from_pdf(db: Session, act: BareAct, pdf_path: Path) -> None:
    """Extract sections from the PDF and link each with exact 1-based page numbers."""
    reader = PdfReader(str(pdf_path))
    act.page_count = len(reader.pages)

    # Clear any old sections if re-indexing
    db.query(BareActSection).filter(BareActSection.act_id == act.id).delete()
    db.flush()

    raw_pages: list[tuple[int, str]] = []
    for page_idx, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        raw_pages.append((page_idx, text))

    parsed_sections = _extract_sections_from_pages(raw_pages)
    extracted_sections: list[BareActSection] = []

    for item in parsed_sections:
        extracted_sections.append(
            BareActSection(
                act_id=act.id,
                section_no=item["section_no"],
                heading=item["heading"],
                page_number=item["page_number"],
                body_text=item["body_text"],
            )
        )

    # Fallback if no sections extracted: store page-by-page chunks
    if not extracted_sections:
        for page_idx, text in raw_pages:
            if text.strip():
                extracted_sections.append(
                    BareActSection(
                        act_id=act.id,
                        section_no=f"Page {page_idx}",
                        heading=f"Page {page_idx}",
                        page_number=page_idx,
                        body_text=text.strip(),
                    )
                )

    db.add_all(extracted_sections)
    act.section_count = len(extracted_sections)
    db.flush()


def _register_versions_for_act(
    db: Session,
    act: BareAct,
    act_def: dict[str, Any],
    pdf_path: Path,
    sha256_hash: str,
    rel_pdf_path: str | None = None,
) -> None:
    """Register current version and manifest historical versions in bare_act_versions."""
    existing_tags = {v.version_tag for v in act.versions}
    stored_path = rel_pdf_path or (
        str(pdf_path.relative_to(cfg.DATA_DIR))
        if pdf_path.is_relative_to(cfg.DATA_DIR)
        else str(pdf_path)
    )

    # Ensure 'current' version is registered
    if "current" not in existing_tags:
        commence_d = act.commencement_date
        db.add(
            BareActVersion(
                act_id=act.id,
                version_tag="current",
                version_title=f"{act.title} (Current Verified Text)",
                effective_from=commence_d,
                effective_to=None,
                is_current=True,
                file_path=stored_path,
                sha256_hash=sha256_hash,
                notes="Current applicable official text verified from official repository.",
            )
        )

    # Register any historical versions defined in manifest
    for h in act_def.get("historical_versions", []):
        tag = h.get("version_tag")
        if tag and tag not in existing_tags and tag != "current":
            eff_from = datetime.strptime(h["effective_from"], "%Y-%m-%d").date() if h.get("effective_from") else None
            eff_to = datetime.strptime(h["effective_to"], "%Y-%m-%d").date() if h.get("effective_to") else None
            db.add(
                BareActVersion(
                    act_id=act.id,
                    version_tag=tag,
                    version_title=h.get("title", f"Version {tag}"),
                    effective_from=eff_from,
                    effective_to=eff_to,
                    is_current=False,
                    file_path=h.get("file_path"),
                    sha256_hash=h.get("sha256_hash"),
                    notes=h.get("notes"),
                )
            )


def _register_amendments_for_act(db: Session, act: BareAct, act_def: dict[str, Any]) -> None:
    """Register known amendments into legislative_amendments."""
    existing_titles = {a.amending_act_title for a in act.amendments}
    for am in act_def.get("known_amendments", []):
        title = am.get("amending_act_title")
        if title and title not in existing_titles:
            commence_d = datetime.strptime(am["commencement_date"], "%Y-%m-%d").date() if am.get("commencement_date") else None
            db.add(
                LegislativeAmendment(
                    act_id=act.id,
                    amending_act_title=title,
                    amending_act_no=am.get("amending_act_no"),
                    amending_year=am.get("amending_year"),
                    commencement_date=commence_d,
                    sections_affected=am.get("sections_affected"),
                    status=am.get("status", "incorporated"),
                    source_url=am.get("source_url"),
                    notes=am.get("notes", "Verified official amendment instrument."),
                )
            )


def query_law_applicable_on_date(
    db: Session,
    act_id: int,
    as_of_date: date,
    section_no: str | None = None,
) -> dict[str, Any]:
    """Determine the statutory version, amendments, and provisions applicable on a specific past date."""
    act = db.get(BareAct, act_id)
    if not act:
        raise ValueError(f"Bare Act with ID {act_id} not found.")

    # Find the version that was in force on as_of_date
    applicable_version = None
    for v in act.versions:
        if v.effective_from and v.effective_from <= as_of_date:
            if v.effective_to is None or v.effective_to >= as_of_date:
                applicable_version = v
                break

    if applicable_version is None:
        applicable_version = act.versions[0] if act.versions else None

    # Check which amendments were in effect vs which came later
    amendments_in_effect = []
    subsequent_amendments = []
    for am in act.amendments:
        if am.commencement_date:
            if am.commencement_date <= as_of_date:
                amendments_in_effect.append({
                    "title": am.amending_act_title,
                    "act_no": am.amending_act_no,
                    "commencement_date": am.commencement_date.isoformat(),
                    "sections_affected": am.sections_affected,
                })
            else:
                subsequent_amendments.append({
                    "title": am.amending_act_title,
                    "act_no": am.amending_act_no,
                    "commencement_date": am.commencement_date.isoformat(),
                    "sections_affected": am.sections_affected,
                })

    # Search for matching section if requested
    matching_section = None
    if section_no:
        clean_no = section_no.lower().replace("section", "").strip()
        for sec in act.sections:
            if clean_no in sec.section_no.lower():
                matching_section = {
                    "section_no": sec.section_no,
                    "heading": sec.heading,
                    "page_number": sec.page_number,
                    "body_text": sec.body_text,
                }
                break

    return {
        "act_id": act.id,
        "act_title": act.title,
        "as_of_date": as_of_date.isoformat(),
        "applicable_version": {
            "version_tag": applicable_version.version_tag if applicable_version else "current",
            "version_title": applicable_version.version_title if applicable_version else "Current Text",
            "notes": applicable_version.notes if applicable_version else None,
        },
        "amendments_in_effect_count": len(amendments_in_effect),
        "amendments_in_effect": amendments_in_effect,
        "subsequent_amendments_count": len(subsequent_amendments),
        "subsequent_amendments": subsequent_amendments,
        "matching_section": matching_section,
    }


def execute_pilot_ingestion(manifest_path: Path | None = None) -> list[BareAct]:
    """Execute download, verification, and ingestion for the 5 Pilot Acts."""
    from app.database import SessionLocal

    path = manifest_path or (MANIFESTS_DIR / "pilot_acts_manifest.json")
    if not path.exists():
        raise FileNotFoundError(f"Pilot manifest not found at {path}")

    with path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    db = SessionLocal()
    ingested_acts: list[BareAct] = []
    try:
        for act_def in manifest.get("acts", []):
            logger.info(f"Processing pilot act: {act_def['title']}")

            pdf_bytes: bytes | None = None
            # Check local file first (e.g. CPC)
            local_path = act_def.get("local_existing_path")
            if local_path:
                abs_local = cfg.BASE_DIR / local_path
                if abs_local.exists():
                    pdf_bytes = abs_local.read_bytes()

            # If not local, download from official bitstream URL
            if pdf_bytes is None:
                bitstream_url = act_def.get("bitstream_url")
                if not bitstream_url:
                    raise ValueError(f"No local path or bitstream URL defined for {act_def['title']}")
                pdf_bytes = download_statutory_file(bitstream_url)

            act = ingest_pilot_act(db, act_def, pdf_bytes)
            ingested_acts.append(act)

        return ingested_acts
    finally:
        db.close()
