from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_admin
from ..utils import compute_payment_status, format_display_date, parse_display_date

router = APIRouter(
    prefix="/admin/clubs", tags=["admin"], dependencies=[Depends(get_current_admin)]
)


def _to_out(club: models.Club) -> schemas.ClubOut:
    return schemas.ClubOut(
        id=club.id,
        name=club.name,
        district=club.district,
        location=club.location,
        status=club.status,
        members_count=club.members_count,
        fee_amount=club.fee_amount,
        last_paid_date=format_display_date(club.last_paid_date),
        next_due_date=format_display_date(club.next_due_date),
        payment_status=compute_payment_status(club.next_due_date),
        joined=club.created_at.strftime("%d %b %Y"),
    )


def _get_or_404(db: Session, club_id: int) -> models.Club:
    club = db.get(models.Club, club_id)
    if club is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")
    return club


@router.get("", response_model=list[schemas.ClubOut])
def list_clubs(db: Session = Depends(get_db)):
    clubs = db.query(models.Club).order_by(models.Club.created_at.desc()).all()
    return [_to_out(c) for c in clubs]


@router.post("", response_model=schemas.ClubOut)
def create_club(payload: schemas.ClubCreate, db: Session = Depends(get_db)):
    club = models.Club(
        name=payload.name.strip() or "Untitled Club",
        district=payload.district.strip() or "—",
        location=payload.location.strip() or "—",
        status="active",
        members_count=payload.members_count or 10,
        fee_amount=payload.fee_amount or 0,
        last_paid_date=parse_display_date(payload.first_payment_date),
        next_due_date=parse_display_date(payload.next_due_date),
    )
    db.add(club)
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.patch("/{club_id}/status", response_model=schemas.ClubOut)
def set_club_status(club_id: int, payload: schemas.ClubStatusUpdate, db: Session = Depends(get_db)):
    club = _get_or_404(db, club_id)
    if payload.status not in ("active", "suspended"):
        raise HTTPException(status_code=422, detail="status must be 'active' or 'suspended'")
    club.status = payload.status
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.post("/{club_id}/payment", response_model=schemas.ClubOut)
def record_payment(club_id: int, payload: schemas.PaymentRecord, db: Session = Depends(get_db)):
    club = _get_or_404(db, club_id)
    if payload.amount:
        club.fee_amount = payload.amount
    parsed_paid = parse_display_date(payload.date_paid)
    parsed_due = parse_display_date(payload.next_due)
    club.last_paid_date = parsed_paid or date.today()
    club.next_due_date = parsed_due or (club.last_paid_date + timedelta(days=30))
    db.commit()
    db.refresh(club)
    return _to_out(club)


@router.get("/{club_id}/stats", response_model=schemas.ClubStatsOut)
def club_stats(club_id: int, db: Session = Depends(get_db)):
    club = _get_or_404(db, club_id)

    total_members = (
        db.query(models.Member).filter(models.Member.club_id == club_id).count()
    )
    latest_meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == club_id)
        .order_by(models.Meeting.date.desc())
        .first()
    )
    attendance_percent = 0
    if latest_meeting and total_members:
        checked_in = (
            db.query(models.CheckIn)
            .filter(models.CheckIn.meeting_id == latest_meeting.id)
            .count()
        )
        attendance_percent = round(checked_in / total_members * 100)

    return schemas.ClubStatsOut(club=_to_out(club), attendance_percent=attendance_percent)
