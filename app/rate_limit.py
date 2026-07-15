"""Rate limiting and account-lockout helpers, backed by Postgres. An
in-memory dict would give each uvicorn worker process its own separate
budget instead of a shared one — with 2 worker processes in production,
that silently doubles every limit here (an attacker split across workers
gets ~2x the intended requests/attempts before being throttled)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from . import models


def rate_limit_ok(db: Session, key: str, max_per_window: int, window_seconds: int) -> bool:
    """True if `key` (e.g. "guest:1.2.3.4") is still under its request
    budget for the trailing window. Records this call as one of the
    requests either way."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    db.query(models.RateLimitHit).filter(
        models.RateLimitHit.key == key, models.RateLimitHit.ts < cutoff
    ).delete(synchronize_session=False)
    count = db.query(models.RateLimitHit).filter(models.RateLimitHit.key == key).count()
    db.add(models.RateLimitHit(key=key))
    db.commit()
    return count + 1 <= max_per_window


def record_failed_attempt(db: Session, key: str) -> None:
    db.add(models.FailedAttempt(key=key))
    db.commit()


def is_locked_out(db: Session, key: str, max_attempts: int, window_seconds: int) -> bool:
    """True once `key` has racked up max_attempts failures inside the
    trailing window. Doesn't itself record an attempt — call
    record_failed_attempt separately, only on an actual failed login."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    db.query(models.FailedAttempt).filter(
        models.FailedAttempt.key == key, models.FailedAttempt.ts < cutoff
    ).delete(synchronize_session=False)
    db.commit()
    count = db.query(models.FailedAttempt).filter(models.FailedAttempt.key == key).count()
    return count >= max_attempts


def clear_failed_attempts(db: Session, key: str) -> None:
    db.query(models.FailedAttempt).filter(models.FailedAttempt.key == key).delete(
        synchronize_session=False
    )
    db.commit()
