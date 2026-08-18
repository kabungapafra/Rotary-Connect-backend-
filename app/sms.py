"""Thin wrapper around the Yoola SMS gateway.

Sending is always best-effort: a signup, check-in, or event save must never
fail because the SMS provider is slow or down, so every call here swallows
its own errors and just logs them.
"""

import logging
import re

import requests

from . import config, models
from .database import SessionLocal

logger = logging.getLogger("rotary.sms")

_MAX_MESSAGE_LENGTH = 480  # a handful of SMS segments; also caps abuse cost

# Store links appended to first-time credential messages, so a new member can
# install the app straight from the SMS instead of searching the stores for
# it. Defined once here because three different flows hand out credentials
# (system admin adds a member, president adds a member, new club president)
# and their instructions must not drift apart.
ANDROID_APP_URL = "https://play.google.com/store/apps/details?id=com.digiflecttech.rotaryconnect"
IOS_APP_URL = "https://apps.apple.com/us/app/rotary-connect-club-meetings/id6793908530"
APP_DOWNLOAD_LINE = f"Get the app - Android: {ANDROID_APP_URL} iPhone: {IOS_APP_URL}"

# Every distinct kind of message the app sends, mapped to the Club column
# that gates it — each is on by default (see the model) so nothing changes
# until a club's SMS preferences are edited. Keys are what call sites pass
# as `sms_type`; also the admin dashboard's per-club SMS-types form fields.
SMS_TYPE_COLUMNS: dict[str, str] = {
    "birthday": "sms_birthday_enabled",
    "guest_thank_you": "sms_guest_thank_you_enabled",
    "event_reminder": "sms_event_reminder_enabled",
    "event_thank_you": "sms_event_thank_you_enabled",
    "new_member": "sms_new_member_enabled",
    "new_president": "sms_new_president_enabled",
    "admin_pin_reset": "sms_admin_pin_reset_enabled",
    "self_service_pin_reset": "sms_self_service_pin_reset_enabled",
}


def _log_attempt(phone: str, status: str, club_id: int | None = None) -> None:
    """Record one send attempt so the admin dashboard's SMS view can show
    real numbers instead of guessing. Best-effort like everything else here
    — a logging failure must never be the reason an SMS call raises.
    `club_id` attributes the send to a club for the per-club usage figure;
    it stays None for sends made outside any club context."""
    db = SessionLocal()
    try:
        db.add(models.SmsLog(phone=phone, status=status, club_id=club_id))
        db.commit()
    except Exception:
        logger.exception("Failed to record SMS log entry")
    finally:
        db.close()


def normalize_ugandan_phone(raw: str) -> str | None:
    """Convert a locally-entered number (e.g. "0772 000 000") to the
    country-code form Yoola expects ("256772000000"). Returns None if the
    input doesn't look like a plausible phone number."""
    digits = re.sub(r"\D", "", raw or "")
    if digits.startswith("0"):
        digits = "256" + digits[1:]
    elif digits.startswith("256"):
        pass
    elif len(digits) == 9:
        digits = "256" + digits
    if not re.fullmatch(r"256\d{9}", digits):
        return None
    return digits


def _club_sms_allows(club_id: int, sms_type: str | None) -> bool:
    db = SessionLocal()
    try:
        club = db.get(models.Club, club_id)
        if club is None or not club.sms_enabled:
            return False
        if sms_type is None:
            return True
        column = SMS_TYPE_COLUMNS.get(sms_type)
        return column is None or getattr(club, column)
    finally:
        db.close()


def send_sms(
    phone: str, message: str, club_id: int | None = None, sms_type: str | None = None
) -> bool:
    """Send one SMS. Returns whether it was actually sent (False if SMS
    isn't configured, the phone is invalid, the request failed, or — when
    `club_id` is given — that club has withheld SMS overall or, when
    `sms_type` is also given, that specific message type)."""
    if not config.SMS_ENABLED:
        logger.info("SMS disabled (no YOOLA_API_KEY) — skipped message to %s", phone)
        return False
    if club_id is not None and not _club_sms_allows(club_id, sms_type):
        logger.info(
            "SMS disabled for club %s (type=%s) — skipped message to %s",
            club_id, sms_type, phone,
        )
        return False

    number = normalize_ugandan_phone(phone)
    if number is None:
        logger.warning("Skipped SMS to invalid phone number: %r", phone)
        return False

    body = message.strip()[:_MAX_MESSAGE_LENGTH]
    if not body:
        return False

    try:
        response = requests.post(
            config.YOOLA_API_URL,
            json={"api_key": config.YOOLA_API_KEY, "phone": number, "message": body},
            timeout=15,
        )
        if response.status_code >= 400:
            logger.error(
                "Yoola SMS to %s failed: %s %s", number, response.status_code, response.text[:300]
            )
            _log_attempt(number, "failed", club_id)
            return False
        _log_attempt(number, "sent", club_id)
        return True
    except requests.RequestException as exc:
        logger.error("Yoola SMS to %s raised %s", number, exc)
        _log_attempt(number, "failed", club_id)
        return False


def send_bulk_sms(
    phones: list[str], message: str, club_id: int | None = None, sms_type: str | None = None
) -> None:
    """Send the same message to several numbers, one at a time. Used for
    club-wide announcements (new fellowship events); a bad number in the
    list must not stop the rest from going out."""
    for phone in phones:
        send_sms(phone, message, club_id=club_id, sms_type=sms_type)
