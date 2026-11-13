from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    FeeAdjustment,
    FeeMemo,
    FeeMemoLine,
    FeeMemoMatter,
    Matter,
    OfficeSettings,
    Payment,
)
from app.schemas import (
    FeeAdjustmentOut,
    FeeLineOut,
    FeeMemoCreate,
    FeeMemoOut,
    FeeMemoUpdate,
    MemoQuickStatusRequest,
    PaymentCreate,
    PaymentOut,
)
from app.services.audit_service import create_snapshot, record_audit
from app.utils.indian_format import amount_to_indian_words, format_indian_currency, ist_now, ist_today


def _q2(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _recalculate_memo_totals(memo: FeeMemo) -> None:
    gross = sum((_q2(line.quantity * line.unit_rate) for line in memo.lines), Decimal("0.00"))
    adj = sum((_q2(a.signed_amount) for a in memo.adjustments), Decimal("0.00"))
    subtotal = gross + adj
    if subtotal < Decimal("0.00"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Net amount after adjustments cannot be negative.",
        )
    tax_pct = Decimal(str(memo.tax_percent)) if memo.tax_percent is not None else Decimal("0.00")
    tax_amt = _q2(subtotal * (tax_pct / Decimal("100")))
    net = _q2(subtotal + tax_amt)
    memo.gross_total = _q2(gross)
    memo.adjustment_total = _q2(adj)
    memo.tax_percent = _q2(tax_pct)
    memo.tax_amount = tax_amt
    memo.net_total = net
    memo.amount_in_words = amount_to_indian_words(net)


def memo_to_out(memo: FeeMemo) -> FeeMemoOut:
    paid = sum((Decimal(str(p.amount)) for p in memo.payments), Decimal("0.00"))
    net = Decimal(str(memo.net_total))
    balance = max(Decimal("0.00"), _q2(net - paid))
    today = ist_today()
    is_overdue = (
        memo.status in ("Issued", "Partially Paid")
        and memo.due_date is not None
        and memo.due_date < today
        and balance > Decimal("0.00")
    )
    return FeeMemoOut(
        id=memo.id,
        bill_number=memo.bill_number,
        bill_date=memo.bill_date,
        due_date=memo.due_date,
        addressee_client_id=memo.addressee_client_id,
        addressee_name=memo.addressee_name,
        addressee_address=memo.addressee_address,
        subject_line=memo.subject_line,
        status=memo.status,
        is_overdue=is_overdue,
        matter_ids=[link.matter_id for link in memo.matter_links],
        matter_titles=[link.matter.title for link in memo.matter_links if link.matter is not None],
        gross_total=float(memo.gross_total),
        adjustment_total=float(memo.adjustment_total),
        tax_percent=float(memo.tax_percent),
        tax_amount=float(memo.tax_amount),
        net_total=float(memo.net_total),
        net_total_formatted=format_indian_currency(memo.net_total),
        amount_in_words=memo.amount_in_words,
        paid_total=float(_q2(paid)),
        balance_due=float(balance),
        balance_due_formatted=format_indian_currency(balance),
        notes=memo.notes,
        footer_bank_details=memo.footer_bank_details,
        lines=[
            FeeLineOut(
                id=line.id,
                sort_order=line.sort_order,
                linked_hearing_id=line.linked_hearing_id,
                category=line.category,
                description=line.description,
                quantity=float(line.quantity),
                unit_rate=float(line.unit_rate),
                line_amount=float(line.line_amount),
            )
            for line in memo.lines
        ],
        adjustments=[
            FeeAdjustmentOut(id=a.id, reason=a.reason, signed_amount=float(a.signed_amount))
            for a in memo.adjustments
        ],
        payments=[
            PaymentOut(
                id=p.id,
                payment_date=p.payment_date,
                amount=float(p.amount),
                method=p.method,
                reference_no=p.reference_no,
                notes=p.notes,
            )
            for p in memo.payments
        ],
        is_demo=memo.is_demo,
    )


def _next_bill_number(db: Session) -> str:
    settings = db.scalar(select(OfficeSettings))
    if settings is None:
        settings = OfficeSettings()
        db.add(settings)
        db.flush()
    while True:
        candidate = f"{settings.bill_number_prefix}{settings.next_bill_seq:03d}"
        settings.next_bill_seq += 1
        exists = db.scalar(select(FeeMemo).where(FeeMemo.bill_number == candidate))
        if exists is None:
            return candidate


def list_memos(db: Session, status_filter: str | None = None) -> list[FeeMemoOut]:
    memos = list(
        db.scalars(
            select(FeeMemo)
            .options(
                selectinload(FeeMemo.matter_links).selectinload(FeeMemoMatter.matter),
                selectinload(FeeMemo.lines),
                selectinload(FeeMemo.adjustments),
                selectinload(FeeMemo.payments),
            )
            .order_by(FeeMemo.bill_date.desc(), FeeMemo.id.desc())
        ).all()
    )
    if status_filter:
        memos = [m for m in memos if m.status == status_filter]
    return [memo_to_out(m) for m in memos]


def get_memo(db: Session, memo_id: int) -> FeeMemoOut:
    memo = db.scalar(
        select(FeeMemo)
        .execution_options(populate_existing=True)
        .options(
            selectinload(FeeMemo.matter_links).selectinload(FeeMemoMatter.matter),
            selectinload(FeeMemo.lines),
            selectinload(FeeMemo.adjustments),
            selectinload(FeeMemo.payments),
        )
        .where(FeeMemo.id == memo_id)
    )
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    return memo_to_out(memo)


def create_memo(db: Session, payload: FeeMemoCreate, actor: str) -> FeeMemoOut:
    bill_no = payload.bill_number.strip() if payload.bill_number and payload.bill_number.strip() else _next_bill_number(db)
    if db.scalar(select(FeeMemo).where(FeeMemo.bill_number == bill_no)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Bill number '{bill_no}' already exists.")

    for mid in payload.matter_ids:
        if db.get(Matter, mid) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Matter {mid} not found.")

    memo = FeeMemo(
        bill_number=bill_no,
        bill_date=payload.bill_date,
        due_date=payload.due_date,
        addressee_client_id=payload.addressee_client_id,
        addressee_name=payload.addressee_name.strip(),
        addressee_address=payload.addressee_address,
        subject_line=payload.subject_line,
        letterhead_id=payload.letterhead_id,
        tax_percent=_q2(payload.tax_percent),
        notes=payload.notes,
        footer_bank_details=payload.footer_bank_details,
        status="Draft",
    )
    db.add(memo)
    db.flush()

    for mid in dict.fromkeys(payload.matter_ids):
        db.add(FeeMemoMatter(memo_id=memo.id, matter_id=mid))

    for idx, line in enumerate(payload.lines):
        amt = _q2(line.quantity * line.unit_rate)
        memo.lines.append(
            FeeMemoLine(
                sort_order=idx,
                linked_hearing_id=line.linked_hearing_id,
                category=line.category,
                description=line.description.strip(),
                quantity=_q2(line.quantity),
                unit_rate=_q2(line.unit_rate),
                line_amount=amt,
            )
        )

    for adj in payload.adjustments:
        memo.adjustments.append(
            FeeAdjustment(reason=adj.reason.strip(), signed_amount=_q2(adj.signed_amount))
        )

    _recalculate_memo_totals(memo)
    record_audit(db, actor=actor, entity_type="fee_memo", entity_id=memo.id, action="create", after={"bill_number": memo.bill_number, "net_total": str(memo.net_total)})
    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)


def update_memo(db: Session, memo_id: int, payload: FeeMemoUpdate, actor: str) -> FeeMemoOut:
    memo = db.scalar(
        select(FeeMemo)
        .options(
            selectinload(FeeMemo.matter_links),
            selectinload(FeeMemo.lines),
            selectinload(FeeMemo.adjustments),
            selectinload(FeeMemo.payments),
        )
        .where(FeeMemo.id == memo_id)
    )
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    if memo.status != "Draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot modify financial lines of a memo in '{memo.status}' status. Void and issue a revised memo instead.",
        )

    data = payload.model_dump(exclude_unset=True)
    if "bill_number" in data and data["bill_number"]:
        new_no = data["bill_number"].strip()
        existing = db.scalar(select(FeeMemo).where(FeeMemo.bill_number == new_no, FeeMemo.id != memo_id))
        if existing is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Bill number '{new_no}' already exists.")
        memo.bill_number = new_no
        del data["bill_number"]

    matter_ids = data.pop("matter_ids", None)
    lines = data.pop("lines", None)
    adjustments = data.pop("adjustments", None)

    for k, v in data.items():
        setattr(memo, k, v)

    if matter_ids is not None:
        memo.matter_links.clear()
        db.flush()
        for mid in dict.fromkeys(matter_ids):
            if db.get(Matter, mid) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Matter {mid} not found.")
            db.add(FeeMemoMatter(memo_id=memo.id, matter_id=mid))

    if lines is not None:
        memo.lines.clear()
        db.flush()
        for idx, line in enumerate(lines):
            qty = _q2(Decimal(str(line["quantity"])))
            rate = _q2(Decimal(str(line["unit_rate"])))
            memo.lines.append(
                FeeMemoLine(
                    sort_order=idx,
                    linked_hearing_id=line.get("linked_hearing_id"),
                    category=line.get("category", "Appearances"),
                    description=line["description"].strip(),
                    quantity=qty,
                    unit_rate=rate,
                    line_amount=_q2(qty * rate),
                )
            )

    if adjustments is not None:
        memo.adjustments.clear()
        db.flush()
        for adj in adjustments:
            memo.adjustments.append(
                FeeAdjustment(reason=adj["reason"].strip(), signed_amount=_q2(Decimal(str(adj["signed_amount"]))))
            )

    _recalculate_memo_totals(memo)
    record_audit(db, actor=actor, entity_type="fee_memo", entity_id=memo.id, action="update", after={"bill_number": memo.bill_number, "net_total": str(memo.net_total)})
    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)


