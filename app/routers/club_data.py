from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session, joinedload

from .. import models, schemas
from ..database import get_db
from ..event_announcements import (
    CHECKIN_LEAD_MINUTES,
    checkin_window_utc,
    is_registration_open,
    next_occurrence_utc,
    parse_event_time,
    rsvp_target_date,
    schedule_event_announcement,
    unschedule_event_announcement,
    venue_from_meta,
)
from ..push import send_bulk_push, tokens_for_club
from ..security import get_current_member
from ..storage import delete_gallery_image, upload_gallery_image
from ..utils import compute_week_streak, get_or_create_meeting, is_club_access_blocked
from .club_members import MANAGER_ROLES

_REMOVE_IMAGE = "__remove__"


def _apply_r2_image(obj: "models.Event | models.Project", image: str | None, prefix: str) -> None:
    """Upload a new "data:image/...;base64,..." photo, clear it on the
    `__remove__` sentinel, or leave it untouched when omitted — replacing
    the old R2 object (if any) either way an image changes. Shared by
    events (banner) and projects (photo), same storage approach as the
    gallery."""
    if image is None:
        return
    if obj.storage_key:
        delete_gallery_image(obj.storage_key)
    if image == _REMOVE_IMAGE:
        obj.image = None
        obj.storage_key = None
        return
    try:
        url, key = upload_gallery_image(image, obj.club_id, prefix=prefix)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    obj.image = url
    obj.storage_key = key

router = APIRouter(prefix="/club", tags=["club"])


def _require_manager(member: models.Member) -> None:
    if member.role not in MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club President or Secretary can manage this",
        )


# ── events ──────────────────────────────────────────────────────────────

