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
    # count here (auth lookup + meetings + one batched check-ins query,
    # plus incidental overhead) proves the fix, not an exact number.
    assert counter.count <= 6, f"expected a small, flat query count, got {counter.count}"

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