def issue_memo(db: Session, memo_id: int, actor: str) -> FeeMemoOut:
    memo = db.scalar(select(FeeMemo).options(selectinload(FeeMemo.lines)).where(FeeMemo.id == memo_id))
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    if memo.status != "Draft":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Memo is already '{memo.status}'.")
    if not memo.lines:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Add at least one fee line before issuing.")

    create_snapshot(f"issue_memo_{memo.bill_number}")
    memo.status = "Issued"
    memo.issued_at = ist_now()
    record_audit(db, actor=actor, entity_type="fee_memo", entity_id=memo.id, action="issue", after={"bill_number": memo.bill_number})
    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)


def void_memo(db: Session, memo_id: int, reason: str, actor: str) -> FeeMemoOut:
    memo = db.get(FeeMemo, memo_id)
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    if memo.status == "Voided":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Memo is already voided.")

    create_snapshot(f"void_memo_{memo.bill_number}")
    before_status = memo.status
    memo.status = "Voided"
    memo.void_reason = reason.strip()
    record_audit(db, actor=actor, entity_type="fee_memo", entity_id=memo.id, action="void", before={"status": before_status}, after={"status": "Voided", "reason": memo.void_reason})
    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)


def record_payment(db: Session, memo_id: int, payload: PaymentCreate, actor: str) -> FeeMemoOut:
    memo = db.scalar(
        select(FeeMemo).options(selectinload(FeeMemo.payments)).where(FeeMemo.id == memo_id)
    )
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    if memo.status in ("Draft", "Voided"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Payments can only be recorded on Issued memos (current status: '{memo.status}').",
        )

    amt = _q2(payload.amount)
    already_paid = sum((Decimal(str(p.amount)) for p in memo.payments), Decimal("0.00"))
    net = Decimal(str(memo.net_total))
    if not payload.allow_overpayment and (already_paid + amt) > net:
        remaining = max(Decimal("0.00"), net - already_paid)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Payment of {format_indian_currency(amt)} exceeds remaining balance of {format_indian_currency(remaining)}.",
        )

    payment = Payment(
        memo_id=memo.id,
        payment_date=payload.payment_date,
        amount=amt,
        method=payload.method,
        reference_no=payload.reference_no,
        notes=payload.notes,
        recorded_by=actor,
    )
    db.add(payment)
    db.flush()

    new_paid = already_paid + amt
    memo.status = "Paid" if new_paid >= net else "Partially Paid"
    record_audit(
        db,
        actor=actor,
        entity_type="payment",
        entity_id=payment.id,
        action="record_payment",
        after={"memo_id": memo.id, "amount": str(amt), "new_status": memo.status},
    )
    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)


