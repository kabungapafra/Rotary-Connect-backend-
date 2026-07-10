from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import SessionLocal, get_db
from ..event_announcements import (
    next_occurrence_utc,
    parse_event_time,
    schedule_event_announcement,
    unschedule_event_announcement,
    venue_from_meta,
)
from ..security import get_current_member
from ..sms import send_bulk_sms
from ..utils import get_or_create_meeting
from .club_members import PRESIDENT_ROLES

router = APIRouter(prefix="/club", tags=["club"])


def _announce_new_event(club_id: int, event_name: str, event_meta: str) -> None:
    """Runs as a background task with its own DB session — the request's
    session may already be torn down by the time this executes."""
    db = SessionLocal()
    try:
        club = db.get(models.Club, club_id)
        if club is None:
            return
        phones = [
            m.phone
            for m in db.query(models.Member).filter(models.Member.club_id == club_id)
            if m.phone
        ]
        text = f"📅 New fellowship: {event_name}"
        if event_meta.strip():
            text += f" — {event_meta.strip()}"
        text += f". See you there! — {club.name}"
        send_bulk_sms(phones, text)
    finally:
        db.close()


def _require_president(member: models.Member) -> None:
    if member.role not in PRESIDENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club President can manage this",
        )


# ── events ──────────────────────────────────────────────────────────────

