from fastapi import APIRouter, Depends
from pydantic import BaseModel

from .. import config
from ..security import get_current_admin
from ..sms import send_sms

router = APIRouter(prefix="/admin/sms", tags=["admin"], dependencies=[Depends(get_current_admin)])


class SmsStatusOut(BaseModel):
    enabled: bool


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
