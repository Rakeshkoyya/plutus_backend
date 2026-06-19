import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.deps import get_current_user, require_writer
from app.models import Student, StudentCategory, StudentFee, User
from app.schemas import StudentCreate, StudentOut, StudentUpdate

router = APIRouter(prefix="/api/students", tags=["students"])


def _out(student: Student) -> StudentOut:
    data = StudentOut.model_validate(student)
    data.category_name = student.category.name if student.category else None
    return data


async def _validate_category(
    db: AsyncSession, category_id: uuid.UUID | None, ws_id: uuid.UUID
) -> None:
    if category_id is None:
        return
    cat = await db.get(StudentCategory, category_id)
    if not cat or cat.workspace_id != ws_id:
        raise HTTPException(422, "Selected category does not exist.")


@router.get("", response_model=list[StudentOut])
async def list_students(
    search: str | None = Query(None),
    category_id: uuid.UUID | None = Query(None),
    enrollment_status: str | None = Query(None),
    unenrolled_year: str | None = Query(None, description="Exclude students already enrolled this year"),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Student)
        .where(Student.workspace_id == user.workspace_id)
        .options(selectinload(Student.category))
    )
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                Student.name.ilike(like),
                Student.roll_number.ilike(like),
                Student.admission_number.ilike(like),
            )
        )
    if category_id:
        stmt = stmt.where(Student.category_id == category_id)
    if enrollment_status:
        stmt = stmt.where(Student.enrollment_status == enrollment_status)
    if unenrolled_year:
        enrolled = select(StudentFee.student_id).where(
            StudentFee.workspace_id == user.workspace_id,
            StudentFee.academic_year == unenrolled_year,
        )
        stmt = stmt.where(Student.id.notin_(enrolled))
    stmt = stmt.order_by(Student.name).limit(500)
    rows = (await db.execute(stmt)).scalars().all()
    return [_out(s) for s in rows]


@router.post("", response_model=StudentOut, status_code=201)
async def create_student(
    body: StudentCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    await _validate_category(db, body.category_id, user.workspace_id)
    student = Student(workspace_id=user.workspace_id, **body.model_dump())
    db.add(student)
    await db.flush()
    await db.refresh(student, attribute_names=["category"])
    return _out(student)


@router.get("/{student_id}", response_model=StudentOut)
async def get_student(
    student_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = (
        select(Student)
        .where(Student.id == student_id, Student.workspace_id == user.workspace_id)
        .options(selectinload(Student.category))
    )
    student = (await db.execute(stmt)).scalar_one_or_none()
    if not student:
        raise HTTPException(404, "Student not found")
    return _out(student)


@router.put("/{student_id}", response_model=StudentOut)
async def update_student(
    student_id: uuid.UUID,
    body: StudentUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    stmt = (
        select(Student)
        .where(Student.id == student_id, Student.workspace_id == user.workspace_id)
        .options(selectinload(Student.category))
    )
    student = (await db.execute(stmt)).scalar_one_or_none()
    if not student:
        raise HTTPException(404, "Student not found")
    data = body.model_dump(exclude_unset=True)
    if "category_id" in data:
        await _validate_category(db, data["category_id"], user.workspace_id)
    for k, v in data.items():
        setattr(student, k, v)
    await db.flush()
    await db.refresh(student, attribute_names=["category"])
    return _out(student)
