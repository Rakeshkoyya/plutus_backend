import uuid
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.deps import get_current_user, require_writer
from app.models import (
    FeeStructure,
    Installment,
    Student,
    StudentCategory,
    StudentFee,
    Transaction,
    User,
)
from app.schemas import (
    StudentFeeCreate,
    StudentFeeDetail,
    StudentFeeListItem,
    StudentFeeUpdate,
    TransactionOut,
)
from app.services.fees import (
    aggregate_paid,
    overdue_amount,
    proportional_installments,
    q,
    recompute_installment,
    recompute_student_fee,
)

router = APIRouter(prefix="/api/student-fees", tags=["student-fees"])


async def _load_sf(db: AsyncSession, sf_id: uuid.UUID, ws_id: uuid.UUID) -> StudentFee:
    stmt = (
        select(StudentFee)
        .where(StudentFee.id == sf_id, StudentFee.workspace_id == ws_id)
        .options(
            selectinload(StudentFee.installments),
            selectinload(StudentFee.student).selectinload(Student.category),
        )
    )
    sf = (await db.execute(stmt)).scalar_one_or_none()
    if not sf:
        raise HTTPException(404, "Student fee record not found")
    return sf


def _category_name(sf: StudentFee) -> str | None:
    return sf.student.category.name if sf.student and sf.student.category else None


def _detail(sf: StudentFee) -> StudentFeeDetail:
    recompute_student_fee(sf)
    paid = aggregate_paid(sf.installments)
    opening = q(sf.opening_dues)
    total_payable = q(q(sf.net_fee) + opening)
    return StudentFeeDetail(
        id=sf.id,
        student_id=sf.student_id,
        student_name=sf.student.name if sf.student else "",
        class_name=sf.student.class_name if sf.student else None,
        category_name=_category_name(sf),
        academic_year=sf.academic_year,
        total_fee=q(sf.total_fee),
        discount=q(sf.discount),
        net_fee=q(sf.net_fee),
        opening_dues=opening,
        total_payable=total_payable,
        paid=paid,
        balance=q(total_payable - paid),
        status=sf.status,
        installments=sorted(sf.installments, key=lambda i: i.installment_number),
    )


@router.get("", response_model=list[StudentFeeListItem])
async def list_student_fees(
    class_name: str | None = Query(None),
    status: str | None = Query(None),
    academic_year: str | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(StudentFee)
        .where(StudentFee.workspace_id == user.workspace_id)
        .options(
            selectinload(StudentFee.installments),
            selectinload(StudentFee.student).selectinload(Student.category),
        )
        .order_by(StudentFee.created_at.desc())
    )
    if academic_year:
        stmt = stmt.where(StudentFee.academic_year == academic_year)
    rows = (await db.execute(stmt)).scalars().all()

    items: list[StudentFeeListItem] = []
    for sf in rows:
        recompute_student_fee(sf)
        if class_name and (not sf.student or sf.student.class_name != class_name):
            continue
        if status and sf.status != status:
            continue
        if search and (not sf.student or search.lower() not in sf.student.name.lower()):
            continue
        paid = aggregate_paid(sf.installments)
        opening = q(sf.opening_dues)
        total_payable = q(q(sf.net_fee) + opening)
        items.append(
            StudentFeeListItem(
                id=sf.id,
                student_id=sf.student_id,
                student_name=sf.student.name if sf.student else "",
                class_name=sf.student.class_name if sf.student else None,
                category_name=_category_name(sf),
                academic_year=sf.academic_year,
                total_fee=q(sf.total_fee),
                discount=q(sf.discount),
                net_fee=q(sf.net_fee),
                opening_dues=opening,
                paid=paid,
                pending=q(total_payable - paid),
                status=sf.status,
            )
        )
    return items


