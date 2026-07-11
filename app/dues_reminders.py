"""Dues reminder push: a nudge to members who haven't paid dues for the
current period yet. last_dues_reminded makes this idempotent per period —
same pattern as birthdays.py's last_birthday_wished — so the sweep can run
as often as it likes without repeat-nagging.
"""

import logging

from sqlalchemy.orm import Session

from . import models
from .push import send_push
from .utils import current_period_label

logger = logging.getLogger("rotary.dues_reminders")


def remind_if_due(db: Session, member: models.Member, period_label: str) -> None:
    """Push one member a reminder if they're unpaid for `period_label` and
    haven't already been reminded for it. Only marks them reminded if a
    push actually sent — a member with no registered device just gets
    tried again next sweep."""
    if member.last_dues_reminded == period_label:
        return
    paid = db.query(models.DuesPayment).filter(
        models.DuesPayment.member_id == member.id,
        models.DuesPayment.period_label == period_label,
    ).first()
    if paid is not None:
        return
    tokens = [
        row.token
        for row in db.query(models.DeviceToken).filter(
            models.DeviceToken.member_id == member.id
        )
    ]
    sent = False
    for token in tokens:
        if send_push(
            token,
            "Dues reminder",
            f"Your {period_label} dues are still outstanding — {member.club.name}.",
            data={"type": "dues"},
        ):
            sent = True
    if sent:
        member.last_dues_reminded = period_label
        db.commit()


def run_sweep(db: Session) -> int:
    """Check every club with dues configured; return how many members were
    newly reminded."""
    count = 0
    for setting in db.query(models.ClubDuesSetting).all():
        period_label = current_period_label(setting.period)
        members = db.query(models.Member).filter(
            models.Member.club_id == setting.club_id
        ).all()
        for member in members:
            before = member.last_dues_reminded
            remind_if_due(db, member, period_label)
            if member.last_dues_reminded != before:
                count += 1
    logger.info("Dues reminder sweep: reminded %d member(s)", count)
    return count
