"""Endpoints for the public marketing website (rotaryconnect.digiflecttech.dev).

Split from the admin routers because the POST here is the only write path
in the API that is open to the whole internet — it is unauthenticated by
necessity (a club filling the form has no account yet), so it is rate
limited, size capped, and lands in its own triage table rather than
touching clubs/members. See models.JoinRequest for why it isn't a Club.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..rate_limit import rate_limit_ok
from ..security import get_current_admin
from ..sms import normalize_ugandan_phone

# Generous next to the login limiter — a club officer legitimately filling
# this in once is the norm, and the cost of a false 429 is a lost lead.
_JOIN_MAX_PER_WINDOW = 5
_JOIN_WINDOW_SECONDS = 3600

# A 512x512 PNG data URL runs well under this; anything larger is either a
# camera original or someone probing the endpoint. Rejected rather than
# silently truncated so the submitter can be told to use a smaller file.
_MAX_LOGO_CHARS = 800_000

_STATUSES = ("new", "contacted", "approved", "declined")

router = APIRouter(prefix="/site", tags=["site"])

admin_router = APIRouter(
    prefix="/admin/join-requests",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)


@router.post("/join-requests", response_model=schemas.JoinRequestOut, status_code=201)
def create_join_request(
    payload: schemas.JoinRequestCreate, request: Request, db: Session = Depends(get_db)
):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limit_ok(
        db, f"join_request:{client_ip}", _JOIN_MAX_PER_WINDOW, _JOIN_WINDOW_SECONDS
    ):
        raise HTTPException(status_code=429, detail="Too many requests — try again later")

    club_name = payload.club_name.strip()[:160]
    if not club_name:
        raise HTTPException(status_code=422, detail="Club name is required")

    phone = normalize_ugandan_phone(payload.phone)
    if phone is None:
        raise HTTPException(status_code=422, detail="Enter a valid phone number")

    if payload.logo is not None and len(payload.logo) > _MAX_LOGO_CHARS:
        raise HTTPException(status_code=422, detail="Logo image is too large — use one under 500KB")

    club_type = payload.club_type.strip().lower()
    if club_type not in ("rotary", "rotaract"):
        club_type = "rotary"

    join_request = models.JoinRequest(
        club_name=club_name,
        club_type=club_type,
        district=payload.district.strip()[:20],
        location=payload.location.strip()[:160],
        charter_date=payload.charter_date,
        # Clamped rather than rejected: a wrong headcount shouldn't cost
        # the lead, and it's only ever shown to the admin as a rough size.
        members_count=max(0, min(payload.members_count, 100_000)),
        logo=payload.logo,
        contact_name=payload.contact_name.strip()[:120],
        contact_role=payload.contact_role.strip()[:80],
        phone=phone,
        email=payload.email.strip()[:160],
        dob=payload.dob.strip()[:20],
        heard_about=payload.heard_about.strip()[:80],
        problems=", ".join(p.strip() for p in payload.problems if p.strip())[:500],
        notes=payload.notes.strip()[:2000],
    )
    db.add(join_request)
    db.commit()
    db.refresh(join_request)
    return join_request


@admin_router.get("", response_model=list[schemas.JoinRequestOut])
def list_join_requests(status_filter: str = "all", db: Session = Depends(get_db)):
    query = db.query(models.JoinRequest)
    if status_filter != "all":
        query = query.filter(models.JoinRequest.status == status_filter)
    return query.order_by(models.JoinRequest.created_at.desc()).all()


def _get_or_404(db: Session, request_id: int) -> models.JoinRequest:
    join_request = db.get(models.JoinRequest, request_id)
    if join_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Join request not found"
        )
    return join_request


@admin_router.patch("/{request_id}/status", response_model=schemas.JoinRequestOut)
def set_join_request_status(
    request_id: int, payload: schemas.JoinRequestStatusUpdate, db: Session = Depends(get_db)
):
    join_request = _get_or_404(db, request_id)
    if payload.status not in _STATUSES:
        raise HTTPException(status_code=422, detail=f"status must be one of {_STATUSES}")
    join_request.status = payload.status
    db.commit()
    db.refresh(join_request)
    return join_request


@admin_router.delete("/{request_id}")
def delete_join_request(request_id: int, db: Session = Depends(get_db)):
    """No FK cleanup needed here, unlike delete_club/delete_member — nothing
    references join_requests."""
    db.delete(_get_or_404(db, request_id))
    db.commit()
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Site content (events / news / projects)
#
# Written out per type rather than through a CRUD factory: they read the
# same today but already differ in how the public endpoint orders and
# filters them, and the rest of this codebase keeps its routes explicit.
# ---------------------------------------------------------------------------

content_admin_router = APIRouter(
    prefix="/admin/site",
    tags=["admin"],
    dependencies=[Depends(get_current_admin)],
)


def _fetch_or_404(db: Session, model, row_id: int, label: str):
    row = db.get(model, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"{label} not found")
    return row


# --- Events ---------------------------------------------------------------


@router.get("/events", response_model=list[schemas.SiteEventOut])
def public_events(db: Session = Depends(get_db)):
    """Upcoming published events only. Past events fall off the site on
    their own so the admin never has to prune the list by hand."""
    return (
        db.query(models.SiteEvent)
        .filter(
            models.SiteEvent.published.is_(True),
            models.SiteEvent.event_date >= date.today(),
        )
        .order_by(models.SiteEvent.event_date)
        .all()
    )


@content_admin_router.get("/events", response_model=list[schemas.SiteEventOut])
def list_events(db: Session = Depends(get_db)):
    """Everything, including unpublished and past — the admin needs to see
    what the public endpoint is hiding."""
    return db.query(models.SiteEvent).order_by(models.SiteEvent.event_date.desc()).all()


@content_admin_router.post("/events", response_model=schemas.SiteEventOut, status_code=201)
def create_event(payload: schemas.SiteEventIn, db: Session = Depends(get_db)):
    row = models.SiteEvent(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.put("/events/{event_id}", response_model=schemas.SiteEventOut)
def update_event(event_id: int, payload: schemas.SiteEventIn, db: Session = Depends(get_db)):
    row = _fetch_or_404(db, models.SiteEvent, event_id, "Event")
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.delete("/events/{event_id}")
def delete_event(event_id: int, db: Session = Depends(get_db)):
    db.delete(_fetch_or_404(db, models.SiteEvent, event_id, "Event"))
    db.commit()
    return {"deleted": True}


# --- News -----------------------------------------------------------------


@router.get("/news", response_model=list[schemas.SiteNewsOut])
def public_news(db: Session = Depends(get_db)):
    """Newest first. Unlike events, old news stays up — that's the point."""
    return (
        db.query(models.SiteNews)
        .filter(models.SiteNews.published.is_(True))
        .order_by(models.SiteNews.published_on.desc())
        .all()
    )


