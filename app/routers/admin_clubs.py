from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_admin
from ..sms import send_sms
from ..storage import delete_gallery_image, delete_gallery_photo, store_club_logo
from ..utils import (
    compute_payment_status,
    format_display_date,
    generate_member_number,
    generate_pin,
    parse_display_date,
)

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
        club_type=club.club_type,
        members_count=club.members_count,
        fee_amount=club.fee_amount,
        last_paid_date=format_display_date(club.last_paid_date),
        next_due_date=format_display_date(club.next_due_date),
        payment_status=compute_payment_status(club.next_due_date),
        joined=club.created_at.strftime("%d %b %Y"),
        logo=club.logo,
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


@router.post("", response_model=schemas.ClubCreateResponse)
def create_club(
    payload: schemas.ClubCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)
):
    president_phone = payload.president_phone.strip()
    if president_phone and db.query(models.Member).filter(
        models.Member.phone == president_phone
    ).first():
        raise HTTPException(
            status_code=422,
            detail="A member with the president's phone number already exists",
        )

    club = models.Club(
        name=payload.name.strip() or "Untitled Club",
        district=payload.district.strip() or "—",
        location=payload.location.strip() or "—",
        status="active",
        club_type="rotaract" if payload.club_type.strip().lower() == "rotaract" else "rotary",
        members_count=payload.members_count or 10,
        fee_amount=payload.fee_amount or 0,
        last_paid_date=parse_display_date(payload.first_payment_date),
        next_due_date=parse_display_date(payload.next_due_date),
    )
    db.add(club)
    db.flush()
    try:
        club.logo, club.logo_storage_key = store_club_logo(payload.logo, club.id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # The club's first administrator: only this Club President account can
    # add and manage the club's other administrators and members.
    president_out = None
    if payload.president_name.strip() and president_phone:
        pin = generate_pin()
        president = models.Member(
            club_id=club.id,
            member_number=generate_member_number(db),
            name=payload.president_name.strip(),
            role="Club President",
            is_board=True,
            status="active",
            email=payload.president_email.strip(),
            phone=president_phone,
            dob=payload.president_dob.strip(),
            pin_hash=security.hash_pin(pin),
        )
        db.add(president)
        db.flush()
        president_out = schemas.PresidentCredentials(
            id=president.id,
            name=president.name,
            phone=president.phone,
            member_number=president.member_number,
            pin=pin,
        )
        background_tasks.add_task(
            send_sms,
            president_phone,
            f"Welcome aboard Rotary Connect, President - {club.name}. "
            f"Your login: Member No. {president.member_number} or your phone number, PIN {pin}. "
            f"Download the app and sign in to get started.",
        )

    db.commit()
    db.refresh(club)
    return schemas.ClubCreateResponse(club=_to_out(club), president=president_out)


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


@router.delete("/{club_id}")
def delete_club(club_id: int, db: Session = Depends(get_db)):
    """Remove a club and everything belonging to it. Every table with a
    non-nullable FK into clubs/members (added over time as features grew:
    polls, dues, transactions, minutes, milestones, gallery, apologies,
    RSVPs) has to be cleared first or the final delete trips a Postgres
    FK violation — see the equivalent cleanup in tests/conftest.py."""
    club = _get_or_404(db, club_id)
    meeting_ids = [
        m.id for m in db.query(models.Meeting).filter(models.Meeting.club_id == club_id)
    ]
    if meeting_ids:
        db.query(models.CheckIn).filter(models.CheckIn.meeting_id.in_(meeting_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Meeting).filter(models.Meeting.id.in_(meeting_ids)).delete(
            synchronize_session=False
        )
    event_ids = [e.id for e in db.query(models.Event).filter(models.Event.club_id == club_id)]
    if event_ids:
        db.query(models.EventRsvp).filter(models.EventRsvp.event_id.in_(event_ids)).delete(
            synchronize_session=False
        )
    poll_ids = [p.id for p in db.query(models.Poll).filter(models.Poll.club_id == club_id)]
    if poll_ids:
        db.query(models.PollVote).filter(models.PollVote.poll_id.in_(poll_ids)).delete(
            synchronize_session=False
        )
    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == club_id).delete(
        synchronize_session=False
    )
    photos = db.query(models.GalleryPhoto).filter(models.GalleryPhoto.club_id == club_id)
    for photo in photos:
        if photo.storage_key:
            delete_gallery_photo(photo.storage_key)
    photos.delete(synchronize_session=False)
    docs = db.query(models.ClubDocument).filter(models.ClubDocument.club_id == club_id)
    for doc in docs:
        delete_gallery_image(doc.storage_key)
    docs.delete(synchronize_session=False)
    member_ids = [
        m.id for m in db.query(models.Member.id).filter(models.Member.club_id == club_id)
    ]
    if member_ids:
        db.query(models.DeviceToken).filter(models.DeviceToken.member_id.in_(member_ids)).delete(
            synchronize_session=False
        )
    db.query(models.Apology).filter(models.Apology.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.DuesPayment).filter(models.DuesPayment.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Transaction).filter(models.Transaction.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Poll).filter(models.Poll.club_id == club_id).delete(synchronize_session=False)
    db.query(models.Minute).filter(models.Minute.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Milestone).filter(models.Milestone.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.ClubDuesSetting).filter(models.ClubDuesSetting.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Member).filter(models.Member.club_id == club_id).delete(
        synchronize_session=False
    )
    db.query(models.Event).filter(models.Event.club_id == club_id).delete(
        synchronize_session=False
    )
    project_ids = [
        p.id for p in db.query(models.Project.id).filter(models.Project.club_id == club_id)
    ]
    if project_ids:
        db.query(models.ProjectUpdate).filter(
            models.ProjectUpdate.project_id.in_(project_ids)
        ).delete(synchronize_session=False)
    db.query(models.Project).filter(models.Project.club_id == club_id).delete(
        synchronize_session=False
    )
    if club.logo_storage_key:
        delete_gallery_image(club.logo_storage_key)
    db.delete(club)
    db.commit()
    return {"deleted": True}


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
