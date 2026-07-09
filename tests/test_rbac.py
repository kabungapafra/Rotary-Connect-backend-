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


def test_secretary_can_generate_qr_but_not_create_event(client, make_member, make_event):
    secretary = make_member(role="Secretary", suffix="013", is_board=True)
    event = make_event()

    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(secretary))
    assert res.status_code == 200
    assert res.json()["link"].endswith(f"/rsvp/{event.id}")

    res = client.post(
        "/club/events",
        json={"dow": "SAT", "name": "Secretary should not create this", "meta": ""},
        headers=_auth(secretary),
    )
    assert res.status_code == 403


def test_treasurer_cannot_generate_event_qr(client, make_member, make_event):
    # Treasurer is a real role (needed so the app's Treasury-card gating has
    # something to check against) but isn't one of the five roles allowed
    # to generate event registration links.
    treasurer = make_member(role="Treasurer", suffix="014")
    event = make_event()
    res = client.get(f"/club/events/{event.id}/registration", headers=_auth(treasurer))
    assert res.status_code == 403
