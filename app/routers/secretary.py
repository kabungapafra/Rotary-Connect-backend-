import tempfile

from collections import defaultdict
from datetime import date

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import get_db
from ..security import get_current_member
from ..storage import delete_gallery_image, upload_club_document
from ..transcription import process_minute_audio
from .club_members import HISTORY_EDITOR_ROLES, PRESIDENT_ROLES
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


def _require_history_editor(member: models.Member) -> None:
    """Club history (milestones) is the one part of the Secretary
    workspace also open to the President and Immediate Past President."""
    if member.role not in HISTORY_EDITOR_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the President, Immediate Past President, or "
            "Secretary can manage club history",
        )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=422, detail="meeting_date must be YYYY-MM-DD")


# ── minutes ────────────────────────────────────────────────────────────

def _minute_out(m: models.Minute) -> schemas.MinuteOut:
    return schemas.MinuteOut(
        id=m.id,
        title=m.title,
        meeting_date=m.meeting_date.isoformat(),
        status=m.status,
        body=m.body or "",
        created_at=m.created_at,
    )


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
    return [_minute_out(m) for m in rows]


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
    return _minute_out(minute)


@router.post("/minutes/from-audio", response_model=schemas.MinuteOut)
def create_minute_from_audio(
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    title: str = Form(...),
    meeting_date: str = Form(...),
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    """Upload a meeting recording; Groq transcribes it and drafts the
    minutes in the background. Returns the placeholder minute immediately
    with status `processing` — the app polls the list to see it flip to
    `draft` (or `failed`)."""
    _require_secretary(member)
    if not config.GROQ_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="Audio transcription isn't configured on this server yet",
        )
    if not title.strip():
        raise HTTPException(status_code=422, detail="Title is required")

    # Spool the upload to disk first — the background task outlives this
    # request, so it can't read from the request's stream.
    with tempfile.NamedTemporaryFile(prefix="rotary-upload-", delete=False) as tmp:
        while chunk := audio.file.read(1024 * 1024):
            tmp.write(chunk)
        tmp_path = tmp.name

    minute = models.Minute(
        club_id=member.club_id,
        title=title.strip(),
        meeting_date=_parse_date(meeting_date),
        status="processing",
        created_by=member.id,
    )
    db.add(minute)
    db.commit()
    db.refresh(minute)
    background_tasks.add_task(process_minute_audio, minute.id, tmp_path)
    return _minute_out(minute)


