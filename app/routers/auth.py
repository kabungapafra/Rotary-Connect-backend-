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
from ..sms import send_sms
from ..utils import generate_pin, is_club_access_blocked

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

# Self-service PIN reset: capped per member (not just per IP) so the limit
# can't be sidestepped by switching networks. 30 days rather than a shorter
# window — this exists to stop SMS-cost abuse of one member's number, not
# to rate-limit genuine forgetfulness, which is rare enough that "3 a
# month" is generous in practice.
_FORGOT_PIN_IP_WINDOW_SECONDS = 600
_FORGOT_PIN_IP_MAX_PER_WINDOW = 10
_FORGOT_PIN_MEMBER_WINDOW_SECONDS = 30 * 24 * 3600
_FORGOT_PIN_MEMBER_MAX_PER_WINDOW = 3


@router.post("/login", response_model=schemas.LoginResponse)
def login(
    payload: schemas.LoginRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(db, f"login_ip:{client_ip}", _IP_MAX_PER_WINDOW, _IP_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    account_key = f"login_id:{payload.identifier.strip().lower()}"
    if is_locked_out(db, account_key, _LOCKOUT_MAX_ATTEMPTS, _LOCKOUT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts on this account — try again in 15 minutes",
        )

    member = security.find_member_by_identifier(db, payload.identifier)
    if member is None or not security.verify_pin(payload.pin, member.pin_hash):
        record_failed_attempt(db, account_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid member number/phone or PIN",
        )
    clear_failed_attempts(db, account_key)
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


@router.post("/forgot-pin")
def forgot_pin(
    payload: schemas.ForgotPinRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    db: Session = Depends(get_db),
):
    """Self-service PIN reset: unauthenticated by necessity (you've lost
    the PIN, so you can't prove who you are any other way in-app), so the
    response is always identical regardless of whether the identifier
    matched a real member or the per-member limit was already used up —
    otherwise this endpoint would let anyone enumerate valid member
    numbers/phones just by watching which ones get a different reply.
    The new PIN only ever reaches the phone number already on file, never
    the caller, so this can't be used to take over an account — the worst
    case is SMS-cost nuisance, which the per-member cap above bounds.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(
        db, f"forgot_pin_ip:{client_ip}",
        _FORGOT_PIN_IP_MAX_PER_WINDOW, _FORGOT_PIN_IP_WINDOW_SECONDS,
    ):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    generic_response = {
        "message": "If that member number or phone is registered, we've sent a new PIN by SMS."
    }
    member = security.find_member_by_identifier(db, payload.identifier)
    if member is None:
        return generic_response

    if not rate_limit_ok(
        db, f"forgot_pin_member:{member.id}",
        _FORGOT_PIN_MEMBER_MAX_PER_WINDOW, _FORGOT_PIN_MEMBER_WINDOW_SECONDS,
    ):
        return generic_response

    new_pin = generate_pin()
    member.pin_hash = security.hash_pin(new_pin)
    db.commit()
    background_tasks.add_task(
        send_sms,
        member.phone,
        f"Your Rotary Connect PIN has been reset. Member No. {member.member_number}, "
        f"new PIN {new_pin}. Sign in with these to continue.",
    )
    return generic_response
