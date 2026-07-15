"""club_members.py: only the President/Secretary may add or manage members
(everyone else can only list the roster), a duplicate phone is rejected,
and the returned one-time PIN actually matches what got hashed into the
new member's row (the whole point of returning it — a Secretary reads it
off the screen once to hand to the new member)."""

from app import models, security
from app.routers.club_members import PRESIDENT_ROLE


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_any_member_can_list_the_roster(client, make_member):
    member = make_member(role="Member", suffix="100")
    make_member(role="President", suffix="101")
    res = client.get("/club/members", headers=_auth(member))
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_plain_member_cannot_add_a_member(client, make_member):
    member = make_member(role="Member", suffix="102")
    res = client.post(
        "/club/members",
        json={"name": "New Guy", "phone": "256700100102", "email": "", "dob": ""},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_president_can_add_a_member_and_the_returned_pin_matches(client, make_member, db):
    president = make_member(role=PRESIDENT_ROLE, suffix="103")
    res = client.post(
        "/club/members",
        json={"name": "New Guy", "phone": "256700100103", "email": "", "dob": ""},
        headers=_auth(president),
    )
    assert res.status_code == 200
    body = res.json()
    pin = body["pin"]
    new_member_id = body["member"]["id"]

    row = db.get(models.Member, new_member_id)
    assert security.verify_pin(pin, row.pin_hash)

    db.delete(row)
    db.commit()


def test_adding_a_member_with_a_duplicate_phone_is_rejected(client, make_member):
    president = make_member(role=PRESIDENT_ROLE, suffix="104")
    existing = make_member(role="Member", suffix="105")
    res = client.post(
        "/club/members",
        json={"name": "Dupe", "phone": existing.phone, "email": "", "dob": ""},
        headers=_auth(president),
    )
    assert res.status_code == 422


def test_president_can_suspend_a_member(client, make_member):
    president = make_member(role=PRESIDENT_ROLE, suffix="106")
    target = make_member(role="Member", suffix="107")
    res = client.patch(
        f"/club/members/{target.id}", json={"status": "suspended"}, headers=_auth(president)
    )
    assert res.status_code == 200
    assert res.json()["status"] == "suspended"


def test_cannot_manage_a_member_in_a_different_club(client, make_member, db, test_club):
    president = make_member(role=PRESIDENT_ROLE, suffix="108")
    other_club = models.Club(name="Another Club", status="active")
    db.add(other_club)
    db.commit()
    db.refresh(other_club)
    other_member = models.Member(
        club_id=other_club.id,
        member_number="OTHERC-0001",
        name="Not Yours",
        role="Member",
        status="active",
        email="",
        phone="256700100109",
        dob="",
        pin_hash=security.hash_pin("1234"),
    )
    db.add(other_member)
    db.commit()
    db.refresh(other_member)

    res = client.patch(
        f"/club/members/{other_member.id}",
        json={"status": "suspended"},
        headers=_auth(president),
    )
    assert res.status_code == 404

    db.delete(other_member)
    db.delete(other_club)
    db.commit()
