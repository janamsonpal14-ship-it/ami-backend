from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import BareActCreate, BareActDetailOut, BareActOut, ResearchAskRequest, ResearchConfigUpdate
from app.services.auth_service import get_current_user, require_admin
from app.services.law_service import (
    ask_legal_research,
    create_bare_act_from_text,
    delete_bare_act,
    get_bare_act_detail,
    get_bare_act_file,
    get_bare_act_page_text,
    get_research_status,
    list_bare_acts,
    list_research_history,
    render_bare_act_pdf_page,
    search_bare_acts_and_sections,
    update_research_config,
    upload_bare_act_file,
)

router = APIRouter(prefix="/api", tags=["law"])


@router.get("/bare-acts", response_model=list[BareActOut])
def get_bare_acts(
    q: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_bare_acts(db, q)


@router.post("/bare-acts", response_model=BareActDetailOut, status_code=status.HTTP_201_CREATED)
def create_bare_act(
    payload: BareActCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_bare_act_from_text(db, payload, actor=user.login_id)


@router.post("/bare-acts/upload", response_model=BareActDetailOut, status_code=status.HTTP_201_CREATED)
async def upload_bare_act(
    title: str = Form(""),
    short_title: str | None = Form(None),
    year: str | None = Form(None),
    jurisdiction: str = Form("India"),
    version_date: str | None = Form(None),
    source_note: str | None = Form(None),
    tags: str | None = Form(None),
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    parsed_year: int | None = None
    if year is not None and year.strip():
        try:
            parsed_year = int(year.strip())
        except ValueError:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid enactment year.")

    file_bytes = await file.read()
    return upload_bare_act_file(
        db,
        title=title,
        short_title=short_title,
        year=parsed_year,
        jurisdiction=jurisdiction,
        version_date=version_date,
        source_note=source_note,
        tags=tags,
        filename=file.filename or "bare_act.pdf",
        file_bytes=file_bytes,
        actor=user.login_id,
    )


@router.get("/bare-acts/search")
def search_bare_acts(
    q: str = Query(""),
    act_ids: str | None = Query(None),
    limit: int = Query(20, ge=1, le=50),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    parsed_ids: list[int] | None = None
    if act_ids and act_ids.strip():
        parsed_ids = []
        for part in act_ids.split(","):
            tok = part.strip()
            if tok.isdigit():
                parsed_ids.append(int(tok))
    return search_bare_acts_and_sections(db, q, act_ids=parsed_ids, limit=limit)


@router.get("/bare-acts/{act_id}", response_model=BareActDetailOut)
def read_bare_act_detail(
    act_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_bare_act_detail(db, act_id)


@router.get("/bare-acts/{act_id}/file")
def download_bare_act_original_file(
    act_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file_path, media_type, filename = get_bare_act_file(db, act_id)
    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        filename=filename,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@router.get("/bare-acts/{act_id}/page/{page_number}")
def get_bare_act_original_page_image(
    act_id: int,
    page_number: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    png_bytes = render_bare_act_pdf_page(db, act_id, page_number)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/bare-acts/{act_id}/page-text/{page_number}")
def read_bare_act_original_page_text(
    act_id: int,
    page_number: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_bare_act_page_text(db, act_id, page_number)


@router.delete("/bare-acts/{act_id}")
def remove_bare_act(
    act_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    return delete_bare_act(db, act_id, actor=user.login_id)


@router.get("/research/status")
def research_status(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_research_status(db)


@router.patch("/research/config")
def patch_research_config(
    payload: ResearchConfigUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_admin(user)
    return update_research_config(db, payload, actor=user.login_id)


@router.post("/research/ask")
def post_research_ask(
    payload: ResearchAskRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return ask_legal_research(db, payload, actor=user.login_id)


@router.get("/research/history")
def get_research_history(
    limit: int = Query(25, ge=1, le=100),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_research_history(db, limit=limit)
