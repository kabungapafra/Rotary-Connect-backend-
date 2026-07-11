"""Thin wrapper around Firebase Cloud Messaging.

Sending is always best-effort: a login, event save, or scheduled reminder
must never fail because FCM is slow, misconfigured, or a device's token has
gone stale — every call here swallows its own errors and just logs them,
same reasoning as sms.py.
"""

import json
import logging

from . import config, models
from .database import SessionLocal

logger = logging.getLogger("rotary.push")

_app = None
_init_failed = False


def _get_app():
    """Lazily initializes the Firebase Admin app from the service account
    JSON in FIREBASE_CREDENTIALS_JSON. Returns None (and never retries) if
    push isn't configured or the credentials don't parse — a malformed env
    var must not crash every request that tries to send a push."""
    global _app, _init_failed
    if not config.PUSH_ENABLED or _init_failed:
        return None
    if _app is None:
        try:
            import firebase_admin
            from firebase_admin import credentials

            cred = credentials.Certificate(json.loads(config.FIREBASE_CREDENTIALS_JSON))
            _app = firebase_admin.initialize_app(cred)
        except Exception:
            logger.exception("Failed to initialize Firebase Admin — push disabled")
            _init_failed = True
            return None
    return _app


def _prune_token(token: str) -> None:
    """A token FCM reports as unregistered will only ever fail again —
    delete it so we stop paying for (and logging) retries against it."""
    db = SessionLocal()
    try:
        db.query(models.DeviceToken).filter(models.DeviceToken.token == token).delete()
        db.commit()
    except Exception:
        logger.exception("Failed to prune stale device token")
    finally:
        db.close()


def send_push(
    token: str, title: str, body: str, data: dict[str, str] | None = None
) -> bool:
    """Send one push. Returns whether it was actually sent (False if push
    isn't configured or the send failed)."""
    app = _get_app()
    if app is None:
        logger.info("Push disabled (no FIREBASE_CREDENTIALS_JSON) — skipped a message")
        return False

    from firebase_admin import messaging
    from firebase_admin.exceptions import NotFoundError

    try:
        messaging.send(
            messaging.Message(
                token=token,
                notification=messaging.Notification(title=title, body=body),
                data=data or {},
            ),
            app=app,
        )
        return True
    except NotFoundError:
        logger.info("Pruning stale/unregistered device token")
        _prune_token(token)
        return False
    except Exception:
        logger.exception("FCM push failed")
        return False


def send_bulk_push(
    tokens: list[str], title: str, body: str, data: dict[str, str] | None = None
) -> None:
    """Same message to several device tokens — a bad/stale token in the
    list must not stop the rest from going out."""
    for token in tokens:
        send_push(token, title, body, data)


def tokens_for_club(db, club_id: int) -> list[str]:
    """Every registered device token belonging to a member of this club —
    the push equivalent of sms.py callers collecting member.phone lists."""
    return [
        row.token
        for row in db.query(models.DeviceToken)
        .join(models.Member, models.DeviceToken.member_id == models.Member.id)
        .filter(models.Member.club_id == club_id)
        .all()
    ]
