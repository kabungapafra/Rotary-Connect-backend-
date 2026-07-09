from datetime import date, datetime, time, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from .. import config, models
from ..database import get_db
from ..security import get_current_admin
from ..sms import send_sms

router = APIRouter(prefix="/admin/sms", tags=["admin"], dependencies=[Depends(get_current_admin)])


class SmsStatusOut(BaseModel):
    enabled: bool


class SmsSummaryOut(BaseModel):
    enabled: bool
    sent_today: int
    failed_today: int
    sent_total: int


class SmsTestRequest(BaseModel):
    phone: str
    message: str = "Rotary Connect: this is a test message from the system admin dashboard."


class SmsTestResponse(BaseModel):
    sent: bool
    enabled: bool


@router.get("/status", response_model=SmsStatusOut)
def sms_status():
    """Whether this deployment has a Yoola key configured — doesn't reveal
    the key itself, just whether sending is active."""
    return SmsStatusOut(enabled=config.SMS_ENABLED)


@router.post("/test", response_model=SmsTestResponse)
def sms_test(payload: SmsTestRequest):
    """Send a test SMS synchronously (unlike the normal best-effort
    background sends) so the caller gets a real success/failure result
    instead of firing blind."""
    sent = send_sms(payload.phone, payload.message)
    return SmsTestResponse(sent=sent, enabled=config.SMS_ENABLED)


@router.get("/summary", response_model=SmsSummaryOut)
def sms_summary(db: Session = Depends(get_db)):
    """Real counts from the send log — no delivery-receipt webhook exists
    yet, so this reports what we actually know: attempted sends and
    whether the gateway accepted or rejected each one."""
    today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    sent_today = (
        db.query(models.SmsLog)
        .filter(models.SmsLog.status == "sent", models.SmsLog.created_at >= today_start)
        .count()
    )
    failed_today = (
        db.query(models.SmsLog)
        .filter(models.SmsLog.status == "failed", models.SmsLog.created_at >= today_start)
        .count()
    )
    sent_total = db.query(models.SmsLog).filter(models.SmsLog.status == "sent").count()
    return SmsSummaryOut(
        enabled=config.SMS_ENABLED,
        sent_today=sent_today,
        failed_today=failed_today,
        sent_total=sent_total,
    )
