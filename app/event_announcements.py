"""Event SMS: a reminder 4 hours before each weekly fellowship, and a
thank-you 1 hour after it to whoever checked in.

Events only carry a day-of-week (`dow`) plus a free-text "TIME & VENUE"
field (`meta`, e.g. "6:00 PM - Mbalwa Gardens Hall") — there's no separate
structured time column. We parse a clock time out of the front of `meta`
and use it to schedule two *recurring* cron jobs (one pair per event) in
Africa/Kampala's fixed UTC+3 (no DST, so a static offset is safe).

If `meta` has no parseable time, we can't compute either offset at all
— the caller falls back to an immediate one-off announcement instead of
silently never notifying the club.
"""

import logging
import re
from datetime import date, datetime, timedelta, timezone

from apscheduler.jobstores.base import JobLookupError
from sqlalchemy.orm import Session

from . import models
from .database import SessionLocal
from .push import send_bulk_push, tokens_for_club
from .scheduler import scheduler
from .sms import send_bulk_sms

logger = logging.getLogger("rotary.event_announcements")

_EAT_OFFSET_HOURS = 3  # Africa/Kampala is a fixed UTC+3, year-round.
_REMINDER_LEAD_HOURS = 4
_THANK_YOU_LAG_HOURS = 1

# Check-in (and the Home screen's "ongoing" badge) is only open in a window
# around a meeting's scheduled start — early enough to be useful at the
# door, but not open all day. Shared by checkin.py and club_data.py so the
# two never drift apart.
CHECKIN_LEAD_MINUTES = 15
CHECKIN_WINDOW_MINUTES = 60

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


def parse_event_end_time(meta: str) -> tuple[int, int] | None:
    """The event's END time, when the TIME field holds a range written as
    "6:00 PM to 8:00 PM" ("to" on purpose — the dash is already the
    time/venue separator in legacy metas like "6:00 PM - Hall"). None when
    there's no second clock time, which is every event created before end
    times existed."""
    if not meta:
        return None
    head = re.split(r"[-–—·,]", meta, maxsplit=1)[0]
    matches = [m for m in _TIME_RE.finditer(head) if m.group(1)]
    if len(matches) < 2:
        return None
    match = matches[1]
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


# Registration for an event's occurrence closes this long before its end.
REGISTRATION_CLOSE_LEAD_MINUTES = 15


def _week_occurrence_date(dow: str, eat_today: date) -> date:
    """The date this week's occurrence of `dow` falls on — the calendar
    (Week view) always shows the current Mon-Sun, so a Tuesday event's
    card sits on that real date all week, not just when today is Tuesday."""
    monday = eat_today - timedelta(days=eat_today.weekday())
    idx = _DOW_ORDER.index(dow.upper()[:3]) if dow.upper()[:3] in _DOW_ORDER else 2
    return monday + timedelta(days=idx)


def is_registration_open(dow: str, meta: str, now: datetime | None = None) -> bool:
    """False once this week's occurrence has closed — either it's a
    earlier day this week that's already fully over, or it's today and
    we're within REGISTRATION_CLOSE_LEAD_MINUTES of its end time (or
    past it). Still open for a later day this week (registration ahead
    of that occurrence) and reopens for a new week once this one rolls
    past Sunday. Events without a parseable end time never close on
    their own day (legacy metas) but still close once the day's past."""
    now = now or datetime.now(timezone.utc)
    eat_today = (now + timedelta(hours=_EAT_OFFSET_HOURS)).date()
    occurrence = _week_occurrence_date(dow, eat_today)
    if occurrence != eat_today:
        return occurrence > eat_today
    end = parse_event_end_time(meta)
    if end is None:
        return True
    closes_at = local_time_on_date_utc(*end, eat_today) - timedelta(
        minutes=REGISTRATION_CLOSE_LEAD_MINUTES
    )
    return now < closes_at


def is_event_editable(dow: str, meta: str, now: datetime | None = None) -> bool:
    """False once this week's occurrence has fully ended — either an
    earlier day this week already passed entirely, or it's today and
    we're past its actual end time (not registration's earlier cutoff).
    An event that already happened shouldn't have its name/time/venue
    rewritten after the fact. Events without a parseable end time are
    always editable on their own day (legacy metas), but still lock once
    the day's past. Reopens for a new week, same as is_registration_open."""
    now = now or datetime.now(timezone.utc)
    eat_today = (now + timedelta(hours=_EAT_OFFSET_HOURS)).date()
    occurrence = _week_occurrence_date(dow, eat_today)
    if occurrence != eat_today:
        return occurrence > eat_today
    end = parse_event_end_time(meta)
    if end is None:
        return True
    ends_at = local_time_on_date_utc(*end, eat_today)
    return now < ends_at


