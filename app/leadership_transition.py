"""Rotary year (July 1 - June 30) leadership handover: the club's
President-Elect becomes President, the outgoing President becomes
Immediate Past President, and the previous IPP quietly rolls back to a
plain Member — same as every other outgoing board seat, which also gets
cleared so the new President builds their own cabinet from scratch.

Runs as a daily idempotent sweep (see main.py) rather than a one-shot job
timed for exactly July 1, so a missed day (deploy downtime, etc.) still
catches up correctly. A club with no President-Elect assigned when its
Rotary year turns is simply skipped for that whole year — the sitting
President carries on, and the transition next runs for that club at the
following July 1, once a PE has been assigned.
"""

from datetime import date

from sqlalchemy.orm import Session

from . import models
from .routers.club_members import PRESIDENT_ROLES

IPP_ROLE = "Immediate Past President"
PE_ROLE = "President-Elect"
NEW_PRESIDENT_ROLE = "President"


def run_leadership_transitions(db: Session, today: date | None = None) -> None:
    today = today or date.today()
    if today.month < 7:
        return  # the Rotary year hasn't turned yet this calendar year
    rotary_year = today.year
    clubs = (
        db.query(models.Club)
        .filter(
            (models.Club.last_leadership_transition_year.is_(None))
            | (models.Club.last_leadership_transition_year < rotary_year)
        )
        .all()
    )
    for club in clubs:
        _transition_club(db, club, rotary_year)


def _transition_club(db: Session, club: models.Club, rotary_year: int) -> None:
    members = db.query(models.Member).filter(models.Member.club_id == club.id).all()
    president = next((m for m in members if m.role in PRESIDENT_ROLES), None)
    president_elect = next((m for m in members if m.role == PE_ROLE), None)

    if president_elect is None:
        # Nothing to promote into — leave the club exactly as-is. Marking
        # this Rotary year as handled means the next attempt is the
        # following July 1, not tomorrow: a club that forgot to name a PE
        # gets one full year to do so before the sweep looks again.
        club.last_leadership_transition_year = rotary_year
        db.commit()
        return

    # Every other board seat (including whoever was IPP) is cleared back to
    # a plain Member — the new President reassigns the cabinet themselves.
    for m in members:
        if m.id == president_elect.id:
            continue
        if president is not None and m.id == president.id:
            continue
        if m.role != "Member" or m.is_board:
            m.role = "Member"
            m.is_board = False

    if president is not None:
        president.role = IPP_ROLE
        president.is_board = True

    president_elect.role = NEW_PRESIDENT_ROLE
    president_elect.is_board = True
    president_elect.needs_board_setup = True

    club.last_leadership_transition_year = rotary_year
    db.commit()
