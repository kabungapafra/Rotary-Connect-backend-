from datetime import date

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..security import get_current_member
from ..sms import send_sms
from ..utils import generate_member_number, generate_pin

router = APIRouter(prefix="/club/members", tags=["club"])

PRESIDENT_ROLE = "Club President"  # stored on a club's auto-created president
# "President" is the mobile app's Add Member role-dropdown label — a member
# manually given that title has the same authority as the auto-created one.
PRESIDENT_ROLES = {"Club President", "President"}

# The Secretary shares the President's management powers (members, events,
# projects, votes). The reverse doesn't hold: the Secretary workspace stays
# the Secretary's alone.
MANAGER_ROLES = PRESIDENT_ROLES | {"Secretary"}

# Executive roles allowed to generate an event's registration QR/link.
EVENT_REGISTRATION_ROLES = PRESIDENT_ROLES | {
    "Sergeant-at-Arms",
    "President-Elect",
    "Secretary",
    "Immediate Past President",
}


def _require_manager(member: models.Member) -> None:
    """Only the Club President or Secretary can add and manage the club's
    other administrators and members."""
    if member.role not in MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club President or Secretary can manage members",
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
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
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
    background_tasks.add_task(
        send_sms,
        phone,
        f"Welcome to {member.club.name}! Your Rotary Connect login: "
        f"Member No. {new_member.member_number}, PIN {pin}. "
        f"Download the app and sign in to get started.",
    )
    return schemas.ClubMemberCreateResponse(member=new_member, pin=pin)


@router.patch("/{member_id}", response_model=schemas.MemberOut)
def update_member(
    member_id: int,
    payload: schemas.ClubMemberUpdate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
    target = db.get(models.Member, member_id)
    if target is None or target.club_id != member.club_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Member not found")

    if payload.role is not None:
        target.role = payload.role.strip() or "Member"
    if payload.is_board is not None:
        target.is_board = payload.is_board
    if payload.status is not None:
        if payload.status not in ("active", "suspended", "terminated"):
            raise HTTPException(
                status_code=422,
                detail="status must be 'active', 'suspended' or 'terminated'",
            )
        # Dated only on the transition into "terminated", cleared on
        # reactivation — the club report's membership section counts
        # terminations by this date, not by whatever status happens to be
        # set right now.
        if payload.status == "terminated" and target.status != "terminated":
            target.terminated_at = date.today()
        elif payload.status == "active":
            target.terminated_at = None
        target.status = payload.status
    db.commit()
    db.refresh(target)
    return target