def venue_from_meta(meta: str) -> str:
    """Whatever follows the clock time in "6:00 PM - Hall" -> "Hall". If
    there's no separator (or no parseable time at all), the whole field is
    the venue text — better than showing nothing."""
    parts = re.split(r"[-–—·,]", meta, maxsplit=1)
    if len(parts) == 2 and parse_event_time(parts[0]) is not None:
        return parts[1].strip()
    return meta.strip()


def next_occurrence_utc(
    dow: str, local_hour: int, local_minute: int, now: datetime | None = None
) -> datetime:
    """The next absolute UTC datetime this weekly dow/local-time falls on
    (today counts, if it hasn't passed yet)."""
    now = now or datetime.now(timezone.utc)
    idx = _DOW_ORDER.index(dow.upper()[:3]) if dow.upper()[:3] in _DOW_ORDER else 2
    utc_hour = local_hour - _EAT_OFFSET_HOURS
    day_shift = 0
    while utc_hour < 0:
        utc_hour += 24
        day_shift -= 1
    while utc_hour >= 24:
        utc_hour -= 24
        day_shift += 1
    target_idx = (idx + day_shift) % 7
    days_ahead = (target_idx - now.weekday()) % 7
    candidate = (now + timedelta(days=days_ahead)).replace(
        hour=utc_hour, minute=local_minute, second=0, microsecond=0
    )
    if candidate < now:
        candidate += timedelta(days=7)
    return candidate


def local_time_on_date_utc(local_hour: int, local_minute: int, on_date: date) -> datetime:
    """The absolute UTC instant `local_hour:local_minute` (Africa/Kampala)
    falls on for a specific calendar date — unlike next_occurrence_utc,
    this doesn't search for the next future occurrence, since the caller
    (check-in's time-window gate) already knows which date it's checking."""
    utc_hour = local_hour - _EAT_OFFSET_HOURS
    day_shift = 0
    while utc_hour < 0:
        utc_hour += 24
        day_shift -= 1
    while utc_hour >= 24:
        utc_hour -= 24
        day_shift += 1
    target_date = on_date + timedelta(days=day_shift)
    return datetime(
        target_date.year, target_date.month, target_date.day,
        utc_hour, local_minute, tzinfo=timezone.utc,
    )


def checkin_window_utc(
    local_hour: int, local_minute: int, on_date: date
) -> tuple[datetime, datetime]:
    """(opens_at, closes_at) in UTC for the check-in window around a
    meeting starting at `local_hour:local_minute` on `on_date`."""
    start = local_time_on_date_utc(local_hour, local_minute, on_date)
    return (
        start - timedelta(minutes=CHECKIN_LEAD_MINUTES),
        start + timedelta(minutes=CHECKIN_WINDOW_MINUTES),
    )


def rsvp_target_date(dow: str, created: date) -> date:
    """The meeting date a web RSVP is for: the first occurrence of the
    event's weekly dow on/after the day the RSVP was made (same day
    counts)."""
    idx = _DOW_ORDER.index(dow.upper()[:3]) if dow.upper()[:3] in _DOW_ORDER else 2
    return created + timedelta(days=(idx - created.weekday()) % 7)


def _shifted_cron(
    dow: str, local_hour: int, local_minute: int, shift_hours: int
) -> tuple[str, int, int]:
    """Local event time, shifted by `shift_hours` (negative = before,
    positive = after) -> (apscheduler day_of_week, UTC hour, UTC minute),
    correctly rolling the day-of-week if the shift crosses midnight."""
    idx = _DOW_ORDER.index(dow.upper()[:3]) if dow.upper()[:3] in _DOW_ORDER else 2
    total_minutes = local_hour * 60 + local_minute
    total_minutes += (shift_hours - _EAT_OFFSET_HOURS) * 60
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


def _reminder_job_id(event_id: int) -> str:
    return f"event_announce_{event_id}"


def _thank_you_job_id(event_id: int) -> str:
    return f"event_thank_you_{event_id}"


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
        send_bulk_push(
            tokens_for_club(db, event.club_id),
            f"📅 {event.name}",
            f"Starts in {_REMINDER_LEAD_HOURS} hours"
            + (f" — {event.meta.strip()}" if event.meta.strip() else ""),
            data={"type": "event", "event_id": str(event.id)},
        )
    finally:
        db.close()


