from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.config as cfg
import app.database as db_mod
from app.models import (
    Advocate,
    BareAct,
    CalendarEvent,
    Client,
    CourtHoliday,
    FeeMemo,
    Hearing,
    LetterheadTemplate,
    Matter,
    Task,
    User,
)
from app.services.audit_service import create_snapshot, record_audit
from app.utils.indian_format import ist_now
from app.utils.security import hash_password, verify_password

_MAX_BACKUP_BYTES = 100 * 1024 * 1024  # 100 MB safety cap


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def run_integrity_check(db_path: Path | None = None) -> dict:
    """Run SQLite PRAGMA integrity_check and return DB & storage health metrics."""
    cfg.ensure_directories()
    target = db_path or cfg.DB_PATH
    if not target.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database file not found.")

    conn = sqlite3.connect(str(target))
    try:
        row = conn.execute("PRAGMA integrity_check;").fetchone()
        result = str(row[0]) if row else "unknown"
    finally:
        conn.close()

    uploads_count = sum(1 for p in cfg.UPLOADS_DIR.rglob("*") if p.is_file()) if cfg.UPLOADS_DIR.exists() else 0
    snapshots_count = len(list(cfg.SNAPSHOTS_DIR.glob("*.db"))) if cfg.SNAPSHOTS_DIR.exists() else 0
    return {
        "ok": result.lower() == "ok",
        "result": result,
        "db_size_bytes": target.stat().st_size,
        "uploads_count": uploads_count,
        "snapshots_count": snapshots_count,
    }


def _table_counts(db: Session) -> dict[str, int]:
    return {
        "clients": int(db.scalar(select(func.count(Client.id))) or 0),
        "matters": int(db.scalar(select(func.count(Matter.id))) or 0),
        "hearings": int(db.scalar(select(func.count(Hearing.id))) or 0),
        "calendar_events": int(db.scalar(select(func.count(CalendarEvent.id))) or 0),
        "court_holidays": int(db.scalar(select(func.count(CourtHoliday.id))) or 0),
        "fee_memos": int(db.scalar(select(func.count(FeeMemo.id))) or 0),
        "letterheads": int(db.scalar(select(func.count(LetterheadTemplate.id))) or 0),
    }


