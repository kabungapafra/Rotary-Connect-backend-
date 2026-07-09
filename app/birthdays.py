"""Birthday-wish SMS. Members enter their date of birth as free text (e.g.
"14 Mar 1990"), so parsing is lenient and unparseable values are just
skipped rather than treated as errors.

`last_birthday_wished` makes the check idempotent (safe to call as often as
we like) — that's what lets us check opportunistically on login/check-in as
a fallback for the daily sweep missing a sleeping Render free-tier dyno.
"""

import logging
from datetime import date

from sqlalchemy.orm import Session

from . import models
from .sms import send_sms

logger = logging.getLogger("rotary.birthdays")

_DOB_FORMATS = ("%d %b %Y", "%d %B %Y", "%d/%m/%Y", "%d-%m-%Y")


def _parse_month_day(dob: str) -> tuple[int, int] | None:
    from datetime import datetime

    dob = dob.strip()
    if not dob:
        return None
    for fmt in _DOB_FORMATS:
        try:
            parsed = datetime.strptime(dob, fmt)
            return parsed.month, parsed.day
        except ValueError:
            continue
    return None


def wish_if_due(db: Session, member: models.Member, today: date | None = None) -> None:
    """Send (and record) a birthday SMS for one member if today is their
    birthday and they haven't already been wished today."""
    today = today or date.today()
    if member.last_birthday_wished == today:
        return
    month_day = _parse_month_day(member.dob)
    if month_day != (today.month, today.day):
        return
    sent = send_sms(
        member.phone,
        f"Happy birthday, {member.name.split()[0]}! 🎉 Wishing you a wonderful year "
        f"ahead from all of us at {member.club.name}.",
    )
    if sent:
        member.last_birthday_wished = today
        db.commit()


def run_daily_sweep(db: Session) -> int:
    """Check every member with a phone number; return how many were wished."""
    today = date.today()
    members = db.query(models.Member).filter(models.Member.phone != "").all()
    count = 0
    for member in members:
        before = member.last_birthday_wished
        wish_if_due(db, member, today)
        if member.last_birthday_wished != before:
            count += 1
    logger.info("Birthday sweep: wished %d member(s)", count)
    return count
