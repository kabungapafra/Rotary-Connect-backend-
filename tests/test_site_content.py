"""Website content the system admin edits: events, news and projects shown
on the public marketing site.

The point of these endpoints is that the site's Events/News/Projects
sections stop being hardcoded arrays in the frontend. So the tests focus on
the split that makes that safe: what the public sees vs what the admin
sees, and that editing needs an admin token.
"""

from datetime import date, timedelta

import pytest

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def cleanup(db):
    """Tests share the app's Postgres (see conftest), so track and remove
    the rows each test creates."""
    created = []
    yield created
    for model, row_id in created:
        row = db.get(model, row_id)
        if row is not None:
            db.delete(row)
    db.commit()


# --- Events ---------------------------------------------------------------


def test_past_and_unpublished_events_stay_off_the_site(client, db, cleanup):
    """The site's Events section is "what's coming up" — a finished event
    left on the page is worse than no section at all, and the admin should
    not have to prune by hand."""
    headers = _admin_auth(db)
    upcoming = client.post(
        "/admin/site/events",
        json={
            "event_date": (date.today() + timedelta(days=7)).isoformat(),
            "title": "Upcoming test fellowship",
            "meta": "7pm",
            "kind": "Weekly",
        },
        headers=headers,
    ).json()
    past = client.post(
        "/admin/site/events",
        json={
            "event_date": (date.today() - timedelta(days=7)).isoformat(),
            "title": "Past test fellowship",
        },
        headers=headers,
    ).json()
    draft = client.post(
        "/admin/site/events",
        json={
            "event_date": (date.today() + timedelta(days=9)).isoformat(),
            "title": "Draft test fellowship",
            "published": False,
        },
        headers=headers,
    ).json()
    for row in (upcoming, past, draft):
        cleanup.append((models.SiteEvent, row["id"]))

    public_titles = [e["title"] for e in client.get("/site/events").json()]
    assert "Upcoming test fellowship" in public_titles
    assert "Past test fellowship" not in public_titles
    assert "Draft test fellowship" not in public_titles

    # The admin list is the opposite: it must show what the site is hiding,
    # or a draft becomes invisible and unrecoverable in the dashboard.
    admin_titles = [e["title"] for e in client.get("/admin/site/events", headers=headers).json()]
    for title in ("Upcoming test fellowship", "Past test fellowship", "Draft test fellowship"):
        assert title in admin_titles


def test_editing_an_event_changes_what_the_site_serves(client, db, cleanup):
    headers = _admin_auth(db)
    created = client.post(
        "/admin/site/events",
        json={
            "event_date": (date.today() + timedelta(days=3)).isoformat(),
            "title": "Original title",
        },
        headers=headers,
    ).json()
    cleanup.append((models.SiteEvent, created["id"]))

    client.put(
        f"/admin/site/events/{created['id']}",
        json={
            "event_date": (date.today() + timedelta(days=3)).isoformat(),
            "title": "Corrected title",
            "meta": "Now at 6pm",
            "kind": "Service",
        },
        headers=headers,
    )
    served = [e for e in client.get("/site/events").json() if e["id"] == created["id"]]
    assert served and served[0]["title"] == "Corrected title"
    assert served[0]["meta"] == "Now at 6pm"


# --- News -----------------------------------------------------------------


def test_old_news_stays_published_unlike_events(client, db, cleanup):
    """News is a running archive; unlike events, age is not a reason to
    drop an item off the page."""
    headers = _admin_auth(db)
    old = client.post(
        "/admin/site/news",
        json={
            "published_on": (date.today() - timedelta(days=400)).isoformat(),
            "title": "An old announcement",
            "body": "Still worth reading.",
        },
        headers=headers,
    ).json()
    cleanup.append((models.SiteNews, old["id"]))

    assert old["id"] in [n["id"] for n in client.get("/site/news").json()]


# --- Projects -------------------------------------------------------------


def test_project_progress_is_clamped_to_a_drawable_percentage(client, db, cleanup):
    """The site renders this as a progress bar width — an out-of-range
    value would paint a bar that overflows or inverts."""
    headers = _admin_auth(db)
    over = client.post(
        "/admin/site/projects",
        json={"title": "Over-reported project", "progress_percent": 250},
        headers=headers,
    ).json()
    under = client.post(
        "/admin/site/projects",
        json={"title": "Negative project", "progress_percent": -40},
        headers=headers,
    ).json()
    cleanup.append((models.SiteProject, over["id"]))
    cleanup.append((models.SiteProject, under["id"]))

    assert over["progress_percent"] == 100
    assert under["progress_percent"] == 0


# --- Access ---------------------------------------------------------------


