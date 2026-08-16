"""Coverage for the per-club admin panels: finances, event oversight,
broadcast and the audit trail.

These endpoints exist so an admin can see and act on one club without
being a member of it. The risks worth pinning down are that a panel
silently aggregates across clubs, that the admin's numbers disagree with
the club treasurer's, and that an action happens with nobody recorded as
having taken it.
"""

import uuid
from datetime import date, timedelta

from app import models, security
from app.routers import treasury
from app.utils import current_period_label


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    return {"Authorization": f"Bearer {security.create_admin_access_token(admin.id)}"}


# ── finances ────────────────────────────────────────────────────────────

def test_finances_matches_what_the_clubs_own_treasurer_sees(
    client, db, test_club, make_member
):
    """The admin panel and the club's treasury screen must never disagree
    about who has paid — they are the same numbers, computed once."""
    paid = make_member(suffix=uuid.uuid4().hex[:8], name="Paid Member")
    make_member(suffix=uuid.uuid4().hex[:8], name="Unpaid Member")
    db.add(models.ClubDuesSetting(club_id=test_club.id, amount=10_000, period="quarterly"))
    db.flush()
    db.add(
        models.DuesPayment(
            club_id=test_club.id,
            member_id=paid.id,
            period_label=current_period_label("quarterly"),
        )
    )
    db.commit()

    body = client.get(
        f"/admin/clubs/{test_club.id}/finances", headers=_admin_auth(db)
    ).json()

    assert body["dues_paid_count"] == 1
    assert body["dues_unpaid_count"] == 1
    assert body["summary"]["dues_collected"] == 10_000
    assert body["summary"]["dues_outstanding"] == 10_000

    # The club-side computation, called directly, must produce the same thing.
    assert body["summary"] == treasury.build_summary(db, test_club.id).model_dump()

    db.query(models.DuesPayment).filter(
        models.DuesPayment.club_id == test_club.id
    ).delete()
    db.commit()


def test_finances_are_scoped_to_one_club(client, db, test_club, make_member):
    """A transaction belonging to another club must not show up here."""
    member = make_member(suffix=uuid.uuid4().hex[:8])
    other = models.Club(name=f"Other {uuid.uuid4().hex[:6]}", district="", location="",
                        status="active")
    db.add(other)
    db.flush()
    db.add_all([
        models.Transaction(club_id=test_club.id, kind="income", label="Dues",
                           amount=5_000, created_by=member.id),
        models.Transaction(club_id=other.id, kind="income", label="Not ours",
                           amount=999_999, created_by=member.id),
    ])
    db.commit()

    body = client.get(
        f"/admin/clubs/{test_club.id}/finances", headers=_admin_auth(db)
    ).json()

    labels = [t["label"] for t in body["recent_transactions"]]
    assert labels == ["Dues"]
    assert body["summary"]["total_income"] == 5_000

    db.query(models.Transaction).filter(
        models.Transaction.club_id.in_([test_club.id, other.id])
    ).delete(synchronize_session=False)
    db.query(models.ClubDuesSetting).filter(
        models.ClubDuesSetting.club_id == other.id
    ).delete()
    db.commit()
    db.delete(db.get(models.Club, other.id))
    db.commit()


# ── event oversight ─────────────────────────────────────────────────────

