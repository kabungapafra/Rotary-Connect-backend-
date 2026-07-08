from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from .database import Base, engine
from .routers import (
    admin_analytics,
    admin_auth,
    admin_clubs,
    admin_members,
    auth,
    checkin,
    club_members,
)
from .seed import seed_bootstrap_data

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
app.include_router(admin_auth.router)
app.include_router(admin_clubs.router)
app.include_router(admin_members.router)
app.include_router(admin_analytics.router)


@app.on_event("startup")
def on_startup() -> None:
    # No Alembic yet — create_all is enough for the current MVP stage, plus
    # idempotent ALTERs for columns added after a deployment's tables existed
    # (create_all never alters existing tables).
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE clubs ADD COLUMN IF NOT EXISTS logo TEXT"))
    seed_bootstrap_data()


@app.get("/health")
def health():
    return {"status": "ok"}