def set_memo_quick_status(db: Session, memo_id: int, payload: MemoQuickStatusRequest, actor: str) -> FeeMemoOut:
    memo = db.scalar(
        select(FeeMemo)
        .options(selectinload(FeeMemo.lines), selectinload(FeeMemo.payments))
        .where(FeeMemo.id == memo_id)
    )
    if memo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fee Memo not found.")
    if memo.status == "Voided":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot change status of a Voided memo.")

    before_status = memo.status
    net = Decimal(str(memo.net_total))
    paid = sum((Decimal(str(p.amount)) for p in memo.payments), Decimal("0.00"))
    pay_date = payload.payment_date or ist_today()

    if payload.status == "Pending":
        if memo.status == "Draft":
            if not memo.lines:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Add at least one fee line before issuing.")
            create_snapshot(f"issue_memo_{memo.bill_number}")
            memo.issued_at = ist_now()
        elif memo.payments:
            create_snapshot(f"reset_memo_pending_{memo.bill_number}")
            memo.payments.clear()
        memo.status = "Issued"
        record_audit(
            db,
            actor=actor,
            entity_type="fee_memo",
            entity_id=memo.id,
            action="mark_pending",
            before={"status": before_status},
            after={"status": "Issued"},
        )
    elif payload.status == "Paid":
        if memo.status == "Draft":
            if not memo.lines:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Add at least one fee line before marking Paid.")
            create_snapshot(f"issue_memo_{memo.bill_number}")
            memo.issued_at = ist_now()
        remaining = max(Decimal("0.00"), _q2(net - paid))
        amt = _q2(payload.amount) if payload.amount is not None else remaining
        if remaining > Decimal("0.00"):
            if amt < remaining:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Paid amount ({format_indian_currency(amt)}) is less than balance due ({format_indian_currency(remaining)}).",
                )
            if amt > remaining:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Amount exceeds remaining balance of {format_indian_currency(remaining)}.",
                )
            db.add(
                Payment(
                    memo_id=memo.id,
                    payment_date=pay_date,
                    amount=amt,
                    method=payload.method,
                    reference_no=payload.reference_no,
                    notes="Marked Paid",
                    recorded_by=actor,
                )
            )
        memo.status = "Paid"
        record_audit(
            db,
            actor=actor,
            entity_type="fee_memo",
            entity_id=memo.id,
            action="mark_paid",
            before={"status": before_status},
            after={"status": "Paid", "amount": str(amt)},
        )
    elif payload.status == "Partially Paid":
        if memo.status == "Draft":
            if not memo.lines:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Add at least one fee line first.")
            create_snapshot(f"issue_memo_{memo.bill_number}")
            memo.issued_at = ist_now()
        remaining = max(Decimal("0.00"), _q2(net - paid))
        if remaining <= Decimal("0.00"):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This memo already has zero remaining balance.")
        if payload.amount is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter the partial payment amount received.")
        amt = _q2(payload.amount)
        if amt >= remaining:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Partial amount covers the full remaining balance — choose Paid instead.",
            )
        db.add(
            Payment(
                memo_id=memo.id,
                payment_date=pay_date,
                amount=amt,
                method=payload.method,
                reference_no=payload.reference_no,
                notes="Partial payment recorded",
                recorded_by=actor,
            )
        )
        memo.status = "Partially Paid"
        record_audit(
            db,
            actor=actor,
            entity_type="fee_memo",
            entity_id=memo.id,
            action="mark_partially_paid",
            before={"status": before_status},
            after={"status": "Partially Paid", "amount": str(amt)},
        )

    db.commit()
    db.expire(memo)
    return get_memo(db, memo.id)
