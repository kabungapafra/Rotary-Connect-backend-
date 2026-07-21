from datetime import date, datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..birthdays import wish_if_due
from ..database import get_db
from ..event_announcements import (
    CHECKIN_LEAD_MINUTES,
    CHECKIN_WINDOW_MINUTES,
    checkin_window_utc,
    parse_event_time,
)
from ..rate_limit import rate_limit_ok
from ..security import get_current_member, get_optional_member
from ..seed import DEFAULT_CLUB_NAME
from ..sms import normalize_ugandan_phone
from ..utils import get_or_create_meeting

router = APIRouter(prefix="/checkin", tags=["checkin"])

DEFAULT_MEETING_NAME = "Weekly Fellowship Meeting"


def _check_in_window_error(club_id: int, db: Session) -> str | None:
    """None if check-in is currently allowed. Otherwise the message to show.

    Only enforced when at least one of today's events has a parseable time
    — a club with no schedulable event today (or an unparseable TIME &
    VENUE field) falls back to the old "any time" behavior rather than
    blocking check-in on data the schedule can't account for."""
    today = date.today()
    todays_dow = today.strftime("%a").upper()
    todays_events = (
        db.query(models.Event)
        .filter(models.Event.club_id == club_id, models.Event.dow == todays_dow)
        .all()
    )
    now = datetime.now(timezone.utc)
    checked_any = False
    for event in todays_events:
        parsed = parse_event_time(event.meta)
        if parsed is None:
            continue
        checked_any = True
        opens_at, closes_at = checkin_window_utc(*parsed, today)
        if opens_at <= now <= closes_at:
            return None
    if not checked_any:
        return None
    return (
        f"Check-in opens {CHECKIN_LEAD_MINUTES} minutes before the meeting "
        f"and closes {CHECKIN_WINDOW_MINUTES // 60} hour after it starts."
    )


# Per-IP throttle for the unauthenticated guest endpoint — the per-phone
# daily dedup below stops repeat SMS to one number, this stops someone from
# working through many club_ids against a single victim number.
_GUEST_WINDOW_SECONDS = 600
_GUEST_MAX_PER_WINDOW = 5


def _guest_rate_limit_ok(db: Session, client_ip: str) -> bool:
    return rate_limit_ok(db, f"guest:{client_ip}", _GUEST_MAX_PER_WINDOW, _GUEST_WINDOW_SECONDS)


def _get_or_create_todays_meeting(db: Session, club_id: int) -> models.Meeting:
    return get_or_create_meeting(db, club_id)


