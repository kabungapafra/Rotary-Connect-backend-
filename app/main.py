import logging
import os
import traceback as traceback_module
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import config, models
from .birthdays import run_daily_sweep
from .database import Base, SessionLocal, engine
from .dues_reminders import run_sweep as run_dues_reminder_sweep
from .event_announcements import reschedule_all_event_announcements
from .leadership_transition import run_leadership_transitions
from .routers import (
    admin_analytics,
    admin_auth,
    admin_clubs,
    admin_members,
    admin_sms,
    auth,
    checkin,
    club_data,
    club_members,
    event_registration,
    gallery,
    polls,
    push,
    secretary,
    treasury,
)
from .scheduler import scheduler
from .seed import seed_bootstrap_data
from .storage import backfill_gallery_thumbs, migrate_legacy_photos
from .thank_you import send_pending_thank_yous

logger = logging.getLogger("rotary.main")

# Every module here logs through logging.getLogger("rotary.*"), which
# propagates to the root logger — uvicorn only configures its own
# "uvicorn.*" loggers, not root, so without this, INFO-level app logs
# (migration counts, SMS sends, ...) were silently dropped: only WARNING+
# ever reached stderr, via Python's handler-less "last resort" fallback.
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

if config.SENTRY_ENABLED:
    import sentry_sdk

    sentry_sdk.init(dsn=config.SENTRY_DSN, traces_sample_rate=0.1)

# The interactive docs advertise the whole API surface to anyone who finds
# them; keep them off in production and opt in locally with DOCS_ENABLED=1.
_docs_enabled = os.getenv("DOCS_ENABLED") == "1"
app = FastAPI(
    title="Rotary Connect API",
    docs_url="/docs" if _docs_enabled else None,
    redoc_url="/redoc" if _docs_enabled else None,
    openapi_url="/openapi.json" if _docs_enabled else None,
)

# The mobile app is a native HTTP client, not a browser, so CORS never
# applies to it either way. The only real browser caller is the admin
# dashboard — this used to be allow_origins=["*"], which let any website
# call an API that hands out bearer tokens and stores member phone
# numbers from a visitor's own browser session.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://rotary.digiflecttech.dev"],
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_methods=["*"],
    allow_headers=["*"],
)

# Requests slower than this (or any 5xx) get a slow_requests row so the
# admin System Health page can show a dying/slow API after the fact.
# Module-level (not captured in a closure) so tests can lower it to 0.
SLOW_REQUEST_MS = int(os.getenv("SLOW_REQUEST_MS", "1500"))


@app.middleware("http")
async def record_slow_requests(request: Request, call_next):
    import time as time_module

    start = time_module.monotonic()
    response = await call_next(request)
    duration_ms = int((time_module.monotonic() - start) * 1000)
    if request.method != "OPTIONS" and (
        duration_ms >= SLOW_REQUEST_MS or response.status_code >= 500
    ):
        db = SessionLocal()
        try:
            db.add(
                models.SlowRequest(
                    method=request.method,
                    path=request.url.path[:255],
                    status_code=response.status_code,
                    duration_ms=duration_ms,
                )
            )
            db.commit()
        except Exception:
            logger.exception("Failed to persist slow-request log")
        finally:
            db.close()
    return response


