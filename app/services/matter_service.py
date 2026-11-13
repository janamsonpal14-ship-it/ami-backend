from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import Advocate, Client, ClientMatter, Matter, MatterAdvocate
from app.schemas import (
    AdvocateOut,
    ClientCreate,
    ClientOut,
    ClientUpdate,
    DuplicateMatchOut,
    MatterClientSummary,
    MatterCreate,
    MatterSummaryOut,
    MatterUpdate,
)
from app.services.audit_service import record_audit
from app.utils.security import normalize_case_number, normalize_name


def client_to_out(client: Client) -> ClientOut:
    return ClientOut(
        id=client.id,
        display_name=client.display_name,
        contact_info=client.contact_info,
        notes=client.notes,
        is_active=client.is_active,
        is_demo=client.is_demo,
        matter_count=len(client.matter_links),
    )


def list_clients(db: Session, query: str | None = None, suggest: str | None = None) -> list[ClientOut]:
    clients = list(
        db.scalars(
            select(Client)
            .options(selectinload(Client.matter_links))
            .order_by(Client.display_name)
        ).all()
    )
    if suggest:
        norm = normalize_name(suggest)
        tokens = set(norm.split())
        scored: list[tuple[int, Client]] = []
        for c in clients:
            c_tokens = set(c.normalized_name.split())
            if norm and (norm in c.normalized_name or c.normalized_name in norm):
                scored.append((100, c))
            elif tokens and c_tokens:
                overlap = len(tokens & c_tokens)
                if overlap > 0:
                    scored.append((overlap * 20, c))
        scored.sort(key=lambda item: (-item[0], item[1].display_name))
        return [client_to_out(c) for _, c in scored[:8]]

    if query:
        q = query.lower().strip()
        clients = [
            c for c in clients
            if q in c.display_name.lower() or (c.contact_info and q in c.contact_info.lower())
        ]
    return [client_to_out(c) for c in clients]


def create_client(db: Session, payload: ClientCreate, actor: str) -> ClientOut:
    name = payload.display_name.strip()
    client = Client(
        display_name=name,
        normalized_name=normalize_name(name),
        contact_info=payload.contact_info,
        notes=payload.notes,
        is_active=True,
    )
    db.add(client)
    db.flush()
    record_audit(db, actor=actor, entity_type="client", entity_id=client.id, action="create", after={"display_name": name})
    db.commit()
    db.refresh(client)
    return client_to_out(client)


def update_client(db: Session, client_id: int, payload: ClientUpdate, actor: str) -> ClientOut:
    client = db.scalar(select(Client).options(selectinload(Client.matter_links)).where(Client.id == client_id))
    if client is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found.")
    data = payload.model_dump(exclude_unset=True)
    if "display_name" in data and data["display_name"]:
        client.display_name = data["display_name"].strip()
        client.normalized_name = normalize_name(client.display_name)
        del data["display_name"]
    for k, v in data.items():
        setattr(client, k, v)
    record_audit(db, actor=actor, entity_type="client", entity_id=client.id, action="update", after={"display_name": client.display_name})
    db.commit()
    db.refresh(client)
    return client_to_out(client)


def list_advocates(db: Session) -> list[AdvocateOut]:
    advs = list(db.scalars(select(Advocate).where(Advocate.is_active == True).order_by(Advocate.full_name)).all())
    return [AdvocateOut(id=a.id, full_name=a.full_name, is_active=a.is_active) for a in advs]


def create_or_get_advocate(db: Session, full_name: str, actor: str) -> AdvocateOut:
    clean = " ".join(full_name.strip().split())
    existing = db.scalars(select(Advocate)).all()
    for adv in existing:
        if adv.full_name.lower() == clean.lower():
            if not adv.is_active:
                adv.is_active = True
                db.commit()
            return AdvocateOut(id=adv.id, full_name=adv.full_name, is_active=adv.is_active)
    adv = Advocate(full_name=clean, is_active=True)
    db.add(adv)
    db.flush()
    record_audit(db, actor=actor, entity_type="advocate", entity_id=adv.id, action="create", after={"full_name": clean})
    db.commit()
    db.refresh(adv)
    return AdvocateOut(id=adv.id, full_name=adv.full_name, is_active=adv.is_active)


