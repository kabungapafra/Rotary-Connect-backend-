from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member
from .club_members import PRESIDENT_ROLE

router = APIRouter(prefix="/club", tags=["club"])


def _require_president(member: models.Member) -> None:
    if member.role != PRESIDENT_ROLE:
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


@router.post("/events", response_model=schemas.EventOut)
def create_event(
    payload: schemas.EventCreate,
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
        count = db.query(models.CheckIn).filter(models.CheckIn.meeting_id == m.id).count()
        out.append(
            schemas.MeetingOut(
                date=m.date.strftime("%d %b %Y"), name=m.name, checkin_count=count
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
