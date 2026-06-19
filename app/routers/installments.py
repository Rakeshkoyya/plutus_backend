import uuid
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import require_writer
from app.models import Installment, Student, StudentFee, Transaction, User
from app.schemas import (
    InstallmentDueDateUpdate,
    PaymentIn,
    StudentFeeDetail,
)
from app.routers.student_fees import _detail
from app.services.fees import q, recompute_student_fee

router = APIRouter(prefix="/api/installments", tags=["installments"])


async def _load_installment(db: AsyncSession, inst_id: uuid.UUID, ws_id: uuid.UUID):
    inst = await db.get(Installment, inst_id)
    if not inst:
        raise HTTPException(404, "Installment not found")
    stmt = (
        select(StudentFee)
        .where(StudentFee.id == inst.student_fee_id, StudentFee.workspace_id == ws_id)
        .options(
            selectinload(StudentFee.installments),
            selectinload(StudentFee.student).selectinload(Student.category),
        )
    )
    sf = (await db.execute(stmt)).scalar_one_or_none()
    if not sf:
        raise HTTPException(404, "Installment not found")
    inst = next(i for i in sf.installments if i.id == inst_id)
    return inst, sf


@router.put("/{inst_id}", response_model=StudentFeeDetail)
async def update_due_date(
    inst_id: uuid.UUID,
    body: InstallmentDueDateUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    inst, sf = await _load_installment(db, inst_id, user.workspace_id)
    old = inst.due_date
    inst.due_date = body.due_date
    db.add(
        Transaction(
            workspace_id=user.workspace_id,
            student_fee_id=sf.id,
            installment_id=inst.id,
            amount=q(0),
            type="installment_edit",
            note=f"Due date for installment #{inst.installment_number} changed "
                 f"from {old or '—'} to {body.due_date}",
            created_by=user.id,
            created_by_name=user.name,
        )
    )
    recompute_student_fee(sf)
    await db.flush()
    return _detail(sf)


@router.post("/{inst_id}/pay", response_model=StudentFeeDetail)
async def add_payment(
    inst_id: uuid.UUID,
    body: PaymentIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    inst, sf = await _load_installment(db, inst_id, user.workspace_id)
    amount = q(body.amount)
    if amount <= 0:
        raise HTTPException(422, "Payment amount must be positive.")
    remaining = q(q(inst.amount) - q(inst.paid_amount))
    if amount > remaining:
        raise HTTPException(
            422, f"Payment ₹{amount} exceeds remaining balance ₹{remaining} on this installment."
        )
    inst.paid_amount = q(q(inst.paid_amount) + amount)
    inst.paid_date = body.paid_on or date.today()
    db.add(
        Transaction(
            workspace_id=user.workspace_id,
            student_fee_id=sf.id,
            installment_id=inst.id,
            amount=amount,
            type="payment",
            mode=body.mode,
            receipt_number=body.receipt_number,
            note=body.note,
            created_by=user.id,
            created_by_name=user.name,
        )
    )
    recompute_student_fee(sf)
    await db.flush()
    return _detail(sf)


@router.post("/{inst_id}/mark-paid", response_model=StudentFeeDetail)
async def mark_paid(
    inst_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    inst, sf = await _load_installment(db, inst_id, user.workspace_id)
    remaining = q(q(inst.amount) - q(inst.paid_amount))
    if remaining <= 0:
        raise HTTPException(422, "Installment is already fully paid.")
    inst.paid_amount = q(inst.amount)
    inst.paid_date = date.today()
    db.add(
        Transaction(
            workspace_id=user.workspace_id,
            student_fee_id=sf.id,
            installment_id=inst.id,
            amount=remaining,
            type="payment",
            mode="cash",
            note=f"Marked installment #{inst.installment_number} as fully paid",
            created_by=user.id,
            created_by_name=user.name,
        )
    )
    recompute_student_fee(sf)
    await db.flush()
    return _detail(sf)


@router.post("/{inst_id}/undo", response_model=StudentFeeDetail)
async def undo(
    inst_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    inst, sf = await _load_installment(db, inst_id, user.workspace_id)
    # Most recent reversible transaction on this installment (payment)
    last = (
        await db.execute(
            select(Transaction)
            .where(
                Transaction.installment_id == inst.id,
                Transaction.type == "payment",
            )
            .order_by(Transaction.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not last:
        raise HTTPException(422, "No payment to undo on this installment.")

    inst.paid_amount = q(max(q(inst.paid_amount) - q(last.amount), q(0)))
    if q(inst.paid_amount) <= 0:
        inst.paid_date = None
    db.add(
        Transaction(
            workspace_id=user.workspace_id,
            student_fee_id=sf.id,
            installment_id=inst.id,
            amount=q(-last.amount),
            type="undo",
            note=f"Reverted payment of ₹{q(last.amount)} on installment #{inst.installment_number}",
            created_by=user.id,
            created_by_name=user.name,
        )
    )
    recompute_student_fee(sf)
    await db.flush()
    return _detail(sf)
