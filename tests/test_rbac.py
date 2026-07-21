"""Role checks are enforced server-side — hiding a button in the app is
not access control on its own. These hit the real endpoints with real
tokens for each role, the same way the manual verification during
development did, just made repeatable.
"""

from app import security


def _token_for(member):
    return security.create_access_token(member.id)


def _auth(member):
    return {"Authorization": f"Bearer {_token_for(member)}"}


def test_plain_member_cannot_create_event(client, make_member):
    member = make_member(role="Member", suffix="010")
    res = client.post(
        "/club/events",
        json={"dow": "WED", "name": "Should be rejected", "meta": ""},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_plain_member_cannot_generate_event_qr(client, make_member, make_event):
    member = make_member(role="Member", suffix="011")
    event = make_event()
    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(member))
    assert res.status_code == 403


def test_president_role_can_create_event(client, make_member):
    # "President" (the Add Member dropdown label) must carry the same
    # authority as the legacy "Club President" set at club creation.
    member = make_member(role="President", suffix="012", is_board=True)
    res = client.post(
        "/club/events",
        json={"dow": "FRI", "name": "President-created", "meta": "5:00 PM - Hall"},
        headers=_auth(member),
    )
    assert res.status_code == 200


def test_secretary_shares_president_management_powers(client, make_member, make_event):
    # The Secretary can do everything the President can — events, projects,
    # members — not just generate QR links.
    secretary = make_member(role="Secretary", suffix="013", is_board=True)
    event = make_event()

    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(secretary))
    assert res.status_code == 200
    assert res.json()["link"].endswith(f"/rsvp/{event.id}")

    res = client.post(
        "/club/events",
        json={"dow": "SAT", "name": "Secretary-created", "meta": ""},
        headers=_auth(secretary),
    )
    assert res.status_code == 200

    res = client.post(
        "/club/projects",
        json={"name": "Secretary project", "area": "", "pct": 0, "desc": ""},
        headers=_auth(secretary),
    )
    assert res.status_code == 200
    # Delete it too — both to prove the Secretary can manage (not just
    # create) projects, and because test_club's teardown doesn't clean
    # up projects, so a leftover row trips the club FK.
    res = client.delete(f"/club/projects/{res.json()['id']}", headers=_auth(secretary))
    assert res.status_code == 200

    res = client.post(
        "/club/members",
        json={"name": "Added By Secretary", "phone": "256700990130", "role": "Member"},
        headers=_auth(secretary),
    )
    assert res.status_code == 200


def test_treasurer_can_generate_event_qr(client, make_member, make_event):
    # Treasurer often runs event logistics/payments — added to the
    # event-registration role set alongside Board Director.
    treasurer = make_member(role="Treasurer", suffix="014")
    event = make_event()
    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(treasurer))
    assert res.status_code == 200


def test_board_director_can_generate_event_qr(client, make_member, make_event):
    director = make_member(role="Board Director", suffix="015")
    event = make_event()
    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(director))
    assert res.status_code == 200


def test_committee_chair_cannot_generate_event_qr(client, make_member, make_event):
    # A plain committee chair title is not one of the roles allowed to
    # generate event registration links (unlike Treasurer/Board Director).
    chair = make_member(role="Membership Chair", suffix="016")
    event = make_event()
    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(chair))
    assert res.status_code == 403
