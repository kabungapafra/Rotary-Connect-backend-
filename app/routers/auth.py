from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import schemas, security
from ..database import get_db

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=schemas.LoginResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    member = security.find_member_by_identifier(db, payload.identifier)
    if member is None or not security.verify_pin(payload.pin, member.pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid member number/phone or PIN",
        )
    token = security.create_access_token(member.id)
    return schemas.LoginResponse(
        access_token=token,
        member=member,
        club_name=member.club.name,
        club_logo=member.club.logo,
    )
