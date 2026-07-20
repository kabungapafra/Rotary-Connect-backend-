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
    phone = f"2567{uuid.uuid4().hex[:8]}"
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
