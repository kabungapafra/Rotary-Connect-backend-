"""The Rotary-year leadership handover (July 1): President-Elect becomes
President, the outgoing President becomes Immediate Past President, and
everyone else's board seat is cleared so the new President reassigns
their own cabinet — including the previous IPP, who quietly becomes a
plain Member rather than keeping any special title.
"""

from datetime import date

from app import models
from app.leadership_transition import run_leadership_transitions

JULY_1 = date(2026, 7, 1)
JUNE_30 = date(2026, 6, 30)


def test_does_nothing_before_july(db, test_club, make_member):
    president = make_member(role="Club President", suffix="100", is_board=True)
    pe = make_member(role="President-Elect", suffix="101", is_board=True)

    run_leadership_transitions(db, today=JUNE_30)
    db.refresh(president)
    db.refresh(pe)

    assert president.role == "Club President"
    assert pe.role == "President-Elect"


def test_full_handover_promotes_pe_and_rolls_ipp_forward(db, test_club, make_member):
    president = make_member(role="Club President", suffix="102", is_board=True)
    pe = make_member(role="President-Elect", suffix="103", is_board=True)
    old_ipp = make_member(role="Immediate Past President", suffix="104", is_board=True)
    secretary = make_member(role="Secretary", suffix="105", is_board=True)
    plain_member = make_member(role="Member", suffix="106")

    run_leadership_transitions(db, today=JULY_1)
    for m in (president, pe, old_ipp, secretary, plain_member):
        db.refresh(m)

    assert pe.role == "President" and pe.is_board is True
    assert pe.needs_board_setup is True

    assert president.role == "Immediate Past President" and president.is_board is True

    # The board is cleared, including whoever was IPP before — they don't
    # keep any special title, just become a plain Member like everyone else.
    assert old_ipp.role == "Member" and old_ipp.is_board is False
    assert secretary.role == "Member" and secretary.is_board is False
    assert plain_member.role == "Member" and plain_member.is_board is False

    club = db.get(models.Club, test_club.id)
    assert club.last_leadership_transition_year == 2026


def test_no_president_elect_leaves_club_untouched_for_the_year(db, test_club, make_member):
    president = make_member(role="Club President", suffix="107", is_board=True)
    secretary = make_member(role="Secretary", suffix="108", is_board=True)

    run_leadership_transitions(db, today=JULY_1)
    db.refresh(president)
    db.refresh(secretary)

    assert president.role == "Club President"
    assert secretary.role == "Secretary"

    club = db.get(models.Club, test_club.id)
    assert club.last_leadership_transition_year == 2026

    # A later sweep in the same Rotary year doesn't retry — even if a PE
    # gets assigned mid-year, promotion waits for the following July 1.
    secretary.role = "President-Elect"
    db.commit()
    run_leadership_transitions(db, today=date(2026, 9, 1))
    db.refresh(president)
    db.refresh(secretary)
    assert president.role == "Club President"
    assert secretary.role == "President-Elect"


def test_sweep_is_idempotent_within_the_same_rotary_year(db, test_club, make_member):
    pe = make_member(role="President-Elect", suffix="109", is_board=True)

    run_leadership_transitions(db, today=JULY_1)
    db.refresh(pe)
    assert pe.role == "President"

    # A second run later in the same Rotary year must be a no-op: the club
    # was already marked handled for 2026, so this new PE stays untouched
    # until 2027's July 1, not promoted immediately.
    another_pe = make_member(role="President-Elect", suffix="110")
    run_leadership_transitions(db, today=date(2026, 8, 1))
    db.refresh(another_pe)
    assert another_pe.role == "President-Elect"
    assert db.get(models.Member, pe.id).role == "President"


def test_transition_runs_again_the_following_rotary_year(db, test_club, make_member):
    president = make_member(role="President", suffix="111", is_board=True)
    pe = make_member(role="President-Elect", suffix="112", is_board=True)

    run_leadership_transitions(db, today=JULY_1)
    db.refresh(president)
    db.refresh(pe)
    assert pe.role == "President"
    assert president.role == "Immediate Past President"

    # A new PE for year two; the sweep a year later promotes them and rolls
    # the chain forward again.
    pe.needs_board_setup = False
    new_pe = make_member(role="President-Elect", suffix="113", is_board=True)
    db.commit()

    run_leadership_transitions(db, today=date(2027, 7, 1))
    db.refresh(pe)
    db.refresh(president)
    db.refresh(new_pe)

    assert new_pe.role == "President"
    assert pe.role == "Immediate Past President"
    # The year-one IPP is now two years out — a plain Member, not IPP.
    assert president.role == "Member" and president.is_board is False


def test_dismiss_board_setup_endpoint(client, db, make_member):
    from app import security

    pe = make_member(role="President-Elect", suffix="114", is_board=True)
    run_leadership_transitions(db, today=JULY_1)
    db.refresh(pe)
    assert pe.needs_board_setup is True

    token = security.create_access_token(pe.id)
    res = client.post(
        "/club/members/dismiss-board-setup", headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200
    assert res.json()["needs_board_setup"] is False


def test_assigning_a_new_president_elect_clears_the_prompt(client, db, make_member):
    from app import security

    pe = make_member(role="President-Elect", suffix="115", is_board=True)
    run_leadership_transitions(db, today=JULY_1)
    db.refresh(pe)
    assert pe.needs_board_setup is True

    candidate = make_member(role="Member", suffix="116")
    token = security.create_access_token(pe.id)
    res = client.patch(
        f"/club/members/{candidate.id}",
        json={"role": "President-Elect"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200

    db.refresh(pe)
    assert pe.needs_board_setup is False
