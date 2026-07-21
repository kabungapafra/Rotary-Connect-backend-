"""Secretary workspace: minutes are Secretary-only writes; club-history
entries are also writable by the President and Immediate Past President;
reports are computed from real data, not fabricated numbers, and readable
by any club member."""

import base64

import pytest

from app import models, security, storage


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_plain_member_cannot_create_minute_or_milestone(client, make_member):
    member = make_member(role="Member", suffix="050")

    res = client.post(
        "/club/secretary/minutes",
        json={"title": "Weekly Fellowship Meeting", "meeting_date": "2026-07-08"},
        headers=_auth(member),
    )
    assert res.status_code == 403

    res = client.post(
        "/club/secretary/milestones",
        json={"year": "2026", "title": "Club chartered"},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_secretary_can_create_and_approve_minutes(client, make_member):
    secretary = make_member(role="Secretary", suffix="051", is_board=True)
    member = make_member(role="Member", suffix="052")

    res = client.post(
        "/club/secretary/minutes",
        json={"title": "Weekly Fellowship Meeting", "meeting_date": "2026-07-08"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    minute = res.json()
    assert minute["status"] == "draft"

    res = client.get("/club/secretary/minutes", headers=_auth(member))
    assert res.status_code == 200
    assert any(m["id"] == minute["id"] for m in res.json())

    res = client.patch(
        f"/club/secretary/minutes/{minute['id']}",
        json={"status": "approved"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    assert res.json()["status"] == "approved"


def test_secretary_can_add_and_delete_milestone(client, make_member):
    secretary = make_member(role="Secretary", suffix="053", is_board=True)

    res = client.post(
        "/club/secretary/milestones",
        json={"year": "2026", "title": "Club chartered", "category": "Milestones"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    milestone_id = res.json()["id"]

    res = client.get("/club/secretary/milestones", headers=_auth(secretary))
    assert any(m["id"] == milestone_id for m in res.json())

    res = client.delete(f"/club/secretary/milestones/{milestone_id}", headers=_auth(secretary))
    assert res.status_code == 200

    res = client.get("/club/secretary/milestones", headers=_auth(secretary))
    assert not any(m["id"] == milestone_id for m in res.json())


def test_president_and_ipp_can_edit_club_history_other_executives_cannot(
    client, make_member
):
    """The Secretary owns minutes exclusively, but club history is shared
    with the President and Immediate Past President — the roles who'd
    actually know the club's own history. A Treasurer, despite also being
    a privileged executive role, is not one of them."""
    president = make_member(role="President", suffix="054", is_board=True)
    ipp = make_member(role="Immediate Past President", suffix="055", is_board=True)
    treasurer = make_member(role="Treasurer", suffix="056", is_board=True)

    for editor in (president, ipp):
        res = client.post(
            "/club/secretary/milestones",
            json={"year": "2026", "title": "Club chartered", "category": "Milestones"},
            headers=_auth(editor),
        )
        assert res.status_code == 200
        milestone_id = res.json()["id"]

        res = client.delete(
            f"/club/secretary/milestones/{milestone_id}", headers=_auth(editor)
        )
        assert res.status_code == 200

    res = client.post(
        "/club/secretary/milestones",
        json={"year": "2026", "title": "Club chartered", "category": "Milestones"},
        headers=_auth(treasurer),
    )
    assert res.status_code == 403


def test_monthly_report_reflects_real_membership_count(client, make_member):
    make_member(role="Member", suffix="054")
    viewer = make_member(role="Member", suffix="055")

    res = client.get("/club/secretary/monthly-report", headers=_auth(viewer))
    assert res.status_code == 200
    body = res.json()
    membership_section = next(s for s in body["sections"] if s["section"] == "Membership")
    current = next(r for r in membership_section["rows"] if r["label"] == "Current membership")
    # 2 members created above, in a club that started with none.
    assert current["value"] == "2"


def test_monthly_report_membership_net_change_and_active_only_count(client, make_member):
    president = make_member(role="President", suffix="063", is_board=True)
    suspended = make_member(role="Member", suffix="064")
    to_terminate = make_member(role="Member", suffix="065")
    make_member(role="Member", suffix="066")

    client.patch(
        f"/club/members/{suspended.id}", json={"status": "suspended"}, headers=_auth(president)
    )
    client.patch(
        f"/club/members/{to_terminate.id}",
        json={"status": "terminated"},
        headers=_auth(president),
    )

    res = client.get("/club/secretary/monthly-report", headers=_auth(president))
    assert res.status_code == 200
    membership = next(
        s for s in res.json()["sections"] if s["section"] == "Membership"
    )
    rows = {r["label"]: r["value"] for r in membership["rows"]}
    # 4 members created above, minus the 1 suspended and 1 terminated.
    assert rows["Current membership"] == "2"
    # All 4 were created "now" (this month), regardless of later status.
    assert rows["New members"] == "4"
    assert rows["Terminations/resignations"] == "1"
    assert rows["Net change"] == "+3"


def test_monthly_report_projects_include_beneficiaries_and_area(client, db, make_member):
    president = make_member(role="President", suffix="067", is_board=True)
    client.post(
        "/club/projects",
        json={
            "name": "Borehole Drive",
            "area_of_focus": "Water, Sanitation, and Hygiene",
            "beneficiaries_reached": 500,
        },
        headers=_auth(president),
    )

    res = client.get("/club/secretary/monthly-report", headers=_auth(president))
    assert res.status_code == 200
    projects = next(s for s in res.json()["sections"] if s["section"] == "Projects")
    rows = {r["label"]: r["value"] for r in projects["rows"]}
    assert rows["Total beneficiaries reached"] == "500"
    assert rows["Water, Sanitation, and Hygiene"] == "1"

    db.query(models.Project).filter(models.Project.club_id == president.club_id).delete()
    db.commit()


_TINY_PDF = "data:application/pdf;base64," + base64.b64encode(
    b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
).decode()


@pytest.mark.skipif(storage._client is None, reason="R2 storage not configured")
def test_secretary_uploads_and_deletes_club_document(client, make_member):
    secretary = make_member(role="Secretary", suffix="056", is_board=True)
    member = make_member(role="Member", suffix="057")

    # Documents are the Secretary's alone — members can't upload or list.
    res = client.post(
        "/club/secretary/documents",
        json={"title": "Club Constitution", "file": _TINY_PDF},
        headers=_auth(member),
    )
    assert res.status_code == 403
    res = client.get("/club/secretary/documents", headers=_auth(member))
    assert res.status_code == 403

    res = client.post(
        "/club/secretary/documents",
        json={"title": "Club Constitution", "file": _TINY_PDF},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    doc = res.json()
    # Stored on R2, not in Postgres — the row holds a public URL.
    assert doc["url"].startswith("http")

    res = client.get("/club/secretary/documents", headers=_auth(secretary))
    assert any(d["id"] == doc["id"] for d in res.json())

    # Non-PDF uploads are rejected — the section is PDF-only by design.
    res = client.post(
        "/club/secretary/documents",
        json={"title": "Sneaky image", "file": "data:image/png;base64,aWJt"},
        headers=_auth(secretary),
    )
    assert res.status_code == 422

    res = client.delete(
        f"/club/secretary/documents/{doc['id']}", headers=_auth(secretary)
    )
    assert res.status_code == 200
    res = client.get("/club/secretary/documents", headers=_auth(secretary))
    assert not any(d["id"] == doc["id"] for d in res.json())


def test_secretary_edits_minute_body_and_title(client, make_member):
    secretary = make_member(role="Secretary", suffix="058", is_board=True)
    member = make_member(role="Member", suffix="059")

    res = client.post(
        "/club/secretary/minutes",
        json={"title": "Weekly Fellowship Meeting", "meeting_date": "2026-07-13"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    minute = res.json()
    assert minute["body"] == ""

    # The body is where the actual minutes text lives — editable until approved.
    res = client.patch(
        f"/club/secretary/minutes/{minute['id']}",
        json={"body": "## Call to Order\nThe President called...", "title": "July 13 Fellowship"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    updated = res.json()
    assert updated["body"].startswith("## Call to Order")
    assert updated["title"] == "July 13 Fellowship"
    assert updated["status"] == "draft"  # editing doesn't change approval

    # Members can read but not edit.
    res = client.patch(
        f"/club/secretary/minutes/{minute['id']}",
        json={"body": "vandalism"},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_from_audio_reports_unconfigured_when_groq_key_missing(client, make_member, monkeypatch):
    # Without a GROQ_API_KEY the endpoint must say so loudly (503), never
    # accept the upload and silently drop it.
    from app import config as app_config

    monkeypatch.setattr(app_config, "GROQ_ENABLED", False)
    secretary = make_member(role="Secretary", suffix="060", is_board=True)
    res = client.post(
        "/club/secretary/minutes/from-audio",
        files={"audio": ("meeting.m4a", b"\x00\x01", "audio/mp4")},
        data={"title": "Weekly Meeting", "meeting_date": "2026-07-13"},
        headers=_auth(secretary),
    )
    assert res.status_code == 503


def test_secretary_deletes_minutes_members_cannot(client, make_member):
    secretary = make_member(role="Secretary", suffix="061", is_board=True)
    member = make_member(role="Member", suffix="062")

    res = client.post(
        "/club/secretary/minutes",
        json={"title": "Scrap this", "meeting_date": "2026-07-13"},
        headers=_auth(secretary),
    )
    minute_id = res.json()["id"]

    # Members can't delete.
    res = client.delete(f"/club/secretary/minutes/{minute_id}", headers=_auth(member))
    assert res.status_code == 403

    # The secretary can — any status, approved included (the app puts a
    # confirm dialog in front of this).
    client.patch(
        f"/club/secretary/minutes/{minute_id}",
        json={"status": "approved"},
        headers=_auth(secretary),
    )
    res = client.delete(f"/club/secretary/minutes/{minute_id}", headers=_auth(secretary))
    assert res.status_code == 200
    res = client.get("/club/secretary/minutes", headers=_auth(secretary))
    assert not any(m["id"] == minute_id for m in res.json())
