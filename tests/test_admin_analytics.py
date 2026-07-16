"""admin_analytics.py aggregates across every club (system-admin only, not
club-scoped) — locks in the auth gate and that the headline counts
(clubs/members/mrr) actually reflect what's in the DB rather than static
placeholders, which is the entire reason this endpoint exists.

Also covers the whole unhandled-error pipeline end to end: no third-party
error tracker is configured, so main.py's global exception handler
persisting to ErrorLog (and this router's GET /errors reading it back) is
the *only* place an unhandled exception is visible at all — this must
actually work, not just look plausible."""

from fastapi.testclient import TestClient

from app import models, security
from app.main import app
from app.routers import admin_analytics


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_analytics_requires_admin_auth(client):
    res = client.get("/admin/analytics")
    assert res.status_code == 401


def test_member_token_cannot_access_admin_analytics(client, make_member):
    member = make_member(suffix="130")
    token = security.create_access_token(member.id)
    res = client.get("/admin/analytics", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401


def test_analytics_counts_reflect_real_data(client, db, test_club, make_member):
    test_club.fee_amount = 50_000
    db.commit()
    make_member(role="Member", suffix="131")
    make_member(role="Member", suffix="132")

    res = client.get("/admin/analytics", headers=_admin_auth(db))
    assert res.status_code == 200
    body = res.json()

    assert body["total_clubs"] >= 1
    assert body["active_members"] >= 2
    assert "UGX" in body["mrr_formatted"]
    assert len(body["payment_legend"]) == 3
    assert len(body["attendance_values"]) == 6


def test_analytics_includes_per_club_attendance_and_engagement(
    client, db, test_club, make_member
):
    """The Analytics page's per-club breakdown: a club whose recent meeting
    had every member check in must appear with a real attendance percent,
    and the 30-day engagement counters must reflect actual check-ins —
    otherwise the admin's 'which clubs are alive' view silently lies."""
    from datetime import date

    member = make_member(role="Member", suffix="141")
    test_club.members_count = 1
    db.commit()

    meeting = models.Meeting(club_id=test_club.id, date=date.today(), name="Weekly")
    db.add(meeting)
    db.flush()
    checkin = models.CheckIn(member_id=member.id, meeting_id=meeting.id)
    db.add(checkin)
    db.commit()

    res = client.get("/admin/analytics", headers=_admin_auth(db))
    assert res.status_code == 200
    body = res.json()

    rows = [c for c in body["club_attendance"] if c["club_name"] == test_club.name]
    assert rows, "the club must appear in the per-club attendance list"
    assert rows[0]["attendance_percent"] == 100
    assert rows[0]["meetings_held"] >= 1
    assert body["engagement"]["checkins_30d"] >= 1

    db.delete(checkin)
    db.delete(meeting)
    db.commit()


def test_errors_endpoint_requires_admin_auth(client):
    assert client.get("/admin/analytics/errors").status_code == 401


def test_unhandled_exception_is_logged_and_visible_via_errors_endpoint(
    client, db, test_club, monkeypatch
):
    def _boom(_next_due_date):
        raise ValueError("boom: simulated failure for error-log coverage")

    monkeypatch.setattr(admin_analytics, "compute_payment_status", _boom)

    # The shared `client` fixture re-raises server exceptions (so ordinary
    # test failures show a real traceback) — this is the one test that
    # deliberately triggers a 500 and needs to see the response body a real
    # HTTP client would get. Deliberately not entered as a context manager:
    # that would re-run the app's startup lifespan (already run once by the
    # session-scoped `client` fixture) and crash on starting the shared
    # scheduler singleton a second time. Routes don't need a second
    # startup — they're bound to the same shared `app` either way.
    unguarded_client = TestClient(app, raise_server_exceptions=False)
    res = unguarded_client.get("/admin/analytics", headers=_admin_auth(db))
    assert res.status_code == 500
    assert res.json() == {"detail": "Internal server error"}

    # monkeypatch has already reverted compute_payment_status by the time
    # this second, unrelated request runs.
    res = client.get("/admin/analytics/errors", headers=_admin_auth(db))
    assert res.status_code == 200
    errors = res.json()
    assert any(
        e["exception_type"] == "ValueError" and "boom" in e["message"] for e in errors
    ), errors

    db.query(models.ErrorLog).filter(
        models.ErrorLog.exception_type == "ValueError",
        models.ErrorLog.message.like("boom:%"),
    ).delete(synchronize_session=False)
    db.commit()