@router.post("", response_model=schemas.CheckInResponse)
def check_in(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    meeting = _get_or_create_todays_meeting(db, member.club_id)
    existing = (
        db.query(models.CheckIn)
        .filter(models.CheckIn.member_id == member.id, models.CheckIn.meeting_id == meeting.id)
        .first()
    )
    if existing:
        return schemas.CheckInResponse(
            already_checked_in=True,
            checked_in_at=existing.checked_in_at,
            meeting_name=meeting.name,
        )

    window_error = _check_in_window_error(member.club_id, db)
    if window_error is not None:
        raise HTTPException(status_code=422, detail=window_error)

    row = models.CheckIn(member_id=member.id, meeting_id=meeting.id)
    db.add(row)
    db.commit()
    db.refresh(row)
    # Same opportunistic birthday check as login — whichever the member
    # hits first today triggers it.
    background_tasks.add_task(wish_if_due, db, member)
    return schemas.CheckInResponse(
        already_checked_in=False,
        checked_in_at=row.checked_in_at,
        meeting_name=meeting.name,
    )


@router.post("/guest", response_model=schemas.GuestCheckInResponse)
def guest_check_in(
    payload: schemas.GuestCheckInRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """Unauthenticated: a walk-in guest registers themselves (or is
    registered by whoever is holding the phone) without any member being
    logged in — including a member of a *different* club, visiting this
    one. Scoped to a real club so the thank-you message names the right
    club and can't be abused to spam arbitrary text. The thank-you SMS
    itself is sent later by the periodic sweep in thank_you.py, 2 hours
    after this check-in, not from here."""
    client_ip = request.client.host if request.client else "unknown"
    if not _guest_rate_limit_ok(db, client_ip):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    club: models.Club | None = None
    if payload.club_id is not None:
        club = db.get(models.Club, payload.club_id)
    elif payload.club_name is not None and payload.club_name.strip():
        query_name = payload.club_name.strip()
        club = (
            db.query(models.Club)
            .filter(func.lower(models.Club.name) == query_name.lower())
            .first()
        )
        if club is None:
            raise HTTPException(
                status_code=404,
                detail=f'No club found named "{query_name}" — check the spelling.',
            )
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")
    name = payload.name.strip()[:120]
    if not name:
        raise HTTPException(status_code=422, detail="Guest name is required")
    phone = normalize_ugandan_phone(payload.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail="Enter a valid phone number")

    today = date.today()
    already = (
        db.query(models.GuestVisit)
        .filter(
            models.GuestVisit.club_id == club.id,
            models.GuestVisit.phone == phone,
            models.GuestVisit.visit_date == today,
        )
        .first()
    )
    if already:
        # Already logged (and thanked) today — idempotent no-op rather than
        # an error, so a retried request from a flaky connection is safe.
        return schemas.GuestCheckInResponse(ok=True, club_id=club.id, club_name=club.name)

    # Same door-side window a member check-in is held to (opens
    # CHECKIN_LEAD_MINUTES before the meeting, closes CHECKIN_WINDOW_MINUTES
    # after) — a guest scanning the club QR isn't a separate, unguarded path
    # to logging attendance whenever.
    window_error = _check_in_window_error(club.id, db)
    if window_error is not None:
        raise HTTPException(status_code=422, detail=window_error)

    visit = models.GuestVisit(
        club_id=club.id,
        name=name,
        phone=phone,
        host_name=payload.host_name.strip()[:120],
        guest_type=payload.guest_type.strip()[:40],
        visit_date=today,
    )
    db.add(visit)
    db.commit()
    return schemas.GuestCheckInResponse(ok=True, club_id=club.id, club_name=club.name)


@router.get("/club/{club_id}", response_model=schemas.VisitorClubOut)
def visitor_club(club_id: int, request: Request, db: Session = Depends(get_db)):
    """Unauthenticated: the visitor dashboard the app shows after a guest
    checks in at a club (and on later launches, until they scan a different
    club). Exposes only what the club already publishes — name, logo,
    branding type, and its events — never members or attendance. Per-IP
    rate limited so it can't be used to bulk-harvest every club's profile."""
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(db, f"visitorclub:{client_ip}", 30, _GUEST_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")
    club = db.get(models.Club, club_id)
    if club is None:
        raise HTTPException(status_code=404, detail="Club not found")
    events = (
        db.query(models.Event)
        .filter(models.Event.club_id == club.id)
        .order_by(models.Event.id)
        .all()
    )
    return schemas.VisitorClubOut(
        club_id=club.id,
        name=club.name,
        logo=club.logo,
        club_type=club.club_type,
        events=events,
    )


@router.get("/today", response_model=schemas.TodayResponse)
def today(
    db: Session = Depends(get_db),
    member: models.Member | None = Depends(get_optional_member),
):
    """Auth is optional — a logged-in member (this is also what the app's
    "Who's here" button calls) sees their own club's roster. Logged out
    (or an unrecognized/expired token), this falls back to the seeded
    default club rather than accepting a club_id from the request: an
    arbitrary club_id here would let anyone enumerate every club's roster
    and check-in times."""
    today_date = date.today()
    if member is not None:
        club_id = member.club_id
    else:
        default_club = (
            db.query(models.Club).filter(models.Club.name == DEFAULT_CLUB_NAME).first()
        )
        club_id = default_club.id if default_club else None
    meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id, models.Meeting.date == today_date)
        .first()
    )
    if meeting is None:
        return schemas.TodayResponse(
            meeting_name=DEFAULT_MEETING_NAME,
            date=today_date.isoformat(),
            member_count=0,
            members=[],
        )

    rows = (
        db.query(models.CheckIn)
        .filter(models.CheckIn.meeting_id == meeting.id)
        .order_by(models.CheckIn.checked_in_at)
        .all()
    )
    members = [
        schemas.CheckInMemberOut(
            name=row.member.name, role=row.member.role, checked_in_at=row.checked_in_at
        )
        for row in rows
    ]
    return schemas.TodayResponse(
        meeting_name=meeting.name,
        date=today_date.isoformat(),
        member_count=len(members),
        members=members,
    )
