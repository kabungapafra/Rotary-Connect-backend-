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
