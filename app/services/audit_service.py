from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

import app.config as cfg
from app.models import AuditEvent
from app.utils.indian_format import ist_now


def record_audit(
    db: Session,
    *,
    actor: str,
    entity_type: str,
    entity_id: int | str,
    action: str,
    before: Any = None,
    after: Any = None,
) -> AuditEvent:
    """Append an immutable audit log entry."""
    event = AuditEvent(
        actor=actor,
        entity_type=entity_type,
        entity_id=str(entity_id),
        action=action,
        before_json=json.dumps(before, default=str) if before is not None else None,
        after_json=json.dumps(after, default=str) if after is not None else None,
    )
    db.add(event)
    return event


def create_snapshot(reason: str, db_path: Path | None = None) -> Path | None:
    """Create a safe SQLite online-backup snapshot before risky/bulk actions."""
    source = db_path or cfg.DB_PATH
    if not source.exists() or str(source) == ":memory:":
        return None
    cfg.ensure_directories()
    stamp = ist_now().strftime("%Y%m%d_%H%M%S")
    safe_reason = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in reason)[:40]
    target = cfg.SNAPSHOTS_DIR / f"auto_{stamp}_{safe_reason}.db"
    src_conn = sqlite3.connect(str(source))
    dst_conn = sqlite3.connect(str(target))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        dst_conn.close()
        src_conn.close()
    return target

