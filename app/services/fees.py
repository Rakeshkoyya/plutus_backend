"""Business logic for fee status computation and installment generation."""
from datetime import date, timedelta
from decimal import Decimal

from app.models import Installment, StudentFee


def q(value) -> Decimal:
    """Coerce to a 2dp Decimal."""
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def installment_status(inst: Installment, today: date | None = None) -> str:
    today = today or date.today()
    paid = q(inst.paid_amount)
    amount = q(inst.amount)
    if paid >= amount and amount > 0:
        return "paid"
    if inst.due_date and inst.due_date < today and paid < amount:
        return "overdue"
    if paid > 0:
        return "partial"
    return "pending"


def recompute_installment(inst: Installment, today: date | None = None) -> None:
    inst.status = installment_status(inst, today)
    if q(inst.paid_amount) >= q(inst.amount) and q(inst.amount) > 0:
        inst.paid_date = inst.paid_date or (today or date.today())


def aggregate_paid(installments: list[Installment]) -> Decimal:
    return q(sum(q(i.paid_amount) for i in installments))


def overdue_amount(installments: list[Installment], today: date | None = None) -> Decimal:
    today = today or date.today()
    total = Decimal("0")
    for i in installments:
        if i.due_date and i.due_date < today and q(i.paid_amount) < q(i.amount):
            total += q(i.amount) - q(i.paid_amount)
    return q(total)


def student_fee_status(sf: StudentFee, installments: list[Installment], today: date | None = None) -> str:
    today = today or date.today()
    net = q(sf.net_fee)
    paid = aggregate_paid(installments)
    if net > 0 and paid >= net:
        return "paid"
    if overdue_amount(installments, today) > 0:
        return "overdue"
    if paid > 0:
        return "partial"
    return "pending"


def recompute_student_fee(sf: StudentFee, today: date | None = None) -> None:
    for inst in sf.installments:
        recompute_installment(inst, today)
    sf.status = student_fee_status(sf, sf.installments, today)


def even_split(total: Decimal, n: int) -> list[Decimal]:
    """Split ``total`` into ``n`` parts; remainder lands on the last part."""
    total = q(total)
    if n <= 0:
        return []
    base = (total / n).quantize(Decimal("0.01"))
    parts = [base] * (n - 1)
    parts.append(q(total - base * (n - 1)))
    return parts


def default_due_dates(n: int, start: date | None = None) -> list[date]:
    """Generate monthly due dates starting from the academic year start (April)."""
    if start is None:
        today = date.today()
        year = today.year if today.month >= 4 else today.year - 1
        start = date(year, 4, 1)
    out = []
    for i in range(n):
        month_index = start.month - 1 + i * (12 // max(n, 1) if n <= 12 else 1)
        # spread roughly across the year
        y = start.year + month_index // 12
        m = month_index % 12 + 1
        out.append(date(y, m, 1))
    return out


def proportional_installments(net_fee: Decimal, template_amounts: list[Decimal]) -> list[Decimal]:
    """Scale template installment amounts down so they sum to net_fee (after discount)."""
    net_fee = q(net_fee)
    gross = q(sum(q(a) for a in template_amounts))
    if gross <= 0:
        return even_split(net_fee, len(template_amounts) or 1)
    scaled = [q(q(a) * net_fee / gross) for a in template_amounts]
    # fix rounding drift on the last element
    drift = net_fee - q(sum(scaled))
    if scaled:
        scaled[-1] = q(scaled[-1] + drift)
    return scaled
