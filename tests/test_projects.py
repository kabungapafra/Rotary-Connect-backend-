"""Project photos persist to real storage (R2), same as event banners —
they used to be client-only state that vanished on the next reload."""

from app import models, security
from app.storage import delete_gallery_image

_TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lE"
    "QVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_project_photo_persists_after_reload(client, db, make_member):
    president = make_member(role="President", suffix="070", is_board=True)

    res = client.post(
        "/club/projects",
        json={"name": "Clean Water Borehole", "area": "Water & sanitation", "image": _TINY_PNG},
        headers=_auth(president),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["image"] is not None
    assert body["image"].startswith("http")

    res = client.get("/club/projects", headers=_auth(president))
    reloaded = next(p for p in res.json() if p["id"] == body["id"])
    assert reloaded["image"] == body["image"]

    project = db.get(models.Project, body["id"])
    delete_gallery_image(project.storage_key)
    db.delete(project)
    db.commit()


def test_removing_project_photo_clears_it(client, db, make_member):
    president = make_member(role="President", suffix="071", is_board=True)

    created = client.post(
        "/club/projects",
        json={"name": "Tree Drive", "image": _TINY_PNG},
        headers=_auth(president),
    ).json()

    res = client.patch(
        f"/club/projects/{created['id']}",
        json={"name": "Tree Drive", "image": "__remove__"},
        headers=_auth(president),
    )
    assert res.status_code == 200
    assert res.json()["image"] is None

    db.query(models.Project).filter(models.Project.id == created["id"]).delete()
    db.commit()
