"""push.py: registering a device token creates a row, and re-registering
the same token (app reinstall, or a shared front-desk device someone else
signs into) moves it to whoever's current, rather than erroring or
creating a duplicate — the docstring's whole point."""

from app import models, security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_register_device_token_creates_a_row(client, make_member, db):
    member = make_member(suffix="120")
    res = client.post(
        "/push/register",
        json={"token": "fcm-token-120", "platform": "android"},
        headers=_auth(member),
    )
    assert res.status_code == 200
    assert res.json()["ok"] is True

    row = db.query(models.DeviceToken).filter(models.DeviceToken.token == "fcm-token-120").first()
    assert row is not None
    assert row.member_id == member.id
    assert row.platform == "android"

    db.delete(row)
    db.commit()


def test_re_registering_the_same_token_moves_it_to_the_new_member(client, make_member, db):
    first = make_member(suffix="121")
    second = make_member(suffix="122")

    client.post(
        "/push/register", json={"token": "shared-device-token", "platform": "ios"},
        headers=_auth(first),
    )
    res = client.post(
        "/push/register", json={"token": "shared-device-token", "platform": "android"},
        headers=_auth(second),
    )
    assert res.status_code == 200

    rows = (
        db.query(models.DeviceToken)
        .filter(models.DeviceToken.token == "shared-device-token")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].member_id == second.id
    assert rows[0].platform == "android"

    db.delete(rows[0])
    db.commit()


def test_register_device_token_requires_auth(client):
    res = client.post(
        "/push/register", json={"token": "no-auth-token", "platform": "android"}
    )
    assert res.status_code == 401