def matter_to_summary(matter: Matter) -> MatterSummaryOut:
    return MatterSummaryOut(
        id=matter.id,
        title=matter.title,
        court_forum=matter.court_forum,
        case_type=matter.case_type,
        case_number=matter.case_number,
        connected_cases=matter.connected_cases,
        current_stage=matter.current_stage,
        status=matter.status,
        current_next_date=matter.current_next_date,
        current_next_purpose=matter.current_next_purpose,
        notes=matter.notes,
        cnr=matter.cnr,
        ecourts_synced_at=matter.ecourts_synced_at,
        ecourts_data_json=matter.ecourts_data_json,
        clients=[
            MatterClientSummary(
                client_id=link.client.id,
                display_name=link.client.display_name,
                role=link.role,
                is_billing_client=link.is_billing_client,
            )
            for link in matter.client_links
            if link.client is not None
        ],
        advocates=[
            AdvocateOut(id=link.advocate.id, full_name=link.advocate.full_name, is_active=link.advocate.is_active)
            for link in matter.advocate_links
            if link.advocate is not None
        ],
        is_demo=matter.is_demo,
    )


def find_possible_duplicate_matters(
    db: Session, *, title: str, case_number: str, client_ids: list[int]
) -> list[DuplicateMatchOut]:
    """Suggest potential duplicate matters while leaving the decision to the user."""
    norm_case = normalize_case_number(case_number)
    norm_title = normalize_name(title)
    matters = list(
        db.scalars(select(Matter).options(selectinload(Matter.client_links))).all()
    )
    matches: list[DuplicateMatchOut] = []
    for m in matters:
        reasons: list[str] = []
        if norm_case and m.normalized_case_number and norm_case == m.normalized_case_number:
            reasons.append("Exact same case number")
        elif norm_title and normalize_name(m.title) == norm_title:
            reasons.append("Identical party/matter title")
        elif client_ids and norm_title:
            m_clients = {link.client_id for link in m.client_links}
            if m_clients & set(client_ids) and (
                norm_title in normalize_name(m.title) or normalize_name(m.title) in norm_title
            ):
                reasons.append("Similar title under the same client")
        if reasons:
            matches.append(
                DuplicateMatchOut(
                    matter_id=m.id,
                    title=m.title,
                    case_number=m.case_number,
                    court_forum=m.court_forum,
                    reason="; ".join(reasons),
                )
            )
    return matches


def list_matters(db: Session, query: str | None = None, status_filter: str | None = None, client_id: int | None = None) -> list[MatterSummaryOut]:
    matters = list(
        db.scalars(
            select(Matter)
            .options(
                selectinload(Matter.client_links).selectinload(ClientMatter.client),
                selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate),
            )
            .order_by(Matter.current_next_date.is_(None), Matter.current_next_date, Matter.title)
        ).all()
    )
    result: list[MatterSummaryOut] = []
    q = query.lower().strip() if query else ""
    for m in matters:
        if status_filter and m.status != status_filter:
            continue
        if client_id is not None and not any(link.client_id == client_id for link in m.client_links):
            continue
        if q:
            client_names = " ".join(link.client.display_name.lower() for link in m.client_links if link.client)
            haystack = f"{m.title} {m.court_forum} {m.case_number} {m.case_type} {m.current_stage} {client_names}".lower()
            if q not in haystack:
                continue
        result.append(matter_to_summary(m))
    return result


def create_matter(db: Session, payload: MatterCreate, actor: str) -> MatterSummaryOut:
    for link in payload.clients:
        if db.get(Client, link.client_id) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Client {link.client_id} not found.")

    if not payload.allow_duplicate:
        dupes = find_possible_duplicate_matters(
            db,
            title=payload.title,
            case_number=payload.case_number,
            client_ids=[c.client_id for c in payload.clients],
        )
        if dupes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "message": "Possible duplicate matter detected. Review suggestions or confirm creation.",
                    "suggestions": [d.model_dump() for d in dupes],
                },
            )

    matter = Matter(
        title=payload.title.strip(),
        court_forum=payload.court_forum.strip(),
        case_type=payload.case_type.strip(),
        case_number=payload.case_number.strip(),
        normalized_case_number=normalize_case_number(payload.case_number),
        connected_cases=payload.connected_cases,
        current_stage=payload.current_stage.strip(),
        status=payload.status,
        current_next_date=payload.current_next_date,
        current_next_purpose=payload.current_next_purpose,
        notes=payload.notes,
        cnr=payload.cnr.strip().upper() if payload.cnr else None,
    )
    db.add(matter)
    db.flush()

    for link in payload.clients:
        db.add(
            ClientMatter(
                client_id=link.client_id,
                matter_id=matter.id,
                role=link.role,
                is_billing_client=link.is_billing_client,
            )
        )
    for adv_id in set(payload.advocate_ids):
        if db.get(Advocate, adv_id):
            db.add(MatterAdvocate(matter_id=matter.id, advocate_id=adv_id))

    record_audit(db, actor=actor, entity_type="matter", entity_id=matter.id, action="create", after={"title": matter.title, "case_number": matter.case_number})
    db.commit()
    return get_matter_summary(db, matter.id)


