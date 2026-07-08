from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas, security
from ..database import get_db

router = APIRouter(prefix="/admin/auth", tags=["admin"])


@router.post("/login", response_model=schemas.AdminLoginResponse)
def admin_login(payload: schemas.AdminLoginRequest, db: Session = Depends(get_db)):
    admin = (
        db.query(models.AdminUser)
        .filter(models.AdminUser.email == payload.email.strip().lower())
        .first()
    )
    if admin is None or not security.verify_password(payload.password, admin.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )
    token = security.create_admin_access_token(admin.id)
    return schemas.AdminLoginResponse(access_token=token, admin=admin)
