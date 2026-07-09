"""Guest thank-you SMS, sent 2 hours after check-in — long enough for the
fellowship itself to be over — rather than the instant they scan the QR
code. Same idempotent-sweep pattern as birthdays.py: a `thanked_at` column
makes repeated sweeps safe, and a periodic job (plus one run at startup)
means a guest is still thanked even if the free-tier dyno was asleep or
restarted during the 2-hour wait.
"""

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models
from .sms import send_sms

logger = logging.getLogger("rotary.thank_you")

THANK_YOU_DELAY = timedelta(hours=2)


def send_pending_thank_yous(db: Session) -> int:
    cutoff = datetime.now(timezone.utc) - THANK_YOU_DELAY
    due = (
        db.query(models.GuestVisit)
        .filter(models.GuestVisit.thanked_at.is_(None), models.GuestVisit.created_at <= cutoff)
        .all()
    )
    count = 0
    for visit in due:
        club = visit.club
        sent = send_sms(
            visit.phone,
            f"Thank you for visiting {club.name} today, {visit.name.split()[0]}! "
            f"We hope to welcome you again soon. — {club.name}",
        )
        if sent:
            visit.thanked_at = datetime.now(timezone.utc)
            db.commit()
            count += 1
    if count:
        logger.info("Thank-you sweep: sent %d", count)
    return count
