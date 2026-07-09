from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import schemas, security
from ..birthdays import wish_if_due
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginResponse)
def login(
    payload: schemas.LoginRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    member = security.find_member_by_identifier(db, payload.identifier)
    if member is None or not security.verify_pin(payload.pin, member.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid member number/phone or PIN",
        )
    token = security.create_access_token(member.id)
    # Opportunistic birthday check: catches members on days the free-tier
    # server was asleep for the scheduled daily sweep. wish_if_due is a
    # no-op if it's not their birthday or they were already wished today.
    background_tasks.add_task(wish_if_due, db, member)
    return schemas.LoginResponse(
        access_token=token,
        member=member,
        club_id=member.club_id,
        club_name=member.club.name,
        club_logo=member.club.logo,
    )
