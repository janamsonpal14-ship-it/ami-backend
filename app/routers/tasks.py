from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.models import Task, User
from app.schemas import TaskCreate, TaskOut, TaskUpdate
from app.services.audit_service import record_audit
from app.services.auth_service import get_current_user
from app.utils.indian_format import ist_now

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


def _task_to_out(t: Task) -> TaskOut:
    return TaskOut(
        id=t.id,
        title=t.title,
        task_type=t.task_type,
        due_date=t.due_date,
        due_time=t.due_time,
        priority=t.priority,
        status=t.status,
        notes=t.notes,
        matter_id=t.matter_id,
        matter_title=t.matter.title if t.matter else None,
        client_id=t.client_id,
    )


@router.get("", response_model=list[TaskOut])
def list_tasks(
    status_filter: str | None = None,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = (
        select(Task)
        .options(selectinload(Task.matter))
        .order_by(Task.status != "Open", Task.due_date.is_(None), Task.due_date, Task.id.desc())
    )
    tasks = list(db.scalars(stmt).all())
    if status_filter:
        tasks = [t for t in tasks if t.status == status_filter]
    return [_task_to_out(t) for t in tasks]


@router.post("", response_model=TaskOut)
def create_task(
    payload: TaskCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = Task(
        title=payload.title.strip(),
        task_type=payload.task_type,
        due_date=payload.due_date,
        due_time=payload.due_time,
        priority=payload.priority,
        notes=payload.notes,
        matter_id=payload.matter_id,
        client_id=payload.client_id,
        source_hearing_id=payload.source_hearing_id,
        status="Open",
    )
    db.add(task)
    db.flush()
    record_audit(db, actor=user.login_id, entity_type="task", entity_id=task.id, action="create", after={"title": task.title})
    db.commit()
    refreshed = db.scalar(select(Task).options(selectinload(Task.matter)).where(Task.id == task.id))
    assert refreshed is not None
    return _task_to_out(refreshed)


@router.patch("/{task_id}", response_model=TaskOut)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    task = db.scalar(select(Task).options(selectinload(Task.matter)).where(Task.id == task_id))
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found.")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(task, k, v)
    if data.get("status") == "Completed":
        task.completed_at = ist_now()
    elif data.get("status") == "Open":
        task.completed_at = None
    record_audit(db, actor=user.login_id, entity_type="task", entity_id=task.id, action="update", after={"status": task.status})
    db.commit()
    refreshed = db.scalar(select(Task).options(selectinload(Task.matter)).where(Task.id == task.id))
    assert refreshed is not None
    return _task_to_out(refreshed)

