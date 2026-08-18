"""A club's logo could only ever be supplied at onboarding, so a club created
without one had no way to get one and the dashboard could only ever show its
initials."""

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    return {"Authorization": f"Bearer {security.create_admin_access_token(admin.id)}"}


# 1x1 transparent PNG
_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "YPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)


def test_logo_can_be_set_after_the_club_exists(client, db, test_club):
    assert test_club.logo is None
    res = client.patch(
        f"/admin/clubs/{test_club.id}/logo", json={"logo": _PNG}, headers=_admin_auth(db)
    )
    assert res.status_code == 200, res.json()
    db.refresh(test_club)
    assert test_club.logo is not None
    assert res.json()["logo"] is not None


def test_logo_can_be_cleared(client, db, test_club):
    client.patch(
        f"/admin/clubs/{test_club.id}/logo", json={"logo": _PNG}, headers=_admin_auth(db)
    )
    res = client.patch(
        f"/admin/clubs/{test_club.id}/logo", json={"logo": None}, headers=_admin_auth(db)
    )
    assert res.status_code == 200
    db.refresh(test_club)
    assert test_club.logo is None
    assert test_club.logo_storage_key is None


def test_setting_a_logo_leaves_the_rest_of_the_club_alone(client, db, test_club):
    """The endpoint must touch the logo and nothing else."""
    before = (test_club.name, test_club.status, test_club.club_type, test_club.sms_enabled)
    client.patch(
        f"/admin/clubs/{test_club.id}/logo", json={"logo": _PNG}, headers=_admin_auth(db)
    )
    db.refresh(test_club)
    assert (test_club.name, test_club.status, test_club.club_type, test_club.sms_enabled) == before


def test_unknown_club_is_404(client, db):
    res = client.patch("/admin/clubs/9999999/logo", json={"logo": None}, headers=_admin_auth(db))
    assert res.status_code == 404
