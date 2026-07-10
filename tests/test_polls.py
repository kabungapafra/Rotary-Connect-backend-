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


def test_election_needs_two_candidates_and_draw_picks_a_server_side_winner(client, make_member):
    board = make_member(role="President", suffix="043", is_board=True)
    member = make_member(role="Member", suffix="044")

    res = client.post(
        "/club/polls",
        json={"type": "election", "title": "Next Secretary", "options": ["Only one"]},
        headers=_auth(board),
    )
    assert res.status_code == 422

    res = client.post(
        "/club/polls",
        json={
            "type": "draw",
            "title": "Raffle",
            "options": ["Alice", "Bob", "Carol"],
        },
        headers=_auth(board),
    )
    assert res.status_code == 200
    poll = res.json()

    # A member can't vote on a draw, and can't resolve it either.
    res = client.post(f"/club/polls/{poll['id']}/vote", json={"choice": "Alice"}, headers=_auth(member))
    assert res.status_code == 422
    res = client.post(f"/club/polls/{poll['id']}/draw", headers=_auth(member))
    assert res.status_code == 403

    res = client.post(f"/club/polls/{poll['id']}/draw", headers=_auth(board))
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "closed"
    assert body["winner"] in ["Alice", "Bob", "Carol"]


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
