import os
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.deps import require_writer
from app.models import (
    FeeInstallmentTemplate,
    FeeStructure,
    Installment,
    Student,
    StudentCategory,
    StudentFee,
    Transaction,
    User,
)
from app.schemas import (
    ImportAnalyzeOut,
    ImportAnalyzeSheet,
    ImportCommitIn,
    ImportCommitOut,
)
from app.services.ai_import import (
    QUARTER_FIELDS,
    QUARTER_LABELS,
    ai_mapping,
    build_year_options,
    detect_academic_year,
    heuristic_mapping,
    read_workbook,
)
from app.services.fees import even_split, q, recompute_student_fee

router = APIRouter(prefix="/api/imports", tags=["imports"])

CACHE_DIR = Path(".import_cache")
CACHE_DIR.mkdir(exist_ok=True)


def _num(v) -> Decimal:
    if v is None or str(v).strip() == "":
        return Decimal("0")
    try:
        cleaned = str(v).replace(",", "").replace("₹", "").replace("Rs", "").strip()
        return q(Decimal(cleaned))
    except (InvalidOperation, ValueError):
        return Decimal("0")


@router.get("/enabled")
async def import_enabled():
    return {"ai_enabled": settings.ai_enabled, "model": settings.openrouter_model}


@router.post("/analyze", response_model=ImportAnalyzeOut)
async def analyze(
    file: UploadFile = File(...),
    user: User = Depends(require_writer),
):
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(422, "Please upload an .xlsx file.")
    token = uuid.uuid4().hex
    path = CACHE_DIR / f"{token}.xlsx"
    content = await file.read()
    path.write_bytes(content)

    try:
        sheets = read_workbook(str(path))
    except Exception as e:
        path.unlink(missing_ok=True)
        raise HTTPException(422, f"Could not read spreadsheet: {e}")

    ai_used = False
    out_sheets = []
    detected_years: list[str] = []
    for s in sheets:
        sample = s["rows"][:6]
        suggestion = await ai_mapping(s["sheet_name"], s["columns"], sample)
        if suggestion is not None:
            ai_used = True
        else:
            suggestion = heuristic_mapping(s["columns"])
        det_year, year_source = detect_academic_year(
            s["sheet_name"], s["columns"], sample, file.filename
        )
        if det_year:
            detected_years.append(det_year)
        out_sheets.append(
            ImportAnalyzeSheet(
                sheet_name=s["sheet_name"],
                columns=s["columns"],
                sample_rows=[{k: (str(v) if v is not None else None) for k, v in r.items()} for r in sample],
                row_count=len(s["rows"]),
                suggested_entity=suggestion["entity"],
                suggested_mapping=suggestion["mapping"],
                confidence=suggestion.get("confidence"),
                notes=suggestion.get("notes"),
                detected_academic_year=det_year,
                academic_year_source=year_source,
            )
        )

    return ImportAnalyzeOut(
        file_token=token,
        ai_used=ai_used,
        academic_year=settings.default_academic_year,
        year_options=build_year_options(settings.default_academic_year, detected_years),
        sheets=out_sheets,
    )