@router.get("/events", response_model=list[schemas.EventOut])
def list_events(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    events = (
        db.query(models.Event)
        .filter(models.Event.club_id == member.club_id)
        .order_by(models.Event.id)
        .all()
    )
    return [
        schemas.EventOut(
            id=e.id,
            dow=e.dow,
            name=e.name,
            meta=e.meta,
            image=e.image,
            registration_open=is_registration_open(e.dow, e.meta),
        )
        for e in events
    ]


@router.get("/events/next", response_model=schemas.NextMeetingOut)
def next_meeting(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """The soonest upcoming occurrence across all of the club's weekly
    events — real date/time/venue computed from each event's day-of-week
    and parsed time, not a static placeholder. Once today's/this-week's
    occurrence has passed, `next_occurrence_utc` naturally rolls to next
    week, so this always reflects what's actually next.

    Exception: while an event's check-in window is still open (same window
    `checkin.py` gates check-in on — opens 15 min before start, closes 1
    hour after), that event is still "the next meeting" and is returned
    with `ongoing=True`, instead of `next_occurrence_utc` already having
    rolled it to next week."""
    events = db.query(models.Event).filter(models.Event.club_id == member.club_id).all()
    if not events:
        raise HTTPException(status_code=404, detail="No events scheduled for this club yet")

    now = datetime.now(timezone.utc)
    today = now.date()
    todays_dow = today.strftime("%a").upper()

    ongoing_event = None
    ongoing_dt = None
    for event in events:
        if event.dow != todays_dow:
            continue
        parsed = parse_event_time(event.meta)
        if parsed is None:
            continue
        opens_at, closes_at = checkin_window_utc(*parsed, today)
        if opens_at <= now <= closes_at:
            start = opens_at + timedelta(minutes=CHECKIN_LEAD_MINUTES)
            if ongoing_dt is None or start < ongoing_dt:
                ongoing_dt, ongoing_event = start, event

    if ongoing_event is not None:
        best_event, best_dt, ongoing = ongoing_event, ongoing_dt, True
    else:
        best_event = None
        best_dt = None
        for event in events:
            parsed = parse_event_time(event.meta)
            hour, minute = parsed if parsed else (12, 0)
            next_dt = next_occurrence_utc(event.dow, hour, minute, now=now)
            if best_dt is None or next_dt < best_dt:
                best_dt, best_event = next_dt, event
        ongoing = False

    local_dt = best_dt + timedelta(hours=3)  # Africa/Kampala, fixed UTC+3
    parsed = parse_event_time(best_event.meta)
    time_label = f"{local_dt.strftime('%I:%M %p').lstrip('0')}" if parsed else ""
    return schemas.NextMeetingOut(
        event_id=best_event.id,
        name=best_event.name,
        venue=venue_from_meta(best_event.meta),
        time_label=time_label,
        date_iso=local_dt.date().isoformat(),
        ongoing=ongoing,
    )


@router.post("/events", response_model=schemas.EventOut)
def create_event(
    payload: schemas.EventCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
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
    _apply_r2_image(event, payload.image, prefix="events")
    db.commit()
    # Schedule the recurring "4 hours before" reminder + "1 hour after"
    # thank-you SMS. If the TIME & VENUE text has no parseable clock time,
    # neither offset can be computed, so no reminder is scheduled at all
    # (never an immediate announcement as a fallback).
    schedule_event_announcement(event)
    # Push, unlike the SMS reminder pair above, fires once immediately —
    # members get an in-app nudge that a new fellowship was posted, on top
    # of (not instead of) the recurring pre-meeting reminder.
    send_bulk_push(
        tokens_for_club(db, event.club_id),
        "New event posted",
        f"{event.name}" + (f" — {event.meta.strip()}" if event.meta.strip() else ""),
        data={"type": "event", "event_id": str(event.id)},
    )
    return event


@router.patch("/events/{event_id}", response_model=schemas.EventOut)
def update_event(
    event_id: int,
    payload: schemas.EventCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    event.dow = payload.dow.strip().upper()[:3] or event.dow
    event.name = payload.name.strip() or event.name
    event.meta = payload.meta.strip()
    _apply_r2_image(event, payload.image, prefix="events")
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
    _require_manager(member)
    event = db.get(models.Event, event_id)
    if event is None or event.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Event not found")
    unschedule_event_announcement(event.id)
    if event.storage_key:
        delete_gallery_image(event.storage_key)
    # Web RSVPs hold a non-nullable FK into events — clear them first or
    # the delete below trips the constraint (same manual FK enumeration as
    # the admin delete endpoints).
    db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event.id).delete(
        synchronize_session=False
    )
    db.delete(event)
    db.commit()
    return {"deleted": True}


# ── projects ────────────────────────────────────────────────────────────

def _normalize_area_of_focus(value: str | None) -> str | None:
    """None/blank stays "Uncategorized" in reports rather than guessing;
    anything not in Rotary's 7 official areas is rejected outright so the
    report's per-area breakdown never silently drops a project into the
    wrong bucket."""
    if value is None or not value.strip():
        return None
    if value not in schemas.ROTARY_AREAS_OF_FOCUS:
        raise HTTPException(status_code=422, detail="Not a recognized area of focus")
    return value


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    projects = (
        db.query(models.Project)
        .filter(models.Project.club_id == member.club_id)
        .order_by(models.Project.id)
        .all()
    )
    return [_project_out(p, _updates_for(db, p.id)) for p in projects]


def _updates_for(db: Session, project_id: int) -> list[schemas.ProjectUpdateOut]:
    rows = (
        db.query(models.ProjectUpdate)
        .filter(models.ProjectUpdate.project_id == project_id)
        .options(joinedload(models.ProjectUpdate.author))
        .order_by(models.ProjectUpdate.created_at.desc())
        .all()
    )
    return [
        schemas.ProjectUpdateOut(
            id=r.id,
            pct=r.pct,
            note=r.note,
            author_name=r.author.name,
            created_at=r.created_at,
        )
        for r in rows
    ]


def _project_out(
    project: models.Project, updates: list[schemas.ProjectUpdateOut]
) -> schemas.ProjectOut:
    out = schemas.ProjectOut.model_validate(project)
    out.updates = updates
    return out


@router.post("/projects", response_model=schemas.ProjectOut)
def create_project(
    payload: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
    if not payload.name.strip():
        raise HTTPException(status_code=422, detail="Project name is required")
    project = models.Project(
        club_id=member.club_id,
        name=payload.name.strip(),
        area=payload.area.strip(),
        pct=max(0, min(100, payload.pct)),
        desc=payload.desc.strip(),
        deadline=payload.deadline.strip(),
        area_of_focus=_normalize_area_of_focus(payload.area_of_focus),
        beneficiaries_reached=max(0, payload.beneficiaries_reached),
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    _apply_r2_image(project, payload.image, prefix="projects")
    db.commit()
    return _project_out(project, [])


@router.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(
    project_id: int,
    payload: schemas.ProjectCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
    project = db.get(models.Project, project_id)
    if project is None or project.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Project not found")
    project.name = payload.name.strip() or project.name
    project.area = payload.area.strip()
    project.pct = max(0, min(100, payload.pct))
    project.desc = payload.desc.strip()
    project.deadline = payload.deadline.strip()
    project.area_of_focus = _normalize_area_of_focus(payload.area_of_focus)
    project.beneficiaries_reached = max(0, payload.beneficiaries_reached)
    _apply_r2_image(project, payload.image, prefix="projects")
    db.commit()
    db.refresh(project)
    return _project_out(project, _updates_for(db, project.id))


@router.post("/projects/{project_id}/updates", response_model=schemas.ProjectOut)
def add_project_update(
    project_id: int,
    payload: schemas.ProjectUpdateCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """Log a progress update — what's been done and the project's current
    completion %. The lightweight follow-up flow, separate from editing
    the project's core details (name, area, deadline, ...)."""
    _require_manager(member)
    project = db.get(models.Project, project_id)
    if project is None or project.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Project not found")
    pct = max(0, min(100, payload.pct))
    db.add(
        models.ProjectUpdate(
            project_id=project.id,
            pct=pct,
            note=payload.note.strip(),
            created_by=member.id,
        )
    )
    project.pct = pct
    db.commit()
    db.refresh(project)
    return _project_out(project, _updates_for(db, project.id))


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_manager(member)
    project = db.get(models.Project, project_id)
    if project is None or project.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.storage_key:
        delete_gallery_image(project.storage_key)
    db.query(models.ProjectUpdate).filter(
        models.ProjectUpdate.project_id == project.id
    ).delete(synchronize_session=False)
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
    # One query for every check-in across all 20 meetings (instead of one
    # query per meeting), with the member eager-loaded via a JOIN so
    # `r.member.name` below doesn't trigger a lazy-load per row either —
    # collapses what used to be hundreds of queries into a single one.
    meeting_ids = [m.id for m in meetings]
    checkins_by_meeting: dict[int, list[models.CheckIn]] = defaultdict(list)
    if meeting_ids:
        rows = (
            db.query(models.CheckIn)
            .filter(models.CheckIn.meeting_id.in_(meeting_ids))
            .options(joinedload(models.CheckIn.member))
            .order_by(models.CheckIn.checked_in_at)
            .all()
        )
        for row in rows:
            checkins_by_meeting[row.meeting_id].append(row)

    # Non-members on the same register: walk-in guests who scanned the club
    # QR that day, and people who registered ahead of time through an
    # event's public web RSVP form. An RSVP names an event (a weekly dow),
    # not a date — it targets the first occurrence of that dow on/after the
    # day it was made, so each RSVP lands on exactly one meeting date.
    meeting_dates = {m.date for m in meetings}
    guests_by_date: dict[date, list[schemas.MeetingGuest]] = defaultdict(list)
    if meeting_dates:
        visits = (
            db.query(models.GuestVisit)
            .filter(
                models.GuestVisit.club_id == member.club_id,
                models.GuestVisit.visit_date.in_(meeting_dates),
            )
            .order_by(models.GuestVisit.created_at)
            .all()
        )
        for v in visits:
            guests_by_date[v.visit_date].append(
                schemas.MeetingGuest(
                    name=v.name,
                    type=v.guest_type or "Guest",
                    club_name=v.member_club,
                    time=v.created_at.strftime("%H:%M") if v.created_at else "",
                    via="scan",
                )
            )
        club_events = {
            e.id: e
            for e in db.query(models.Event).filter(models.Event.club_id == member.club_id)
        }
        if club_events:
            rsvps = (
                db.query(models.EventRsvp)
                .filter(models.EventRsvp.event_id.in_(club_events.keys()))
                .order_by(models.EventRsvp.created_at)
                .all()
            )
            for r in rsvps:
                target = rsvp_target_date(club_events[r.event_id].dow, r.created_at.date())
                if target in meeting_dates:
                    guests_by_date[target].append(
                        schemas.MeetingGuest(
                            name=r.name,
                            type=r.attendee_type or "Guest",
                            club_name=r.club_name,
                            time=r.created_at.strftime("%H:%M"),
                            via="web",
                        )
                    )

    out = []
    for m in meetings:
        rows = checkins_by_meeting.get(m.id, [])
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
                guests=guests_by_date.get(m.date, []),
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
    checked_in_today = today_meeting is not None and (
        db.query(models.CheckIn)
        .filter(
            models.CheckIn.member_id == member.id,
            models.CheckIn.meeting_id == today_meeting.id,
        )
        .first()
        is not None
    )
    return schemas.MemberSummaryOut(
        check_in_count=check_in_count,
        meetings_total=meetings_total,
        attendance_percent=min(100, attendance),
        today_meeting_name=today_meeting.name if today_meeting else "Weekly Fellowship Meeting",
        member_count=member_count,
        club_status="suspended" if is_club_access_blocked(member.club) else "active",
        checked_in_today=checked_in_today,
        week_streak=compute_week_streak(db, member),
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
    # "Can't attend" makes no sense from someone who already attended — a
    # checked-in member sending an apology would put them on both sides of
    # the register at once.
    already_in = (
        db.query(models.CheckIn)
        .filter(
            models.CheckIn.member_id == member.id,
            models.CheckIn.meeting_id == meeting.id,
        )
        .first()
    )
    if already_in:
        raise HTTPException(
            status_code=422,
            detail="You're already checked in for this meeting — no apology needed.",
        )
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
