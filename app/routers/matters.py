from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import (
    AdvocateCreate,
    AdvocateOut,
    BatchEodRequest,
    ClientCreate,
    ClientOut,
    ClientUpdate,
    DuplicateMatchOut,
    HearingOut,
    HearingSaveRequest,
    MatterCreate,
    MatterSummaryOut,
    MatterUpdate,
    NotReachedRequest,
)
from app.services.auth_service import get_current_user
from app.services.hearing_service import (
    batch_save_eod,
    get_pending_eod_matters,
    list_matter_hearings,
    mark_not_reached,
    save_hearing,
)
from app.services.matter_service import (
    check_conflicts,
    create_client,
    create_matter,
    create_or_get_advocate,
    delete_matter,
    find_possible_duplicate_matters,
    get_matter_summary,
    list_advocates,
    list_clients,
    list_matters,
    update_client,
    update_matter,
)

router = APIRouter(prefix="/api", tags=["matters_and_hearings"])


@router.get("/clients", response_model=list[ClientOut])
def get_clients(
    q: str | None = None,
    suggest: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_clients(db, query=q, suggest=suggest)


@router.post("/clients", response_model=ClientOut)
def post_client(
    payload: ClientCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_client(db, payload, actor=user.login_id)


@router.patch("/clients/{client_id}", response_model=ClientOut)
def patch_client(
    client_id: int,
    payload: ClientUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_client(db, client_id, payload, actor=user.login_id)


@router.get("/advocates", response_model=list[AdvocateOut])
def get_advocates(
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_advocates(db)


@router.post("/advocates", response_model=AdvocateOut)
def post_advocate(
    payload: AdvocateCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_or_get_advocate(db, payload.full_name, actor=user.login_id)


@router.get("/matters", response_model=list[MatterSummaryOut])
def get_matters(
    q: str | None = None,
    status: str | None = None,
    client_id: int | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_matters(db, query=q, status_filter=status, client_id=client_id)


@router.get("/matters/check-duplicate", response_model=list[DuplicateMatchOut])
def check_matter_duplicate(
    title: str = Query(default=""),
    case_number: str = Query(default=""),
    client_ids: list[int] = Query(default=[]),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return find_possible_duplicate_matters(db, title=title, case_number=case_number, client_ids=client_ids)


@router.get("/matters/conflict-check")
def conflict_check(
    q: str = Query(default=""),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return check_conflicts(db, q)


@router.post("/matters", response_model=MatterSummaryOut)
def post_matter(
    payload: MatterCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return create_matter(db, payload, actor=user.login_id)


@router.get("/matters/{matter_id}", response_model=MatterSummaryOut)
def get_matter(
    matter_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return get_matter_summary(db, matter_id)


@router.patch("/matters/{matter_id}", response_model=MatterSummaryOut)
def patch_matter(
    matter_id: int,
    payload: MatterUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return update_matter(db, matter_id, payload, actor=user.login_id)


@router.delete("/matters/{matter_id}")
def remove_matter(
    matter_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return delete_matter(db, matter_id, actor=user.login_id)



@router.get("/matters/{matter_id}/hearings", response_model=list[HearingOut])
def get_hearings(
    matter_id: int,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return list_matter_hearings(db, matter_id)


@router.post("/matters/{matter_id}/hearings", response_model=HearingOut)
def post_hearing(
    matter_id: int,
    payload: HearingSaveRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return save_hearing(db, matter_id, payload, actor=user.login_id)


@router.post("/matters/{matter_id}/hearings/not-reached", response_model=HearingOut)
def post_not_reached(
    matter_id: int,
    payload: NotReachedRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return mark_not_reached(db, matter_id, payload, actor=user.login_id)


@router.get("/hearings/pending-eod")
def pending_eod(
    target_date: date | None = Query(default=None, alias="date"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return {"items": get_pending_eod_matters(db, target_date)}


@router.post("/hearings/batch-eod", response_model=list[HearingOut])
def post_batch_eod(
    payload: BatchEodRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return batch_save_eod(db, payload, actor=user.login_id)

