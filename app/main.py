import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .birthdays import run_daily_sweep
from .database import Base, SessionLocal, engine
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
)
from .scheduler import scheduler
from .seed import seed_bootstrap_data
from .storage import migrate_legacy_photos
from .thank_you import send_pending_thank_yous

logger = logging.getLogger("rotary.main")

app = FastAPI(title="Rotary Connect API")

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


@app.on_event("startup")
def on_startup() -> None:
    # No Alembic yet — create_all is enough for the current MVP stage, plus
    # idempotent ALTERs for columns added after a deployment's tables existed
    # (create_all never alters existing tables).
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE clubs ADD COLUMN IF NOT EXISTS logo TEXT"))
        conn.execute(
            text("ALTER TABLE members ADD COLUMN IF NOT EXISTS last_birthday_wished DATE")
        )
        conn.execute(
            text("ALTER TABLE guest_visits ADD COLUMN IF NOT EXISTS thanked_at TIMESTAMPTZ")
        )
        conn.execute(
            text("ALTER TABLE gallery_photos ADD COLUMN IF NOT EXISTS storage_key TEXT")
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
    scheduler.start()
    _run_birthday_sweep_job()
    _run_thank_you_sweep_job()

    with SessionLocal() as db:
        reschedule_all_event_announcements(db)


@app.get("/health")
def health():
    return {"status": "ok"}
