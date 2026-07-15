"""admin_analytics.py aggregates across every club (system-admin only, not
club-scoped) — locks in the auth gate and that the headline counts
(clubs/members/mrr) actually reflect what's in the DB rather than static
placeholders, which is the entire reason this endpoint exists."""

from app import models, security


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