@router.post("/commit", response_model=ImportCommitOut)
async def commit(
    body: ImportCommitIn,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_writer),
):
    path = CACHE_DIR / f"{body.file_token}.xlsx"
    if not path.exists():
        raise HTTPException(404, "Upload expired. Please re-upload the file.")

    workbook = {s["sheet_name"]: s for s in read_workbook(str(path), sample_size=10000)}
    result = ImportCommitOut(
        students_created=0, student_fees_created=0, fee_structures_created=0, skipped=0, errors=[]
    )

    # Category cache: name(lower) -> id. New category names found in the file are
    # created on the fly so the school's groupings survive the import.
    existing_cats = (
        await db.execute(
            select(StudentCategory).where(StudentCategory.workspace_id == user.workspace_id)
        )
    ).scalars().all()
    cat_cache: dict[str, uuid.UUID] = {c.name.strip().lower(): c.id for c in existing_cats}

    async def resolve_category(raw_value) -> uuid.UUID | None:
        if raw_value is None or str(raw_value).strip() == "":
            return None
        name = str(raw_value).strip()
        key = name.lower()
        if key in cat_cache:
            return cat_cache[key]
        cat = StudentCategory(workspace_id=user.workspace_id, name=name)
        db.add(cat)
        await db.flush()
        cat_cache[key] = cat.id
        return cat.id

    for sel in body.sheets:
        if sel.entity == "skip":
            continue
        sheet = workbook.get(sel.sheet_name)
        if not sheet:
            result.errors.append(f"Sheet '{sel.sheet_name}' not found.")
            continue
        year = sel.academic_year or settings.default_academic_year
        mapping = sel.mapping

        if sel.entity == "students_fees":
            for raw in sheet["rows"]:
                name_col = mapping.get("name")
                name = str(raw.get(name_col)).strip() if name_col and raw.get(name_col) else ""
                if not name:
                    result.skipped += 1
                    continue
                category_id = await resolve_category(
                    raw.get(mapping["category"]) if mapping.get("category") else None
                )
                student = Student(
                    workspace_id=user.workspace_id,
                    name=name,
                    admission_number=_get(raw, mapping, "admission_number"),
                    roll_number=_get(raw, mapping, "roll_number"),
                    class_name=_get(raw, mapping, "class_name"),
                    section=_get(raw, mapping, "section"),
                    category_id=category_id,
                    father_name=_get(raw, mapping, "father_name"),
                    father_phone=_get(raw, mapping, "father_phone"),
                    mother_name=_get(raw, mapping, "mother_name"),
                    mother_phone=_get(raw, mapping, "mother_phone"),
                    phone=_get(raw, mapping, "phone"),
                )
                db.add(student)
                await db.flush()
                result.students_created += 1

                # Fee amounts: prefer explicit total + fee-after-discount, else derive.
                total = _num(raw.get(mapping.get("total_fee"))) if mapping.get("total_fee") else Decimal("0")
                fad = _num(raw.get(mapping.get("fee_after_discount"))) if mapping.get("fee_after_discount") else None
                discount = _num(raw.get(mapping.get("discount"))) if mapping.get("discount") else Decimal("0")
                if fad is not None and fad > 0 and total > 0 and fad <= total:
                    discount = q(total - fad)
                    net = fad
                elif fad is not None and fad > 0 and total <= 0:
                    total = fad
                    net = fad
                else:
                    net = q(total - discount)
                opening = _num(raw.get(mapping.get("opening_dues"))) if mapping.get("opening_dues") else Decimal("0")

                # Skip rows with no money at all (likely blank/placeholder rows).
                if total <= 0 and opening <= 0:
                    continue

                # Total collected: sum of mapped quarter columns, else single paid_amount.
                quarter_cols = [f for f in QUARTER_FIELDS if mapping.get(f)]
                if quarter_cols:
                    total_paid = q(sum(_num(raw.get(mapping[f])) for f in quarter_cols))
                    inst_rows = [
                        Installment(installment_number=i + 1, label=QUARTER_LABELS[i], amount=amt, due_date=None)
                        for i, amt in enumerate(even_split(net, 4))
                    ]
                else:
                    total_paid = _num(raw.get(mapping.get("paid_amount"))) if mapping.get("paid_amount") else Decimal("0")
                    inst_rows = [Installment(installment_number=1, amount=net, paid_amount=Decimal("0"), due_date=None)]

                total_paid = max(total_paid, Decimal("0"))
                _waterfall(inst_rows, total_paid)
                applied_paid = q(sum(q(i.paid_amount) for i in inst_rows))

                sf = StudentFee(
                    workspace_id=user.workspace_id,
                    student_id=student.id,
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
                if applied_paid > 0:
                    first_paid = next((i for i in inst_rows if q(i.paid_amount) > 0), inst_rows[0])
                    db.add(
                        Transaction(
                            workspace_id=user.workspace_id,
                            student_fee_id=sf.id,
                            installment_id=first_paid.id,
                            amount=applied_paid,
                            type="payment",
                            receipt_number=_get(raw, mapping, "receipt_number"),
                            note="Imported opening balance",
                            created_by=user.id,
                            created_by_name=user.name,
                        )
                    )
                recompute_student_fee(sf)
                result.student_fees_created += 1

        elif sel.entity == "fee_structures":
            for raw in sheet["rows"]:
                class_col = mapping.get("class_name")
                class_name = str(raw.get(class_col)).strip() if class_col and raw.get(class_col) else ""
                if not class_name:
                    result.skipped += 1
                    continue
                total = _num(raw.get(mapping.get("total_amount"))) if mapping.get("total_amount") else Decimal("0")
                if total <= 0:
                    result.skipped += 1
                    continue
                num_col = mapping.get("num_installments")
                try:
                    n = int(float(str(raw.get(num_col)))) if num_col and raw.get(num_col) else 1
                except (ValueError, TypeError):
                    n = 1
                n = max(1, min(n, 12))
                row_year = _get(raw, mapping, "academic_year") or year
                amounts = even_split(total, n)
                fs = FeeStructure(
                    workspace_id=user.workspace_id,
                    class_name=class_name,
                    academic_year=row_year,
                    total_amount=total,
                    num_installments=n,
                    created_by=user.id,
                    templates=[
                        FeeInstallmentTemplate(installment_number=i + 1, amount=amounts[i], due_date=None)
                        for i in range(n)
                    ],
                )
                db.add(fs)
                result.fee_structures_created += 1

    await db.flush()
    path.unlink(missing_ok=True)
    return result


def _get(raw: dict, mapping: dict, field: str):
    col = mapping.get(field)
    if not col:
        return None
    v = raw.get(col)
    if v is None or str(v).strip() == "":
        return None
    return str(v).strip()


def _waterfall(installments: list[Installment], total_paid: Decimal) -> None:
    """Apply a lump-sum collected amount across installments in order.

    Fills each installment up to its amount before moving to the next; any excess
    lands on the last installment. This keeps a fully-paid student showing as paid
    even when the source sheet recorded the total in a single quarter column.
    """
    remaining = q(total_paid)
    for inst in installments:
        if remaining <= 0:
            break
        pay = min(remaining, q(inst.amount))
        inst.paid_amount = pay
        remaining = q(remaining - pay)
    if remaining > 0 and installments:
        last = installments[-1]
        last.paid_amount = q(q(last.paid_amount) + remaining)
