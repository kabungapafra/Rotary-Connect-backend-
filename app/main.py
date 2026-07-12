import logging
import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .birthdays import run_daily_sweep
from .database import Base, SessionLocal, engine
from .dues_reminders import run_sweep as run_dues_reminder_sweep
from .event_announcements import reschedule_all_event_announcements
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
from .storage import migrate_legacy_photos
from .thank_you import send_pending_thank_yous

logger = logging.getLogger("rotary.main")

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


@app.on_event("startup")
def on_startup() -> None:
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
    scheduler.start()
    _run_birthday_sweep_job()
    _run_thank_you_sweep_job()
    _run_dues_reminder_sweep_job()

    with SessionLocal() as db:
        reschedule_all_event_announcements(db)


@app.get("/health")
def health():
    return {"status": "ok"}
