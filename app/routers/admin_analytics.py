from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_admin
from ..utils import compute_payment_status

router = APIRouter(
    prefix="/admin/analytics", tags=["admin"], dependencies=[Depends(get_current_admin)]
)

_STATUS_LABELS = {"paid": "Paid", "due-soon": "Due Soon", "overdue": "Overdue"}
_STATUS_ORDER = ["paid", "due-soon", "overdue"]


@router.get("", response_model=schemas.AnalyticsOut)
def analytics(db: Session = Depends(get_db)):
    clubs = db.query(models.Club).all()
    members = db.query(models.Member).all()

    total_clubs = len(clubs)
    active_clubs = sum(1 for c in clubs if c.status == "active")
    total_members = sum(c.members_count for c in clubs)
    active_members = sum(1 for m in members if m.status == "active")

    month_start = date.today().replace(day=1)
    new_clubs_this_month = sum(
        1 for c in clubs if c.created_at.date() >= month_start
    )

    mrr = sum(c.fee_amount for c in clubs)
    mrr_formatted = f"UGX {mrr:,}"

    status_counts: dict[str, int] = defaultdict(int)
    for c in clubs:
        status_counts[compute_payment_status(c.next_due_date)] += 1
    payment_legend = [
        schemas.PaymentLegendItem(
            name=_STATUS_LABELS[key], count=status_counts.get(key, 0), color_key=key
        )
        for key in _STATUS_ORDER
    ]

    # Real weekly attendance trend: for each of the last 6 ISO weeks, average
    # (checked-in members / club member count) across every club that held a
    # meeting that week. Weeks with no meetings anywhere show 0.
    today = date.today()
    week_starts = [
        today - timedelta(days=today.weekday()) - timedelta(weeks=w) for w in range(5, -1, -1)
    ]
    club_member_counts = {c.id: (c.members_count or 1) for c in clubs}

    meetings = db.query(models.Meeting).all()
    checkins_per_meeting: dict[int, int] = defaultdict(int)
    for ci in db.query(models.CheckIn).all():
        checkins_per_meeting[ci.meeting_id] += 1

    attendance_values: list[int] = []
    attendance_labels: list[str] = []
    for i, week_start in enumerate(week_starts):
        week_end = week_start + timedelta(days=7)
        week_meetings = [m for m in meetings if week_start <= m.date < week_end]
        if week_meetings:
            pct_sum = 0.0
            for m in week_meetings:
                denom = club_member_counts.get(m.club_id, 1)
                pct_sum += min(100.0, checkins_per_meeting.get(m.id, 0) / denom * 100)
            attendance_values.append(round(pct_sum / len(week_meetings)))
        else:
            attendance_values.append(0)
        attendance_labels.append(f"Wk {i + 1}")

    avg_attendance_percent = (
        round(sum(attendance_values) / len(attendance_values)) if attendance_values else 0
    )

    todays_meetings = [m for m in meetings if m.date == today]
    meetings_today = len(todays_meetings)
    checkins_today = sum(checkins_per_meeting.get(m.id, 0) for m in todays_meetings)

    # Per-club attendance over the last 4 weeks — the "which clubs are
    # engaged, which are going quiet" breakdown the totals above hide.
    four_weeks_ago = today - timedelta(weeks=4)
    club_names = {c.id: c.name for c in clubs}
    club_attendance: list[schemas.ClubAttendanceItem] = []
    for club_id, club_name in club_names.items():
        recent = [m for m in meetings if m.club_id == club_id and m.date >= four_weeks_ago]
        denom = club_member_counts.get(club_id, 1)
        if recent:
            pct = round(
                sum(
                    min(100.0, checkins_per_meeting.get(m.id, 0) / denom * 100)
                    for m in recent
                )
                / len(recent)
            )
        else:
            pct = 0
        club_attendance.append(
            schemas.ClubAttendanceItem(
                club_name=club_name,
                attendance_percent=pct,
                meetings_held=len(recent),
                member_count=next(
                    (c.members_count for c in clubs if c.id == club_id), 0
                ),
            )
        )
    club_attendance.sort(key=lambda item: item.attendance_percent, reverse=True)

    cutoff_30d = datetime.now(timezone.utc) - timedelta(days=30)
    date_30d = today - timedelta(days=30)
    engagement = schemas.EngagementOut(
        checkins_30d=db.query(models.CheckIn)
        .filter(models.CheckIn.checked_in_at >= cutoff_30d)
        .count(),
        guest_visits_30d=db.query(models.GuestVisit)
        .filter(models.GuestVisit.visit_date >= date_30d)
        .count(),
        apologies_30d=db.query(models.Apology)
        .filter(models.Apology.meeting_date >= date_30d)
        .count(),
        gallery_uploads_30d=db.query(models.GalleryPhoto)
        .filter(models.GalleryPhoto.created_at >= cutoff_30d)
        .count(),
    )

    return schemas.AnalyticsOut(
        total_clubs=total_clubs,
        active_clubs=active_clubs,
        total_members=total_members,
        active_members=active_members,
        new_clubs_this_month=new_clubs_this_month,
        avg_attendance_percent=avg_attendance_percent,
        meetings_today=meetings_today,
        checkins_today=checkins_today,
        mrr_formatted=mrr_formatted,
        payment_legend=payment_legend,
        attendance_labels=attendance_labels,
        attendance_values=attendance_values,
        club_attendance=club_attendance,
        engagement=engagement,
    )


@router.get("/errors", response_model=list[schemas.ErrorLogOut])
def recent_errors(db: Session = Depends(get_db)):
    """The last 50 unhandled API exceptions — no third-party error tracker
    is configured, so this (backed by main.py's global exception handler)
    is the only place these are visible at all outside server logs."""
    return (
        db.query(models.ErrorLog)
        .order_by(models.ErrorLog.created_at.desc())
        .limit(50)
        .all()
    )


@router.get("/monitoring", response_model=schemas.MonitoringOut)
def monitoring(db: Session = Depends(get_db)):
    """Everything the System Health page shows besides its live /health
    probe: member-side problems (failed PINs, lockouts, PIN resets) with
    the member and club named where the identifier matched someone, and
    the slow/5xx request log recorded by main.py's timing middleware."""
    events = (
        db.query(models.MemberEvent)
        .order_by(models.MemberEvent.created_at.desc())
        .limit(100)
        .all()
    )
    slow = (
        db.query(models.SlowRequest)
        .order_by(models.SlowRequest.created_at.desc())
        .limit(100)
        .all()
    )
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    events_today = (
        db.query(models.MemberEvent)
        .filter(models.MemberEvent.created_at >= today_start)
        .count()
    )
    slow_today = (
        db.query(models.SlowRequest)
        .filter(models.SlowRequest.created_at >= today_start)
        .count()
    )
    return schemas.MonitoringOut(
        member_events=events,
        slow_requests=slow,
        events_today=events_today,
        slow_today=slow_today,
    )
