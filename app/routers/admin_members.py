import random

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_admin
from ..sms import send_sms
from ..storage import delete_gallery_image, delete_gallery_photo
from ..utils import generate_member_number, generate_pin

router = APIRouter(
    prefix="/admin/members", tags=["admin"], dependencies=[Depends(get_current_admin)]
)


def _to_out(member: models.Member) -> schemas.AdminMemberOut:
    return schemas.AdminMemberOut(
        id=member.id,
        name=member.name,
        phone=member.phone,
        club=member.club.name,
        status=member.status,
    )


def _get_or_404(db: Session, member_id: int) -> models.Member:
    member = db.get(models.Member, member_id)
    if member is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")
    return member


@router.get("", response_model=list[schemas.AdminMemberOut])
def list_members(
    search: str = "",
    club: str = "all",
    status_filter: str = "all",
    db: Session = Depends(get_db),
):
    query = db.query(models.Member).options(joinedload(models.Member.club))

    q = search.strip().lower()
    if q:
        query = query.filter(
            (models.Member.name.ilike(f"%{q}%")) | (models.Member.phone.ilike(f"%{q}%"))
        )
    if status_filter != "all":
        query = query.filter(models.Member.status == status_filter)

    members = query.order_by(models.Member.name).all()
    if club != "all":
        members = [m for m in members if m.club.name == club]
    return [_to_out(m) for m in members]


@router.post("", response_model=schemas.ClubMemberCreateResponse)
def create_member(
    payload: schemas.AdminMemberCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Lets the system admin add a member to any club directly — e.g. to
    bootstrap a club whose only member (the auto-created president) was
    since removed, without routing through that club's own president."""
    club = db.get(models.Club, payload.club_id)
    if club is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Club not found")
    name = payload.name.strip()
    phone = payload.phone.strip()
    if not name or not phone:
        raise HTTPException(status_code=422, detail="Name and phone are required")
    if db.query(models.Member).filter(models.Member.phone == phone).first():
        raise HTTPException(
            status_code=422, detail="A member with this phone number already exists"
        )

    pin = generate_pin()
    new_member = models.Member(
        club_id=club.id,
        member_number=generate_member_number(db),
        name=name,
        role=payload.role.strip() or "Member",
        is_board=payload.is_board,
        status="active",
        email=payload.email.strip(),
        phone=phone,
        dob=payload.dob.strip(),
        pin_hash=security.hash_pin(pin),
    )
    db.add(new_member)
    db.commit()
    db.refresh(new_member)
    background_tasks.add_task(
        send_sms,
        phone,
        f"Welcome to {club.name}! Your Rotary Connect login: "
        f"Member No. {new_member.member_number}, PIN {pin}. "
        f"Download the app and sign in to get started.",
    )
    return schemas.ClubMemberCreateResponse(member=new_member, pin=pin)


@router.patch("/{member_id}/status", response_model=schemas.AdminMemberOut)
def set_member_status(
    member_id: int, payload: schemas.MemberStatusUpdate, db: Session = Depends(get_db)
):
    member = _get_or_404(db, member_id)
    if payload.status not in ("active", "suspended"):
        raise HTTPException(status_code=422, detail="status must be 'active' or 'suspended'")
    member.status = payload.status
    db.commit()
    db.refresh(member)
    return _to_out(member)


@router.post("/{member_id}/reset-password", response_model=schemas.ResetPasswordResponse)
def reset_password(member_id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    member = _get_or_404(db, member_id)
    new_pin = f"{random.randint(0, 9999):04d}"
    member.pin_hash = security.hash_pin(new_pin)
    db.commit()
    background_tasks.add_task(
        send_sms,
        member.phone,
        f"Your Rotary Connect PIN has been reset. Member No. {member.member_number}, "
        f"new PIN {new_pin}. Sign in with these to continue.",
    )
    return schemas.ResetPasswordResponse(member_name=member.name, new_pin=new_pin)


@router.delete("/{member_id}")
def delete_member(member_id: int, db: Session = Depends(get_db)):
    """Same FK-cleanup gap as delete_club (see its docstring) — a member
    who ever voted, recorded a transaction, wrote up minutes, etc. would
    otherwise trip a Postgres FK violation on the final delete."""
    member = _get_or_404(db, member_id)
    db.query(models.CheckIn).filter(models.CheckIn.member_id == member_id).delete(
        synchronize_session=False
    )
    poll_ids = [p.id for p in db.query(models.Poll).filter(models.Poll.created_by == member_id)]
    if poll_ids:
        db.query(models.PollVote).filter(models.PollVote.poll_id.in_(poll_ids)).delete(
            synchronize_session=False
        )
    db.query(models.PollVote).filter(models.PollVote.member_id == member_id).delete(
        synchronize_session=False
    )
    db.query(models.Poll).filter(models.Poll.created_by == member_id).delete(
        synchronize_session=False
    )
    photos = db.query(models.GalleryPhoto).filter(
        models.GalleryPhoto.uploaded_by == member_id
    )
    for photo in photos:
        if photo.storage_key:
            delete_gallery_photo(photo.storage_key)
    photos.delete(synchronize_session=False)
    docs = db.query(models.ClubDocument).filter(models.ClubDocument.created_by == member_id)
    for doc in docs:
        delete_gallery_image(doc.storage_key)
    docs.delete(synchronize_session=False)
    db.query(models.DeviceToken).filter(models.DeviceToken.member_id == member_id).delete(
        synchronize_session=False
    )
    db.query(models.Apology).filter(models.Apology.member_id == member_id).delete(
        synchronize_session=False
    )
    db.query(models.Transaction).filter(models.Transaction.created_by == member_id).delete(
        synchronize_session=False
    )
    db.query(models.DuesPayment).filter(models.DuesPayment.member_id == member_id).delete(
        synchronize_session=False
    )
    db.query(models.Minute).filter(models.Minute.created_by == member_id).delete(
        synchronize_session=False
    )
    db.query(models.Milestone).filter(models.Milestone.created_by == member_id).delete(
        synchronize_session=False
    )
    db.query(models.ProjectUpdate).filter(models.ProjectUpdate.created_by == member_id).delete(
        synchronize_session=False
    )
    db.delete(member)
    db.commit()
    return {"deleted": True}


@router.get("/{member_id}/activity", response_model=schemas.MemberActivityOut)
def member_activity(member_id: int, db: Session = Depends(get_db)):
    member = _get_or_404(db, member_id)
    check_ins = (
        db.query(models.CheckIn)
        .filter(models.CheckIn.member_id == member_id)
        .order_by(models.CheckIn.checked_in_at.desc())
        .all()
    )
    last = check_ins[0].checked_in_at.strftime("%d %b %Y") if check_ins else None
    return schemas.MemberActivityOut(
        member_name=member.name, check_in_count=len(check_ins), last_check_in=last
    )
