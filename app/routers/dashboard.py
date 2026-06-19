from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import get_db
from app.deps import get_current_user
from app.models import StudentFee, User
from app.schemas import ChartData, DashboardSummary, OverdueStudent
from app.services.fees import aggregate_paid, q, recompute_student_fee

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


async def _load_year(db: AsyncSession, ws_id, year: str | None):
    stmt = (
        select(StudentFee)
        .where(StudentFee.workspace_id == ws_id)
        .options(selectinload(StudentFee.installments), selectinload(StudentFee.student))
    )
    if year:
        stmt = stmt.where(StudentFee.academic_year == year)
    rows = (await db.execute(stmt)).scalars().all()
    for sf in rows:
        recompute_student_fee(sf)
    return rows


@router.get("/summary", response_model=DashboardSummary)
async def summary(
    academic_year: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = date.today()
    rows = await _load_year(db, user.workspace_id, academic_year)
    total_fee = q(sum(q(sf.net_fee) for sf in rows))
    collected = q(sum(aggregate_paid(sf.installments) for sf in rows))
    pending_count = 0
    overdue_amt = Decimal("0")
    for sf in rows:
        for i in sf.installments:
            unpaid = q(i.amount) - q(i.paid_amount)
            if unpaid <= 0:
                continue
            if i.due_date and i.due_date < today:
                overdue_amt += unpaid
            elif i.due_date is None or i.due_date >= today:
                pending_count += 1
    return DashboardSummary(
        total_fee=total_fee,
        collected_fee=collected,
        pending_installments=pending_count,
        overdue_amount=q(overdue_amt),
    )


@router.get("/overdue-students", response_model=list[OverdueStudent])
async def overdue_students(
    academic_year: str | None = Query(None),
    limit: int = Query(20, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = date.today()
    rows = await _load_year(db, user.workspace_id, academic_year)
    out: list[OverdueStudent] = []
    for sf in rows:
        overdue_total = Decimal("0")
        earliest = None
        for i in sf.installments:
            unpaid = q(i.amount) - q(i.paid_amount)
            if unpaid > 0 and i.due_date and i.due_date < today:
                overdue_total += unpaid
                if earliest is None or i.due_date < earliest:
                    earliest = i.due_date
        if overdue_total > 0:
            out.append(
                OverdueStudent(
                    student_fee_id=sf.id,
                    student_name=sf.student.name if sf.student else "",
                    class_name=sf.student.class_name if sf.student else None,
                    overdue_amount=q(overdue_total),
                    earliest_due_date=earliest,
                )
            )
    out.sort(key=lambda o: (o.earliest_due_date or date.max))
    return out[offset : offset + limit]


@router.get("/chart-data", response_model=ChartData)
async def chart_data(
    academic_year: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    today = date.today()
    rows = await _load_year(db, user.workspace_id, academic_year)
    collected = Decimal("0")
    pending = Decimal("0")
    overdue = Decimal("0")
    for sf in rows:
        for i in sf.installments:
            collected += q(i.paid_amount)
            unpaid = q(i.amount) - q(i.paid_amount)
            if unpaid <= 0:
                continue
            if i.due_date and i.due_date < today:
                overdue += unpaid
            else:
                pending += unpaid
    return ChartData(collected=q(collected), pending=q(pending), overdue=q(overdue))
