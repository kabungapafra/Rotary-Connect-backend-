"""POST /admin/members: lets the system admin add a member to any club
directly (bootstrapping a club whose auto-created president was removed,
or adding a member without routing through that club's own president) —
a duplicate phone is rejected, an unknown club 404s, and the returned
one-time PIN actually matches what got hashed into the new member's row."""

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_admin_can_add_a_member_and_the_returned_pin_matches(client, db, test_club):
    res = client.post(
        "/admin/members",
        json={
            "club_id": test_club.id,
            "name": "New Guy",
            "phone": "256700200201",
            "email": "",
            "dob": "",
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 200
    body = res.json()
    pin = body["pin"]
    new_member_id = body["member"]["id"]

    row = db.get(models.Member, new_member_id)
    assert row.club_id == test_club.id
    assert security.verify_pin(pin, row.pin_hash)

    db.delete(row)
    db.commit()


def test_admin_adding_a_member_with_a_duplicate_phone_is_rejected(client, db, test_club, make_member):
    existing = make_member(role="Member", suffix="202")
    res = client.post(
        "/admin/members",
        json={
            "club_id": test_club.id,
            "name": "Dupe",
            "phone": existing.phone,
            "email": "",
            "dob": "",
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 422


def test_admin_adding_a_member_to_an_unknown_club_404s(client, db):
    res = client.post(
        "/admin/members",
        json={"club_id": 9_999_999, "name": "Nobody", "phone": "256700200203", "email": "", "dob": ""},
        headers=_admin_auth(db),
    )
    assert res.status_code == 404
