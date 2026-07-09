import random

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_admin
from ..sms import send_sms

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
    member = _get_or_404(db, member_id)
    db.query(models.CheckIn).filter(models.CheckIn.member_id == member_id).delete(
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