@router.get("/{sf_id}", response_model=StudentFeeDetail)
async def get_student_fee(
    sf_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sf = await _load_sf(db, sf_id, user.workspace_id)
    return _detail(sf)


@router.post("", response_model=StudentFeeDetail, status_code=201)
async def enroll(
    body: StudentFeeCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    student = await db.get(Student, body.student_id)
    if not student or student.workspace_id != user.workspace_id:
        raise HTTPException(404, "Student not found")

    year = body.academic_year or settings.default_academic_year
    dup = (
        await db.execute(
            select(StudentFee).where(
                StudentFee.workspace_id == user.workspace_id,
                StudentFee.student_id == body.student_id,
                StudentFee.academic_year == year,
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(409, "This student is already enrolled for this academic year.")

    total = q(body.total_fee)
    discount = q(body.discount)
    net = q(total - discount)
    opening = q(body.opening_dues)

    # Decide installment amounts/dates
    inst_rows: list[Installment] = []
    if body.use_custom_schedule and body.installments:
        inst_sum = q(sum(q(i.amount) for i in body.installments))
        if inst_sum != net:
            raise HTTPException(
                422, f"Installment amounts (₹{inst_sum}) must equal net payable (₹{net})."
            )
        for i in body.installments:
            inst_rows.append(
                Installment(
                    installment_number=i.installment_number,
                    label=i.label,
                    amount=q(i.amount),
                    due_date=i.due_date,
                )
            )
    elif body.fee_structure_id:
        fs = await db.get(FeeStructure, body.fee_structure_id)
        if not fs or fs.workspace_id != user.workspace_id:
            raise HTTPException(404, "Fee structure not found")
        await db.refresh(fs, attribute_names=["templates"])
        templates = sorted(fs.templates, key=lambda t: t.installment_number)
        scaled = proportional_installments(net, [t.amount for t in templates])
        for idx, t in enumerate(templates):
            inst_rows.append(
                Installment(
                    installment_number=t.installment_number,
                    label=t.label,
                    amount=scaled[idx],
                    due_date=t.due_date,
                )
            )
    else:
        inst_rows.append(Installment(installment_number=1, amount=net, due_date=None))

    sf = StudentFee(
        workspace_id=user.workspace_id,
        student_id=body.student_id,
        fee_structure_id=body.fee_structure_id,
        academic_year=year,
        total_fee=total,
        discount=discount,
        net_fee=net,
        opening_dues=opening,
        created_by=user.id,
        installments=inst_rows,
    )
    db.add(sf)
    await db.flush()

    # First payment
    if body.first_payment and q(body.first_payment.amount) > 0:
        target = next(
            (i for i in sf.installments if i.installment_number == body.first_payment.installment_number),
            sf.installments[0],
        )
        pay = q(body.first_payment.amount)
        target.paid_amount = q(q(target.paid_amount) + pay)
        target.paid_date = body.first_payment.paid_on or date.today()
        db.add(
            Transaction(
                workspace_id=user.workspace_id,
                student_fee_id=sf.id,
                installment_id=target.id,
                amount=pay,
                type="payment",
                mode=body.first_payment.mode,
                receipt_number=body.first_payment.receipt_number,
                note="Initial payment at enrollment",
                created_by=user.id,
                created_by_name=user.name,
            )
        )

    recompute_student_fee(sf)
    await db.flush()
    return _detail(await _load_sf(db, sf.id, user.workspace_id))


@router.put("/{sf_id}", response_model=StudentFeeDetail)
async def update_discount(
    sf_id: uuid.UUID,
    body: StudentFeeUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    sf = await _load_sf(db, sf_id, user.workspace_id)
    if body.opening_dues is not None:
        sf.opening_dues = q(body.opening_dues)
    if body.discount is not None:
        old_net = q(sf.net_fee)
        sf.discount = q(body.discount)
        sf.net_fee = q(q(sf.total_fee) - sf.discount)
        # Re-scale UNPAID portion of installments proportionally to the new net
        paid = aggregate_paid(sf.installments)
        remaining = q(sf.net_fee - paid)
        if remaining < 0:
            raise HTTPException(422, "Discount makes net payable lower than amount already paid.")
        unpaid = [i for i in sf.installments if q(i.paid_amount) < q(i.amount)]
        if unpaid:
            current_unpaid_total = q(sum(q(i.amount) - q(i.paid_amount) for i in unpaid))
            scaled = proportional_installments(
                remaining, [q(i.amount) - q(i.paid_amount) for i in unpaid]
            ) if current_unpaid_total > 0 else [remaining]
            for idx, i in enumerate(unpaid):
                i.amount = q(q(i.paid_amount) + scaled[idx])
        db.add(
            Transaction(
                workspace_id=user.workspace_id,
                student_fee_id=sf.id,
                installment_id=None,
                amount=q(sf.net_fee - old_net),
                type="discount",
                note=f"Discount updated to ₹{sf.discount}",
                created_by=user.id,
                created_by_name=user.name,
            )
        )
    recompute_student_fee(sf)
    await db.flush()
    return _detail(await _load_sf(db, sf_id, user.workspace_id))


@router.get("/{sf_id}/transactions", response_model=list[TransactionOut])
async def list_transactions(
    sf_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _load_sf(db, sf_id, user.workspace_id)
    rows = (
        await db.execute(
            select(Transaction)
            .where(Transaction.student_fee_id == sf_id, Transaction.workspace_id == user.workspace_id)
            .order_by(Transaction.created_at.desc())
        )
    ).scalars().all()
    return rows


@router.get("/{sf_id}/installments")
async def list_installments(
    sf_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    sf = await _load_sf(db, sf_id, user.workspace_id)
    return _detail(sf).installments
