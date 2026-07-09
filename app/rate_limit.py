"""In-memory rate limiting and account-lockout helpers. In-memory is fine
for now: single free-tier instance, and every window here is short enough
that a restart just resets the clock rather than opening a real hole.
"""

import time
from collections import defaultdict

_request_log: dict[str, list[float]] = defaultdict(list)
_failed_attempts: dict[str, list[float]] = defaultdict(list)


def rate_limit_ok(key: str, max_per_window: int, window_seconds: int) -> bool:
    """True if `key` (e.g. "guest:1.2.3.4") is still under its request
    budget for the trailing window. Records this call as one of the
    requests either way."""
    now = time.monotonic()
    recent = [t for t in _request_log[key] if now - t < window_seconds]
    recent.append(now)
    _request_log[key] = recent
    return len(recent) <= max_per_window


def record_failed_attempt(key: str) -> None:
    _failed_attempts[key].append(time.monotonic())


def is_locked_out(key: str, max_attempts: int, window_seconds: int) -> bool:
    """True once `key` has racked up max_attempts failures inside the
    trailing window. Doesn't itself record an attempt — call
    record_failed_attempt separately, only on an actual failed login."""
    now = time.monotonic()
    recent = [t for t in _failed_attempts[key] if now - t < window_seconds]
    _failed_attempts[key] = recent
    return len(recent) >= max_attempts


def clear_failed_attempts(key: str) -> None:
    _failed_attempts.pop(key, None)
