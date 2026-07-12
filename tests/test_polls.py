"""Club voting: motions/elections need board or presidential authority to
create, any member can cast one vote, and a random draw resolves
server-side so the result can't be manipulated client-side."""

from app import models, security


def _auth(member):
    token = security.create_access_token(member.id)
    return {"Authorization": f"Bearer {token}"}


def test_plain_member_cannot_create_poll(client, make_member):
    member = make_member(role="Member", suffix="040")
    res = client.post(
        "/club/polls",
        json={"type": "motion", "title": "Adopt the budget"},
        headers=_auth(member),
    )
    assert res.status_code == 403


def test_board_member_creates_motion_and_others_vote_once(client, make_member):
    board = make_member(role="Community Service Director", suffix="041", is_board=True)
    voter = make_member(role="Member", suffix="042")

    res = client.post(
        "/club/polls",
        json={"type": "motion", "title": "Adopt the budget"},
        headers=_auth(board),
    )
    assert res.status_code == 200
    poll = res.json()
    assert poll["options"] == ["Yes", "No", "Abstain"]
    assert poll["status"] == "open"

    res = client.post(
        f"/club/polls/{poll['id']}/vote", json={"choice": "Yes"}, headers=_auth(voter)
    )
    assert res.status_code == 200
    results = {r["label"]: r["count"] for r in res.json()["results"]}
    assert results["Yes"] == 1

    # Can't vote twice.
    res = client.post(
        f"/club/polls/{poll['id']}/vote", json={"choice": "No"}, headers=_auth(voter)
    )
    assert res.status_code == 422

    res = client.get("/club/polls/active", headers=_auth(voter))
    assert res.status_code == 200
    assert res.json()["my_vote"] == "Yes"


def test_election_needs_two_candidates(client, make_member):
    board = make_member(role="President", suffix="043", is_board=True)

    res = client.post(
        "/club/polls",
        json={"type": "election", "title": "Next Secretary", "options": ["Only one"]},
        headers=_auth(board),
    )
    assert res.status_code == 422


def test_draw_pairs_every_member_with_someone_else_no_repeats_no_self(client, make_member):
    # Distinct names matter: assignments pair name strings, so the
    # giver != recipient assertions below would be vacuously wrong (and
    # the draw itself degenerate) if everyone shared the default name.
    board = make_member(role="President", suffix="046", is_board=True, name="Draw Alice")
    m2 = make_member(role="Member", suffix="047", name="Draw Ben")
    m3 = make_member(role="Member", suffix="048", name="Draw Cara")
    m4 = make_member(role="Member", suffix="049", name="Draw Dan")

    res = client.post(
        "/club/polls", json={"type": "draw", "title": "Gift exchange"}, headers=_auth(board)
    )
    assert res.status_code == 200
    poll = res.json()
    # Every current club member is an entrant, not a hand-picked subset.
    names = {f"Rtn. {m.name}" for m in [board, m2, m3, m4]}
    assert set(poll["options"]) == names
    assert poll["assignments"] is None  # not drawn yet

    # A member can't vote on a draw, and can't resolve it either.
    res = client.post(
        f"/club/polls/{poll['id']}/vote", json={"choice": poll["options"][0]}, headers=_auth(m2)
    )
    assert res.status_code == 422
    res = client.post(f"/club/polls/{poll['id']}/draw", headers=_auth(m2))
    assert res.status_code == 403

    res = client.post(f"/club/polls/{poll['id']}/draw", headers=_auth(board))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "closed"
    assignments = body["assignments"]
    givers = {a["giver"] for a in assignments}
    recipients = [a["recipient"] for a in assignments]
    assert givers == names
    assert set(recipients) == names  # every member is someone's recipient
    assert len(recipients) == len(set(recipients))  # no one assigned twice
    for a in assignments:
        assert a["giver"] != a["recipient"]  # no one gets themselves

    # Running it again is rejected — the draw only resolves once.
    res = client.post(f"/club/polls/{poll['id']}/draw", headers=_auth(board))
    assert res.status_code == 422


def test_creating_a_new_poll_closes_the_previous_open_one(client, db, make_member):
    board = make_member(role="President", suffix="045", is_board=True)

    res1 = client.post(
        "/club/polls", json={"type": "motion", "title": "First motion"}, headers=_auth(board)
    )
    poll1_id = res1.json()["id"]

    res2 = client.post(
        "/club/polls", json={"type": "motion", "title": "Second motion"}, headers=_auth(board)
    )
    assert res2.status_code == 200

    poll1 = db.get(models.Poll, poll1_id)
    assert poll1.status == "closed"

    res = client.get("/club/polls/active", headers=_auth(board))
    assert res.json()["title"] == "Second motion"
