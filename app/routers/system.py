from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import desc, select, text
from sqlalchemy.orm import Session

import app.config as cfg
from app.database import get_db
from app.models import AuditEvent, OfficeSettings, User
from app.schemas import AuditLogOut, OfficeSettingsOut, OfficeSettingsUpdate, StartFreshRequest
from app.services.audit_service import create_snapshot, record_audit
from app.services.auth_service import get_current_user, require_admin
from app.services.backup_service import (
    clear_demo_data_for_production,
    export_backup_bundle,
    get_security_readiness,
    restore_backup_bundle,
    restore_snapshot_file,
    run_integrity_check,
)
from app.services.seed_service import reset_demo_data

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/settings", response_model=OfficeSettingsOut)
def get_settings(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.scalar(select(OfficeSettings))
    if s is None:
        s = OfficeSettings()
        db.add(s)
        db.commit()
        db.refresh(s)
    return OfficeSettingsOut(
        office_name=s.office_name,
        advocate_signatory=s.advocate_signatory,
        default_bhc_bench=s.default_bhc_bench,
        bill_number_prefix=s.bill_number_prefix,
        next_bill_seq=s.next_bill_seq,
        default_bank_details=s.default_bank_details,
    )


@router.patch("/settings", response_model=OfficeSettingsOut)
def update_settings(
    payload: OfficeSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    s = db.scalar(select(OfficeSettings))
    if s is None:
        s = OfficeSettings()
        db.add(s)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(s, k, v)
    record_audit(db, actor=user.login_id, entity_type="settings", entity_id=1, action="update")
    db.commit()
    db.refresh(s)
    return OfficeSettingsOut(
        office_name=s.office_name,
        advocate_signatory=s.advocate_signatory,
        default_bhc_bench=s.default_bhc_bench,
        bill_number_prefix=s.bill_number_prefix,
        next_bill_seq=s.next_bill_seq,
        default_bank_details=s.default_bank_details,
    )


@router.get("/snapshots")
def list_snapshots(_user: User = Depends(get_current_user)):
    files = sorted(cfg.SNAPSHOTS_DIR.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True) if cfg.SNAPSHOTS_DIR.exists() else []
    return {
        "snapshots": [
            {"filename": f.name, "size_bytes": f.stat().st_size}
            for f in files[:25]
        ]
    }


@router.post("/snapshots/create")
def trigger_manual_snapshot(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    path = create_snapshot("manual")
    record_audit(db, actor=user.login_id, entity_type="snapshot", entity_id=path.name if path else "none", action="create")
    db.commit()
    return {"status": "ok", "filename": path.name if path else None}


@router.post("/snapshots/{filename}/restore")
def restore_snapshot(
    filename: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    return restore_snapshot_file(db, filename=filename, actor=user.login_id)


@router.get("/system/integrity")
def db_integrity(_user: User = Depends(get_current_user)):
    return run_integrity_check()


@router.get("/system/security-readiness")
def security_readiness(
    request: Request,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_security_readiness(db, host=request.headers.get("host", ""))


@router.post("/system/start-fresh")
def start_fresh_production(
    payload: StartFreshRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    return clear_demo_data_for_production(
        db,
        actor=user.login_id,
        confirmation_text=payload.confirmation_text,
        keep_holidays_and_letterheads=payload.keep_holidays_and_letterheads,
        new_admin_login_id=payload.new_admin_login_id,
        new_admin_full_name=payload.new_admin_full_name,
        new_admin_password=payload.new_admin_password,
    )


@router.post("/backup/export")
def backup_export(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    zip_bytes, filename = export_backup_bundle(db, actor=user.login_id)
    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/backup/restore")
async def backup_restore(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload a valid .zip backup bundle.")
    zip_bytes = await file.read()
    return restore_backup_bundle(db, filename=file.filename or "backup.zip", zip_bytes=zip_bytes, actor=user.login_id)


@router.get("/system/download-mobile-db")
def download_mobile_db(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Serve optimized SQLite database for 100% offline courtroom storage on mobile devices."""
    # Flush SQLite write-ahead log cleanly into amico.db
    try:
        db.execute(text("PRAGMA wal_checkpoint(FULL);"))
        db.commit()
    except Exception:
        pass

    db_path = cfg.DB_PATH
    if not db_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Database file not found.")

    return FileResponse(
        path=str(db_path),
        filename="amico.db",
        media_type="application/x-sqlite3",
        headers={
            "Content-Disposition": 'attachment; filename="amico.db"',
            "Cache-Control": "no-cache",
        },
    )


@router.get("/audit-logs", response_model=list[AuditLogOut])
def get_audit_logs(
    entity_type: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    stmt = select(AuditEvent)
    if entity_type and entity_type.strip():
        stmt = stmt.where(AuditEvent.entity_type == entity_type.strip())
    rows = list(db.scalars(stmt.order_by(desc(AuditEvent.id)).limit(limit)).all())
    return [
        AuditLogOut(
            id=r.id,
            actor=r.actor,
            entity_type=r.entity_type,
            entity_id=r.entity_id,
            action=r.action,
            before_json=r.before_json,
            after_json=r.after_json,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/demo/reset")
def reset_demo(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    reset_demo_data(db, actor=user.login_id)
    return {"status": "ok", "message": "Synthetic demo dataset reset cleanly (pre-reset snapshot saved)."}

