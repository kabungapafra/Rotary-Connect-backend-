from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import admin_analytics, admin_auth, admin_clubs, admin_members, auth, checkin
from .seed import seed_demo_data

app = FastAPI(title="Rotary Connect API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(checkin.router)
app.include_router(admin_auth.router)
app.include_router(admin_clubs.router)
app.include_router(admin_members.router)
app.include_router(admin_analytics.router)


@app.on_event("startup")
def on_startup() -> None:
    # No Alembic yet — create_all is enough for the current MVP stage.
    Base.metadata.create_all(bind=engine)
    seed_demo_data()


@app.get("/health")
def health():
    return {"status": "ok"}