def test_events_report_turnout_and_sort_upcoming_first(client, db, test_club):
    """Turnout is the point of the panel, and a past event must not sit
    above an upcoming one where it would read as the next thing happening."""
    past = models.Event(club_id=test_club.id, dow="MON", name="Last month's gala",
                        meta="", event_date=date.today() - timedelta(days=30))
    soon = models.Event(club_id=test_club.id, dow="FRI", name="Charter night",
                        meta="", event_date=date.today() + timedelta(days=7))
    weekly = models.Event(club_id=test_club.id, dow="WED", name="Weekly fellowship", meta="")
    db.add_all([past, soon, weekly])
    db.flush()
    db.add_all([
        models.EventRsvp(event_id=soon.id, name="Guest A", phone="256700000101"),
        models.EventRsvp(event_id=soon.id, name="Guest B", phone="256700000102"),
    ])
    db.commit()

    rows = client.get(
        f"/admin/clubs/{test_club.id}/events", headers=_admin_auth(db)
    ).json()

    by_name = {r["name"]: r for r in rows}
    assert by_name["Charter night"]["rsvp_count"] == 2
    assert by_name["Weekly fellowship"]["rsvp_count"] == 0
    # A recurring event has no date, so it never becomes "past".
    assert by_name["Weekly fellowship"]["is_upcoming"] is True
    assert by_name["Last month's gala"]["is_upcoming"] is False
    assert rows[-1]["name"] == "Last month's gala", "past events sort last"

    db.query(models.EventRsvp).filter(models.EventRsvp.event_id == soon.id).delete()
    db.query(models.Event).filter(models.Event.club_id == test_club.id).delete()
    db.commit()


def test_a_past_event_cannot_be_cancelled(client, db, test_club):
    """Past one-off events are kept as a historical record — the same rule
    the club's own delete enforces, so the admin route can't bypass it."""
    past = models.Event(club_id=test_club.id, dow="MON", name="Already happened",
                        meta="", event_date=date.today() - timedelta(days=5))
    db.add(past)
    db.commit()
    event_id = past.id

    res = client.delete(
        f"/admin/clubs/{test_club.id}/events/{event_id}", headers=_admin_auth(db)
    )
    assert res.status_code == 422
    assert db.get(models.Event, event_id) is not None

    db.query(models.Event).filter(models.Event.club_id == test_club.id).delete()
    db.commit()


def test_cancelling_an_event_clears_its_rsvps(client, db, test_club):
    """EventRsvp holds a non-nullable FK into events; leaving the rows would
    trip the constraint and 500 instead of cancelling."""
    event = models.Event(club_id=test_club.id, dow="FRI", name="Charter night",
                         meta="", event_date=date.today() + timedelta(days=7))
    db.add(event)
    db.flush()
    db.add(models.EventRsvp(event_id=event.id, name="Guest", phone="256700000103"))
    db.commit()
    event_id = event.id

    res = client.delete(
        f"/admin/clubs/{test_club.id}/events/{event_id}", headers=_admin_auth(db)
    )
    assert res.status_code == 200, res.text
    db.expire_all()
    assert db.get(models.Event, event_id) is None
    assert db.query(models.EventRsvp).filter(
        models.EventRsvp.event_id == event_id
    ).count() == 0


# ── broadcast ───────────────────────────────────────────────────────────

def test_broadcast_reports_who_it_reached(client, db, test_club, make_member):
    """Push is best-effort and silently disabled without credentials, so the
    response says what was actually reached rather than implying delivery."""
    member = make_member(suffix=uuid.uuid4().hex[:8])
    db.add(models.DeviceToken(member_id=member.id, token=f"tok-{uuid.uuid4().hex}",
                              platform="android"))
    db.commit()

    body = client.post(
        f"/admin/clubs/{test_club.id}/broadcast",
        headers=_admin_auth(db),
        json={"title": "Meeting moved", "body": "Now 7pm", "audience": "all"},
    ).json()

    assert body["recipients"] == 1
    assert body["devices"] == 1

    # make_member's teardown deletes members but not their device tokens,
    # whose FK would then block it — clear them here, not in the fixture.
    db.query(models.DeviceToken).filter(
        models.DeviceToken.member_id == member.id
    ).delete()
    db.commit()


