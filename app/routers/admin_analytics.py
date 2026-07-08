from collections import defaultdict
from datetime import date, timedelta

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
    meeting_by_id = {m.id: m for m in meetings}
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

    return schemas.AnalyticsOut(
        total_clubs=total_clubs,
        active_clubs=active_clubs,
        total_members=total_members,
        active_members=active_members,
        new_clubs_this_month=new_clubs_this_month,
        avg_attendance_percent=avg_attendance_percent,
        mrr_formatted=mrr_formatted,
        payment_legend=payment_legend,
        attendance_labels=attendance_labels,
        attendance_values=attendance_values,
    )
