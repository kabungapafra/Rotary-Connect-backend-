from datetime import date, datetime, timedelta

DATE_FORMAT = "%d %b %Y"  # e.g. "12 Aug 2026" — matches the dashboard's own display format


def parse_display_date(value: str | None) -> date | None:
    if not value or not value.strip():
        return None
    try:
        return datetime.strptime(value.strip(), DATE_FORMAT).date()
    except ValueError:
        return None


def format_display_date(value: date | None) -> str | None:
    return value.strftime(DATE_FORMAT) if value else None


def compute_payment_status(next_due_date: date | None) -> str:
    """paid / due-soon (within 7 days) / overdue, derived from the due date
    rather than stored, so it can never drift out of sync."""
    if next_due_date is None:
        return "paid"
    today = date.today()
    if next_due_date < today:
        return "overdue"
    if next_due_date <= today + timedelta(days=7):
        return "due-soon"
    return "paid"
