"""Event banner photos persist to real storage (R2), unlike the client-only
state that used to silently vanish on the next reload."""

import pytest

from app import models, security, storage
from app.storage import delete_gallery_image

# These tests upload to the real R2 bucket; CI runs without R2 credentials,
# where the endpoint (correctly) hard-fails rather than fake a success.
_requires_r2 = pytest.mark.skipif(
    storage._client is None, reason="R2 storage not configured"
)

_TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


@_requires_r2
def test_event_photo_persists_after_reload(client, db, make_member):
    president = make_member(role="President", suffix="060", is_board=True)

    res = client.post(
        "/club/events",
        json={"dow": "WED", "name": "Health Camp", "meta": "9:00 AM - Kira", "image": _TINY_PNG},
        headers=_auth(president),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["image"] is not None
    assert body["image"].startswith("http")

    # Simulates the app reloading the events list — the photo must still
    # be there, unlike the old client-only state that reset on refetch.
    res = client.get("/club/events", headers=_auth(president))
    reloaded = next(e for e in res.json() if e["id"] == body["id"])
    assert reloaded["image"] == body["image"]

    event = db.get(models.Event, body["id"])
    delete_gallery_image(event.storage_key)
    db.delete(event)
    db.commit()


@_requires_r2
def test_removing_event_photo_clears_it(client, db, make_member):
    president = make_member(role="President", suffix="061", is_board=True)

    created = client.post(
        "/club/events",
        json={"dow": "SAT", "name": "Tree Drive", "meta": "", "image": _TINY_PNG},
        headers=_auth(president),
    ).json()

    res = client.patch(
        f"/club/events/{created['id']}",
        json={"dow": "SAT", "name": "Tree Drive", "meta": "", "image": "__remove__"},
        headers=_auth(president),
    )
    assert res.status_code == 200
    assert res.json()["image"] is None

    db.query(models.Event).filter(models.Event.id == created["id"]).delete()
    db.commit()


def test_deleting_an_event_with_web_rsvps_succeeds(client, db, make_member, make_event):
    """EventRsvp rows hold a non-nullable FK into events — deleting an
    event that anyone had registered for via the web form used to trip the
    constraint and fail, leaving the event undeletable."""
    president = make_member(role="President", suffix="062", is_board=True)
    event = make_event(dow="SAT", name="Charity Gala", meta="6:00 PM - Hall")
    # Captured now — after the delete, reading .id off the expired, gone
    # instance raises ObjectDeletedError instead of returning the value.
    event_id = event.id
    db.add(
        models.EventRsvp(
            event_id=event_id,
            name="Web Registrant",
            phone="256700555666",
            attendee_type="Friend & family",
        )
    )
    db.commit()

    res = client.delete(f"/club/events/{event_id}", headers=_auth(president))
    assert res.status_code == 200
    # The delete happened in the API's own session — drop this session's
    # cached instances before re-reading.
    db.expire_all()
    assert db.get(models.Event, event_id) is None
    assert (
        db.query(models.EventRsvp).filter(models.EventRsvp.event_id == event_id).count()
        == 0
    )


def test_ended_event_cannot_be_edited(client, db, make_member, make_event):
    """An event that already ended (today's occurrence, past its end
    time) locks against edits — its historical name/time/venue shouldn't
    be rewritten after the fact."""
    from datetime import date

    president = make_member(role="President", suffix="078", is_board=True)
    todays_dow = date.today().strftime("%a").upper()
    event = make_event(dow=todays_dow, meta="12:00 AM to 12:01 AM · Hall")

    res = client.patch(
        f"/club/events/{event.id}",
        json={"dow": todays_dow, "name": "Renamed", "meta": "6:00 PM - Hall"},
        headers=_auth(president),
    )
    assert res.status_code == 422
    assert "ended" in res.json()["detail"].lower()

    res = client.get("/club/events", headers=_auth(president))
    row = next(e for e in res.json() if e["id"] == event.id)
    assert row["editable"] is False
    # Unaffected by the rejected edit.
    assert row["name"] == "Pytest Fellowship"
