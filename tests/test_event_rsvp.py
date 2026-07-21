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


def test_rescanning_the_same_event_skips_straight_to_confirmation(client, db, make_event):
    # A visitor who already RSVP'd is recognized on a second scan (of the
    # same event's QR) via the cookie set on their first submission, and
    # gets a confirmation instead of a blank form to refill.
    event = make_event()
    client.post(
        f"/rsvp/{event.id}",
        data={"name": "Grace Nabirye", "phone": "0772000012", "attendee_type": "Member"},
    )
    res = client.get(f"/rsvp/{event.id}")
    assert res.status_code == 200
    assert "already registered" in res.text
    assert "Grace" in res.text
    assert "<form" not in res.text

    row = db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event.id).first()
    db.delete(row)
    db.commit()


def test_rescanning_a_different_event_prefills_but_still_shows_the_form(client, db, make_event):
    # A returning visitor's name/phone gets carried over to a *new* event
    # they haven't RSVP'd to yet, but they still have to confirm/submit.
    first_event = make_event()
    client.post(
        f"/rsvp/{first_event.id}",
        data={"name": "Peter Okello", "phone": "0772000013", "attendee_type": "Member"},
    )
    second_event = make_event()
    res = client.get(f"/rsvp/{second_event.id}")
    assert res.status_code == 200
    assert "<form" in res.text
    assert 'value="Peter Okello"' in res.text
    assert 'value="256772000013"' in res.text

    row = db.query(models.EventRsvp).filter(models.EventRsvp.event_id == first_event.id).first()
    db.delete(row)
    db.commit()


def test_resubmitting_the_same_event_and_phone_does_not_duplicate(client, db, make_event):
    event = make_event()
    data = {"name": "Sarah Nakato", "phone": "0772000014", "attendee_type": "Member"}
    client.post(f"/rsvp/{event.id}", data=data)
    client.cookies.clear()  # simulate a second, separate scan with no cookie
    res = client.post(f"/rsvp/{event.id}", data=data)
    assert res.status_code == 200

    rows = db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event.id).all()
    assert len(rows) == 1

    db.delete(rows[0])
    db.commit()
