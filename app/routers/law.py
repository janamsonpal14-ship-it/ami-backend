from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from datetime import date

from app.database import get_db
from app.models import User
from app.schemas import (
    AIUsageSummaryOut,
    ApplicableLawOut,
    BareActCreate,
    BareActDetailOut,
    BareActOut,
    BareActVersionOut,
    FourPartAnalysisOut,
    LegislativeAmendmentOut,
    ResearchAskRequest,
    ResearchConfigUpdate,
    ThreePointAnalysisOut,
)
from app.services.auth_service import get_current_user, require_admin
from app.services.law_service import (
    ask_legal_research,
    create_bare_act_from_text,
    delete_bare_act,
    get_ai_usage_stats,
    get_bare_act_detail,
    get_bare_act_file,
    get_bare_act_page_text,
    get_research_status,
    get_three_point_analysis,
    list_bare_acts,
    list_research_history,
    render_bare_act_pdf_page,
    search_bare_acts_and_sections,
    update_research_config,
    upload_bare_act_file,
)
from app.services.legislative_monitor import (
    check_official_legislative_updates,
    get_monitoring_status,
)
from app.services.statutory_downloader import (
    execute_pilot_ingestion,
    query_law_applicable_on_date,
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


@router.get("/bare-acts/{act_id}/applicable-law", response_model=ApplicableLawOut)
def read_law_applicable_on_date(
    act_id: int,
    as_of_date: date = Query(..., description="Query date in YYYY-MM-DD format"),
    section_no: str | None = Query(None, description="Optional section number e.g. '29A' or 'Section 34'"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve the statutory version and amendments applicable on an exact historical or current date."""
    try:
        return query_law_applicable_on_date(db, act_id, as_of_date, section_no=section_no)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))


@router.get("/bare-acts/{act_id}/versions", response_model=list[BareActVersionOut])
def read_bare_act_versions(
    act_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve historical and current statutory versions for an Act."""
    detail = get_bare_act_detail(db, act_id)
    return detail.versions


@router.get("/bare-acts/{act_id}/amendments", response_model=list[LegislativeAmendmentOut])
def read_bare_act_amendments(
    act_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve tracked legislative amendments and notifications for an Act."""
    detail = get_bare_act_detail(db, act_id)
    return detail.amendments


@router.get("/law/monitoring/status")
def read_legislative_monitoring_status(
    _user: User = Depends(get_current_user),
):
    """Retrieve the execution status and findings of the daily legislative monitoring daemon."""
    return get_monitoring_status()


@router.post("/law/monitoring/run-now")
def trigger_legislative_monitoring_now(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually trigger an immediate check of official repositories for statutory amendments and new laws."""
    require_admin(user)
    return check_official_legislative_updates(db)


@router.post("/bare-acts/pilot-ingest", response_model=list[BareActOut])
def trigger_pilot_ingestion(
    user: User = Depends(get_current_user),
):
    """Admin endpoint to trigger automated download, verification, and ingestion of the 5 Pilot Acts."""
    require_admin(user)
    ingested = execute_pilot_ingestion()
    from app.services.law_service import _act_to_out
    return [_act_to_out(a) for a in ingested]


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


@router.get("/bare-acts/{act_id}/sections/{section_id}/four-part", response_model=FourPartAnalysisOut)
@router.get("/bare-acts/{act_id}/sections/{section_id}/three-point", response_model=FourPartAnalysisOut)
def read_section_four_part_analysis(
    act_id: int,
    section_id: int,
    section_no: str | None = Query(None),
    regenerate: bool = Query(False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Returns the two-part statutory reference:
    1. Statutory Provision & Original PDF Page Reference
    2. Characteristics of the Provision
    """
    return get_three_point_analysis(db, act_id, section_id, section_no=section_no, regenerate=regenerate, actor=user.login_id)


@router.get("/law/ai-usage", response_model=AIUsageSummaryOut)
def read_law_ai_usage(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Returns Pay-As-You-Go AI usage statistics, tokens consumed, and cumulative expenditure."""
    return get_ai_usage_stats(db)

