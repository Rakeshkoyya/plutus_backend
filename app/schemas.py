import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# ---------- Auth ----------
class LoginRequest(BaseModel):
    identifier: str  # username or email
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    username: str | None
    email: str
    role: str
    auth_provider: str
    workspace_id: uuid.UUID


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class AuthConfigOut(BaseModel):
    google_enabled: bool
    ai_enabled: bool
    default_academic_year: str


# ---------- Student categories ----------
class StudentCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class StudentCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    is_active: bool | None = None


class StudentCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    is_active: bool
    student_count: int = 0


# ---------- Students ----------
class StudentBase(BaseModel):
    name: str
    admission_number: str | None = None
    roll_number: str | None = None
    class_name: str | None = None
    section: str | None = None
    category_id: uuid.UUID | None = None
    father_name: str | None = None
    father_phone: str | None = None
    mother_name: str | None = None
    mother_phone: str | None = None
    phone: str | None = None
    enrollment_status: str = "active"
    tc_given: bool = False


class StudentCreate(StudentBase):
    pass


class StudentUpdate(BaseModel):
    name: str | None = None
    admission_number: str | None = None
    roll_number: str | None = None
    class_name: str | None = None
    section: str | None = None
    category_id: uuid.UUID | None = None
    father_name: str | None = None
    father_phone: str | None = None
    mother_name: str | None = None
    mother_phone: str | None = None
    phone: str | None = None
    enrollment_status: str | None = None
    tc_given: bool | None = None


class StudentOut(StudentBase):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    category_name: str | None = None


# ---------- Fee structures ----------
class InstallmentTemplateIn(BaseModel):
    installment_number: int
    label: str | None = None
    amount: Decimal
    due_date: date | None = None


class InstallmentTemplateOut(InstallmentTemplateIn):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class FeeStructureCreate(BaseModel):
    class_name: str
    category_id: uuid.UUID | None = None
    academic_year: str
    total_amount: Decimal
    num_installments: int = Field(ge=1, le=12)
    installments: list[InstallmentTemplateIn]


class FeeStructureUpdate(FeeStructureCreate):
    pass


class FeeStructureOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    class_name: str
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    academic_year: str
    total_amount: Decimal
    num_installments: int
    is_active: bool
    created_at: datetime
    templates: list[InstallmentTemplateOut] = []


# ---------- Student fees / installments ----------
class InstallmentIn(BaseModel):
    installment_number: int
    label: str | None = None
    amount: Decimal
    due_date: date | None = None


class InstallmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    installment_number: int
    label: str | None = None
    amount: Decimal
    due_date: date | None
    paid_amount: Decimal
    status: str
    paid_date: date | None


class FirstPaymentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount: Decimal
    paid_on: date | None = Field(default=None, alias="date")
    mode: str | None = "cash"
    receipt_number: str | None = None
    installment_number: int = 1


class StudentFeeCreate(BaseModel):
    student_id: uuid.UUID
    fee_structure_id: uuid.UUID | None = None
    academic_year: str | None = None
    total_fee: Decimal
    discount: Decimal = Decimal("0")
    opening_dues: Decimal = Decimal("0")
    use_custom_schedule: bool = False
    installments: list[InstallmentIn] = []
    first_payment: FirstPaymentIn | None = None


class StudentFeeUpdate(BaseModel):
    discount: Decimal | None = None
    opening_dues: Decimal | None = None


class StudentFeeListItem(BaseModel):
    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    class_name: str | None
    category_name: str | None = None
    academic_year: str
    total_fee: Decimal
    discount: Decimal
    net_fee: Decimal
    opening_dues: Decimal
    paid: Decimal
    pending: Decimal
    status: str


class StudentFeeDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    student_id: uuid.UUID
    student_name: str
    class_name: str | None
    category_name: str | None = None
    academic_year: str
    total_fee: Decimal
    discount: Decimal
    net_fee: Decimal
    opening_dues: Decimal
    total_payable: Decimal
    paid: Decimal
    balance: Decimal
    status: str
    installments: list[InstallmentOut]


# ---------- Payments ----------
class PaymentIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    amount: Decimal
    paid_on: date | None = Field(default=None, alias="date")
    note: str | None = None
    mode: str | None = "cash"
    receipt_number: str | None = None


class InstallmentDueDateUpdate(BaseModel):
    due_date: date


# ---------- Transactions ----------
class TransactionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    student_fee_id: uuid.UUID
    installment_id: uuid.UUID | None
    amount: Decimal
    type: str
    note: str | None
    mode: str | None
    receipt_number: str | None
    created_at: datetime
    created_by_name: str | None


# ---------- Dashboard ----------
class DashboardSummary(BaseModel):
    total_fee: Decimal
    collected_fee: Decimal
    pending_installments: int
    overdue_amount: Decimal


class OverdueStudent(BaseModel):
    student_fee_id: uuid.UUID
    student_name: str
    class_name: str | None
    overdue_amount: Decimal
    earliest_due_date: date | None


class ChartData(BaseModel):
    collected: Decimal
    pending: Decimal
    overdue: Decimal


# ---------- Import ----------
class ImportAnalyzeSheet(BaseModel):
    sheet_name: str
    columns: list[str]
    sample_rows: list[dict]
    row_count: int
    suggested_entity: str  # students_fees | fee_structures | unknown
    suggested_mapping: dict
    confidence: str | None = None
    notes: str | None = None
    detected_academic_year: str | None = None
    academic_year_source: str | None = None  # sheet | column | filename | None


class ImportAnalyzeOut(BaseModel):
    file_token: str
    ai_used: bool
    academic_year: str
    year_options: list[str] = []
    sheets: list[ImportAnalyzeSheet]


class ImportCommitColumn(BaseModel):
    sheet_name: str
    entity: str  # students_fees | fee_structures | skip
    mapping: dict  # target_field -> source column header
    academic_year: str | None = None


class ImportCommitIn(BaseModel):
    file_token: str
    sheets: list[ImportCommitColumn]


class ImportCommitOut(BaseModel):
    students_created: int
    student_fees_created: int
    fee_structures_created: int
    skipped: int
    errors: list[str]