@router.patch("/minutes/{minute_id}", response_model=schemas.MinuteOut)
def update_minute(
    minute_id: int,
    payload: schemas.MinuteUpdate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    minute = db.get(models.Minute, minute_id)
    if minute is None or minute.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Minute not found")
    if payload.status is not None:
        if payload.status not in ("draft", "approved"):
            raise HTTPException(status_code=422, detail="status must be draft or approved")
        minute.status = payload.status
    if payload.title is not None:
        if not payload.title.strip():
            raise HTTPException(status_code=422, detail="Title is required")
        minute.title = payload.title.strip()
    if payload.body is not None:
        minute.body = payload.body
    db.commit()
    db.refresh(minute)
    return _minute_out(minute)


@router.delete("/minutes/{minute_id}")
def delete_minute(
    minute_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    minute = db.get(models.Minute, minute_id)
    if minute is None or minute.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Minute not found")
    db.delete(minute)
    db.commit()
    return {"deleted": True}


# ── club documents ───────────────────────────────────────────────────────

@router.get("/documents", response_model=list[schemas.ClubDocumentOut])
def list_documents(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    return (
        db.query(models.ClubDocument)
        .filter(models.ClubDocument.club_id == member.club_id)
        .order_by(models.ClubDocument.created_at.desc())
        .all()
    )


@router.post("/documents", response_model=schemas.ClubDocumentOut)
def upload_document(
    payload: schemas.ClubDocumentCreate,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    if not payload.title.strip():
        raise HTTPException(status_code=422, detail="Title is required")
    try:
        url, key = upload_club_document(payload.file, member.club_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    doc = models.ClubDocument(
        club_id=member.club_id,
        title=payload.title.strip(),
        url=url,
        storage_key=key,
        created_by=member.id,
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc


@router.delete("/documents/{document_id}")
def delete_document(
    document_id: int,
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    _require_secretary(member)
    doc = db.get(models.ClubDocument, document_id)
    if doc is None or doc.club_id != member.club_id:
        raise HTTPException(status_code=404, detail="Document not found")
    delete_gallery_image(doc.storage_key)
    db.delete(doc)
    db.commit()
    return {"deleted": True}


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
    _require_history_editor(member)
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
    _require_history_editor(member)
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


def _active_member_count(db: Session, club_id: int) -> int:
    """Active roster only — suspended/terminated members can't check in
    (login is blocked for them), so this also doubles as the correct
    attendance-rate denominator."""
    return (
        db.query(models.Member)
        .filter(models.Member.club_id == club_id, models.Member.status == "active")
        .count()
    )


def _membership_rows(
    db: Session, club_id: int, period_start: date
) -> tuple[int, list[schemas.ReportRow]]:
    """(active_count, Membership section rows) — new members by join date,
    terminations by the date they were marked terminated, both counted
    from `period_start` to now."""
    active_count = _active_member_count(db, club_id)
    board_count = (
        db.query(models.Member)
        .filter(models.Member.club_id == club_id, models.Member.is_board.is_(True))
        .count()
    )
    new_members = (
        db.query(models.Member)
        .filter(models.Member.club_id == club_id, models.Member.created_at >= period_start)
        .count()
    )
    terminations = (
        db.query(models.Member)
        .filter(
            models.Member.club_id == club_id,
            models.Member.status == "terminated",
            models.Member.terminated_at >= period_start,
        )
        .count()
    )
    rows = [
        schemas.ReportRow(label="Current membership", value=str(active_count)),
        schemas.ReportRow(label="Board & officers", value=str(board_count)),
        schemas.ReportRow(label="New members", value=str(new_members)),
        schemas.ReportRow(label="Terminations/resignations", value=str(terminations)),
        schemas.ReportRow(label="Net change", value=f"{new_members - terminations:+d}"),
    ]
    return active_count, rows


def _project_rows(projects: list[models.Project]) -> list[schemas.ReportRow]:
    """Total/completed/hours/beneficiaries, plus a per-area breakdown —
    only areas with at least one project get a row, so an unused area
    doesn't pad the report with zeroes."""
    completed = sum(1 for p in projects if p.pct >= 100)
    area_counts: dict[str, int] = defaultdict(int)
    for p in projects:
        area_counts[p.area_of_focus or "Uncategorized"] += 1
    rows = [
        schemas.ReportRow(label="Total projects", value=str(len(projects))),
        schemas.ReportRow(label="Completed", value=str(completed)),
        schemas.ReportRow(
            label="Total beneficiaries reached",
            value=str(sum(p.beneficiaries_reached for p in projects)),
        ),
    ]
    for area in [*schemas.ROTARY_AREAS_OF_FOCUS, "Uncategorized"]:
        if area_counts.get(area):
            rows.append(schemas.ReportRow(label=area, value=str(area_counts[area])))
    return rows


@router.get("/monthly-report", response_model=schemas.ReportOut)
def monthly_report(
    db: Session = Depends(get_db),
    member: models.Member = Depends(get_current_member),
):
    club = db.get(models.Club, member.club_id)
    today = date.today()
    month_start = today.replace(day=1)

    active_count, membership_rows = _membership_rows(db, member.club_id, month_start)
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
        round(checkins_this_month / (len(meetings_this_month) * active_count) * 100)
        if meetings_this_month and active_count
        else 0
    )
    projects = (
        db.query(models.Project).filter(models.Project.club_id == member.club_id).all()
    )

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
            schemas.ReportSection(section="Membership", rows=membership_rows),
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
            schemas.ReportSection(section="Projects", rows=_project_rows(projects)),
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

    active_count, membership_rows = _membership_rows(db, member.club_id, year_start)
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
        round(checkins_this_year / (len(meetings_this_year) * active_count) * 100)
        if meetings_this_year and active_count
        else 0
    )
    projects = (
        db.query(models.Project).filter(models.Project.club_id == member.club_id).all()
    )
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
            schemas.ReportSection(section="Membership", rows=membership_rows),
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
            schemas.ReportSection(section="Projects", rows=_project_rows(projects)),
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
