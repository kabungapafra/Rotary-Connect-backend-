from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db
from ..rate_limit import (
    clear_failed_attempts,
    is_locked_out,
    rate_limit_ok,
    record_failed_attempt,
)

router = APIRouter(prefix="/admin/auth", tags=["admin"])

_IP_WINDOW_SECONDS = 600
_IP_MAX_PER_WINDOW = 10
_LOCKOUT_WINDOW_SECONDS = 900
_LOCKOUT_MAX_ATTEMPTS = 5


@router.post("/login", response_model=schemas.AdminLoginResponse)
def admin_login(payload: schemas.AdminLoginRequest, request: Request, db: Session = Depends(get_db)):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(db, f"admin_login_ip:{client_ip}", _IP_MAX_PER_WINDOW, _IP_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many requests — try again shortly")

    account_key = f"admin_login_id:{payload.email.strip().lower()}"
    if is_locked_out(db, account_key, _LOCKOUT_MAX_ATTEMPTS, _LOCKOUT_WINDOW_SECONDS):
        raise HTTPException(
            status_code=429,
            detail="Too many failed attempts on this account — try again in 15 minutes",
        )

    admin = (
        db.query(models.AdminUser)
        .filter(models.AdminUser.email == payload.email.strip().lower())
        .first()
    )
    if admin is None or not security.verify_password(payload.password, admin.password_hash):
        record_failed_attempt(db, account_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    clear_failed_attempts(db, account_key)
    token = security.create_admin_access_token(admin.id)
    return schemas.AdminLoginResponse(access_token=token, admin=admin)
