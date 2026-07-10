"""Secretary workspace: minutes and club-history entries are
Secretary/President-only writes; reports are computed from real data, not
fabricated numbers, and readable by any club member."""

from app import security


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
