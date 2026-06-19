import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


class Workspace(Base):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(255))
    username: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")  # local | google
    google_sub: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="superadmin")  # superadmin | admin | staff
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StudentCategory(Base):
    """Editable per-workspace student categories (e.g. Day Scholar, Hosteller, EWS).

    Schools group students by category, and the category often drives which fee
    applies (see FeeStructure.category_id). Categories are user-managed: they can be
    added or removed from Settings.
    """

    __tablename__ = "student_categories"
    __table_args__ = (
        UniqueConstraint("workspace_id", "name", name="uq_category_workspace_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Student(Base):
    __tablename__ = "students"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255))
    admission_number: Mapped[str | None] = mapped_column(String(120), nullable=True)  # SR / Adm. No.
    roll_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    class_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    section: Mapped[str | None] = mapped_column(String(60), nullable=True)
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("student_categories.id", ondelete="SET NULL"), nullable=True
    )
    # Parent / guardian contacts — needed to chase dues and send reminders
    father_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    father_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    mother_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mother_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(40), nullable=True)  # primary contact number
    # Enrollment state (active vs left); tc_given flags a transfer certificate issued
    enrollment_status: Mapped[str] = mapped_column(String(20), default="active")  # active | left
    tc_given: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    category: Mapped["StudentCategory | None"] = relationship()


class FeeStructure(Base):
    __tablename__ = "fee_structures"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    class_name: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("student_categories.id", ondelete="SET NULL"), nullable=True
    )  # fee can vary by category (Day Scholar vs Hosteller); null = applies to all
    academic_year: Mapped[str] = mapped_column(String(20))
    total_amount: Mapped[float] = mapped_column(Numeric(12, 2))
    num_installments: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    category: Mapped["StudentCategory | None"] = relationship()
    templates: Mapped[list["FeeInstallmentTemplate"]] = relationship(
        back_populates="fee_structure",
        cascade="all, delete-orphan",
        order_by="FeeInstallmentTemplate.installment_number",
    )


class FeeInstallmentTemplate(Base):
    __tablename__ = "fee_installment_templates"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    fee_structure_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fee_structures.id", ondelete="CASCADE"), index=True
    )
    installment_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. "1st Quarter"
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    fee_structure: Mapped["FeeStructure"] = relationship(back_populates="templates")


class StudentFee(Base):
    __tablename__ = "student_fees"
    __table_args__ = (
        UniqueConstraint("student_id", "academic_year", name="uq_student_year"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    student_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"), index=True)
    fee_structure_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fee_structures.id"), nullable=True
    )
    academic_year: Mapped[str] = mapped_column(String(20))
    total_fee: Mapped[float] = mapped_column(Numeric(12, 2))
    discount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    net_fee: Mapped[float] = mapped_column(Numeric(12, 2))
    # Arrears carried over from the previous year ("Last yr due"). Adds to the
    # outstanding balance but is kept separate from this year's net_fee.
    opening_dues: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    student: Mapped["Student"] = relationship()
    installments: Mapped[list["Installment"]] = relationship(
        back_populates="student_fee",
        cascade="all, delete-orphan",
        order_by="Installment.installment_number",
    )


class Installment(Base):
    __tablename__ = "installments"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    student_fee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_fees.id", ondelete="CASCADE"), index=True
    )
    installment_number: Mapped[int] = mapped_column(Integer)
    label: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. "1st Quarter"
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_amount: Mapped[float] = mapped_column(Numeric(12, 2), default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    student_fee: Mapped["StudentFee"] = relationship(back_populates="installments")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    student_fee_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("student_fees.id", ondelete="CASCADE"), index=True
    )
    installment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("installments.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[float] = mapped_column(Numeric(12, 2))
    type: Mapped[str] = mapped_column(String(30))  # payment | undo | discount | installment_edit
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    mode: Mapped[str | None] = mapped_column(String(20), nullable=True)  # cash | cheque | online
    receipt_number: Mapped[str | None] = mapped_column(String(60), nullable=True)  # e.g. FR-/2025-2026/151
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
