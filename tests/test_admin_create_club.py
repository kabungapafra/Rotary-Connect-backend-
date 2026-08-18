"""POST /admin/clubs: the auto-created Club President's date of birth is
stored from the wizard's input rather than silently dropped — it was
previously hardcoded to "" regardless of what the admin submitted, which
meant that club's president could never get an automated birthday text."""

import uuid

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_president_dob_is_saved_from_the_wizard(client, db):
    # Must be a real Ugandan number shape: the endpoint normalises and
    # rejects anything that is not 256 + 9 digits.
    # Must stay inside the blocked placeholder range: this previously
    # generated live Ugandan numbers and the endpoint really texted them.
    phone = f"256700000{uuid.uuid4().int % 1000:03d}"
    res = client.post(
        "/admin/clubs",
        json={
            "name": "DOB Test Club",
            "president_name": "Jane Doe",
            "president_email": "",
            "president_phone": phone,
            "president_dob": "08 Jul 1990",
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    body = res.json()
    president_number = body["president"]["member_number"]

    member = db.query(models.Member).filter(models.Member.member_number == president_number).first()
    assert member.dob == "08 Jul 1990"

    club_id = body["club"]["id"]
    db.delete(member)
    db.commit()
    db.query(models.Club).filter(models.Club.id == club_id).delete()
    db.commit()


def test_charter_date_is_saved_at_onboarding(client, db):
    res = client.post(
        "/admin/clubs",
        json={"name": "Charter Test Club", "charter_date": "14 Jun 2018"},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["club"]["charter_date"] == "14 Jun 2018"

    club_id = body["club"]["id"]
    club = db.get(models.Club, club_id)
    assert club.charter_date.isoformat() == "2018-06-14"
    db.delete(club)
    db.commit()


def test_charter_date_can_be_set_and_cleared_after_onboarding(client, db):
    # Onboarding is the only place ClubCreate.charter_date is set — this is
    # the sole path for a club that predates the field (or was created
    # without it) to ever get one.
    res = client.post("/admin/clubs", json={"name": "Charter Patch Club"}, headers=_admin_auth(db))
    club_id = res.json()["club"]["id"]
    assert res.json()["club"]["charter_date"] is None

    res = client.patch(
        f"/admin/clubs/{club_id}/charter-date",
        json={"charter_date": "01 Jan 2020"},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    assert res.json()["charter_date"] == "01 Jan 2020"

    res = client.patch(
        f"/admin/clubs/{club_id}/charter-date",
        json={"charter_date": None},
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    assert res.json()["charter_date"] is None

    db.query(models.Club).filter(models.Club.id == club_id).delete()
    db.commit()
