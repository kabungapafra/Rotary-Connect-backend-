import logging

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .birthdays import run_daily_sweep
from .database import Base, SessionLocal, engine
from .routers import (
    admin_analytics,
    admin_auth,
    admin_clubs,
    admin_members,
    auth,
    checkin,
    club_data,
    club_members,
)
from .seed import seed_bootstrap_data

logger = logging.getLogger("rotary.main")

app = FastAPI(title="Rotary Connect API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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


def _run_birthday_sweep_job() -> None:
    db = SessionLocal()
    try:
        run_daily_sweep(db)
    except Exception:
        logger.exception("Birthday sweep failed")
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
    seed_bootstrap_data()

    # Birthday SMS: a daily sweep at 08:00, plus one run right now at
    # startup — the free-tier dyno sleeps when idle, so "right now" is what
    # actually catches most birthdays (whenever someone next wakes it up).
    # wish_if_due()'s idempotency (see birthdays.py) makes re-running safe.
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(_run_birthday_sweep_job, "cron", hour=8, minute=0)
    scheduler.start()
    _run_birthday_sweep_job()


@app.get("/health")
def health():
    return {"status": "ok"}
