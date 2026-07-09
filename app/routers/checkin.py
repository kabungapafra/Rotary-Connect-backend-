import time
from collections import defaultdict
from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..birthdays import wish_if_due
from ..database import get_db
from ..security import get_current_member
from ..seed import DEFAULT_CLUB_NAME
from ..sms import normalize_ugandan_phone

router = APIRouter(prefix="/checkin", tags=["checkin"])

DEFAULT_MEETING_NAME = "Weekly Fellowship Meeting"

# Per-IP throttle for the unauthenticated guest endpoint — the per-phone
# daily dedup below stops repeat SMS to one number, this stops someone from
# working through many club_ids against a single victim number. In-memory
# is fine: single free-tier instance, and the window is short.
_GUEST_WINDOW_SECONDS = 600
_GUEST_MAX_PER_WINDOW = 5
_guest_request_log: dict[str, list[float]] = defaultdict(list)


def _guest_rate_limit_ok(client_ip: str) -> bool:
    now = time.monotonic()
    recent = [t for t in _guest_request_log[client_ip] if now - t < _GUEST_WINDOW_SECONDS]
    recent.append(now)
    _guest_request_log[client_ip] = recent
    return len(recent) <= _GUEST_MAX_PER_WINDOW


def _get_or_create_todays_meeting(db: Session, club_id: int) -> models.Meeting:
    today = date.today()
    meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id, models.Meeting.date == today)
        .first()
    )
    if meeting is None:
        meeting = models.Meeting(club_id=club_id, name=DEFAULT_MEETING_NAME, date=today)
        db.add(meeting)
        db.commit()
        db.refresh(meeting)
    return meeting


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
    if not _guest_rate_limit_ok(client_ip):
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
        return schemas.GuestCheckInResponse(ok=True, club_name=club.name)

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
    return schemas.GuestCheckInResponse(ok=True, club_name=club.name)


@router.get("/today", response_model=schemas.TodayResponse)
def today(club_id: int | None = None, db: Session = Depends(get_db)):
    """The mobile app doesn't send `club_id` (it only ever shows one club),
    so this falls back to the seeded default club when omitted."""
    today_date = date.today()
    if club_id is None:
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
