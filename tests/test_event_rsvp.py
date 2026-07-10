"""The public RSVP page distinguishes members from guests, and only
requires a club name for a Visiting Rotarian — same categories the
in-app QR check-in already uses."""

from app import models


def test_rsvp_form_offers_member_and_guest_types(client, make_event):
    event = make_event()
    res = client.get(f"/rsvp/{event.id}")
    assert res.status_code == 200
    for label in ["Member", "Prospective member", "Visiting Rotarian", "Friend &amp; family"]:
        assert label in res.text


def test_visiting_rotarian_must_supply_a_club(client, db, make_event):
    event = make_event()
    res = client.post(
        f"/rsvp/{event.id}",
        data={"name": "James Odongo", "phone": "0772000010", "attendee_type": "Visiting Rotarian"},
    )
    assert "Enter a valid name, phone number, and club" in res.text
    assert (
        db.query(models.EventRsvp)
        .filter(models.EventRsvp.event_id == event.id)
        .count()
        == 0
    )


def test_rsvp_stores_attendee_type_and_club(client, db, make_event):
    event = make_event()
    res = client.post(
        f"/rsvp/{event.id}",
        data={
            "name": "James Odongo",
            "phone": "0772000011",
            "attendee_type": "Visiting Rotarian",
            "club_name": "Rotary Club of Naalya",
        },
    )
    assert res.status_code == 200
    assert "See you there" in res.text

    row = db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event.id).first()
    assert row.attendee_type == "Visiting Rotarian"
    assert row.club_name == "Rotary Club of Naalya"

    db.delete(row)
    db.commit()
