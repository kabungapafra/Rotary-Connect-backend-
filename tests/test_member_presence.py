"""Online presence for the admin dashboard's green dot.

Presence is derived from members.last_seen_at, stamped by get_current_member
on ordinary authenticated traffic — the app sends no heartbeats, so this must
keep working without any client change.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app import config, models, security
from app.utils import is_online


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    return {"Authorization": f"Bearer {security.create_admin_access_token(admin.id)}"}


def test_authenticated_request_marks_the_member_online(client, db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    assert member.last_seen_at is None

    token = security.create_access_token(member.id)
    res = client.get("/club/members", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200

    db.refresh(member)
    assert member.last_seen_at is not None
    assert is_online(member.last_seen_at)


def test_member_goes_offline_once_the_window_passes(db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    stale = datetime.now(timezone.utc) - timedelta(minutes=config.ONLINE_WINDOW_MINUTES + 1)
    member.last_seen_at = stale
    db.commit()
    assert is_online(member.last_seen_at) is False


def test_never_seen_member_is_offline():
    assert is_online(None) is False


def test_admin_member_list_reports_online_state(client, db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    member.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    res = client.get(f"/admin/members?search={member.member_number}", headers=_admin_auth(db))
    assert res.status_code == 200
    row = next(m for m in res.json() if m["id"] == member.id)
    assert row["is_online"] is True


def test_club_is_online_when_a_member_is(client, db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    member.last_seen_at = datetime.now(timezone.utc)
    db.commit()

    res = client.get("/admin/clubs", headers=_admin_auth(db))
    assert res.status_code == 200
    row = next(c for c in res.json() if c["id"] == test_club.id)
    assert row["is_online"] is True
    assert row["online_member_count"] >= 1


def test_presence_write_is_throttled(client, db, test_club, make_member):
    """A burst of requests must not become a write per request."""
    member = make_member(suffix=uuid.uuid4().hex[:8])
    token = security.create_access_token(member.id)
    headers = {"Authorization": f"Bearer {token}"}

    client.get("/club/members", headers=headers)
    db.refresh(member)
    first = member.last_seen_at

    client.get("/club/members", headers=headers)
    db.refresh(member)
    assert member.last_seen_at == first, "second request within the throttle rewrote last_seen_at"
