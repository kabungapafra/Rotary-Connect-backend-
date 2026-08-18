"""Admin member lookup — the dashboard shows a member's number next to their
name, so searching that number has to find them. It previously did not,
which made members look missing (and therefore undeletable) even though the
row was there."""

import uuid

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_admin_search_finds_member_by_member_number(client, db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    res = client.get(f"/admin/members?search={member.member_number}", headers=_admin_auth(db))
    assert res.status_code == 200
    assert member.id in [m["id"] for m in res.json()]


def test_admin_search_still_matches_name_and_phone(client, db, test_club, make_member):
    """The member-number match must be an addition, not a replacement."""
    member = make_member(suffix=uuid.uuid4().hex[:8])
    headers = _admin_auth(db)

    by_name = client.get(f"/admin/members?search={member.name}", headers=headers)
    assert by_name.status_code == 200
    assert member.id in [m["id"] for m in by_name.json()]

    by_phone = client.get(f"/admin/members?search={member.phone}", headers=headers)
    assert by_phone.status_code == 200
    assert member.id in [m["id"] for m in by_phone.json()]
