"""A president appointed on a brand-new club must keep the seat through the
upcoming Rotary handover, however close it is.

Without a transition year stamped at creation the field is NULL, which the
leadership sweep reads as "never transitioned" — so a club onboarded in
August with a President-Elect named would have its just-appointed President
demoted to Immediate Past President overnight, and the rest of the board
cleared, before it had served a single day.
"""

import uuid
from datetime import date

from app import models, security
from app.leadership_transition import run_leadership_transitions


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def _create_club(client, db):
    phone = f"25677{uuid.uuid4().int % 10**7:07d}"
    res = client.post(
        "/admin/clubs",
        json={
            "name": f"Transition Test Club {uuid.uuid4().hex[:6]}",
            "president_name": "Newly Appointed",
            "president_phone": phone,
            "club_type": "rotary",
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 200, res.json()
    return db.get(models.Club, res.json()["club"]["id"])


def test_new_club_is_stamped_with_the_current_rotary_year(client, db):
    club = _create_club(client, db)
    assert club.last_leadership_transition_year == date.today().year


def test_sweep_does_not_demote_a_brand_new_president(client, db):
    """Even with a President-Elect present and the handover imminent."""
    club = _create_club(client, db)
    president = (
        db.query(models.Member)
        .filter(models.Member.club_id == club.id, models.Member.role == "Club President")
        .one()
    )
    db.add(models.Member(
        club_id=club.id, member_number=f"PE-{uuid.uuid4().hex[:6]}",
        name="Waiting PE", role="President-Elect", is_board=True, status="active",
        phone=f"25678{uuid.uuid4().int % 10**7:07d}", pin_hash=security.hash_pin("1234"),
    ))
    db.commit()

    # Two days before the handover, and again just after it.
    run_leadership_transitions(db, today=date(date.today().year, 6, 29))
    run_leadership_transitions(db, today=date(date.today().year, 7, 2))
    db.refresh(president)

    assert president.role == "Club President", (
        "a president appointed days before the handover must keep the seat"
    )

    db.query(models.Member).filter(models.Member.club_id == club.id).delete()
    db.delete(club)
    db.commit()