def _record_error(method: str, path: str, exc: Exception) -> None:
    """Persists to Postgres (not just journald) so an unhandled error is
    visible from the admin dashboard even with no Sentry/Crashlytics-style
    account configured. A fresh session, not the failed request's own —
    that session may itself be the thing that broke."""
    db = SessionLocal()
    try:
        db.add(
            models.ErrorLog(
                method=method,
                path=path,
                exception_type=type(exc).__name__,
                message=str(exc)[:2000],
                traceback="".join(
                    traceback_module.format_exception(type(exc), exc, exc.__traceback__)
                )[:10000],
            )
        )
        db.commit()
    except Exception:
        logger.exception("Failed to persist error log")
    finally:
        db.close()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette's own default for an uncaught exception is a bare-text 500
    # with no body guaranteed and no logging call of its own — this ensures
    # every unhandled error is both logged with a full traceback (so it
    # isn't just a client-side mystery) and returns the same JSON shape as
    # every other error response in this API.
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    _record_error(request.method, request.url.path, exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(auth.router)
app.include_router(checkin.router)
app.include_router(club_members.router)
app.include_router(club_data.router)
app.include_router(admin_auth.router)
app.include_router(admin_clubs.router)
app.include_router(admin_members.router)
app.include_router(admin_analytics.router)
app.include_router(admin_sms.router)
app.include_router(gallery.router)
app.include_router(event_registration.router)
app.include_router(treasury.router)
app.include_router(polls.router)
app.include_router(push.router)
app.include_router(secretary.router)


def _run_birthday_sweep_job() -> None:
    db = SessionLocal()
    try:
        run_daily_sweep(db)
    except Exception:
        logger.exception("Birthday sweep failed")
    finally:
        db.close()


def _run_thank_you_sweep_job() -> None:
    db = SessionLocal()
    try:
        send_pending_thank_yous(db)
    except Exception:
        logger.exception("Thank-you sweep failed")
    finally:
        db.close()


def _run_dues_reminder_sweep_job() -> None:
    db = SessionLocal()
    try:
        run_dues_reminder_sweep(db)
    except Exception:
        logger.exception("Dues reminder sweep failed")
    finally:
        db.close()


def _run_leadership_transition_job() -> None:
    db = SessionLocal()
    try:
        run_leadership_transitions(db)
    except Exception:
        logger.exception("Leadership transition sweep failed")
    finally:
        db.close()


def _run_error_log_cleanup_job() -> None:
    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        db.query(models.ErrorLog).filter(models.ErrorLog.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.query(models.MemberEvent).filter(models.MemberEvent.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.query(models.SlowRequest).filter(models.SlowRequest.created_at < cutoff).delete(
            synchronize_session=False
        )
        db.commit()
    except Exception:
        logger.exception("Error log cleanup failed")
    finally:
        db.close()


# Arbitrary but stable app-wide key for the scheduler election below.
_SCHEDULER_LOCK_KEY = 727401
# The winning worker's connection — held open for the whole process
# lifetime, because a Postgres advisory lock lives exactly as long as the
# connection that took it. Never returned to the pool, never closed.
_scheduler_lock_conn = None


def _try_acquire_scheduler_lock() -> bool:
    global _scheduler_lock_conn
    conn = engine.connect()
    got = conn.execute(
        text("SELECT pg_try_advisory_lock(:key)"), {"key": _SCHEDULER_LOCK_KEY}
    ).scalar()
    if got:
        _scheduler_lock_conn = conn
        return True
    conn.close()
    return False


@app.on_event("startup")
def on_startup() -> None:
    if config.JWT_SECRET == "dev-secret-change-me":
        logger.warning(
            "JWT_SECRET is still the insecure default — anyone can forge admin "
            "tokens. Set JWT_SECRET in the environment."
        )
    if config.ADMIN_PASSWORD == "admin123":
        logger.warning(
            "ADMIN_PASSWORD is still the insecure default — set ADMIN_PASSWORD "
            "in the environment before a system-admin account is bootstrapped "
            "with it."
        )

    # No Alembic yet — create_all is enough for the current MVP stage, plus
    # idempotent ALTERs for columns added after a deployment's tables existed
    # (create_all never alters existing tables).
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE clubs ADD COLUMN IF NOT EXISTS logo TEXT"))
        conn.execute(
            text(
                "ALTER TABLE clubs ADD COLUMN IF NOT EXISTS "
                "club_type VARCHAR(20) DEFAULT 'rotary'"
            )
        )
        conn.execute(
            text("ALTER TABLE members ADD COLUMN IF NOT EXISTS last_birthday_wished DATE")
        )
        conn.execute(
            text(
                "ALTER TABLE members ADD COLUMN IF NOT EXISTS "
                "last_dues_reminded VARCHAR(20)"
            )
        )
        conn.execute(
            text("ALTER TABLE guest_visits ADD COLUMN IF NOT EXISTS thanked_at TIMESTAMPTZ")
        )
        conn.execute(
            text("ALTER TABLE gallery_photos ADD COLUMN IF NOT EXISTS storage_key TEXT")
        )
        conn.execute(
            text("ALTER TABLE gallery_photos ADD COLUMN IF NOT EXISTS thumb TEXT")
        )
        conn.execute(text("ALTER TABLE events ADD COLUMN IF NOT EXISTS image TEXT"))
        conn.execute(
            text("ALTER TABLE events ADD COLUMN IF NOT EXISTS storage_key TEXT")
        )
        conn.execute(
            text(
                "ALTER TABLE event_rsvps ADD COLUMN IF NOT EXISTS "
                "attendee_type VARCHAR(40) DEFAULT 'Guest'"
            )
        )
        conn.execute(
            text(
                "ALTER TABLE event_rsvps ADD COLUMN IF NOT EXISTS "
                "club_name VARCHAR(160) DEFAULT ''"
            )
        )
        conn.execute(text("ALTER TABLE polls ADD COLUMN IF NOT EXISTS assignments TEXT"))
        conn.execute(text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS image TEXT"))
        conn.execute(
            text("ALTER TABLE projects ADD COLUMN IF NOT EXISTS storage_key TEXT")
        )
        conn.execute(
            text("ALTER TABLE clubs ADD COLUMN IF NOT EXISTS logo_storage_key TEXT")
        )
        conn.execute(
            text("ALTER TABLE minutes ADD COLUMN IF NOT EXISTS body TEXT DEFAULT ''")
        )
        # Postgres doesn't index FK columns automatically; these back the
        # hottest per-club/per-meeting filters. Names match what create_all
        # gives fresh databases from the models' index=True.
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_members_club_id ON members (club_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_events_club_id ON events (club_id)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS ix_projects_club_id ON projects (club_id)"))
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_check_ins_meeting_id ON check_ins (meeting_id)")
        )
    seed_bootstrap_data()

    with SessionLocal() as db:
        migrate_legacy_photos(db)
        backfill_gallery_thumbs(db)

    # Exactly ONE worker process may run the scheduler. uvicorn runs this
    # startup hook once per worker; before this lock existed every worker
    # started its own in-memory scheduler with all the jobs below, so every
    # scheduled SMS/push (event reminders, post-meeting thank-yous, birthday
    # and dues sweeps) went out once per worker — members received the same
    # message up to --workers times. A Postgres advisory lock elects one
    # winner; the held connection keeps the lock for the process lifetime.
    if not _try_acquire_scheduler_lock():
        logger.info("Another worker holds the scheduler lock — not scheduling here")
        return

    # Birthday SMS: sent at 7am Africa/Kampala (UTC+3, fixed, no DST) ->
    # 04:00 UTC. Plus one run right now at startup — the free-tier dyno
    # sleeps when idle, so "right now" is what actually catches most
    # birthdays (whenever someone next wakes it up). wish_if_due()'s
    # idempotency (see birthdays.py) makes re-running safe.
    scheduler.add_job(_run_birthday_sweep_job, "cron", hour=4, minute=0, id="birthday_sweep", replace_existing=True)
    # Thank-you sweep: guests are thanked 2 hours after check-in, so this
    # just needs to run often enough that nobody waits much past that.
    scheduler.add_job(_run_thank_you_sweep_job, "interval", minutes=15, id="thank_you_sweep", replace_existing=True)
    # Dues reminder: once a week (Monday 8am Africa/Kampala -> 05:00 UTC)
    # is plenty — last_dues_reminded already makes repeat runs a no-op for
    # anyone already reminded this period.
    scheduler.add_job(
        _run_dues_reminder_sweep_job, "cron",
        day_of_week="mon", hour=5, minute=0,
        id="dues_reminder_sweep", replace_existing=True,
    )
    # Error log retention: keeps the table from growing unbounded — old
    # entries have no diagnostic value once the deploy that produced them
    # is long gone.
    scheduler.add_job(
        _run_error_log_cleanup_job, "cron",
        hour=3, minute=15, id="error_log_cleanup", replace_existing=True,
    )
    # Rotary-year leadership handover: idempotent per club (see
    # leadership_transition.py), so a daily run is enough to catch July 1
    # even across a deploy/restart gap.
    scheduler.add_job(
        _run_leadership_transition_job, "cron",
        hour=4, minute=30, id="leadership_transition", replace_existing=True,
    )
    scheduler.start()
    _run_birthday_sweep_job()
    _run_thank_you_sweep_job()
    _run_dues_reminder_sweep_job()
    _run_leadership_transition_job()

    with SessionLocal() as db:
        reschedule_all_event_announcements(db)

    # Only THIS worker's scheduler actually runs — but create/update-event
    # requests land on any worker, whose (dormant) scheduler silently
    # swallows the add_job. Re-syncing all events' jobs here every 10
    # minutes picks those changes up; replace_existing makes it idempotent,
    # and jobs for since-deleted events no-op on their own (the send
    # functions re-read the event and bail if it's gone).
    def _run_event_resync_job() -> None:
        with SessionLocal() as sync_db:
            reschedule_all_event_announcements(sync_db)

    scheduler.add_job(
        _run_event_resync_job, "interval",
        minutes=10, id="event_job_resync", replace_existing=True,
    )


@app.get("/health")
def health():
    return {"status": "ok"}