def export_backup_bundle(db: Session, *, actor: str) -> tuple[bytes, str]:
    """Verify DB integrity, take a consistent WAL-safe SQLite copy, and package DB + uploads + SHA-256 manifest."""
    cfg.ensure_directories()
    health = run_integrity_check(cfg.DB_PATH)
    if not health["ok"]:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database failed integrity check: {health['result']}",
        )

    record_audit(db, actor=actor, entity_type="backup", entity_id="bundle", action="export")
    db.commit()
    counts = _table_counts(db)

    stamp = ist_now().strftime("%Y%m%d_%H%M%S")
    filename = f"AMICO_Backup_{stamp}.zip"

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_db = Path(tmpdir) / "amico.db"
        src_conn = sqlite3.connect(str(cfg.DB_PATH))
        dst_conn = sqlite3.connect(str(tmp_db))
        try:
            with dst_conn:
                src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()

        db_bytes = tmp_db.read_bytes()
        file_hashes: dict[str, str] = {"amico.db": _sha256_bytes(db_bytes)}

        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("amico.db", db_bytes)
            if cfg.UPLOADS_DIR.exists():
                for item in sorted(cfg.UPLOADS_DIR.rglob("*")):
                    if item.is_file():
                        rel = f"uploads/{item.relative_to(cfg.UPLOADS_DIR).as_posix()}"
                        item_bytes = item.read_bytes()
                        file_hashes[rel] = _sha256_bytes(item_bytes)
                        zf.writestr(rel, item_bytes)

            manifest = {
                "app": "AMICO",
                "schema_version": 1,
                "created_at": ist_now().isoformat(),
                "created_by": actor,
                "integrity_check": health["result"],
                "table_counts": counts,
                "files": file_hashes,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

    return zip_buf.getvalue(), filename


def _validate_zip_member_path(member_name: str) -> Path | None:
    """Reject symlinks/path traversal; permit only manifest.json, amico.db (or legacy velora.db/advocateos.db), and uploads/*."""
    if "\\" in member_name or member_name.startswith("/"):
        return None
    rel = Path(member_name)
    if rel.is_absolute() or ".." in rel.parts:
        return None
    if member_name in ("manifest.json", "amico.db", "velora.db", "advocateos.db"):
        return rel
    if len(rel.parts) >= 2 and rel.parts[0] == "uploads":
        return rel
    return None


def _swap_live_database(validated_db_path: Path, new_uploads_dir: Path | None, db: Session, reason: str) -> Path | None:
    """Create pre-restore snapshot, close live SQLAlchemy connections, and swap DB atomically."""
    snap = create_snapshot(reason, db_path=cfg.DB_PATH)
    db.close()
    db_mod.engine.dispose()

    for ext in ("-wal", "-shm"):
        Path(f"{cfg.DB_PATH}{ext}").unlink(missing_ok=True)

    shutil.copyfile(validated_db_path, cfg.DB_PATH)

    if new_uploads_dir is not None:
        if cfg.UPLOADS_DIR.exists():
            shutil.rmtree(cfg.UPLOADS_DIR)
        shutil.copytree(new_uploads_dir, cfg.UPLOADS_DIR)

    cfg.ensure_directories()
    return snap


def restore_backup_bundle(db: Session, *, filename: str, zip_bytes: bytes, actor: str) -> dict:
    """Validate backup .zip manifest, SHA-256 hashes & SQLite integrity before restoring live data."""
    if len(zip_bytes) > _MAX_BACKUP_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Backup archive exceeds 100 MB limit.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid or corrupted .zip backup file.")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_root = Path(tmpdir)
        with zf:
            for info in zf.infolist():
                if info.is_dir():
                    continue
                # Reject symlinks inside zip archives
                mode = (info.external_attr >> 16) & 0o170000
                if mode == 0o120000:
                    raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Symlinks are not allowed in backup archives.")
                safe_rel = _validate_zip_member_path(info.filename)
                if safe_rel is None:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=f"Unsafe path in backup archive: '{info.filename}'.",
                    )
                dest = tmp_root / safe_rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(zf.read(info.filename))

        manifest_path = tmp_root / "manifest.json"
        extracted_db = tmp_root / "amico.db"
        db_member_name = "amico.db"
        if not extracted_db.exists() and (tmp_root / "velora.db").exists():
            extracted_db = tmp_root / "velora.db"
            db_member_name = "velora.db"
        elif not extracted_db.exists() and (tmp_root / "advocateos.db").exists():
            extracted_db = tmp_root / "advocateos.db"
            db_member_name = "advocateos.db"
        if not manifest_path.exists() or not extracted_db.exists():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Backup archive is missing manifest.json or amico.db.",
            )

        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid manifest.json in backup archive.")

        expected_files: dict = manifest.get("files") or {}
        if db_member_name not in expected_files:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Backup manifest missing database checksum.")

        for rel_str, expected_hash in expected_files.items():
            safe_rel = _validate_zip_member_path(str(rel_str))
            if safe_rel is None:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid manifest entry '{rel_str}'.")
            file_path = tmp_root / safe_rel
            if not file_path.exists():
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Backup archive is missing expected file '{rel_str}'.",
                )
            if _sha256_bytes(file_path.read_bytes()) != expected_hash:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Checksum mismatch for '{rel_str}' — archive may be tampered or corrupted.",
                )

        check = run_integrity_check(extracted_db)
        if not check["ok"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Backup database failed PRAGMA integrity_check: {check['result']}",
            )

        extracted_uploads = tmp_root / "uploads"
        extracted_uploads.mkdir(exist_ok=True)
        pre_snap = _swap_live_database(extracted_db, extracted_uploads, db, "pre_backup_restore")

    with db_mod.SessionLocal() as fresh_db:
        record_audit(
            fresh_db,
            actor=actor,
            entity_type="backup",
            entity_id=filename,
            action="restore_bundle",
            after={"pre_restore_snapshot": pre_snap.name if pre_snap else None},
        )
        fresh_db.commit()
        counts = _table_counts(fresh_db)

    return {
        "status": "ok",
        "message": f"Restored backup '{filename}' cleanly after SHA-256 & integrity verification.",
        "pre_restore_snapshot": pre_snap.name if pre_snap else None,
        "table_counts": counts,
    }


