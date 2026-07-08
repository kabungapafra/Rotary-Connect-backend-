import random

from datetime import date, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models

DATE_FORMAT = "%d %b %Y"  # e.g. "12 Aug 2026" — matches the dashboard's own display format


def generate_member_number(db: Session) -> str:
    """Next free RCM-XXXX member number (seeded numbers count up from 0001)."""
    max_id = db.query(func.max(models.Member.id)).scalar() or 0
    candidate = max_id + 1
    while db.query(models.Member).filter(
        models.Member.member_number == f"RCM-{candidate:04d}"
    ).first():
        candidate += 1
    return f"RCM-{candidate:04d}"


def generate_pin() -> str:
    return f"{random.randint(0, 9999):04d}"


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
