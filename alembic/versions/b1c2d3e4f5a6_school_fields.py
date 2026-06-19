"""school data fields: categories, contacts, arrears, labels, receipts

Revision ID: b1c2d3e4f5a6
Revises: 54d58ab11c6d
Create Date: 2026-06-19 16:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "54d58ab11c6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Editable per-workspace student categories (Day Scholar, Hosteller, ...)
    op.create_table(
        "student_categories",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("workspace_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "name", name="uq_category_workspace_name"),
    )
    op.create_index(op.f("ix_student_categories_workspace_id"), "student_categories", ["workspace_id"], unique=False)

    # Students: admission no, category, parent contacts, enrollment state
    op.add_column("students", sa.Column("admission_number", sa.String(length=120), nullable=True))
    op.add_column("students", sa.Column("category_id", sa.UUID(), nullable=True))
    op.add_column("students", sa.Column("father_name", sa.String(length=255), nullable=True))
    op.add_column("students", sa.Column("father_phone", sa.String(length=40), nullable=True))
    op.add_column("students", sa.Column("mother_name", sa.String(length=255), nullable=True))
    op.add_column("students", sa.Column("mother_phone", sa.String(length=40), nullable=True))
    op.add_column("students", sa.Column("phone", sa.String(length=40), nullable=True))
    op.add_column(
        "students",
        sa.Column("enrollment_status", sa.String(length=20), nullable=False, server_default="active"),
    )
    op.add_column(
        "students",
        sa.Column("tc_given", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_foreign_key(
        "fk_students_category", "students", "student_categories", ["category_id"], ["id"], ondelete="SET NULL"
    )

    # Fee structures can vary by category
    op.add_column("fee_structures", sa.Column("category_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "fk_fee_structures_category",
        "fee_structures",
        "student_categories",
        ["category_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # Named installment periods
    op.add_column("fee_installment_templates", sa.Column("label", sa.String(length=60), nullable=True))
    op.add_column("installments", sa.Column("label", sa.String(length=60), nullable=True))

    # Previous-year arrears on the student fee
    op.add_column(
        "student_fees",
        sa.Column("opening_dues", sa.Numeric(precision=12, scale=2), nullable=False, server_default="0"),
    )

    # Receipt number on transactions
    op.add_column("transactions", sa.Column("receipt_number", sa.String(length=60), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "receipt_number")
    op.drop_column("student_fees", "opening_dues")
    op.drop_column("installments", "label")
    op.drop_column("fee_installment_templates", "label")
    op.drop_constraint("fk_fee_structures_category", "fee_structures", type_="foreignkey")
    op.drop_column("fee_structures", "category_id")
    op.drop_constraint("fk_students_category", "students", type_="foreignkey")
    op.drop_column("students", "tc_given")
    op.drop_column("students", "enrollment_status")
    op.drop_column("students", "phone")
    op.drop_column("students", "mother_phone")
    op.drop_column("students", "mother_name")
    op.drop_column("students", "father_phone")
    op.drop_column("students", "father_name")
    op.drop_column("students", "category_id")
    op.drop_column("students", "admission_number")
    op.drop_index(op.f("ix_student_categories_workspace_id"), table_name="student_categories")
    op.drop_table("student_categories")
