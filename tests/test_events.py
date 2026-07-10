"""Event banner photos persist to real storage (R2), unlike the client-only
state that used to silently vanish on the next reload."""

from app import models, security
from app.storage import delete_gallery_image

_TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


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
