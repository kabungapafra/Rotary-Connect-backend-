"""Per-club SMS toggle: a club can be otherwise fully active but have its
SMS specifically withheld (e.g. hasn't paid for SMS credits) — separate
from `status`, which suspends the whole club. send_sms/send_bulk_sms check
it whenever a club_id is given, on top of the deployment-wide
YOOLA_API_KEY gate."""

from app import config, models, security
from app.sms import send_sms


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_club_sms_defaults_enabled(client, db, test_club):
    res = client.get("/admin/clubs", headers=_admin_auth(db))
    assert res.status_code == 200
    row = next(c for c in res.json() if c["id"] == test_club.id)
    assert row["sms_enabled"] is True


def test_admin_can_suspend_and_restore_a_clubs_sms(client, db, test_club):
    res = client.patch(
        f"/admin/clubs/{test_club.id}/sms",
        json={"sms_enabled": False},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200
    assert res.json()["sms_enabled"] is False
    db.refresh(test_club)
    assert test_club.sms_enabled is False

    # Suspending SMS is independent of the club's overall active status.
    assert test_club.status == "active"

    res = client.patch(
        f"/admin/clubs/{test_club.id}/sms",
        json={"sms_enabled": True},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200
    assert res.json()["sms_enabled"] is True


def test_sms_toggle_requires_admin_auth(client, test_club):
    res = client.patch(f"/admin/clubs/{test_club.id}/sms", json={"sms_enabled": False})
    assert res.status_code == 401


def test_admin_can_suspend_and_restore_every_clubs_sms_at_once(client, db, test_club):
    others = [
        models.Club(name="Bulk SMS Club A", district="", location="", status="active"),
        models.Club(name="Bulk SMS Club B", district="", location="", status="active"),
    ]
    db.add_all(others)
    db.commit()
    other_ids = [c.id for c in others]

    try:
        res = client.patch(
            "/admin/clubs/sms", json={"sms_enabled": False}, headers=_admin_auth(db)
        )
        assert res.status_code == 200
        body = res.json()
        assert len(body) >= 3
        assert all(row["sms_enabled"] is False for row in body)
        db.refresh(test_club)
        assert test_club.sms_enabled is False

        res = client.patch(
            "/admin/clubs/sms", json={"sms_enabled": True}, headers=_admin_auth(db)
        )
        assert res.status_code == 200
        assert all(row["sms_enabled"] is True for row in res.json())
        db.refresh(test_club)
        assert test_club.sms_enabled is True
    finally:
        db.query(models.Club).filter(models.Club.id.in_(other_ids)).delete(
            synchronize_session=False
        )
        db.commit()


def test_sms_bulk_toggle_requires_admin_auth(client):
    res = client.patch("/admin/clubs/sms", json={"sms_enabled": False})
    assert res.status_code == 401


def test_send_sms_skips_a_club_with_sms_disabled(db, test_club, monkeypatch):
    test_club.sms_enabled = False
    db.commit()
    monkeypatch.setattr(config, "SMS_ENABLED", True)

    calls = []
    monkeypatch.setattr(
        "app.sms.requests.post",
        lambda *a, **k: calls.append((a, k)) or None,
    )

    sent = send_sms("0772000000", "hello", club_id=test_club.id)
    assert sent is False
    assert calls == [], "the gateway must never be called for a club with SMS disabled"


def test_send_sms_still_sends_for_a_club_with_sms_enabled(db, test_club, monkeypatch):
    assert test_club.sms_enabled is True
    monkeypatch.setattr(config, "SMS_ENABLED", True)

    class _FakeResponse:
        status_code = 200
        text = "ok"

    monkeypatch.setattr("app.sms.requests.post", lambda *a, **k: _FakeResponse())

    sent = send_sms("0772000000", "hello", club_id=test_club.id)
    assert sent is True


def test_send_sms_without_a_club_id_is_unaffected_by_any_clubs_setting(
    db, test_club, monkeypatch
):
    """Calls that don't pass club_id (e.g. the admin's own SMS test tool)
    must not be gated by any particular club's setting."""
    test_club.sms_enabled = False
    db.commit()
    monkeypatch.setattr(config, "SMS_ENABLED", True)

    class _FakeResponse:
        status_code = 200
        text = "ok"

    monkeypatch.setattr("app.sms.requests.post", lambda *a, **k: _FakeResponse())

    sent = send_sms("0772000000", "hello")
    assert sent is True
