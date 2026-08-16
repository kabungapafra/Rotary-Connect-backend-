"""Recording who did what, for accountability.

Deliberately best-effort in the same spirit as sms._log_attempt and
main._record_error: an audit write must never be the reason an admin action
fails. A dropped entry is a gap in the record; a raised exception here would
be a club that could not be suspended.
"""

import logging

from sqlalchemy.orm import Session

from . import models

logger = logging.getLogger("rotary.audit")


def record(
    db: Session,
    actor: models.AdminUser | None,
    action: str,
    *,
    club: models.Club | None = None,
    subject: str = "",
    detail: str = "",
) -> None:
    """Add an audit entry to `db` without committing it.

    Left uncommitted on purpose: the caller commits it in the same
    transaction as the action itself, so the record and the change either
    both land or neither does — an audit log that can disagree with reality
    is worse than none.
    """
    try:
        db.add(
            models.AuditEntry(
                actor_id=actor.id if actor else None,
                actor_email=(actor.email if actor else "")[:120],
                action=action[:60],
                club_id=club.id if club else None,
                club_name=(club.name if club else "")[:160],
                subject=subject[:200],
                detail=detail[:400],
            )
        )
    except Exception:
        logger.exception("Failed to record audit entry for %s", action)
