"""POST /site/join-requests: the marketing site's "request to join" form.

This is the only unauthenticated write path in the API that is open to the
whole public internet, so the tests here are mostly about what a stranger
can and cannot do with it: it must not create clubs, must not accept an
unbounded payload, and must not be readable without an admin token.
"""

import uuid

import pytest

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def _payload(**overrides):
    # Stays inside the blocked placeholder range so a submission can never
    # text a real Ugandan number, same rule as the other suites.
    payload = {
        "club_name": f"Rotary Club of Testville {uuid.uuid4().hex[:6]}",
        "club_type": "Rotaract",
        "district": "9213",
        "location": "Kampala, Uganda",
        "members_count": 40,
        "contact_name": "Jane Doe",
        "contact_role": "President",
        "phone": f"256700000{uuid.uuid4().int % 1000:03d}",
        "email": "jane@example.org",
        "problems": ["Attendance tracking", "Dues & treasury"],
        "notes": "Keen to start next term",
    }
    payload.update(overrides)
    return payload


@pytest.fixture()
def cleanup_join_requests(db):
    """Tests share the app's own Postgres (see conftest), so each test
    removes the rows it created rather than truncating the table."""
    created = []
    yield created
    for request_id in created:
        row = db.get(models.JoinRequest, request_id)
        if row is not None:
            db.delete(row)
    db.commit()


def test_a_submission_is_stored_for_the_admin_to_triage(
    client, db, cleanup_join_requests
):
    res = client.post("/site/join-requests", json=_payload())
    assert res.status_code == 201, res.text
    body = res.json()
    cleanup_join_requests.append(body["id"])

    # "new" is what puts it in the admin's unactioned queue; a submission
    # that landed as anything else would be silently skipped.
    assert body["status"] == "new"
    # Multi-select is flattened for display, not dropped.
    assert body["problems"] == "Attendance tracking, Dues & treasury"
    # Normalised to Club.club_type's vocabulary so onboarding can copy it
    # across without translating "Rotaract" -> "rotaract" itself.
    assert body["club_type"] == "rotaract"


def test_submitting_does_not_create_a_club(client, db, cleanup_join_requests):
    """The whole reason this is a separate table: anyone on the internet can
    POST here, and clubs are what the dashboard bills against."""
    payload = _payload()
    before = db.query(models.Club).count()

    res = client.post("/site/join-requests", json=payload)
    assert res.status_code == 201, res.text
    cleanup_join_requests.append(res.json()["id"])

    assert db.query(models.Club).count() == before
    assert (
        db.query(models.Club).filter(models.Club.name == payload["club_name"]).first()
        is None
    )


def test_an_oversized_logo_is_rejected_rather_than_stored(
    client, cleanup_join_requests
):
    """The logo is a caller-supplied data URL on an open endpoint — without
    a cap, one POST can write an arbitrarily large row."""
    res = client.post(
        "/site/join-requests",
        json=_payload(logo="data:image/png;base64," + "A" * 900_000),
    )
    assert res.status_code == 422, res.text


def test_an_invalid_phone_is_rejected(client):
    """The phone number is the only way back to the club — a request that
    stored an unusable one would be a lead the admin can't action."""
    res = client.post("/site/join-requests", json=_payload(phone="12345"))
    assert res.status_code == 422, res.text


def test_the_queue_is_not_readable_without_an_admin_token(client):
    """Submissions carry a named contact's phone, email and date of birth."""
    assert client.get("/admin/join-requests").status_code == 401


def test_admin_can_read_and_triage_a_submission(client, db, cleanup_join_requests):
    created = client.post("/site/join-requests", json=_payload())
    request_id = created.json()["id"]
    cleanup_join_requests.append(request_id)

    listed = client.get("/admin/join-requests", headers=_admin_auth(db))
    assert listed.status_code == 200, listed.text
    assert request_id in [r["id"] for r in listed.json()]

    moved = client.patch(
        f"/admin/join-requests/{request_id}/status",
        json={"status": "contacted"},
        headers=_admin_auth(db),
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "contacted"

    # Triage drives the dashboard's filter, so an unknown status would put
    # a live request into a bucket the admin never looks at.
    bad = client.patch(
        f"/admin/join-requests/{request_id}/status",
        json={"status": "archived"},
        headers=_admin_auth(db),
    )
    assert bad.status_code == 422, bad.text