@content_admin_router.get("/news", response_model=list[schemas.SiteNewsOut])
def list_news(db: Session = Depends(get_db)):
    return db.query(models.SiteNews).order_by(models.SiteNews.published_on.desc()).all()


@content_admin_router.post("/news", response_model=schemas.SiteNewsOut, status_code=201)
def create_news(payload: schemas.SiteNewsIn, db: Session = Depends(get_db)):
    row = models.SiteNews(**payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.put("/news/{news_id}", response_model=schemas.SiteNewsOut)
def update_news(news_id: int, payload: schemas.SiteNewsIn, db: Session = Depends(get_db)):
    row = _fetch_or_404(db, models.SiteNews, news_id, "News item")
    for field, value in payload.model_dump().items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.delete("/news/{news_id}")
def delete_news(news_id: int, db: Session = Depends(get_db)):
    db.delete(_fetch_or_404(db, models.SiteNews, news_id, "News item"))
    db.commit()
    return {"deleted": True}


# --- Projects -------------------------------------------------------------


@router.get("/projects", response_model=list[schemas.SiteProjectOut])
def public_projects(db: Session = Depends(get_db)):
    """Ordered by id, not date: this is a curated showcase row, so the
    order the admin created them in is the order they should appear."""
    return (
        db.query(models.SiteProject)
        .filter(models.SiteProject.published.is_(True))
        .order_by(models.SiteProject.id)
        .all()
    )


@content_admin_router.get("/projects", response_model=list[schemas.SiteProjectOut])
def list_projects(db: Session = Depends(get_db)):
    return db.query(models.SiteProject).order_by(models.SiteProject.id).all()


@content_admin_router.post("/projects", response_model=schemas.SiteProjectOut, status_code=201)
def create_project(payload: schemas.SiteProjectIn, db: Session = Depends(get_db)):
    row = models.SiteProject(**_clean_project(payload))
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.put("/projects/{project_id}", response_model=schemas.SiteProjectOut)
def update_project(
    project_id: int, payload: schemas.SiteProjectIn, db: Session = Depends(get_db)
):
    row = _fetch_or_404(db, models.SiteProject, project_id, "Project")
    for field, value in _clean_project(payload).items():
        setattr(row, field, value)
    db.commit()
    db.refresh(row)
    return row


@content_admin_router.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db.delete(_fetch_or_404(db, models.SiteProject, project_id, "Project"))
    db.commit()
    return {"deleted": True}


def _clean_project(payload: schemas.SiteProjectIn) -> dict:
    """The site draws a progress bar from this — a percentage outside 0-100
    would render as an overflowing or negative-width bar."""
    data = payload.model_dump()
    data["progress_percent"] = max(0, min(data["progress_percent"], 100))
    return data