@pytest.mark.parametrize("path", ["/admin/site/events", "/admin/site/news", "/admin/site/projects"])
def test_site_content_is_not_editable_without_an_admin_token(client, path):
    """Anonymous reads are the whole point of the public routes, but a
    stranger must not be able to publish to the company's own website."""
    assert client.get(path).status_code == 401
    assert client.post(path, json={"title": "Injected"}).status_code == 401


@pytest.mark.parametrize("path", ["/site/events", "/site/news", "/site/projects"])
def test_public_content_is_readable_anonymously(client, path):
    """The website calls these with no credentials at all."""
    assert client.get(path).status_code == 200


# --- Project photos -------------------------------------------------------

# A real 2x2 PNG: the upload path decodes and re-encodes it, so a
# hand-waved placeholder string would fail in base64 rather than in the
# behaviour under test.
_PIXEL_PNG = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGM8IRfFwMDAxMDAwMDAAAAPmAFE4knAJgAAAABJRU5ErkJggg=="
)


def test_an_unchanged_photo_is_not_recompressed_on_every_save(client, db, cleanup):
    """The stored photo is itself a data URL, so an echoed-back value looks
    exactly like a fresh upload. Re-shrinking it on each save would degrade
    the image a little more every time the admin fixed a typo."""
    created = client.post(
        "/admin/site/projects",
        json={"title": "Photo project", "photo": _PIXEL_PNG},
        headers=_admin_auth(db),
    ).json()
    cleanup.append((models.SiteProject, created["id"]))
    stored = created["photo"]
    assert stored is not None and stored.startswith("data:")

    echoed = client.put(
        f"/admin/site/projects/{created['id']}",
        json={"title": "Renamed project", "photo": stored},
        headers=_admin_auth(db),
    ).json()
    assert echoed["photo"] == stored, "unchanged photo should be byte-identical"
    assert echoed["title"] == "Renamed project"


def test_omitting_photo_leaves_the_existing_one_alone(client, db, cleanup):
    """Editing a project without touching its photo must not wipe it —
    the dashboard omits the field when the admin didn't pick a new file."""
    created = client.post(
        "/admin/site/projects",
        json={"title": "Keeps its photo", "photo": _PIXEL_PNG},
        headers=_admin_auth(db),
    ).json()
    cleanup.append((models.SiteProject, created["id"]))

    edited = client.put(
        f"/admin/site/projects/{created['id']}",
        json={"title": "Still keeps its photo"},
        headers=_admin_auth(db),
    ).json()
    assert edited["photo"] == created["photo"]


def test_an_explicit_null_removes_the_photo(client, db, cleanup):
    """Distinct from omitting it: null is the Remove button."""
    created = client.post(
        "/admin/site/projects",
        json={"title": "Loses its photo", "photo": _PIXEL_PNG},
        headers=_admin_auth(db),
    ).json()
    cleanup.append((models.SiteProject, created["id"]))

    cleared = client.put(
        f"/admin/site/projects/{created['id']}",
        json={"title": "Loses its photo", "photo": None},
        headers=_admin_auth(db),
    ).json()
    assert cleared["photo"] is None


def test_the_stored_photo_is_shrunk_not_kept_at_full_size(client, db, cleanup):
    """These bytes live in Postgres, so an unshrunk phone-camera original
    would be a multi-megabyte row on every project."""
    import base64
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (2400, 1800), (23, 69, 143)).save(buf, "JPEG", quality=95)
    original = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    created = client.post(
        "/admin/site/projects",
        json={"title": "Big photo project", "photo": original},
        headers=_admin_auth(db),
    ).json()
    cleanup.append((models.SiteProject, created["id"]))
    assert len(created["photo"]) < len(original) / 2


def test_a_non_image_payload_is_rejected(client, db):
    """The stored data URL is served straight back to browsers, so bytes
    that are not a real raster image (an SVG carrying a script, say) must
    never make it into the column."""
    res = client.post(
        "/admin/site/projects",
        json={
            "title": "Not an image",
            "photo": "data:image/svg+xml;base64,"
            + "PHN2Zz48c2NyaXB0PmFsZXJ0KDEpPC9zY3JpcHQ+PC9zdmc+",
        },
        headers=_admin_auth(db),
    )
    assert res.status_code == 422, res.text


def test_a_project_without_a_photo_still_serves(client, db, cleanup):
    """The site falls back to its placeholder, so a photo must stay
    optional — requiring one would block publishing."""
    created = client.post(
        "/admin/site/projects",
        json={"title": "Photoless project", "photo_caption": "coming soon"},
        headers=_admin_auth(db),
    ).json()
    cleanup.append((models.SiteProject, created["id"]))
    assert created["photo"] is None

    served = [p for p in client.get("/site/projects").json() if p["id"] == created["id"]]
    assert served and served[0]["photo"] is None