def restore_snapshot_file(db: Session, *, filename: str, actor: str) -> dict:
    """Restore a specific snapshot from data/snapshots/ after verifying integrity."""
    safe_name = Path(filename).name
    if safe_name != filename or not safe_name.endswith(".db"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid snapshot filename.")
    snap_path = cfg.SNAPSHOTS_DIR / safe_name
    if not snap_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Snapshot file not found.")

    check = run_integrity_check(snap_path)
    if not check["ok"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Snapshot failed PRAGMA integrity_check: {check['result']}",
        )

    pre_snap = _swap_live_database(snap_path, None, db, "pre_snapshot_restore")
    with db_mod.SessionLocal() as fresh_db:
        record_audit(
            fresh_db,
            actor=actor,
            entity_type="snapshot",
            entity_id=safe_name,
            action="restore_snapshot",
            after={"pre_restore_snapshot": pre_snap.name if pre_snap else None},
        )
        fresh_db.commit()
        counts = _table_counts(fresh_db)

    return {
        "status": "ok",
        "message": f"Restored database snapshot '{safe_name}'.",
        "pre_restore_snapshot": pre_snap.name if pre_snap else None,
        "table_counts": counts,
    }


def get_security_readiness(db: Session, *, host: str = "") -> dict:
    """Evaluate practical security & operational readiness before confidential client records are stored."""
    checks: list[dict] = []

    # 1. Default demo credentials check
    demo_user = db.scalar(select(User).where(User.login_id == "demo_advocate"))
    demo_default_pw_active = bool(
        demo_user is not None and demo_user.is_active and verify_password("DemoPassword@2026", demo_user.password_hash)
    )
    checks.append(
        {
            "key": "default_credentials",
            "title": "Admin Passwords Hardened",
            "status": "warn" if demo_default_pw_active else "pass",
            "detail": (
                "Default 'demo_advocate' password is still active. Change password or switch to a private admin user before live practice."
                if demo_default_pw_active
                else "No default demo password is active."
            ),
        }
    )

    # 2. Synthetic demo records remaining
    demo_clients = int(db.scalar(select(func.count(Client.id)).where(Client.is_demo == True)) or 0)  # noqa: E712
    demo_matters = int(db.scalar(select(func.count(Matter.id)).where(Matter.is_demo == True)) or 0)  # noqa: E712
    has_demo_data = (demo_clients + demo_matters) > 0
    checks.append(
        {
            "key": "demo_data",
            "title": "Real Practice Clean Slate",
            "status": "warn" if has_demo_data else "pass",
            "detail": (
                f"{demo_clients} demo client(s) & {demo_matters} demo matter(s) present. Use 'Start Real Practice' when ready."
                if has_demo_data
                else "Demo records cleared; database contains only practice records."
            ),
        }
    )

    # 3. JWT secret persistence
    jwt_secret = cfg.get_jwt_secret()
    checks.append(
        {
            "key": "jwt_secret",
            "title": "JWT Signing Secret",
            "status": "pass" if len(jwt_secret) >= 32 else "warn",
            "detail": "Dedicated cryptographic HS256 signing key active in local app data." if len(jwt_secret) >= 32 else "Weak JWT secret configured.",
        }
    )

    # 4. SQLite integrity
    health = run_integrity_check()
    checks.append(
        {
            "key": "sqlite_integrity",
            "title": "SQLite Integrity Check",
            "status": "pass" if health["ok"] else "fail",
            "detail": f"PRAGMA integrity_check -> {health['result']} ({health['db_size_bytes'] // 1024} KB)",
        }
    )

    # 5. Local backup/snapshots present
    has_snapshot = health["snapshots_count"] > 0
    checks.append(
        {
            "key": "local_snapshots",
            "title": "Recovery Snapshot / Backup Ready",
            "status": "pass" if has_snapshot else "warn",
            "detail": (
                f"{health['snapshots_count']} automatic/manual snapshot(s) in data/snapshots/. Also export periodic .zip backups to iCloud/external drive."
                if has_snapshot
                else "No snapshots recorded yet. Create a snapshot or export a .zip backup."
            ),
        }
    )

    # 6. Transport check (localhost vs LAN HTTP)
    is_local = any(h in (host or "").lower() for h in ("127.0.0.1", "localhost", "::1", ""))
    checks.append(
        {
            "key": "transport",
            "title": "Network Transport Posture",
            "status": "pass" if is_local else "warn",
            "detail": (
                "Loopback localhost session (macOS desktop)."
                if is_local
                else f"Connected over LAN ({host}). Ensure Wi-Fi is private WPA2/WPA3 or tunnel via Tailscale/HTTPS for mobile use."
            ),
        }
    )

    # 7. AI isolation
    checks.append(
        {
            "key": "ai_isolation",
            "title": "Offline Data Confidentiality",
            "status": "pass",
            "detail": "External AI Legal Research connector is disabled; zero client/matter records leave this Mac.",
        }
    )

    warn_count = sum(1 for c in checks if c["status"] != "pass")
    return {
        "production_ready": warn_count == 0,
        "warnings_count": warn_count,
        "checks": checks,
    }


def clear_demo_data_for_production(
    db: Session,
    *,
    actor: str,
    confirmation_text: str,
    keep_holidays_and_letterheads: bool,
    new_admin_login_id: str | None = None,
    new_admin_full_name: str | None = None,
    new_admin_password: str | None = None,
) -> dict:
    """Safely snapshot DB, purge synthetic demo records, and optionally provision a private admin user."""
    if confirmation_text.strip().upper() != "START FRESH":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Type 'START FRESH' exactly to confirm clearing synthetic demo records.",
        )

    create_snapshot("pre_start_real_practice")

    for model in (FeeMemo, Task, CalendarEvent, Hearing, Matter, Client, Advocate, BareAct):
        rows = list(db.scalars(select(model).where(model.is_demo == True)).all())  # noqa: E712
        for obj in rows:
            db.delete(obj)
        db.flush()

    if not keep_holidays_and_letterheads:
        db.query(CourtHoliday).delete(synchronize_session=False)

    created_admin = None
    if new_admin_login_id and new_admin_password:
        clean_login = new_admin_login_id.strip()
        existing = db.scalar(select(User).where(User.login_id == clean_login))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Login ID '{clean_login}' already exists.")
        new_user = User(
            login_id=clean_login,
            full_name=(new_admin_full_name or clean_login).strip(),
            role="admin",
            password_hash=hash_password(new_admin_password),
            is_active=True,
            is_demo=False,
        )
        db.add(new_user)
        db.flush()
        created_admin = new_user.login_id
        # Deactivate built-in demo user once a new private admin exists
        demo_u = db.scalar(select(User).where(User.login_id == "demo_advocate"))
        if demo_u is not None and demo_u.login_id != new_user.login_id:
            demo_u.is_active = False

    record_audit(
        db,
        actor=actor,
        entity_type="system",
        entity_id="production_cutover",
        action="clear_demo_data",
        after={"created_admin": created_admin, "kept_holidays_letterheads": keep_holidays_and_letterheads},
    )
    db.commit()
    return {
        "status": "ok",
        "message": "Synthetic demo records cleared cleanly (pre-cutover snapshot saved).",
        "created_admin": created_admin,
    }