def test_broadcast_to_the_board_excludes_ordinary_members(
    client, db, test_club, make_member
):
    """"Board" has to actually narrow the audience — otherwise a committee
    note goes to the whole club."""
    board = make_member(suffix=uuid.uuid4().hex[:8], is_board=True)
    ordinary = make_member(suffix=uuid.uuid4().hex[:8])
    db.add_all([
        models.DeviceToken(member_id=board.id, token=f"tok-{uuid.uuid4().hex}",
                           platform="android"),
        models.DeviceToken(member_id=ordinary.id, token=f"tok-{uuid.uuid4().hex}",
                           platform="ios"),
    ])
    db.commit()

    body = client.post(
        f"/admin/clubs/{test_club.id}/broadcast",
        headers=_admin_auth(db),
        json={"title": "Board only", "body": "Agenda attached", "audience": "board"},
    ).json()

    assert body["recipients"] == 1, "only the board member should be targeted"
    assert body["devices"] == 1

    db.query(models.DeviceToken).filter(
        models.DeviceToken.member_id.in_([board.id, ordinary.id])
    ).delete(synchronize_session=False)
    db.commit()


def test_broadcast_rejects_an_empty_message(client, db, test_club):
    res = client.post(
        f"/admin/clubs/{test_club.id}/broadcast",
        headers=_admin_auth(db),
        json={"title": "  ", "body": "", "audience": "all"},
    )
    assert res.status_code == 422


# ── audit trail ─────────────────────────────────────────────────────────

def test_suspending_a_club_is_recorded_against_an_actor(client, db, test_club):
    """The whole point of the trail: an action with nobody attached to it
    is not accountability."""
    res = client.patch(
        f"/admin/clubs/{test_club.id}/status",
        headers=_admin_auth(db),
        json={"status": "suspended"},
    )
    assert res.status_code == 200, res.text

    entries = client.get(
        f"/admin/clubs/{test_club.id}/audit", headers=_admin_auth(db)
    ).json()

    assert entries, "suspending a club must leave an audit entry"
    entry = entries[0]
    assert entry["action"] == "club.suspended"
    assert entry["actor_email"], "the entry must name who did it"
    assert "active -> suspended" in entry["detail"]

    db.query(models.AuditEntry).filter(
        models.AuditEntry.club_id == test_club.id
    ).delete()
    db.commit()


def test_audit_entries_survive_the_club_being_deleted(client, db, test_club):
    """A record that vanishes with the thing it describes is useless for
    accountability — deleting a club is exactly when the trail matters."""
    club_id = test_club.id
    client.patch(
        f"/admin/clubs/{club_id}/status",
        headers=_admin_auth(db),
        json={"status": "suspended"},
    )
    res = client.delete(f"/admin/clubs/{club_id}", headers=_admin_auth(db))
    assert res.status_code == 200, res.text

    db.expire_all()
    assert db.get(models.Club, club_id) is None
    surviving = db.query(models.AuditEntry).filter(
        models.AuditEntry.club_id == club_id
    ).all()
    assert len(surviving) >= 2, "both the suspend and the delete must remain"
    assert any(e.action == "club.delete" for e in surviving)
    # The name snapshot is why the entry still means something.
    assert all(e.club_name for e in surviving)

    db.query(models.AuditEntry).filter(
        models.AuditEntry.club_id == club_id
    ).delete()
    db.commit()


def test_audit_is_scoped_to_the_club_being_viewed(client, db, test_club):
    """One club's panel must not show actions taken against another."""
    db.add_all([
        models.AuditEntry(actor_id=1, actor_email="a@x.test", action="club.suspended",
                          club_id=test_club.id, club_name="Ours", subject="", detail=""),
        models.AuditEntry(actor_id=1, actor_email="a@x.test", action="club.delete",
                          club_id=test_club.id + 99_999, club_name="Theirs",
                          subject="", detail=""),
    ])
    db.commit()

    entries = client.get(
        f"/admin/clubs/{test_club.id}/audit", headers=_admin_auth(db)
    ).json()

    assert len(entries) == 1, "the other club's entry must not appear"
    assert entries[0]["action"] == "club.suspended"

    db.query(models.AuditEntry).filter(
        models.AuditEntry.club_id.in_([test_club.id, test_club.id + 99_999])
    ).delete(synchronize_session=False)
    db.commit()
