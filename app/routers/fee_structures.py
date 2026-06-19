import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user, require_writer
from app.models import FeeInstallmentTemplate, FeeStructure, StudentCategory, User
from app.schemas import FeeStructureCreate, FeeStructureOut, FeeStructureUpdate
from app.services.fees import q

router = APIRouter(prefix="/api/fee-structures", tags=["fee-structures"])


def _fs_out(fs: FeeStructure) -> FeeStructureOut:
    data = FeeStructureOut.model_validate(fs)
    data.category_name = fs.category.name if fs.category else None
    return data


async def _validate_category(db: AsyncSession, category_id, ws_id) -> None:
    if category_id is None:
        return
    cat = await db.get(StudentCategory, category_id)
    if not cat or cat.workspace_id != ws_id:
        raise HTTPException(422, "Selected category does not exist.")


def _validate_sum(body: FeeStructureCreate):
    total = q(body.total_amount)
    inst_sum = q(sum(q(i.amount) for i in body.installments))
    if inst_sum != total:
        raise HTTPException(
            status_code=422,
            detail=f"Installment amounts (₹{inst_sum}) must equal the total fee (₹{total}).",
        )
    if len(body.installments) != body.num_installments:
        raise HTTPException(
            status_code=422,
            detail="Number of installment rows must match num_installments.",
        )


@router.get("", response_model=list[FeeStructureOut])
async def list_fee_structures(
    class_name: str | None = Query(None),
    academic_year: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(FeeStructure)
        .where(FeeStructure.workspace_id == user.workspace_id, FeeStructure.is_active.is_(True))
        .options(selectinload(FeeStructure.templates), selectinload(FeeStructure.category))
        .order_by(FeeStructure.academic_year.desc(), FeeStructure.class_name)
    )
    if class_name:
        stmt = stmt.where(FeeStructure.class_name == class_name)
    if academic_year:
        stmt = stmt.where(FeeStructure.academic_year == academic_year)
    rows = (await db.execute(stmt)).scalars().all()
    return [_fs_out(fs) for fs in rows]


@router.get("/{fs_id}", response_model=FeeStructureOut)
async def get_fee_structure(
    fs_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(FeeStructure)
        .where(FeeStructure.id == fs_id, FeeStructure.workspace_id == user.workspace_id)
        .options(selectinload(FeeStructure.templates), selectinload(FeeStructure.category))
    )
    fs = (await db.execute(stmt)).scalar_one_or_none()
    if not fs:
        raise HTTPException(404, "Fee structure not found")
    return _fs_out(fs)


@router.post("", response_model=FeeStructureOut, status_code=201)
async def create_fee_structure(
    body: FeeStructureCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    _validate_sum(body)
    await _validate_category(db, body.category_id, user.workspace_id)
    # Archive any existing active structure for the same class + category + year
    existing = (
        await db.execute(
            select(FeeStructure).where(
                FeeStructure.workspace_id == user.workspace_id,
                FeeStructure.class_name == body.class_name,
                FeeStructure.category_id.is_(body.category_id)
                if body.category_id is None
                else FeeStructure.category_id == body.category_id,
                FeeStructure.academic_year == body.academic_year,
                FeeStructure.is_active.is_(True),
            )
        )
    ).scalars().all()
    for e in existing:
        e.is_active = False

    fs = FeeStructure(
        workspace_id=user.workspace_id,
        class_name=body.class_name,
        category_id=body.category_id,
        academic_year=body.academic_year,
        total_amount=q(body.total_amount),
        num_installments=body.num_installments,
        created_by=user.id,
        templates=[
            FeeInstallmentTemplate(
                installment_number=i.installment_number,
                label=i.label,
                amount=q(i.amount),
                due_date=i.due_date,
            )
            for i in body.installments
        ],
    )
    db.add(fs)
    await db.flush()
    await db.refresh(fs, attribute_names=["templates", "category"])
    return _fs_out(fs)


@router.put("/{fs_id}", response_model=FeeStructureOut)
async def update_fee_structure(
    fs_id: uuid.UUID,
    body: FeeStructureUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    _validate_sum(body)
    await _validate_category(db, body.category_id, user.workspace_id)
    stmt = (
        select(FeeStructure)
        .where(FeeStructure.id == fs_id, FeeStructure.workspace_id == user.workspace_id)
        .options(selectinload(FeeStructure.templates), selectinload(FeeStructure.category))
    )
    fs = (await db.execute(stmt)).scalar_one_or_none()
    if not fs:
        raise HTTPException(404, "Fee structure not found")

    fs.class_name = body.class_name
    fs.category_id = body.category_id
    fs.academic_year = body.academic_year
    fs.total_amount = q(body.total_amount)
    fs.num_installments = body.num_installments
    fs.templates.clear()
    for i in body.installments:
        fs.templates.append(
            FeeInstallmentTemplate(
                installment_number=i.installment_number,
                label=i.label,
                amount=q(i.amount),
                due_date=i.due_date,
            )
        )
    await db.flush()
    await db.refresh(fs, attribute_names=["templates", "category"])
    return _fs_out(fs)
