from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import schemas, security
from ..birthdays import wish_if_due
from ..database import get_db
from ..rate_limit import (
    clear_failed_attempts,
    is_locked_out,
    rate_limit_ok,
    record_failed_attempt,
)
from ..utils import is_club_access_blocked

router = APIRouter(prefix="/auth", tags=["auth"])

# PINs are 4 digits (10,000 combinations) — without a limit here, an
# attacker who knows a member number (they're sequential: RC-0001,
# RC-0002, ...) could brute-force one in minutes. The per-IP limit slows a
# single attacker down; the per-account lockout stops the attack even if
# they spread requests across many IPs.
_IP_WINDOW_SECONDS = 600
_IP_MAX_PER_WINDOW = 15
_LOCKOUT_WINDOW_SECONDS = 900
_LOCKOUT_MAX_ATTEMPTS = 5


@router.post("/login", response_model=schemas.LoginResponse)
def login(
    payload: schemas.LoginRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(f"login_ip:{client_ip}", _IP_MAX_PER_WINDOW, _IP_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    account_key = f"login_id:{payload.identifier.strip().lower()}"
    if is_locked_out(account_key, _LOCKOUT_MAX_ATTEMPTS, _LOCKOUT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts on this account — try again in 15 minutes",
        )

    member = security.find_member_by_identifier(db, payload.identifier)
    if member is None or not security.verify_pin(payload.pin, member.pin_hash):
        record_failed_attempt(account_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid member number/phone or PIN",
        )
    clear_failed_attempts(account_key)
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
        club_type=member.club.club_type,
        club_status="suspended" if is_club_access_blocked(member.club) else "active",
    )
