"""Project photos persist to real storage (R2), same as event banners —
they used to be client-only state that vanished on the next reload."""

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


def test_project_report_fields_round_trip(client, db, make_member):
    president = make_member(role="President", suffix="072", is_board=True)

    res = client.post(
        "/club/projects",
        json={
            "name": "Borehole Drive",
            "area_of_focus": "Water, Sanitation, and Hygiene",
            "beneficiaries_reached": 500,
        },
        headers=_auth(president),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["area_of_focus"] == "Water, Sanitation, and Hygiene"
    assert body["beneficiaries_reached"] == 500

    db.query(models.Project).filter(models.Project.id == body["id"]).delete()
    db.commit()


def test_posting_a_progress_update_advances_pct_and_is_returned_in_history(
    client, db, make_member
):
    """The lightweight "what have you done" flow: posting an update sets
    the project's current pct and shows up in its update history — without
    touching name/area/deadline, which stay whatever they were created
    with."""
    president = make_member(role="President", suffix="074", is_board=True)
    created = client.post(
        "/club/projects",
        json={"name": "Borehole Drive", "area": "Kito Village", "pct": 10},
        headers=_auth(president),
    ).json()

    res = client.post(
        f"/club/projects/{created['id']}/updates",
        json={"pct": 45, "note": "Drilling rig arrived on site."},
        headers=_auth(president),
    )
    assert res.status_code == 200
    body = res.json()
    assert body["pct"] == 45
    # Core details untouched by an update.
    assert body["name"] == "Borehole Drive"
    assert body["area"] == "Kito Village"
    assert len(body["updates"]) == 1
    assert body["updates"][0]["note"] == "Drilling rig arrived on site."
    assert body["updates"][0]["author_name"] == president.name

    res = client.get("/club/projects", headers=_auth(president))
    reloaded = next(p for p in res.json() if p["id"] == created["id"])
    assert reloaded["pct"] == 45
    assert len(reloaded["updates"]) == 1

    db.query(models.ProjectUpdate).filter(
        models.ProjectUpdate.project_id == created["id"]
    ).delete()
    db.query(models.Project).filter(models.Project.id == created["id"]).delete()
    db.commit()


def test_plain_member_cannot_post_a_progress_update(client, db, make_member):
    president = make_member(role="President", suffix="075", is_board=True)
    member = make_member(role="Member", suffix="076")
    created = client.post(
        "/club/projects", json={"name": "Tree Drive"}, headers=_auth(president)
    ).json()

    res = client.post(
        f"/club/projects/{created['id']}/updates",
        json={"pct": 50, "note": "Trying anyway"},
        headers=_auth(member),
    )
    assert res.status_code == 403

    db.query(models.Project).filter(models.Project.id == created["id"]).delete()
    db.commit()


def test_deleting_a_project_with_updates_succeeds(client, db, make_member):
    """ProjectUpdate holds a non-nullable FK into projects — deleting a
    project that had progress updates logged used to trip that constraint."""
    president = make_member(role="President", suffix="077", is_board=True)
    created = client.post(
        "/club/projects", json={"name": "Charity Walk"}, headers=_auth(president)
    ).json()
    client.post(
        f"/club/projects/{created['id']}/updates",
        json={"pct": 30, "note": "Route mapped out."},
        headers=_auth(president),
    )

    res = client.delete(f"/club/projects/{created['id']}", headers=_auth(president))
    assert res.status_code == 200
    db.expire_all()
    assert db.get(models.Project, created["id"]) is None
    assert (
        db.query(models.ProjectUpdate)
        .filter(models.ProjectUpdate.project_id == created["id"])
        .count()
        == 0
    )


def test_unrecognized_area_of_focus_is_rejected(client, make_member):
    president = make_member(role="President", suffix="073", is_board=True)
    res = client.post(
        "/club/projects",
        json={"name": "Mystery Project", "area_of_focus": "Made Up Cause"},
        headers=_auth(president),
    )
    assert res.status_code == 422


@_requires_r2
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


@_requires_r2
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
