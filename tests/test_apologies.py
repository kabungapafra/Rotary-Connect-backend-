"""Apologies: a member notes they'll miss today's meeting, and any other
club member (the register screen) can see who apologised and why."""

from datetime import date

from app import models, security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_submit_apology_then_visible_to_another_member(client, db, make_member, test_club):
    absentee = make_member(role="Member", suffix="020")
    viewer = make_member(role="Secretary", suffix="021")

    res = client.post(
        "/club/apologies",
        json={"reason": "Travelling upcountry for work"},
        headers=_auth(absentee),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["member_name"] == absentee.name
    assert body["reason"] == "Travelling upcountry for work"
    assert body["meeting_date"] == date.today().isoformat()

    res = client.get("/club/apologies", headers=_auth(viewer))
    assert res.status_code == 200
    names = [row["member_name"] for row in res.json()]
    assert absentee.name in names

    # Apology rows are cleaned up by the test_club fixture; the meeting
    # created as a side effect isn't (Meeting predates this feature).
    db.query(models.Meeting).filter(
        models.Meeting.club_id == test_club.id, models.Meeting.date == date.today()
    ).delete()
    db.commit()


def test_resubmitting_apology_updates_reason_instead_of_duplicating(client, db, make_member, test_club):
    member = make_member(role="Member", suffix="022")

    client.post("/club/apologies", json={"reason": "First reason"}, headers=_auth(member))
    res = client.post("/club/apologies", json={"reason": "Updated reason"}, headers=_auth(member))
    assert res.status_code == 200
    assert res.json()["reason"] == "Updated reason"

    rows = db.query(models.Apology).filter(models.Apology.member_id == member.id).all()
    assert len(rows) == 1
    assert rows[0].reason == "Updated reason"

    db.query(models.Apology).filter(models.Apology.member_id == member.id).delete()
    db.query(models.Meeting).filter(
        models.Meeting.club_id == test_club.id, models.Meeting.date == date.today()
    ).delete()
    db.commit()
