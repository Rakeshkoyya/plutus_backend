import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import get_current_user, require_writer
from app.models import Student, StudentCategory, User
from app.schemas import StudentCategoryCreate, StudentCategoryOut, StudentCategoryUpdate

router = APIRouter(prefix="/api/categories", tags=["categories"])


async def _counts(db: AsyncSession, ws_id: uuid.UUID) -> dict[uuid.UUID, int]:
    rows = (
        await db.execute(
            select(Student.category_id, func.count(Student.id))
            .where(Student.workspace_id == ws_id, Student.category_id.is_not(None))
            .group_by(Student.category_id)
        )
    ).all()
    return {cid: n for cid, n in rows}


@router.get("", response_model=list[StudentCategoryOut])
async def list_categories(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(StudentCategory).where(StudentCategory.workspace_id == user.workspace_id)
    if not include_inactive:
        stmt = stmt.where(StudentCategory.is_active.is_(True))
    stmt = stmt.order_by(StudentCategory.name)
    cats = (await db.execute(stmt)).scalars().all()
    counts = await _counts(db, user.workspace_id)
    return [
        StudentCategoryOut(
            id=c.id, name=c.name, is_active=c.is_active, student_count=counts.get(c.id, 0)
        )
        for c in cats
    ]


@router.post("", response_model=StudentCategoryOut, status_code=201)
async def create_category(
    body: StudentCategoryCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    name = body.name.strip()
    existing = (
        await db.execute(
            select(StudentCategory).where(
                StudentCategory.workspace_id == user.workspace_id,
                func.lower(StudentCategory.name) == name.lower(),
            )
        )
    ).scalar_one_or_none()
    if existing:
        # Re-activate a previously removed category with the same name instead of erroring.
        if not existing.is_active:
            existing.is_active = True
            await db.flush()
            return StudentCategoryOut(id=existing.id, name=existing.name, is_active=True, student_count=0)
        raise HTTPException(409, f"A category named '{name}' already exists.")
    cat = StudentCategory(workspace_id=user.workspace_id, name=name)
    db.add(cat)
    await db.flush()
    return StudentCategoryOut(id=cat.id, name=cat.name, is_active=cat.is_active, student_count=0)


async def _get_cat(db: AsyncSession, cat_id: uuid.UUID, ws_id: uuid.UUID) -> StudentCategory:
    cat = await db.get(StudentCategory, cat_id)
    if not cat or cat.workspace_id != ws_id:
        raise HTTPException(404, "Category not found")
    return cat


@router.put("/{cat_id}", response_model=StudentCategoryOut)
async def update_category(
    cat_id: uuid.UUID,
    body: StudentCategoryUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    cat = await _get_cat(db, cat_id, user.workspace_id)
    if body.name is not None and body.name.strip():
        cat.name = body.name.strip()
    if body.is_active is not None:
        cat.is_active = body.is_active
    await db.flush()
    counts = await _counts(db, user.workspace_id)
    return StudentCategoryOut(
        id=cat.id, name=cat.name, is_active=cat.is_active, student_count=counts.get(cat.id, 0)
    )


@router.delete("/{cat_id}")
async def delete_category(
    cat_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    cat = await _get_cat(db, cat_id, user.workspace_id)
    counts = await _counts(db, user.workspace_id)
    in_use = counts.get(cat.id, 0)
    if in_use:
        # Keep the record (students still reference it) but hide it from pickers.
        cat.is_active = False
        await db.flush()
        return {"deleted": False, "deactivated": True, "student_count": in_use}
    await db.delete(cat)
    await db.flush()
    return {"deleted": True, "deactivated": False, "student_count": 0}
