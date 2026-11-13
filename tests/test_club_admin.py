"""Club Admin: an alternative first administrator the system admin can create
instead of a President, for clubs where someone other than the President
sets the club up. Their whole job is the roster — above all, adding the
President — so they must be able to add and manage members, but must NOT
inherit the President's other powers (events, polls, Secretary workspace).
They also hold no board seat, so the July leadership handover (which
clears every board role back to Member) must leave them in place, or the
club would lose its roster manager every year.
"""

import uuid
from datetime import date

from app import models, security
from app.leadership_transition import run_leadership_transitions
from app.routers.club_members import CLUB_ADMIN_ROLE


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    return {"Authorization": f"Bearer {security.create_admin_access_token(admin.id)}"}


def _auth(member):
    return {"Authorization": f"Bearer {security.create_access_token(member.id)}"}


def _phone():
    return f"25677{uuid.uuid4().int % 10**7:07d}"


def _create_club(client, db, **extra):
    res = client.post(
        "/admin/clubs",
        json={
            "name": f"Club Admin Test {uuid.uuid4().hex[:6]}",
            "president_name": "First Admin",
            "president_phone": _phone(),
            **extra,
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    return res.json()


def _cleanup(db, club_id):
    db.query(models.Member).filter(models.Member.club_id == club_id).delete()
    db.delete(db.get(models.Club, club_id))
    db.commit()


def test_system_admin_can_onboard_a_club_with_a_club_admin(client, db):
    body = _create_club(client, db, first_admin_role="club_admin")
    try:
        assert body["president"]["role"] == CLUB_ADMIN_ROLE
        row = db.get(models.Member, body["president"]["id"])
        assert row.role == CLUB_ADMIN_ROLE
        assert row.is_board is False
        assert security.verify_pin(body["president"]["pin"], row.pin_hash)
    finally:
        _cleanup(db, body["club"]["id"])


def test_default_first_admin_is_still_the_president(client, db):
    """Existing dashboards that don't send the field must keep working."""
    body = _create_club(client, db)
    try:
        assert body["president"]["role"] == "Club President"
        assert db.get(models.Member, body["president"]["id"]).is_board is True
    finally:
        _cleanup(db, body["club"]["id"])


def test_unknown_first_admin_role_is_rejected(client, db):
    res = client.post(
        "/admin/clubs",
        json={"name": "Bad Role Club", "president_name": "X",
              "president_phone": _phone(), "first_admin_role": "treasurer"},
        headers=_admin_auth(db),
    )
    assert res.status_code == 422


def test_club_admin_can_add_the_president(client, make_member, db):
    admin = make_member(role=CLUB_ADMIN_ROLE, suffix="801")
    res = client.post(
        "/club/members",
        json={"name": "The President", "phone": "256700000802", "role": "President",
              "is_board": True},
        headers=_auth(admin),
    )
    assert res.status_code == 200, res.json()
    row = db.get(models.Member, res.json()["member"]["id"])
    assert row.role == "President"
    assert row.club_id == admin.club_id
    db.delete(row)
    db.commit()


def test_club_admin_can_edit_a_member(client, make_member):
    admin = make_member(role=CLUB_ADMIN_ROLE, suffix="803")
    target = make_member(role="Member", suffix="804")
    res = client.patch(
        f"/club/members/{target.id}", json={"role": "Secretary"}, headers=_auth(admin)
    )
    assert res.status_code == 200
    assert res.json()["role"] == "Secretary"


def test_club_admin_does_not_get_presidential_powers(client, make_member):
    """Creating a poll is a President/Secretary power — not the Club Admin's."""
    admin = make_member(role=CLUB_ADMIN_ROLE, suffix="805")
    res = client.post(
        "/club/polls",
        json={"type": "motion", "title": "Q?", "options": ["A", "B"]},
        headers=_auth(admin),
    )
    assert res.status_code == 403


def test_leadership_handover_keeps_the_club_admin(make_member, db, test_club):
    admin = make_member(role=CLUB_ADMIN_ROLE, suffix="806")
    make_member(role="President", suffix="807")
    make_member(role="President-Elect", suffix="808")
    test_club.last_leadership_transition_year = date.today().year - 1
    db.commit()

    run_leadership_transitions(db, today=date(date.today().year, 7, 2))

    db.refresh(admin)
    assert admin.role == CLUB_ADMIN_ROLE
