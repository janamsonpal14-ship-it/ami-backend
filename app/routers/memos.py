from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LetterheadTemplate, User
from app.schemas import FeeMemoCreate, FeeMemoOut, FeeMemoUpdate, MemoQuickStatusRequest, PaymentCreate, VoidMemoRequest
from app.services.auth_service import get_current_user
from app.services.billing_service import (
    create_memo,
    delete_memo,
    get_fee_schedule,
    get_memo,
    issue_memo,
    list_memos,
    record_payment,
    set_memo_quick_status,
    update_fee_schedule,
    update_memo,
    void_memo,
)
from app.services.memo_exporter import (
    delete_letterhead,
    export_memo_docx,
    export_memo_pdf,
    list_letterheads,
    update_letterhead,
    upload_letterhead,
)

router = APIRouter(prefix="/api", tags=["fee_memos_and_letterheads"])


@router.get("/memos/fee-schedule")
def api_get_fee_schedule(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_fee_schedule(db)


@router.put("/memos/fee-schedule")
@router.post("/memos/fee-schedule")
def api_update_fee_schedule(
    items: list[dict],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_fee_schedule(db, items, actor=user.login_id)


@router.get("/memos", response_model=list[FeeMemoOut])
def get_memos(
    status: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_memos(db, status_filter=status)


@router.post("/memos", response_model=FeeMemoOut)
def post_memo(
    payload: FeeMemoCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_memo(db, payload, actor=user.login_id)


@router.get("/memos/{memo_id}", response_model=FeeMemoOut)
def read_memo(
    memo_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_memo(db, memo_id)


@router.patch("/memos/{memo_id}", response_model=FeeMemoOut)
def patch_memo(
    memo_id: int,
    payload: FeeMemoUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_memo(db, memo_id, payload, actor=user.login_id)


@router.delete("/memos/{memo_id}")
def api_delete_memo(
    memo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return delete_memo(db, memo_id, actor=user.login_id)


@router.post("/memos/{memo_id}/issue", response_model=FeeMemoOut)
def post_issue(
    memo_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return issue_memo(db, memo_id, actor=user.login_id)


@router.post("/memos/{memo_id}/void", response_model=FeeMemoOut)
def post_void(
    memo_id: int,
    payload: VoidMemoRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return void_memo(db, memo_id, payload.reason, actor=user.login_id)


@router.post("/memos/{memo_id}/payments", response_model=FeeMemoOut)
def post_payment(
    memo_id: int,
    payload: PaymentCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return record_payment(db, memo_id, payload, actor=user.login_id)


@router.post("/memos/{memo_id}/quick-status", response_model=FeeMemoOut)
def post_memo_quick_status(
    memo_id: int,
    payload: MemoQuickStatusRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return set_memo_quick_status(db, memo_id, payload, actor=user.login_id)


@router.get("/memos/{memo_id}/export.docx")
def download_memo_docx(
    memo_id: int,
    include_annexure: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content, filename = export_memo_docx(db, memo_id, include_annexure=include_annexure)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/memos/{memo_id}/export.pdf")
def download_memo_pdf(
    memo_id: int,
    include_annexure: bool = Query(default=False),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content, filename = export_memo_pdf(db, memo_id, include_annexure=include_annexure)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Letterhead Endpoints ---

@router.get("/letterheads")
def get_letterheads(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_letterheads(db)


@router.post("/letterheads")
async def create_letterhead(
    file: UploadFile = File(...),
    name: str = Form(default="Office Letterhead"),
    top_margin_mm: int = Form(default=20),
    bottom_margin_mm: int = Form(default=20),
    left_margin_mm: int = Form(default=20),
    right_margin_mm: int = Form(default=20),
    image_width_pct: int = Form(default=100),
    is_default: bool = Form(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await file.read()
    return upload_letterhead(
        db,
        name=name,
        filename=file.filename or "letterhead.png",
        image_bytes=data,
        top_margin_mm=top_margin_mm,
        bottom_margin_mm=bottom_margin_mm,
        left_margin_mm=left_margin_mm,
        right_margin_mm=right_margin_mm,
        image_width_pct=image_width_pct,
        is_default=is_default,
        actor=user.login_id,
    )


class LetterheadPatch(BaseModel):
    name: str | None = None
    top_margin_mm: int | None = None
    bottom_margin_mm: int | None = None
    left_margin_mm: int | None = None
    right_margin_mm: int | None = None
    image_width_pct: int | None = None
    is_default: bool | None = None


@router.patch("/letterheads/{letterhead_id}")
def patch_letterhead(
    letterhead_id: int,
    payload: LetterheadPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_letterhead(db, letterhead_id, payload.model_dump(exclude_unset=True), actor=user.login_id)


@router.get("/letterheads/{letterhead_id}/image")
def get_letterhead_image(
    letterhead_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    import app.config as cfg

    lh = db.get(LetterheadTemplate, letterhead_id)
    if lh is None or not lh.file_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letterhead image not found.")
    img_path = Path(lh.file_path)
    if not img_path.exists():
        img_path = cfg.LETTERHEADS_DIR / img_path.name
    if not img_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Letterhead image not found.")
    return FileResponse(img_path)


@router.delete("/letterheads/{letterhead_id}")
def delete_letterhead_endpoint(
    letterhead_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return delete_letterhead(db, letterhead_id, actor=user.login_id)
