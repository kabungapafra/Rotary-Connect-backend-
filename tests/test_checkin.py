"""checkin.py has three surfaces: authenticated self check-in, the
unauthenticated walk-in guest form, and the unauthenticated /today summary
the app polls before login. Locks in: idempotent check-in, that a
suspended member's token stops working (the fix for the 365-day-lived
token staying valid after suspension), guest dedup/rate-limiting, and that
/today ignores any club_id a caller supplies rather than returning another
club's roster (the fix for the unauthenticated cross-club enumeration —
a request must not be able to pull a different club's data by passing a
club_id, regardless of which club's data would otherwise be returned)."""

from datetime import date, datetime, timezone

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


def test_guest_check_in_respects_the_meeting_window(client, test_club, db, make_event):
    """Guest check-in (the in-app "scan the club QR" flow for walk-ins) must
    be held to the same door-side window as a logged-in member's self
    check-in — it was previously unguarded, letting a guest check in at any
    time of day regardless of whether a meeting was actually happening."""
    todays_dow = date.today().strftime("%a").upper()
    now_utc = datetime.now(timezone.utc)

    # A meeting time ~4 hours away in UTC terms — comfortably outside the
    # 15-min-before/60-min-after window on either side, independent of
    # exact minute rounding.
    far_local_hour = (now_utc.hour + 4 + 3) % 24  # +3 for the fixed EAT offset
    event = make_event(dow=todays_dow, meta=f"{far_local_hour}:00 - Hall")

    res = client.post(
        "/checkin/guest",
        json={"club_id": test_club.id, "name": "Too Early Guest", "phone": "0772000096"},
    )
    assert res.status_code == 422
    assert "opens" in res.json()["detail"].lower()

    # Move the same event's start to right now — the window opens.
    event.meta = f"{(now_utc.hour + 3) % 24}:{now_utc.minute:02d} - Hall"
    db.commit()

    res = client.post(
        "/checkin/guest",
        json={"club_id": test_club.id, "name": "On Time Guest", "phone": "0772000096"},
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == test_club.id).delete()
    db.commit()


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


def test_today_shows_the_authenticated_members_own_club(client, db, make_member, test_club):
    """A logged-in member's "Who's here" view must show THEIR club's
    roster. Previously /today ignored auth entirely and always resolved to
    the seeded default club, so a real member's own check-in never showed
    up here — not even to themselves."""
    member = make_member(suffix="092")
    meeting = models.Meeting(club_id=test_club.id, name="My Club Meeting", date=date.today())
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    db.add(models.CheckIn(member_id=member.id, meeting_id=meeting.id))
    db.commit()

    res = client.get("/checkin/today", headers=_auth(member))
    assert res.status_code == 200
    body = res.json()
    assert body["meeting_name"] == "My Club Meeting"
    names = {m["name"] for m in body["members"]}
    assert member.name in names

    db.query(models.CheckIn).filter(models.CheckIn.meeting_id == meeting.id).delete()
    db.query(models.Meeting).filter(models.Meeting.id == meeting.id).delete()
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
