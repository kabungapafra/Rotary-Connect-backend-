"""GET /club/meetings used to run one query per meeting for its
check-ins, plus a lazy-loaded query per check-in for the member — this
locks in both the fix (bounded query count) and the actual attendee data
it returns."""

from datetime import date, timedelta

from sqlalchemy import event

from app import models, security
from app.database import engine


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


class _QueryCounter:
    def __init__(self):
        self.count = 0

    def __call__(self, *args, **kwargs):
        self.count += 1


def test_meetings_endpoint_uses_a_bounded_number_of_queries(client, db, make_member, test_club):
    member = make_member(role="Member", suffix="080")
    other = make_member(role="Member", suffix="081")

    meetings = [
        models.Meeting(club_id=test_club.id, name="Weekly Fellowship Meeting", date=date.today()),
        models.Meeting(
            club_id=test_club.id,
            name="Board Meeting",
            date=date.today() - timedelta(days=7),
        ),
    ]
    db.add_all(meetings)
    db.commit()
    for m in meetings:
        db.refresh(m)

    db.add_all(
        [
            models.CheckIn(member_id=member.id, meeting_id=meetings[0].id),
            models.CheckIn(member_id=other.id, meeting_id=meetings[0].id),
            models.CheckIn(member_id=member.id, meeting_id=meetings[1].id),
        ]
    )
    db.commit()

    counter = _QueryCounter()
    event.listen(engine, "before_cursor_execute", counter)
    try:
        res = client.get("/club/meetings", headers=_auth(member))
    finally:
        event.remove(engine, "before_cursor_execute", counter)

    assert res.status_code == 200
    body = res.json()
    assert len(body) == 2

    # Regardless of how many meetings/check-ins exist, this must not scale
    # with the number of rows — the old N+1 version issued one query per
    # meeting plus one lazy-load per check-in row, so this tiny 2-meeting/
    # 3-check-in fixture would already have hit 7+ queries. A flat, small
    # count here (auth lookup + meetings + one batched query each for
    # check-ins, guest visits, events and RSVPs, plus incidental overhead)
    # proves the fix, not an exact number.
    assert counter.count <= 9, f"expected a small, flat query count, got {counter.count}"

    this_week = next(m for m in body if m["name"] == "Weekly Fellowship Meeting")
    assert this_week["checkin_count"] == 2
    assert this_week["attended"] is True
    names = {a["name"] for a in this_week["attendees"]}
    assert names == {member.name, other.name}

    board = next(m for m in body if m["name"] == "Board Meeting")
    assert board["checkin_count"] == 1
    assert board["attendees"][0]["role"] == member.role

    db.query(models.CheckIn).filter(
        models.CheckIn.meeting_id.in_([m.id for m in meetings])
    ).delete(synchronize_session=False)
    db.query(models.Meeting).filter(
        models.Meeting.id.in_([m.id for m in meetings])
    ).delete(synchronize_session=False)
    db.commit()


def test_meetings_register_includes_guests_and_web_rsvps(
    client, db, make_member, make_event, test_club
):
    """The register must also show non-members: walk-in guests who scanned
    the club QR that day (with a Visiting Rotarian's own club named), and
    people who registered ahead through the event's public web RSVP form.
    Both were previously invisible — GuestVisit/EventRsvp rows were written
    but never returned to the club anywhere."""
    member = make_member(role="Member", suffix="082")
    today = date.today()
    event_row = make_event(dow=today.strftime("%a").upper(), meta="6:00 PM - Hall")

    meeting = models.Meeting(
        club_id=test_club.id, name="Weekly Fellowship Meeting", date=today
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    db.add(models.CheckIn(member_id=member.id, meeting_id=meeting.id))
    db.add(
        models.GuestVisit(
            club_id=test_club.id,
            name="Visiting Vera",
            phone="256700222333",
            guest_type="Visiting Rotarian",
            member_club="Rotary Club of Naalya",
            visit_date=today,
        )
    )
    # A web RSVP made today for a same-dow event targets today's meeting.
    db.add(
        models.EventRsvp(
            event_id=event_row.id,
            name="Web Wendy",
            phone="256700333444",
            attendee_type="Prospective member",
        )
    )
    db.commit()

    res = client.get("/club/meetings", headers=_auth(member))
    assert res.status_code == 200
    row = next(m for m in res.json() if m["date"] == today.strftime("%d %b %Y"))

    # Members-only count unchanged; non-members live in their own list.
    assert row["checkin_count"] == 1
    guests = {g["name"]: g for g in row["guests"]}
    assert guests["Visiting Vera"]["via"] == "scan"
    assert guests["Visiting Vera"]["club_name"] == "Rotary Club of Naalya"
    assert guests["Web Wendy"]["via"] == "web"
    assert guests["Web Wendy"]["type"] == "Prospective member"

    db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event_row.id).delete()
    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == test_club.id).delete()
    db.query(models.CheckIn).filter(models.CheckIn.meeting_id == meeting.id).delete()
    db.query(models.Meeting).filter(models.Meeting.id == meeting.id).delete()
    db.commit()