# Rotated so consecutive fellowships don't all get the identical text —
# which message a given meeting gets is picked by how many meetings the
# club has held (deterministic: a retry/restart resends the same one, not
# a different one).
_THANK_YOU_MESSAGES = [
    "Dear Rotarians, thank you for joining us for our fellowship. Your "
    "presence made the gathering special. We look forward to welcoming you "
    "again as we continue building friendship, fellowship, and service "
    "together.",
    "Thank you, dear Rotarians, for being part of our fellowship. Your "
    "time, smiles, and friendship made the day memorable. We can't wait to "
    "fellowship with you again soon.",
    "Dear Rotarians, we appreciate your presence at our fellowship. Every "
    "moment shared strengthens our Rotary family. We look forward to "
    "seeing you again at our next gathering.",
    "A big thank you to all Rotarians who joined our fellowship. Your "
    "participation brought energy and joy to our meeting. Let's continue "
    "growing together through friendship and service.",
    "Dear Rotarians, thank you for making our fellowship a success. Your "
    "presence reminds us of the power of Rotary friendship. We warmly "
    "invite you to join us again for more fellowship and service moments.",
]


def _send_event_thank_you(event_id: int) -> None:
    """Fires `_THANK_YOU_LAG_HOURS` after the event's scheduled start —
    thanks whoever actually checked in to today's meeting, not the whole
    club (unlike the reminder, which goes out to everyone beforehand)."""
    db = SessionLocal()
    try:
        event = db.get(models.Event, event_id)
        if event is None:
            return
        club = db.get(models.Club, event.club_id)
        if club is None:
            return
        meeting = (
            db.query(models.Meeting)
            .filter(models.Meeting.club_id == event.club_id, models.Meeting.date == date.today())
            .first()
        )
        if meeting is None:
            return
        checkins = db.query(models.CheckIn).filter(
            models.CheckIn.meeting_id == meeting.id
        ).all()
        if not checkins:
            return
        meetings_held = (
            db.query(models.Meeting)
            .filter(models.Meeting.club_id == event.club_id)
            .count()
        )
        message = _THANK_YOU_MESSAGES[meetings_held % len(_THANK_YOU_MESSAGES)]
        phones = [c.member.phone for c in checkins if c.member.phone]
        send_bulk_sms(phones, f"🙏 {message} — {club.name}")
        tokens = [
            row.token
            for row in db.query(models.DeviceToken).filter(
                models.DeviceToken.member_id.in_([c.member_id for c in checkins])
            )
        ]
        send_bulk_push(
            tokens,
            "🙏 Thanks for coming!",
            f"{message} — {club.name}",
            data={"type": "event", "event_id": str(event.id)},
        )
    finally:
        db.close()


def schedule_event_announcement(event: models.Event) -> bool:
    """(Re)schedule the recurring reminder + thank-you pair for one event.
    Returns False (and unschedules any existing jobs) if `meta` has no
    parseable time."""
    parsed = parse_event_time(event.meta)
    if parsed is None:
        unschedule_event_announcement(event.id)
        logger.warning(
            "Event %d (%r) has no parseable time in meta %r — no reminder scheduled",
            event.id, event.name, event.meta,
        )
        return False
    reminder_dow, reminder_hour, reminder_minute = _shifted_cron(
        event.dow, *parsed, -_REMINDER_LEAD_HOURS
    )
    scheduler.add_job(
        _send_event_reminder,
        "cron",
        day_of_week=reminder_dow,
        hour=reminder_hour,
        minute=reminder_minute,
        args=[event.id],
        id=_reminder_job_id(event.id),
        replace_existing=True,
    )
    thanks_dow, thanks_hour, thanks_minute = _shifted_cron(
        event.dow, *parsed, _THANK_YOU_LAG_HOURS
    )
    scheduler.add_job(
        _send_event_thank_you,
        "cron",
        day_of_week=thanks_dow,
        hour=thanks_hour,
        minute=thanks_minute,
        args=[event.id],
        id=_thank_you_job_id(event.id),
        replace_existing=True,
    )
    return True


def unschedule_event_announcement(event_id: int) -> None:
    for job_id in (_reminder_job_id(event_id), _thank_you_job_id(event_id)):
        try:
            scheduler.remove_job(job_id)
        except JobLookupError:
            pass


def reschedule_all_event_announcements(db: Session) -> None:
    """Called at startup — the in-memory jobstore is wiped on every deploy
    or free-tier dyno restart, so every existing event's reminder needs to
    be re-registered."""
    for event in db.query(models.Event).all():
        schedule_event_announcement(event)
