"""System Health page backend: member-side problems (failed PINs, PIN
resets) must be recorded with the member and club named, and the
/admin/analytics/monitoring endpoint must return them along with the
slow-request log — this is the only place an admin can see that a member
is struggling to sign in or that the API has been slow, so a silent
regression here removes real visibility."""

import uuid

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def _events_for(db, identifier):
    return (
        db.query(models.MemberEvent)
        .filter(models.MemberEvent.identifier == identifier)
        .all()
    )


def test_failed_pin_records_event_with_member_and_club(client, db, test_club, make_member):
    member = make_member(pin="4321", suffix=uuid.uuid4().hex[:8])
    res = client.post("/auth/login", json={"identifier": member.member_number, "pin": "0000"})
    assert res.status_code == 401

    db.expire_all()
    events = _events_for(db, member.member_number)
    assert len(events) == 1
    assert events[0].kind == "login_failed"
    assert events[0].member_name == member.name
    assert events[0].club_name == test_club.name
    assert "Wrong PIN" in events[0].detail
    db.query(models.MemberEvent).filter(models.MemberEvent.id == events[0].id).delete()
    db.commit()


def test_unknown_identifier_records_event_without_member(client, db):
    bogus = f"NOPE-{uuid.uuid4().hex[:8]}"
    res = client.post("/auth/login", json={"identifier": bogus, "pin": "0000"})
    assert res.status_code == 401

    db.expire_all()
    events = _events_for(db, bogus)
    assert len(events) == 1
    assert events[0].kind == "login_failed"
    assert events[0].member_name is None
    assert events[0].club_name is None
    db.query(models.MemberEvent).filter(models.MemberEvent.id == events[0].id).delete()
    db.commit()


def test_monitoring_endpoint_returns_events_and_slow_requests(client, db, test_club, make_member):
    member = make_member(pin="4321", suffix=uuid.uuid4().hex[:8])
    client.post("/auth/login", json={"identifier": member.member_number, "pin": "9999"})
    slow = models.SlowRequest(method="GET", path="/club/me/summary", status_code=200, duration_ms=4200)
    db.add(slow)
    db.commit()

    res = client.get("/admin/analytics/monitoring", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    body = res.json()

    matching = [e for e in body["member_events"] if e["identifier"] == member.member_number]
    assert matching, "the failed login must appear in the monitoring feed"
    assert matching[0]["member_name"] == member.name
    assert matching[0]["club_name"] == test_club.name

    slow_matching = [s for s in body["slow_requests"] if s["id"] == slow.id]
    assert slow_matching and slow_matching[0]["duration_ms"] == 4200
    assert body["events_today"] >= 1
    assert body["slow_today"] >= 1

    db.query(models.MemberEvent).filter(
        models.MemberEvent.identifier == member.member_number
    ).delete()
    db.query(models.SlowRequest).filter(models.SlowRequest.id == slow.id).delete()
    db.commit()


def test_monitoring_requires_admin_auth(client):
    assert client.get("/admin/analytics/monitoring").status_code in (401, 403)


def test_slow_request_middleware_records_when_over_threshold(client, db):
    import app.main as app_main

    original = app_main.SLOW_REQUEST_MS
    app_main.SLOW_REQUEST_MS = 0  # every request counts as slow
    try:
        client.get("/health")
    finally:
        app_main.SLOW_REQUEST_MS = original

    db.expire_all()
    rows = db.query(models.SlowRequest).filter(models.SlowRequest.path == "/health").all()
    assert rows, "middleware must log a request breaching the threshold"
    db.query(models.SlowRequest).filter(models.SlowRequest.path == "/health").delete()
    db.commit()
