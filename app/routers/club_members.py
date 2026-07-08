from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_member
from ..utils import generate_member_number, generate_pin

router = APIRouter(prefix="/club/members", tags=["club"])

PRESIDENT_ROLE = "Club President"


def _require_president(member: models.Member) -> None:
    """Only the Club President can add and manage the club's other
    administrators and members."""
    if member.role != PRESIDENT_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club President can manage members",
        )


@router.get("", response_model=list[schemas.MemberOut])
def list_members(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    return (
        db.query(models.Member)
        .filter(models.Member.club_id == member.club_id)
        .order_by(models.Member.name)
        .all()
    )


@router.post("", response_model=schemas.ClubMemberCreateResponse)
def add_member(
    payload: schemas.ClubMemberCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
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
        club_id=member.club_id,
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
    return schemas.ClubMemberCreateResponse(member=new_member, pin=pin)


@router.patch("/{member_id}", response_model=schemas.MemberOut)
def update_member(
    member_id: int,
    payload: schemas.ClubMemberUpdate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    target = db.get(models.Member, member_id)
    if target is None or target.club_id != member.club_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if payload.role is not None:
        target.role = payload.role.strip() or "Member"
    if payload.is_board is not None:
        target.is_board = payload.is_board
    if payload.status is not None:
        if payload.status not in ("active", "suspended"):
            raise HTTPException(
                status_code=422, detail="status must be 'active' or 'suspended'"
            )
        target.status = payload.status
    db.commit()
    db.refresh(target)
    return target
