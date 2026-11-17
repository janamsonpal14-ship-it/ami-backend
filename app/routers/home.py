from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.services.auth_service import get_current_user
from app.services.excel_service import (
    commit_excel_import,
    export_client_wise_xlsx,
    export_monthly_xlsx,
    preview_excel_import,
)
from app.services.home_service import (
    get_client_wise_report,
    get_court_pack,
    get_home_pinboard,
    get_monthly_report,
    global_search,
)
from app.utils.indian_format import ist_today

router = APIRouter(prefix="/api", tags=["home_and_reports"])


@router.get("/home/pinboard")
def home_pinboard(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_home_pinboard(db)


@router.get("/home/court-pack")
def court_pack(
    target_date: date | None = Query(default=None, alias="date"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_court_pack(db, target_date)


@router.get("/search")
def search(
    q: str = Query(default=""),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return global_search(db, q)


@router.get("/reports/monthly")
def monthly_report(
    year: int | None = None,
    month: int | None = None,
    search: str | None = None,
    filter_mode: str = Query(default="month"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = ist_today()
    y = year or today.year
    m = month or today.month
    return {
        "year": y,
        "month": m,
        "filter_mode": filter_mode,
        "rows": get_monthly_report(db, y, m, search, filter_mode=filter_mode),
    }


@router.get("/reports/monthly/export.xlsx")
def monthly_report_xlsx(
    year: int | None = None,
    month: int | None = None,
    search: str | None = None,
    filter_mode: str = Query(default="month"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = ist_today()
    y = year or today.year
    m = month or today.month
    content, filename = export_monthly_xlsx(db, y, m, search, filter_mode=filter_mode)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports/client-wise")
def client_wise_report(
    search: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"clients": get_client_wise_report(db, search)}


@router.get("/reports/client-wise/export.xlsx")
def client_wise_report_xlsx(
    search: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content, filename = export_client_wise_xlsx(db, search)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import/excel/preview")
async def excel_import_preview(
    file: UploadFile = File(...),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not (file.filename or "").lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Please upload an .xlsx Excel workbook.")
    data = await file.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Workbook exceeds 15 MB limit.")
    return preview_excel_import(db, file.filename or "workbook.xlsx", data)


class ExcelCommitRequest(BaseModel):
    filename: str
    rows: list[dict]


@router.post("/import/excel/commit")
def excel_import_commit(
    payload: ExcelCommitRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return commit_excel_import(db, filename=payload.filename, rows=payload.rows, actor=user.login_id)
