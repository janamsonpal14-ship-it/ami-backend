"""Master Legal Library Batch Ingestor for AMI.

Discovers and ingests verified Bare Acts across all 9 cohorts from official repositories
(India Code DSpace 9.1 API, Maharashtra Government, High Court) into amico.db.
Extracts sections with 1-based page mapping and normalizes storage to relative paths.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import ssl
import sys
import time
from pathlib import Path
from typing import Any

import httpx

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import app.config as cfg
from app.database import SessionLocal
from app.models import BareAct
from app.services.statutory_downloader import ingest_pilot_act

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("ingest_master_library")

MANIFEST_PATH = cfg.DATA_DIR / "manifests" / "master_legal_library_manifest.json"
STATUS_LOG_PATH = cfg.DATA_DIR / "manifests" / "master_library_ingestion_report.json"


def create_ssl_client(timeout: float = 35.0) -> httpx.Client:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    if hasattr(ssl, "OP_LEGACY_SERVER_CONNECT"):
        ctx.options |= ssl.OP_LEGACY_SERVER_CONNECT
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json,application/pdf,*/*",
    }
    return httpx.Client(verify=ctx, timeout=timeout, headers=headers, follow_redirects=True)


def discover_act_pdf_url(client: httpx.Client, act: dict[str, Any]) -> tuple[str, str] | None:
    """Find authentic bitstream PDF content URL from India Code API."""
    title = act.get("title", "").strip()
    year = act.get("year")
    queries_to_try = [
        f'title:"{title}"',
        f'"{title}"',
    ]

    # Clean stripped title without 'The ' prefix
    clean_title = re.sub(r"^The\s+", "", title, flags=re.IGNORECASE).strip()
    if clean_title != title:
        queries_to_try.append(f'title:"{clean_title}"')
        queries_to_try.append(f'"{clean_title}"')

    if year:
        queries_to_try.append(f'"{clean_title}, {year}"')
        queries_to_try.append(f'"{clean_title}" AND "{year}"')

    for q in queries_to_try:
        try:
            resp = client.get(
                "https://indiacode.gov.in/server/api/discover/search/objects",
                params={"query": q, "dsoType": "ITEM", "size": 10},
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get("_embedded", {}).get("searchResult", {}).get("_embedded", {}).get("objects", [])
            if not items:
                continue

            for obj in items:
                item = obj.get("_embedded", {}).get("indexableObject", {})
                item_name = item.get("name", "")
                uuid = item.get("uuid")
                if not uuid:
                    continue

                # Filter out obvious amendment acts unless the act itself is an amendment
                is_amendment_act = "amendment act" in item_name.lower()
                target_is_amendment = "amendment" in title.lower()
                if is_amendment_act and not target_is_amendment:
                    continue

                # Fetch bundles for item
                bundles_url = f"https://indiacode.gov.in/server/api/core/items/{uuid}/bundles"
                b_resp = client.get(bundles_url)
                if b_resp.status_code != 200:
                    continue
                bundles = b_resp.json().get("_embedded", {}).get("bundles", [])
                for b in bundles:
                    if b.get("name") == "ORIGINAL":
                        bs_url = b.get("_links", {}).get("bitstreams", {}).get("href")
                        bs_resp = client.get(bs_url)
                        if bs_resp.status_code != 200:
                            continue
                        bitstreams = bs_resp.json().get("_embedded", {}).get("bitstreams", [])
                        for bs in bitstreams:
                            bs_name = bs.get("name", "")
                            # Prefer English PDF (typically doesn't start with H or h)
                            if bs_name.lower().endswith(".pdf") and not bs_name.lower().startswith("h"):
                                content_url = bs.get("_links", {}).get("content", {}).get("href")
                                if content_url:
                                    return item_name, content_url
        except Exception as e:
            logger.debug(f"Search query error for {q}: {e}")
            continue

    return None


def run_ingestion(
    cohort_filter: int | None = None,
    dry_run: bool = False,
    limit: int | None = None,
) -> None:
    if not MANIFEST_PATH.exists():
        logger.error(f"Manifest not found at {MANIFEST_PATH}")
        sys.exit(1)

    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        manifest_data = json.load(f)

    all_acts: list[dict[str, Any]] = manifest_data.get("acts", [])
    if cohort_filter is not None:
        all_acts = [a for a in all_acts if a.get("cohort") == cohort_filter]

    if limit is not None:
        all_acts = all_acts[:limit]

    logger.info(f"Targeting {len(all_acts)} acts (Cohort filter: {cohort_filter or 'ALL'}, Dry run: {dry_run})")

    db = SessionLocal()
    client = create_ssl_client()

    stats = {
        "total": len(all_acts),
        "already_ingested": 0,
        "successfully_ingested": 0,
        "failed_or_skipped": 0,
        "dry_run_found": 0,
        "dry_run_not_found": 0,
        "details": [],
    }

    try:
        for idx, act in enumerate(all_acts, start=1):
            title = act.get("title")
            short_title = act.get("short_title", title)
            slug = act.get("slug")
            cohort = act.get("cohort")

            logger.info(f"[{idx}/{len(all_acts)}] [Cohort {cohort}] Checking '{short_title}'...")

            # 1. Check if already ingested in database with sections
            existing_act = db.query(BareAct).filter(
                (BareAct.title == title) | (BareAct.short_title == short_title)
            ).first()

            if existing_act and existing_act.section_count > 0:
                logger.info(f"   -> Already ingested with {existing_act.section_count} sections. Skipping.")
                stats["already_ingested"] += 1
                stats["details"].append({
                    "act_id": act.get("id"),
                    "title": title,
                    "cohort": cohort,
                    "status": "already_indexed",
                    "section_count": existing_act.section_count,
                    "page_count": existing_act.page_count,
                })
                continue

            # 2. Discover authentic PDF from India Code or configured source
            time.sleep(0.4)  # Polite pacing
            discovery = discover_act_pdf_url(client, act)

            if not discovery:
                logger.warning(f"   -> Official PDF bitstream not found via discovery.")
                if dry_run:
                    stats["dry_run_not_found"] += 1
                else:
                    stats["failed_or_skipped"] += 1
                stats["details"].append({
                    "act_id": act.get("id"),
                    "title": title,
                    "cohort": cohort,
                    "status": "not_found",
                })
                continue

            found_name, pdf_url = discovery
            logger.info(f"   -> Discovered: '{found_name}' -> {pdf_url}")

            if dry_run:
                stats["dry_run_found"] += 1
                stats["details"].append({
                    "act_id": act.get("id"),
                    "title": title,
                    "cohort": cohort,
                    "status": "discovered_dry_run",
                    "download_url": pdf_url,
                })
                continue

            # 3. Download PDF
            try:
                time.sleep(0.5)  # Spacing
                pdf_resp = client.get(pdf_url)
                pdf_resp.raise_for_status()
                pdf_bytes = pdf_resp.content

                if not pdf_bytes.startswith(b"%PDF-"):
                    logger.warning(f"   -> Downloaded content is not a valid PDF.")
                    stats["failed_or_skipped"] += 1
                    stats["details"].append({
                        "act_id": act.get("id"),
                        "title": title,
                        "cohort": cohort,
                        "status": "invalid_pdf_format",
                    })
                    continue

                # 4. Ingest using statutory engine
                act_record = ingest_pilot_act(db, act, pdf_bytes, actor="statutory_master_ingestor")
                logger.info(
                    f"   -> Successfully ingested: {act_record.section_count} sections, "
                    f"{act_record.page_count} pages, SHA256: {act_record.sha256_hash[:12]}..."
                )
                stats["successfully_ingested"] += 1
                stats["details"].append({
                    "act_id": act.get("id"),
                    "title": title,
                    "cohort": cohort,
                    "status": "ingested",
                    "sections": act_record.section_count,
                    "pages": act_record.page_count,
                    "sha256": act_record.sha256_hash,
                })
            except Exception as e:
                logger.error(f"   -> Failed downloading or ingesting '{title}': {e}")
                db.rollback()
                stats["failed_or_skipped"] += 1
                stats["details"].append({
                    "act_id": act.get("id"),
                    "title": title,
                    "cohort": cohort,
                    "status": "error",
                    "error": str(e),
                })

    finally:
        db.close()
        client.close()

    # Save summary report
    STATUS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATUS_LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    logger.info("=" * 60)
    logger.info(f"Ingestion Finished:")
    logger.info(f"  Total targeted: {stats['total']}")
    logger.info(f"  Already indexed: {stats['already_ingested']}")
    logger.info(f"  Successfully ingested: {stats['successfully_ingested']}")
    logger.info(f"  Failed / Not found: {stats['failed_or_skipped']}")
    if dry_run:
        logger.info(f"  Dry run found: {stats['dry_run_found']}")
        logger.info(f"  Dry run not found: {stats['dry_run_not_found']}")
    logger.info(f"Report saved to: {STATUS_LOG_PATH}")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest statutory acts into AMI Master Library.")
    parser.add_argument("--cohort", type=int, help="Target specific cohort (1-9)")
    parser.add_argument("--dry-run", action="store_true", help="Search and report without downloading")
    parser.add_argument("--limit", type=int, help="Limit number of acts to process")
    args = parser.parse_args()

    run_ingestion(cohort_filter=args.cohort, dry_run=args.dry_run, limit=args.limit)
