from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member
from ..seed import DEFAULT_CLUB_NAME

router = APIRouter(prefix="/checkin", tags=["checkin"])

DEFAULT_MEETING_NAME = "Weekly Fellowship Meeting"


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
    return schemas.CheckInResponse(
        already_checked_in=False,
        checked_in_at=row.checked_in_at,
        meeting_name=meeting.name,
    )


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
