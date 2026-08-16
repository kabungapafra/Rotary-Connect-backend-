"""Coverage for GET /admin/clubs/{id}/overview — the single call behind the
club management screen. The figures on that screen drive real decisions
(whether a club is overusing SMS, whether its members are actually active,
what's been breaking for them), so each one is asserted against rows that
belong to *another* club too: a metric that silently aggregated across all
clubs would still look plausible on screen."""

import uuid

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    return {"Authorization": f"Bearer {security.create_admin_access_token(admin.id)}"}


def _other_club(db):
    club = models.Club(
        name=f"Decoy Club {uuid.uuid4().hex[:6]}",
        district="", location="", status="active",
    )
    db.add(club)
    db.commit()
    db.refresh(club)
    return club


def test_overview_counts_only_the_requested_club(client, db, test_club, make_member):
    """Every usage figure must be scoped to the club being viewed. Rows for
    a second club are seeded alongside so a missing WHERE clause fails here
    rather than quietly inflating a number on the dashboard."""
    decoy = _other_club(db)
    member = make_member(suffix=uuid.uuid4().hex[:8])

    db.add_all([
        models.SmsLog(phone="256700000001", status="sent", club_id=test_club.id),
        models.SmsLog(phone="256700000002", status="sent", club_id=test_club.id),
        models.SmsLog(phone="256700000003", status="failed", club_id=test_club.id),
        models.SmsLog(phone="256700000004", status="sent", club_id=decoy.id),
        # No club at all — an admin-initiated send. Must not land on any club.
        models.SmsLog(phone="256700000005", status="sent", club_id=None),
        models.GalleryPhoto(
            club_id=test_club.id, album="General", image="https://r2/a.webp",
            storage_key="gallery/a.webp", size_bytes=1000, uploaded_by=member.id,
        ),
        models.GalleryPhoto(
            club_id=decoy.id, album="General", image="https://r2/b.webp",
            storage_key="gallery/b.webp", size_bytes=9_999_999, uploaded_by=member.id,
        ),
        models.ClubDocument(
            club_id=test_club.id, title="Constitution", url="https://r2/c.pdf",
            storage_key="documents/c.pdf", size_bytes=500, created_by=member.id,
        ),
        models.ErrorLog(
            method="GET", path="/club/events", exception_type="ValueError",
            message="boom", traceback="...", club_id=test_club.id,
        ),
        models.ErrorLog(
            method="GET", path="/club/members", exception_type="KeyError",
            message="nope", traceback="...", club_id=decoy.id,
        ),
    ])
    db.commit()

    res = client.get(f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["club"]["id"] == test_club.id
    usage = body["usage"]
    assert usage["sms_sent"] == 2, "decoy club's and unattributed sends must not count"
    assert usage["sms_failed"] == 1
    assert usage["storage_bytes"] == 1500, "photo + document bytes for this club only"
    assert usage["storage_photos"] == 1
    assert usage["storage_documents"] == 1
    assert usage["errors_total"] == 1

    assert [e["path"] for e in body["recent_errors"]] == ["/club/events"]

    # Clean up every row this test made. The gallery photos in particular
    # have to go before make_member's teardown deletes their uploader —
    # that fixture clears ClubDocument by created_by but not GalleryPhoto,
    # so an uploaded_by FK left behind here fails the teardown, not the test.
    for club_id in (test_club.id, decoy.id):
        db.query(models.GalleryPhoto).filter(
            models.GalleryPhoto.club_id == club_id
        ).delete()
        db.query(models.ErrorLog).filter(models.ErrorLog.club_id == club_id).delete()
        db.query(models.SmsLog).filter(models.SmsLog.club_id == club_id).delete()
    db.query(models.SmsLog).filter(models.SmsLog.club_id.is_(None)).delete()
    db.commit()
    db.delete(db.get(models.Club, decoy.id))
    db.commit()


def test_overview_separates_active_from_suspended_members(client, db, test_club, make_member):
    """"Active members" is the headline number on the screen, so a suspended
    member must not be counted in it — otherwise a club that has shed half
    its members still reads as fully active."""
    make_member(suffix=uuid.uuid4().hex[:8])
    suspended = make_member(suffix=uuid.uuid4().hex[:8])
    suspended.status = "suspended"
    db.commit()

    res = client.get(f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    usage = res.json()["usage"]

    assert usage["members_total"] == 2
    assert usage["members_active"] == 1
    assert usage["members_suspended"] == 1


def test_overview_is_zeroed_not_null_for_an_unused_club(client, db, test_club):
    """A brand-new club has no photos, SMS or errors. Those must come back
    as 0 rather than null — SUM() over no rows is NULL in SQL, and a null
    would render as an empty tile instead of an honest zero."""
    res = client.get(f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    usage = res.json()["usage"]

    assert usage["storage_bytes"] == 0
    assert usage["sms_sent"] == 0
    assert usage["errors_total"] == 0
    assert res.json()["recent_errors"] == []


def test_overview_names_the_three_officers(client, db, test_club, make_member):
    """The screen names who to contact about a club. Ordinary members must
    not be picked up, and each officer must land in its own slot."""
    # Distinct names on purpose: the fixture defaults them all to the same
    # one, which would let a swapped slot still satisfy the assertions.
    president = make_member(
        role="Club President", suffix=uuid.uuid4().hex[:8], name="Grace Nakato"
    )
    pe = make_member(
        role="President-Elect", suffix=uuid.uuid4().hex[:8], name="Peter Okello"
    )
    secretary = make_member(
        role="Secretary", suffix=uuid.uuid4().hex[:8], name="Sarah Nabirye"
    )
    make_member(role="Treasurer", suffix=uuid.uuid4().hex[:8], name="David Mugisha")
    make_member(suffix=uuid.uuid4().hex[:8], name="Alice Auma")  # plain member
    db.commit()

    body = client.get(
        f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db)
    ).json()

    assert body["president"]["name"] == president.name
    assert body["president"]["phone"] == president.phone
    assert body["president_elect"]["name"] == pe.name
    assert body["secretary"]["name"] == secretary.name


def test_overview_recognises_a_president_promoted_by_rollover(
    client, db, test_club, make_member
):
    """The annual rollover renames the incoming president from
    "Club President" to "President". Both spellings mean president, so a
    club that has been through a rollover must not show an empty slot."""
    promoted = make_member(
        role="President", suffix=uuid.uuid4().hex[:8], name="Robert Ssemwogerere"
    )
    db.commit()

    body = client.get(
        f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db)
    ).json()

    assert body["president"]["name"] == promoted.name


def test_overview_reports_unfilled_officer_posts_as_null(
    client, db, test_club, make_member
):
    """A vacant Secretary post is a real finding for an admin, so it comes
    back as an explicit null rather than being omitted from the payload."""
    make_member(role="Club President", suffix=uuid.uuid4().hex[:8])
    db.commit()

    body = client.get(
        f"/admin/clubs/{test_club.id}/overview", headers=_admin_auth(db)
    ).json()

    assert body["president"] is not None
    assert "president_elect" in body and body["president_elect"] is None
    assert "secretary" in body and body["secretary"] is None


def test_overview_requires_admin_auth(client, test_club):
    """The screen exposes another club's error traces and usage — it must
    not be reachable without an admin token."""
    assert client.get(f"/admin/clubs/{test_club.id}/overview").status_code == 401


def test_overview_404s_for_a_missing_club(client, db):
    res = client.get("/admin/clubs/99999999/overview", headers=_admin_auth(db))
    assert res.status_code == 404
