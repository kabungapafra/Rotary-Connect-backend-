"""Fellowship reminder SMS, sent 4 hours before each weekly event.

Events only carry a day-of-week (`dow`) plus a free-text "TIME & VENUE"
field (`meta`, e.g. "6:00 PM - Mbalwa Gardens Hall") — there's no separate
structured time column. We parse a clock time out of the front of `meta`
and use it to schedule a *recurring* cron job (one per event) that fires
4 hours before that time every week, in Africa/Kampala's fixed UTC+3 (no
DST, so a static offset is safe).

If `meta` has no parseable time, we can't compute "4 hours before" at all
— the caller falls back to an immediate one-off announcement instead of
silently never notifying the club.
"""

import logging
import re

from apscheduler.jobstores.base import JobLookupError
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal
from .scheduler import scheduler
from .sms import send_bulk_sms

logger = logging.getLogger("rotary.event_announcements")

_EAT_OFFSET_HOURS = 3  # Africa/Kampala is a fixed UTC+3, year-round.
_REMINDER_LEAD_HOURS = 4

_DOW_ORDER = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
_APS_DOW = {d: d.lower() for d in _DOW_ORDER}

_TIME_RE = re.compile(r"(\d{1,2}):?(\d{2})?\s*([AaPp][Mm])?")


def parse_event_time(meta: str) -> tuple[int, int] | None:
    """Pull a 24h (hour, minute) out of the start of `meta`, e.g.
    "6:00 PM - Hall" -> (18, 0), "18:00" -> (18, 0). None if unparseable."""
    if not meta:
        return None
    head = re.split(r"[-–—·,]", meta, maxsplit=1)[0].strip()
    match = _TIME_RE.search(head)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    ampm = (match.group(3) or "").lower()
    if ampm == "pm" and hour != 12:
        hour += 12
    elif ampm == "am" and hour == 12:
        hour = 0
    return hour, minute


def _reminder_utc(dow: str, local_hour: int, local_minute: int) -> tuple[str, int, int]:
    """Local event time -> (apscheduler day_of_week, UTC hour, UTC minute)
    for a job firing `_REMINDER_LEAD_HOURS` before the event, correctly
    rolling the day-of-week backward if that crosses midnight."""
    idx = _DOW_ORDER.index(dow.upper()[:3]) if dow.upper()[:3] in _DOW_ORDER else 2
    total_minutes = local_hour * 60 + local_minute
    total_minutes -= (_EAT_OFFSET_HOURS + _REMINDER_LEAD_HOURS) * 60
    day_shift = 0
    while total_minutes < 0:
        total_minutes += 24 * 60
        day_shift -= 1
    while total_minutes >= 24 * 60:
        total_minutes -= 24 * 60
        day_shift += 1
    idx = (idx + day_shift) % 7
    hour, minute = divmod(total_minutes, 60)
    return _APS_DOW[_DOW_ORDER[idx]], hour, minute


def _job_id(event_id: int) -> str:
    return f"event_announce_{event_id}"


def _send_event_reminder(event_id: int) -> None:
    """Runs on the scheduler thread with its own DB session, re-reading the
    event/club fresh each week so an edited name/meta is always current."""
    db = SessionLocal()
    try:
        event = db.get(models.Event, event_id)
        if event is None:
            return
        club = db.get(models.Club, event.club_id)
        if club is None:
            return
        phones = [
            m.phone
            for m in db.query(models.Member).filter(models.Member.club_id == event.club_id)
            if m.phone
        ]
        text = f"📅 Reminder: {event.name}"
        if event.meta.strip():
            text += f" — {event.meta.strip()}"
        text += f" starts in {_REMINDER_LEAD_HOURS} hours. See you there! — {club.name}"
        send_bulk_sms(phones, text)
    finally:
        db.close()


def schedule_event_announcement(event: models.Event) -> bool:
    """(Re)schedule the recurring reminder for one event. Returns False
    (and unschedules any existing job) if `meta` has no parseable time."""
    parsed = parse_event_time(event.meta)
    if parsed is None:
        unschedule_event_announcement(event.id)
        logger.warning(
            "Event %d (%r) has no parseable time in meta %r — no reminder scheduled",
            event.id, event.name, event.meta,
        )
        return False
    aps_dow, utc_hour, utc_minute = _reminder_utc(event.dow, *parsed)
    scheduler.add_job(
        _send_event_reminder,
        "cron",
        day_of_week=aps_dow,
        hour=utc_hour,
        minute=utc_minute,
        args=[event.id],
        id=_job_id(event.id),
        replace_existing=True,
    )
    return True


def unschedule_event_announcement(event_id: int) -> None:
    try:
        scheduler.remove_job(_job_id(event_id))
    except JobLookupError:
        pass


def reschedule_all_event_announcements(db: Session) -> None:
    """Called at startup — the in-memory jobstore is wiped on every deploy
    or free-tier dyno restart, so every existing event's reminder needs to
    be re-registered."""
    for event in db.query(models.Event).all():
        schedule_event_announcement(event)
