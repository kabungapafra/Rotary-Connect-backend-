from datetime import datetime, timedelta, timezone

import bcrypt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from . import config, models
from .database import get_db

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def hash_pin(pin: str) -> str:
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()


def verify_pin(pin: str, pin_hash: str) -> bool:
    return bcrypt.checkpw(pin.encode(), pin_hash.encode())


# Admin passwords use the same bcrypt scheme as member PINs; separate names
# just keep call sites readable about which principal they're hashing for.
hash_password = hash_pin
verify_password = verify_pin


def _normalize(value: str) -> str:
    return "".join(ch for ch in value if ch.isalnum()).upper()


def find_member_by_identifier(db: Session, identifier: str) -> models.Member | None:
    """Match login input against member_number or phone, ignoring spacing/case/dashes.

    Loads the (small, club-sized) member table into Python rather than doing
    dialect-specific string normalization in SQL — simplest correct option
    at this scale.
    """
    norm = _normalize(identifier)
    if not norm:
        return None
    for member in db.query(models.Member).all():
        if _normalize(member.phone) == norm or _normalize(member.member_number) == norm:
            return member
    return None


def create_access_token(member_id: int) -> str:
    # Members stay signed in on their device until they uninstall the app,
    # so member tokens are long-lived; admin tokens keep the short expiry.
    expire = datetime.now(timezone.utc) + timedelta(days=365)
    payload = {"sub": str(member_id), "exp": expire}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def get_current_member(
    token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> models.Member:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        member_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise credentials_error

    member = db.get(models.Member, member_id)
    if member is None:
        raise credentials_error
    return member


# ── admin auth ────────────────────────────────────────────────────────────
# Separate token flavor (role="admin" claim) so a member token can never be
# replayed against admin endpoints, without touching the member token shape
# the mobile app already depends on.
admin_oauth2_scheme = OAuth2PasswordBearer(tokenUrl="admin/auth/login", auto_error=False)


def create_admin_access_token(admin_id: int) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=config.JWT_EXPIRE_MINUTES)
    payload = {"sub": str(admin_id), "role": "admin", "exp": expire}
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def get_current_admin(
    token: str | None = Depends(admin_oauth2_scheme), db: Session = Depends(get_db)
) -> models.AdminUser:
    credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate admin credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if token is None:
        raise credentials_error
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        if payload.get("role") != "admin":
            raise credentials_error
        admin_id = int(payload["sub"])
    except (JWTError, KeyError, TypeError, ValueError):
        raise credentials_error

    admin = db.get(models.AdminUser, admin_id)
    if admin is None:
        raise credentials_error
    return admin
