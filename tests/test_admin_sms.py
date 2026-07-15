"""admin_sms.py: system-admin-only gate, /status and /summary report real
config/DB state rather than being hardcoded, and the /summary counts
actually reflect SmsLog rows split by status and by "today" — the reason
this exists is so the admin dashboard doesn't show a made-up number."""

from datetime import datetime, timedelta, timezone

from app import config, models, security
from app.routers import admin_sms


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_sms_endpoints_require_admin_auth(client):
    assert client.get("/admin/sms/status").status_code == 401
    assert client.get("/admin/sms/summary").status_code == 401
    assert client.post("/admin/sms/test", json={"phone": "0772000000"}).status_code == 401


def test_sms_status_reflects_config(client, db):
    res = client.get("/admin/sms/status", headers=_admin_auth(db))
    assert res.status_code == 200
    assert res.json()["enabled"] is config.SMS_ENABLED


def test_sms_test_calls_send_sms_and_reports_its_result(client, db, monkeypatch):
    """Mocks send_sms rather than letting this hit the real Yoola gateway —
    whatever's configured in this environment, a test run must never place
    a real SMS send."""
    calls = []

    def fake_send_sms(phone, message):
        calls.append((phone, message))
        return True

    monkeypatch.setattr(admin_sms, "send_sms", fake_send_sms)

    res = client.post(
        "/admin/sms/test",
        json={"phone": "0772000000", "message": "hello"},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["sent"] is True
    assert body["enabled"] is config.SMS_ENABLED
    assert calls == [("0772000000", "hello")]


def test_sms_summary_counts_reflect_todays_log_rows(client, db):
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    rows = [
        models.SmsLog(phone="256700000001", status="sent", created_at=now),
        models.SmsLog(phone="256700000002", status="sent", created_at=now),
        models.SmsLog(phone="256700000003", status="failed", created_at=now),
        models.SmsLog(phone="256700000004", status="sent", created_at=yesterday),
    ]
    db.add_all(rows)
    db.commit()

    res = client.get("/admin/sms/summary", headers=_admin_auth(db))
    assert res.status_code == 200
    body = res.json()
    assert body["sent_today"] >= 2
    assert body["failed_today"] >= 1
    assert body["sent_total"] >= 3

    for row in rows:
        db.delete(row)
    db.commit()
