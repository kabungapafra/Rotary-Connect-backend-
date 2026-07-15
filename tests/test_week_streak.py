"""GET /club/me/summary's week_streak: the check-in success screen used to
show a hardcoded "Week streak now 8" regardless of the actual member — this
locks in the real computation an excused absence (an Apology on file)
preserves the streak, matching how a real Rotary club's own attendance
bookkeeping treats a recorded apology, while an unexplained miss breaks it."""

from datetime import date, timedelta

from app import models, security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def _meeting(db, club_id, weeks_ago):
    m = models.Meeting(
        club_id=club_id,
        name="Weekly Fellowship Meeting",
        date=date.today() - timedelta(weeks=weeks_ago),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return m


def test_streak_breaks_at_an_unexplained_miss(client, db, test_club, make_member):
    member = make_member(suffix="150")
    meetings = [_meeting(db, test_club.id, w) for w in (4, 3, 2, 1, 0)]
    # Checked in: 4wk ago, 3wk ago, 1wk ago, today. Missed 2wk ago, no apology.
    for m in [meetings[0], meetings[1], meetings[3], meetings[4]]:
        db.add(models.CheckIn(member_id=member.id, meeting_id=m.id))
    db.commit()

    res = client.get("/club/me/summary", headers=_auth(member))
    assert res.status_code == 200
    # Counting back from today: today (in)=1, 1wk ago (in)=2, 2wk ago (miss) -> stop.
    assert res.json()["week_streak"] == 2

    db.query(models.CheckIn).filter(models.CheckIn.member_id == member.id).delete()
    db.query(models.Meeting).filter(models.Meeting.id.in_([m.id for m in meetings])).delete(
        synchronize_session=False
    )
    db.commit()


def test_an_apology_preserves_the_streak(client, db, test_club, make_member):
    member = make_member(suffix="151")
    meetings = [_meeting(db, test_club.id, w) for w in (4, 3, 2, 1, 0)]
    # Checked in every week except 2wk ago, where an apology is on file instead.
    for m in [meetings[0], meetings[1], meetings[3], meetings[4]]:
        db.add(models.CheckIn(member_id=member.id, meeting_id=m.id))
    db.add(models.Apology(
        club_id=test_club.id, member_id=member.id, meeting_date=meetings[2].date,
    ))
    db.commit()

    res = client.get("/club/me/summary", headers=_auth(member))
    assert res.status_code == 200
    # All 5 meetings count: the apology at 2wk ago doesn't break the streak.
    assert res.json()["week_streak"] == 5

    db.query(models.CheckIn).filter(models.CheckIn.member_id == member.id).delete()
    db.query(models.Apology).filter(models.Apology.member_id == member.id).delete()
    db.query(models.Meeting).filter(models.Meeting.id.in_([m.id for m in meetings])).delete(
        synchronize_session=False
    )
    db.commit()


def test_no_meetings_means_zero_streak(client, make_member):
    member = make_member(suffix="152")
    res = client.get("/club/me/summary", headers=_auth(member))
    assert res.status_code == 200
    assert res.json()["week_streak"] == 0
