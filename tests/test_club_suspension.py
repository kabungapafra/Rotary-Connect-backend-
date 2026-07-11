"""club_status (surfaced at login and in /club/me/summary) drives the app's
Club Suspended screen. It's blocked either by the admin's manual
status='suspended' toggle, or — new — by an overdue next_due_date, so a
club whose paid period lapsed gets the same screen without anyone having
to remember to flip a switch. Both paths are derived at read time
(is_club_access_blocked), never stored, so recording a payment or
reactivating the club self-heals it immediately."""

from datetime import date, timedelta


def _login(client, member, pin="4321"):
    return client.post(
        "/auth/login", json={"identifier": member.member_number, "pin": pin}
    )


def test_active_club_with_no_due_date_is_not_blocked(client, make_member):
    member = make_member(pin="4321")
    res = _login(client, member)
    assert res.status_code == 200
    assert res.json()["club_status"] == "active"


def test_manually_suspended_club_blocks_login(client, db, test_club, make_member):
    member = make_member(pin="4321")
    test_club.status = "suspended"
    db.commit()

    res = _login(client, member)
    assert res.status_code == 200
    assert res.json()["club_status"] == "suspended"


def test_overdue_dues_block_login_even_if_status_is_active(client, db, test_club, make_member):
    member = make_member(pin="4321")
    test_club.status = "active"
    test_club.next_due_date = date.today() - timedelta(days=1)
    db.commit()

    res = _login(client, member)
    assert res.status_code == 200
    assert res.json()["club_status"] == "suspended"


def test_future_due_date_does_not_block_login(client, db, test_club, make_member):
    member = make_member(pin="4321")
    test_club.status = "active"
    test_club.next_due_date = date.today() + timedelta(days=30)
    db.commit()

    res = _login(client, member)
    assert res.status_code == 200
    assert res.json()["club_status"] == "active"


def test_recording_a_payment_self_heals_an_overdue_block(client, db, test_club, make_member):
    """The exact scenario the feature is for: a club goes overdue, gets
    blocked, then the admin records a new payment — no separate
    "un-suspend" action should be needed."""
    member = make_member(pin="4321")
    test_club.next_due_date = date.today() - timedelta(days=1)
    db.commit()
    assert _login(client, member).json()["club_status"] == "suspended"

    test_club.next_due_date = date.today() + timedelta(days=30)
    db.commit()
    assert _login(client, member).json()["club_status"] == "active"


def test_me_summary_reflects_the_same_blocked_state(client, db, test_club, make_member):
    member = make_member(pin="4321")
    test_club.next_due_date = date.today() - timedelta(days=1)
    db.commit()

    token = _login(client, member).json()["access_token"]
    res = client.get("/club/me/summary", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200
    assert res.json()["club_status"] == "suspended"
