from datetime import date

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_member
from .club_members import PRESIDENT_ROLES
from .treasury import treasury_summary

router = APIRouter(prefix="/club/secretary", tags=["secretary"])


def _require_secretary(member: models.Member) -> None:
    """Strictly the Secretary — the workspace is theirs alone, the
    President doesn't share it (unlike most other privileged actions)."""
    if member.role != "Secretary":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Club Secretary can manage this",
        )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="meeting_date must be YYYY-MM-DD")


# ── minutes ────────────────────────────────────────────────────────────

@router.get("/minutes", response_model=list[schemas.MinuteOut])
def list_minutes(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    rows = (
        db.query(models.Minute)
        .filter(models.Minute.club_id == member.club_id)
        .order_by(models.Minute.meeting_date.desc())
        .all()
    )
    return [
        schemas.MinuteOut(
            id=m.id,
            title=m.title,
            meeting_date=m.meeting_date.isoformat(),
            status=m.status,
            created_at=m.created_at,
        )
        for m in rows
    ]


@router.post("/minutes", response_model=schemas.MinuteOut)
def create_minute(
    payload: schemas.MinuteCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Title is required")
    minute = models.Minute(
        club_id=member.club_id,
        title=payload.title.strip(),
        meeting_date=_parse_date(payload.meeting_date),
        status="draft",
        created_by=member.id,
    )
    db.add(minute)
    db.commit()
    db.refresh(minute)
    return schemas.MinuteOut(
        id=minute.id,
        title=minute.title,
        meeting_date=minute.meeting_date.isoformat(),
        status=minute.status,
        created_at=minute.created_at,
    )


@router.patch("/minutes/{minute_id}", response_model=schemas.MinuteOut)
def update_minute_status(
    minute_id: int,
    payload: schemas.MinuteStatusUpdate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    if payload.status not in ("draft", "approved"):
        raise HTTPException(status_code=422, detail="status must be draft or approved")
    minute = db.get(models.Minute, minute_id)
    if minute is None or minute.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Minute not found")
    minute.status = payload.status
    db.commit()
    db.refresh(minute)
    return schemas.MinuteOut(
        id=minute.id,
        title=minute.title,
        meeting_date=minute.meeting_date.isoformat(),
        status=minute.status,
        created_at=minute.created_at,
    )


# ── club history / milestones ────────────────────────────────────────────

@router.get("/milestones", response_model=list[schemas.MilestoneOut])
def list_milestones(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    rows = (
        db.query(models.Milestone)
        .filter(models.Milestone.club_id == member.club_id)
        .order_by(models.Milestone.year.desc(), models.Milestone.created_at.desc())
        .all()
    )
    return [
        schemas.MilestoneOut(
            id=m.id, year=m.year, title=m.title, category=m.category, text=m.text
        )
        for m in rows
    ]


@router.post("/milestones", response_model=schemas.MilestoneOut)
def create_milestone(
    payload: schemas.MilestoneCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    if not payload.title.strip() or not payload.year.strip():
        raise HTTPException(status_code=422, detail="Year and title are required")
    milestone = models.Milestone(
        club_id=member.club_id,
        year=payload.year.strip(),
        title=payload.title.strip(),
        category=payload.category.strip() or "Milestones",
        text=payload.text.strip(),
        created_by=member.id,
    )
    db.add(milestone)
    db.commit()
    db.refresh(milestone)
    return schemas.MilestoneOut(
        id=milestone.id,
        year=milestone.year,
        title=milestone.title,
        category=milestone.category,
        text=milestone.text,
    )


@router.delete("/milestones/{milestone_id}")
def delete_milestone(
    milestone_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    milestone = db.get(models.Milestone, milestone_id)
    if milestone is None or milestone.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Milestone not found")
    db.delete(milestone)
    db.commit()
    return {"deleted": True}


# ── reports (real data, computed on request) ─────────────────────────────

def _role_holder(db: Session, club_id: int, roles: set[str]) -> str:
    m = (
        db.query(models.Member)
        .filter(models.Member.club_id == club_id, models.Member.role.in_(roles))
        .first()
    )
    return f"Rtn. {m.name}" if m else "Not assigned"


@router.get("/monthly-report", response_model=schemas.ReportOut)
def monthly_report(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    club = db.get(models.Club, member.club_id)
    today = date.today()
    month_start = today.replace(day=1)

    member_count = (
        db.query(models.Member).filter(models.Member.club_id == member.club_id).count()
    )
    board_count = (
        db.query(models.Member)
        .filter(models.Member.club_id == member.club_id, models.Member.is_board.is_(True))
        .count()
    )
    meetings_this_month = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == member.club_id, models.Meeting.date >= month_start)
        .all()
    )
    meeting_ids = [m.id for m in meetings_this_month]
    checkins_this_month = (
        db.query(models.CheckIn).filter(models.CheckIn.meeting_id.in_(meeting_ids)).count()
        if meeting_ids
        else 0
    )
    avg_attendance = (
        round(checkins_this_month / (len(meetings_this_month) * member_count) * 100)
        if meetings_this_month and member_count
        else 0
    )
    projects = (
        db.query(models.Project).filter(models.Project.club_id == member.club_id).all()
    )
    completed_projects = sum(1 for p in projects if p.pct >= 100)

    treasury = treasury_summary(db=db, member=member)

    return schemas.ReportOut(
        title=f"Monthly Report — {today.strftime('%B %Y')}",
        subtitle=f"{club.name if club else 'Club'} · Prepared by the Club Secretary",
        sections=[
            schemas.ReportSection(
                section="Club information",
                rows=[
                    schemas.ReportRow(label="Club name", value=club.name if club else ""),
                    schemas.ReportRow(
                        label="President",
                        value=_role_holder(db, member.club_id, PRESIDENT_ROLES),
                    ),
                    schemas.ReportRow(
                        label="Secretary", value=_role_holder(db, member.club_id, {"Secretary"})
                    ),
                    schemas.ReportRow(
                        label="Treasurer", value=_role_holder(db, member.club_id, {"Treasurer"})
                    ),
                ],
            ),
            schemas.ReportSection(
                section="Membership",
                rows=[
                    schemas.ReportRow(label="Current membership", value=str(member_count)),
                    schemas.ReportRow(label="Board & officers", value=str(board_count)),
                ],
            ),
            schemas.ReportSection(
                section="Attendance",
                rows=[
                    schemas.ReportRow(
                        label="Meetings held this month", value=str(len(meetings_this_month))
                    ),
                    schemas.ReportRow(label="Total check-ins", value=str(checkins_this_month)),
                    schemas.ReportRow(
                        label="Average attendance", value=f"{avg_attendance}%"
                    ),
                ],
            ),
            schemas.ReportSection(
                section="Projects",
                rows=[
                    schemas.ReportRow(label="Total projects", value=str(len(projects))),
                    schemas.ReportRow(label="Completed", value=str(completed_projects)),
                ],
            ),
            schemas.ReportSection(
                section="Treasury",
                rows=[
                    schemas.ReportRow(
                        label=f"Dues collected ({treasury.dues_period_label})",
                        value=f"UGX {treasury.dues_collected:,}",
                    ),
                    schemas.ReportRow(
                        label="Outstanding dues", value=f"UGX {treasury.dues_outstanding:,}"
                    ),
                    schemas.ReportRow(
                        label="Total income", value=f"UGX {treasury.total_income:,}"
                    ),
                    schemas.ReportRow(
                        label="Total expenses", value=f"UGX {treasury.total_expenses:,}"
                    ),
                ],
            ),
        ],
    )


@router.get("/annual-report", response_model=schemas.ReportOut)
def annual_report(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    club = db.get(models.Club, member.club_id)
    today = date.today()
    year_start = today.replace(month=1, day=1)

    member_count = (
        db.query(models.Member).filter(models.Member.club_id == member.club_id).count()
    )
    meetings_this_year = (
        db.query(models.Meeting)
        .filter(models.Meeting.club_id == member.club_id, models.Meeting.date >= year_start)
        .all()
    )
    meeting_ids = [m.id for m in meetings_this_year]
    checkins_this_year = (
        db.query(models.CheckIn).filter(models.CheckIn.meeting_id.in_(meeting_ids)).count()
        if meeting_ids
        else 0
    )
    avg_attendance = (
        round(checkins_this_year / (len(meetings_this_year) * member_count) * 100)
        if meetings_this_year and member_count
        else 0
    )
    projects = (
        db.query(models.Project).filter(models.Project.club_id == member.club_id).all()
    )
    completed_projects = sum(1 for p in projects if p.pct >= 100)
    year_tx = (
        db.query(models.Transaction)
        .filter(models.Transaction.club_id == member.club_id)
        .filter(models.Transaction.created_at >= year_start)
        .all()
    )
    income = sum(t.amount for t in year_tx if t.kind == "income")
    expenses = sum(t.amount for t in year_tx if t.kind == "expense")

    return schemas.ReportOut(
        title=f"Rotary Club Annual Report {today.year}",
        subtitle=f"{club.name if club else 'Club'} · Prepared by the Club Secretary",
        sections=[
            schemas.ReportSection(
                section="Membership",
                rows=[schemas.ReportRow(label="Current members", value=str(member_count))],
            ),
            schemas.ReportSection(
                section="Club leadership",
                rows=[
                    schemas.ReportRow(
                        label="President",
                        value=_role_holder(db, member.club_id, PRESIDENT_ROLES),
                    ),
                    schemas.ReportRow(
                        label="Secretary", value=_role_holder(db, member.club_id, {"Secretary"})
                    ),
                    schemas.ReportRow(
                        label="Treasurer", value=_role_holder(db, member.club_id, {"Treasurer"})
                    ),
                ],
            ),
            schemas.ReportSection(
                section="Projects",
                rows=[
                    schemas.ReportRow(label="Total projects", value=str(len(projects))),
                    schemas.ReportRow(label="Completed", value=str(completed_projects)),
                ],
            ),
            schemas.ReportSection(
                section="Meetings & attendance",
                rows=[
                    schemas.ReportRow(
                        label="Meetings held", value=str(len(meetings_this_year))
                    ),
                    schemas.ReportRow(
                        label="Average attendance", value=f"{avg_attendance}%"
                    ),
                ],
            ),
            schemas.ReportSection(
                section="Financial summary",
                rows=[
                    schemas.ReportRow(label="Income", value=f"UGX {income:,}"),
                    schemas.ReportRow(label="Expenses", value=f"UGX {expenses:,}"),
                ],
            ),
        ],
    )
