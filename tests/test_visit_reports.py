"""Club visit reports: a member self-reports a meeting they attended at
another club (no QR of theirs to scan after the fact) so the Secretary can
manually credit it as a make-up. Any member can submit one; only the
Secretary can see the list."""

from datetime import date

from app import security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_any_member_can_submit_a_visit_report(client, make_member):
    member = make_member(role="Member", suffix="060")
    res = client.post(
        "/checkin/visit-report",
        json={
            "visited_club_name": "Rotary Club of Kampala North",
            "meeting_date": date.today().isoformat(),
            "meeting_type": "Club meeting",
            "notes": "Great speaker on WASH projects",
        },
        headers=_auth(member),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["visited_club_name"] == "Rotary Club of Kampala North"
    assert body["member_name"] == member.name
    assert body["notes"] == "Great speaker on WASH projects"
    # No district field anywhere in the response.
    assert "district" not in body


def test_visit_report_requires_a_club_name(client, make_member):
    member = make_member(role="Member", suffix="061")
    res = client.post(
        "/checkin/visit-report",
        json={"visited_club_name": "   ", "meeting_date": date.today().isoformat()},
        headers=_auth(member),
    )
    assert res.status_code == 422


def test_only_secretary_can_list_visit_reports(client, make_member):
    secretary = make_member(role="Secretary", suffix="062", is_board=True)
    member = make_member(role="Member", suffix="063")
    president = make_member(role="President", suffix="064", is_board=True)

    client.post(
        "/checkin/visit-report",
        json={
            "visited_club_name": "Rotary Club of Entebbe",
            "meeting_date": date.today().isoformat(),
            "meeting_type": "Online meeting",
        },
        headers=_auth(member),
    )

    res = client.get("/club/secretary/visit-reports", headers=_auth(secretary))
    assert res.status_code == 200
    reports = res.json()
    assert len(reports) == 1
    assert reports[0]["visited_club_name"] == "Rotary Club of Entebbe"
    assert reports[0]["meeting_type"] == "Online meeting"
    assert reports[0]["member_name"] == member.name

    # The workspace is the Secretary's alone — not even the President.
    res = client.get("/club/secretary/visit-reports", headers=_auth(president))
    assert res.status_code == 403

    res = client.get("/club/secretary/visit-reports", headers=_auth(member))
    assert res.status_code == 403
