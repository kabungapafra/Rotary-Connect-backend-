"""Thin wrapper around the Yoola SMS gateway.

Sending is always best-effort: a signup, check-in, or event save must never
fail because the SMS provider is slow or down, so every call here swallows
its own errors and just logs them.
"""

import logging
import re

import requests

from . import config

logger = logging.getLogger("rotary.sms")

_MAX_MESSAGE_LENGTH = 480  # a handful of SMS segments; also caps abuse cost


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


def send_sms(phone: str, message: str) -> bool:
    """Send one SMS. Returns whether it was actually sent (False if SMS
    isn't configured, the phone is invalid, or the request failed)."""
    if not config.SMS_ENABLED:
        logger.info("SMS disabled (no YOOLA_API_KEY) — skipped message to %s", phone)
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
            return False
        return True
    except requests.RequestException as exc:
        logger.error("Yoola SMS to %s raised %s", number, exc)
        return False


def send_bulk_sms(phones: list[str], message: str) -> None:
    """Send the same message to several numbers, one at a time. Used for
    club-wide announcements (new fellowship events); a bad number in the
    list must not stop the rest from going out."""
    for phone in phones:
        send_sms(phone, message)