def get_matter_summary(db: Session, matter_id: int) -> MatterSummaryOut:
    matter = db.scalar(
        select(Matter)
        .execution_options(populate_existing=True)
        .options(
            selectinload(Matter.client_links).selectinload(ClientMatter.client),
            selectinload(Matter.advocate_links).selectinload(MatterAdvocate.advocate),
        )
        .where(Matter.id == matter_id)
    )
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")
    return matter_to_summary(matter)


def update_matter(db: Session, matter_id: int, payload: MatterUpdate, actor: str) -> MatterSummaryOut:
    matter = db.scalar(
        select(Matter)
        .options(selectinload(Matter.client_links), selectinload(Matter.advocate_links))
        .where(Matter.id == matter_id)
    )
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    data = payload.model_dump(exclude_unset=True)
    clients = data.pop("clients", None)
    advocate_ids = data.pop("advocate_ids", None)

    for k, v in data.items():
        setattr(matter, k, v)
    if "case_number" in data:
        matter.normalized_case_number = normalize_case_number(matter.case_number)

    if clients is not None:
        matter.client_links.clear()
        db.flush()
        for c in clients:
            db.add(
                ClientMatter(
                    client_id=c["client_id"],
                    matter_id=matter.id,
                    role=c.get("role", "Client"),
                    is_billing_client=c.get("is_billing_client", True),
                )
            )

    if advocate_ids is not None:
        matter.advocate_links.clear()
        db.flush()
        for adv_id in set(advocate_ids):
            if db.get(Advocate, adv_id):
                db.add(MatterAdvocate(matter_id=matter.id, advocate_id=adv_id))

    record_audit(db, actor=actor, entity_type="matter", entity_id=matter.id, action="update", after={"title": matter.title, "stage": matter.current_stage})
    db.commit()
    return get_matter_summary(db, matter.id)


def delete_matter(db: Session, matter_id: int, actor: str) -> dict:
    matter = db.scalar(
        select(Matter)
        .options(
            selectinload(Matter.client_links),
            selectinload(Matter.advocate_links),
            selectinload(Matter.hearings),
            selectinload(Matter.tasks),
        )
        .where(Matter.id == matter_id)
    )
    if matter is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Matter not found.")

    deleted_title = matter.title
    deleted_id = matter.id
    record_audit(
        db,
        actor=actor,
        entity_type="matter",
        entity_id=deleted_id,
        action="delete",
        before={"title": matter.title, "case_number": matter.case_number},
    )
    db.delete(matter)
    db.commit()
    return {"status": "success", "deleted_matter_id": deleted_id, "title": deleted_title}



def check_conflicts(db: Session, query: str) -> dict:
    """Search existing clients and matters/adverse party names for potential conflicts of interest."""
    raw = query.strip()
    if len(raw) < 2:
        return {"query": raw, "has_potential_conflict": False, "client_matches": [], "matter_matches": []}

    norm_q = normalize_name(raw)
    tokens = {t for t in norm_q.split() if len(t) >= 3}

    clients = list(db.scalars(select(Client).options(selectinload(Client.matter_links))).all())
    client_matches: list[dict] = []
    for c in clients:
        c_tokens = set(c.normalized_name.split())
        if (norm_q and norm_q in c.normalized_name) or (raw.lower() in (c.contact_info or "").lower()) or (tokens and len(tokens & c_tokens) >= 1 and (len(tokens) == 1 or len(tokens & c_tokens) >= 2)):
            client_matches.append(
                {
                    "client_id": c.id,
                    "display_name": c.display_name,
                    "matter_count": len(c.matter_links),
                    "reason": "Existing Chambers Client",
                }
            )

    matters = list(
        db.scalars(
            select(Matter).options(selectinload(Matter.client_links).selectinload(ClientMatter.client))
        ).all()
    )
    matter_matches: list[dict] = []
    for m in matters:
        hay_title = normalize_name(m.title)
        hay_notes = (m.notes or "").lower()
        hay_conn = (m.connected_cases or "").lower()
        reasons: list[str] = []
        if norm_q and norm_q in hay_title:
            reasons.append("Appears in Matter Title / Cause Title")
        elif tokens and len(tokens & set(hay_title.split())) >= min(2, len(tokens)):
            reasons.append("Partial Party Name Match in Title")
        if raw.lower() in hay_notes or raw.lower() in hay_conn:
            reasons.append("Mentioned in Notes / Connected Cases")
        if reasons:
            matter_matches.append(
                {
                    "matter_id": m.id,
                    "title": m.title,
                    "case_number": m.case_number,
                    "court_forum": m.court_forum,
                    "status": m.status,
                    "clients": [link.client.display_name for link in m.client_links if link.client],
                    "reason": " & ".join(reasons),
                }
            )

    return {
        "query": raw,
        "has_potential_conflict": bool(client_matches or matter_matches),
        "client_matches": client_matches[:15],
        "matter_matches": matter_matches[:15],
    }
