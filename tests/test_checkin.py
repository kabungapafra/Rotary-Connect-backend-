"""checkin.py has three surfaces: authenticated self check-in, the
unauthenticated walk-in guest form, and the unauthenticated /today summary
the app polls before login. Locks in: idempotent check-in, that a
suspended member's token stops working (the fix for the 365-day-lived
token staying valid after suspension), guest dedup/rate-limiting, and that
/today ignores any club_id a caller supplies rather than returning another
club's roster (the fix for the unauthenticated cross-club enumeration —
a request must not be able to pull a different club's data by passing a
club_id, regardless of which club's data would otherwise be returned)."""

from datetime import date

from app import models, security
from app.seed import DEFAULT_CLUB_NAME


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_check_in_creates_a_row_and_is_idempotent(client, make_member, test_club, db):
    member = make_member(suffix="090")
    res = client.post("/checkin", headers=_auth(member))
    assert res.status_code == 200
    assert res.json()["already_checked_in"] is False

    res = client.post("/checkin", headers=_auth(member))
    assert res.status_code == 200
    assert res.json()["already_checked_in"] is True

    db.query(models.CheckIn).filter(models.CheckIn.member_id == member.id).delete()
    db.query(models.Meeting).filter(models.Meeting.club_id == test_club.id).delete()
    db.commit()


def test_suspended_member_cannot_check_in(client, make_member, db):
    """A suspended member's existing token must stop working immediately —
    not just get filtered out of rosters — since member tokens live for
    365 days and there's no separate revocation list."""
    member = make_member(suffix="091")
    token_headers = _auth(member)
    member.status = "suspended"
    db.commit()

    res = client.post("/checkin", headers=token_headers)
    assert res.status_code == 401


def test_guest_check_in_dedups_same_phone_same_day(client, test_club, db):
    payload = {
        "club_id": test_club.id,
        "name": "Visiting Guest",
        "phone": "0772000099",
    }
    res = client.post("/checkin/guest", json=payload)
    assert res.status_code == 200
    assert res.json()["ok"] is True

    # Second registration for the same phone/club/day is a no-op, not a
    # duplicate row (each duplicate would also queue a second thank-you SMS).
    res = client.post("/checkin/guest", json=payload)
    assert res.status_code == 200

    count = (
        db.query(models.GuestVisit)
        .filter(models.GuestVisit.club_id == test_club.id, models.GuestVisit.phone == "256772000099")
        .count()
    )
    assert count == 1

    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == test_club.id).delete()
    db.commit()


def test_guest_check_in_rejects_invalid_phone(client, test_club):
    res = client.post(
        "/checkin/guest",
        json={"club_id": test_club.id, "name": "Bad Phone", "phone": "abc"},
    )
    assert res.status_code == 422


def test_guest_check_in_rejects_unknown_club(client):
    res = client.post(
        "/checkin/guest",
        json={"club_id": 999999, "name": "Nobody", "phone": "0772000098"},
    )
    assert res.status_code == 404


def test_guest_check_in_throttles_after_five_requests_per_ip(client, test_club, db):
    for i in range(5):
        res = client.post(
            "/checkin/guest",
            json={
                "club_id": test_club.id,
                "name": f"Guest {i}",
                "phone": f"07720001{i:02d}",
            },
        )
        assert res.status_code == 200

    res = client.post(
        "/checkin/guest",
        json={"club_id": test_club.id, "name": "One Too Many", "phone": "0772000199"},
    )
    assert res.status_code == 429

    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == test_club.id).delete()
    db.commit()


def test_today_ignores_a_client_supplied_club_id(client, db, make_member):
    """The endpoint is unauthenticated and used to accept a club_id query
    param — anyone could enumerate any club's roster/check-in times by
    guessing ids. It must now always resolve to the seeded default club,
    never to whatever club_id a caller passes."""
    default_club = models.Club(name=DEFAULT_CLUB_NAME, status="active")
    other_club = models.Club(name="Some Other Rotary Club", status="active")
    db.add_all([default_club, other_club])
    db.commit()
    db.refresh(default_club)
    db.refresh(other_club)

    other_member = models.Member(
        club_id=other_club.id,
        member_number="OTHER-0001",
        name="Secret Other-Club Member",
        role="Member",
        status="active",
        email="",
        phone="256700111222",
        dob="",
        pin_hash=security.hash_pin("1234"),
    )
    db.add(other_member)
    db.commit()
    db.refresh(other_member)

    other_meeting = models.Meeting(
        club_id=other_club.id, name="Other Club Meeting", date=date.today()
    )
    db.add(other_meeting)
    db.commit()
    db.refresh(other_meeting)
    db.add(models.CheckIn(member_id=other_member.id, meeting_id=other_meeting.id))
    db.commit()

    res = client.get(f"/checkin/today?club_id={other_club.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["meeting_name"] != "Other Club Meeting"
    names = {m["name"] for m in body["members"]}
    assert "Secret Other-Club Member" not in names

    db.query(models.CheckIn).filter(models.CheckIn.meeting_id == other_meeting.id).delete()
    db.query(models.Meeting).filter(models.Meeting.id == other_meeting.id).delete()
    db.query(models.Member).filter(models.Member.id == other_member.id).delete()
    db.query(models.Club).filter(models.Club.id.in_([default_club.id, other_club.id])).delete(
        synchronize_session=False
    )
    db.commit()


def test_visitor_club_profile_is_public_display_data_only(client, test_club):
    """The visitor dashboard endpoint must work with no auth (walk-ins have
    no account) but expose only what the club already publishes — never
    members or attendance."""
    res = client.get(f"/checkin/club/{test_club.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["club_id"] == test_club.id
    assert body["name"] == test_club.name
    assert "events" in body
    assert "members" not in body


def test_visitor_club_profile_unknown_club_is_404(client):
    assert client.get("/checkin/club/999999").status_code == 404