@router.get("/events", response_model=list[schemas.EventOut])
def list_events(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    return (
        db.query(models.Event)
        .filter(models.Event.club_id == member.club_id)
        .order_by(models.Event.id)
        .all()
    )


@router.get("/events/next", response_model=schemas.NextMeetingOut)
def next_meeting(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """The soonest upcoming occurrence across all of the club's weekly
    events — real date/time/venue computed from each event's day-of-week
    and parsed time, not a static placeholder. Once today's/this-week's
    occurrence has passed, `next_occurrence_utc` naturally rolls to next
    week, so this always reflects what's actually next."""
    events = db.query(models.Event).filter(models.Event.club_id == member.club_id).all()
    if not events:
        raise HTTPException(status_code=404, detail="No events scheduled for this club yet")

    best_event = None
    best_dt = None
    for event in events:
        parsed = parse_event_time(event.meta)
        hour, minute = parsed if parsed else (12, 0)
        next_dt = next_occurrence_utc(event.dow, hour, minute)
        if best_dt is None or next_dt < best_dt:
            best_dt, best_event = next_dt, event

    local_dt = best_dt + timedelta(hours=3)  # Africa/Kampala, fixed UTC+3
    parsed = parse_event_time(best_event.meta)
    time_label = f"{local_dt.strftime('%I:%M %p').lstrip('0')}" if parsed else ""
    return schemas.NextMeetingOut(
        event_id=best_event.id,
        name=best_event.name,
        venue=venue_from_meta(best_event.meta),
        time_label=time_label,
        date_iso=local_dt.date().isoformat(),
    )


@router.post("/events", response_model=schemas.EventOut)
def create_event(
    payload: schemas.EventCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Event name is required")
    event = models.Event(
        club_id=member.club_id,
        dow=payload.dow.strip().upper()[:3] or "WED",
        name=payload.name.strip(),
        meta=payload.meta.strip(),
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    # Schedule the recurring "4 hours before" reminder. If the TIME &
    # VENUE text has no parseable clock time, we can't compute that — fall
    # back to one immediate announcement so the club still hears about it.
    if not schedule_event_announcement(event):
        background_tasks.add_task(_announce_new_event, member.club_id, event.name, event.meta)
    return event


@router.patch("/events/{event_id}", response_model=schemas.EventOut)
def update_event(
    event_id: int,
    payload: schemas.EventCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    event.dow = payload.dow.strip().upper()[:3] or event.dow
    event.name = payload.name.strip() or event.name
    event.meta = payload.meta.strip()
    db.commit()
    db.refresh(event)
    # Day/time may have changed — reschedule the recurring reminder.
    schedule_event_announcement(event)
    return event


@router.delete("/events/{event_id}")
def delete_event(
    event_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    unschedule_event_announcement(event.id)
    db.delete(event)
    db.commit()
    return {"deleted": True}


# ── projects ────────────────────────────────────────────────────────────

@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    return (
        db.query(models.Project)
        .filter(models.Project.club_id == member.club_id)
        .order_by(models.Project.id)
        .all()
    )


@router.post("/projects", response_model=schemas.ProjectOut)
def create_project(
    payload: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Project name is required")
    project = models.Project(
        club_id=member.club_id,
        name=payload.name.strip(),
        area=payload.area.strip(),
        pct=max(0, min(100, payload.pct)),
        desc=payload.desc.strip(),
        deadline=payload.deadline.strip(),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: int,
    payload: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    project = db.get(models.Project, project_id)
    if project is None or project.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Project not found")
    project.name = payload.name.strip() or project.name
    project.area = payload.area.strip()
    project.pct = max(0, min(100, payload.pct))
    project.desc = payload.desc.strip()
    project.deadline = payload.deadline.strip()
    db.commit()
    db.refresh(project)
    return project


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_president(member)
    project = db.get(models.Project, project_id)
    if project is None or project.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Project not found")
    db.delete(project)
    db.commit()
    return {"deleted": True}


# ── meetings history & member summary ───────────────────────────────────

@router.get("/meetings", response_model=list[schemas.MeetingOut])
def list_meetings(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    meetings = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == member.club_id)
        .order_by(models.Meeting.date.desc())
        .limit(20)
        .all()
    )
    out = []
    for m in meetings:
        rows = (
            db.query(models.CheckIn)
            .filter(models.CheckIn.meeting_id == m.id)
            .order_by(models.CheckIn.checked_in_at)
            .all()
        )
        out.append(
            schemas.MeetingOut(
                date=m.date.strftime("%d %b %Y"),
                name=m.name,
                checkin_count=len(rows),
                attended=any(r.member_id == member.id for r in rows),
                attendees=[
                    schemas.MeetingAttendee(
                        name=r.member.name,
                        role=r.member.role,
                        time=r.checked_in_at.strftime("%H:%M"),
                    )
                    for r in rows
                ],
            )
        )
    return out


@router.get("/me/summary", response_model=schemas.MemberSummaryOut)
def my_summary(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    meetings_total = (
        db.query(models.Meeting).filter(models.Meeting.club_id == member.club_id).count()
    )
    check_in_count = (
        db.query(models.CheckIn).filter(models.CheckIn.member_id == member.id).count()
    )
    attendance = round(check_in_count / meetings_total * 100) if meetings_total else 0
    today_meeting = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == member.club_id, models.Meeting.date == date.today())
        .first()
    )
    member_count = (
        db.query(models.Member).filter(models.Member.club_id == member.club_id).count()
    )
    return schemas.MemberSummaryOut(
        check_in_count=check_in_count,
        meetings_total=meetings_total,
        attendance_percent=min(100, attendance),
        today_meeting_name=today_meeting.name if today_meeting else "Weekly Fellowship Meeting",
        member_count=member_count,
    )


# ── apologies ───────────────────────────────────────────────────────────

@router.post("/apologies", response_model=schemas.ApologyOut)
def submit_apology(
    payload: schemas.ApologyCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """A member apologises for a meeting they'll miss — the app sends the
    upcoming fellowship's date; one apology per member per meeting date,
    same as a check-in."""
    on_date = None
    if payload.meeting_date:
        try:
            on_date = date.fromisoformat(payload.meeting_date)
        except ValueError:
            raise HTTPException(status_code=422, detail="meeting_date must be YYYY-MM-DD")
    meeting = get_or_create_meeting(db, member.club_id, on_date)
    existing = (
        db.query(models.Apology)
        .filter(models.Apology.member_id == member.id, models.Apology.meeting_date == meeting.date)
        .first()
    )
    if existing:
        existing.reason = payload.reason.strip()
        db.commit()
        db.refresh(existing)
        row = existing
    else:
        row = models.Apology(
            club_id=member.club_id,
            member_id=member.id,
            meeting_date=meeting.date,
            reason=payload.reason.strip(),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return schemas.ApologyOut(
        id=row.id,
        member_name=member.name,
        member_role=member.role,
        meeting_date=row.meeting_date.isoformat(),
        reason=row.reason,
        created_at=row.created_at,
    )


@router.get("/apologies", response_model=list[schemas.ApologyOut])
def list_apologies(
    meeting_date: date | None = None,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """Visible to any club member — the register screen decides who to
    show the tab to, same as it already does for the club register."""
    on_date = meeting_date or date.today()
    rows = (
        db.query(models.Apology)
        .filter(models.Apology.club_id == member.club_id, models.Apology.meeting_date == on_date)
        .order_by(models.Apology.created_at)
        .all()
    )
    return [
        schemas.ApologyOut(
            id=row.id,
            member_name=row.member.name,
            member_role=row.member.role,
            meeting_date=row.meeting_date.isoformat(),
            reason=row.reason,
            created_at=row.created_at,
        )
        for row in rows
    ]
