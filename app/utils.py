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


def get_or_create_meeting(db: Session, club_id: int, on_date: date | None = None) -> "models.Meeting":
    """One row per club per calendar day a meeting happens — shared by
    check-in and apologies so both land on the same Meeting row."""
    on_date = on_date or date.today()
    meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id, models.Meeting.date == on_date)
        .first()
    )
    if meeting is None:
        meeting = models.Meeting(club_id=club_id, name="Weekly Fellowship Meeting", date=on_date)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
    return meeting


def current_period_label(period: str, on_date: date | None = None) -> str:
    """The dues period a payment made "today" belongs to, e.g. "2026-Q3"
    (quarterly), "2026-07" (monthly), "2026" (annual)."""
    on_date = on_date or date.today()
    if period == "monthly":
        return f"{on_date.year}-{on_date.month:02d}"
    if period == "annual":
        return f"{on_date.year}"
    quarter = (on_date.month - 1) // 3 + 1
    return f"{on_date.year}-Q{quarter}"


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


def compute_week_streak(db: Session, member: "models.Member") -> int:
    """Consecutive meetings (most recent first, today or earlier) the
    member either checked into or sent an apology for — an excused
    absence preserves the streak, same as a Rotary club's own attendance
    bookkeeping treats a recorded apology, and matches this app already
    tracking apologies for exactly that reason. Stops at the first
    meeting with neither a check-in nor an apology on file."""
    meetings = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == member.club_id, models.Meeting.date <= date.today())
        .order_by(models.Meeting.date.desc())
        .all()
    )
    if not meetings:
        return 0

    checked_in_meeting_ids = {
        row[0]
        for row in db.query(models.CheckIn.meeting_id).filter(
            models.CheckIn.member_id == member.id,
            models.CheckIn.meeting_id.in_([m.id for m in meetings]),
        )
    }
    apologized_dates = {
        row[0]
        for row in db.query(models.Apology.meeting_date).filter(
            models.Apology.member_id == member.id,
            models.Apology.meeting_date.in_([m.date for m in meetings]),
        )
    }

    streak = 0
    for meeting in meetings:
        if meeting.id in checked_in_meeting_ids or meeting.date in apologized_dates:
            streak += 1
        else:
            break
    return streak


def is_club_access_blocked(club: models.Club) -> bool:
    """Whether members of this club should see the Club Suspended screen —
    either the system admin suspended it directly, or its dues went
    overdue. Derived at read time (never stored), same as
    compute_payment_status, so recording a payment self-heals it the
    moment next_due_date moves back into the future — no separate
    "un-suspend" step needed."""
    return club.status == "suspended" or compute_payment_status(club.next_due_date) == "overdue"
