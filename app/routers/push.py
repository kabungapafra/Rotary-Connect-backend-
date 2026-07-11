from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member

router = APIRouter(prefix="/push", tags=["push"])


@router.post("/register")
def register_device_token(
    payload: schemas.RegisterPushToken,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """Registers (or re-registers) one device's FCM token against the
    logged-in member. A token is unique to a device, not a member — if it
    was previously registered to someone else on this club's device (e.g. a
    shared front-desk phone), it now points here instead."""
    existing = db.query(models.DeviceToken).filter(
        models.DeviceToken.token == payload.token
    ).first()
    if existing is not None:
        existing.member_id = member.id
        existing.platform = payload.platform
    else:
        db.add(models.DeviceToken(
            member_id=member.id, token=payload.token, platform=payload.platform
        ))
    db.commit()
    return {"ok": True}
