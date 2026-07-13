"""Shared fixtures. Tests run against the same Postgres DATABASE_URL the
app itself uses (there's no CREATEDB privilege available to spin up a
throwaway database locally) — every fixture that creates a row cleans it
up in teardown, and CI (see .github/workflows/backend.yml) gets full
isolation for free from a fresh, disposable Postgres service container.
"""

import pytest
from fastapi.testclient import TestClient

from app import models, security
from app.database import SessionLocal
from app.main import app
from app.rate_limit import _failed_attempts, _request_log


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Rate limits and lockouts are in-memory and keyed by identifier/IP —
    without a reset, one test's failed-login attempts would carry over
    and lock out the next test using the same identifier."""
    _request_log.clear()
    _failed_attempts.clear()
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture()
def test_club(db):
    club = models.Club(name="Pytest Club", district="", location="", status="active")
    db.add(club)
    db.commit()
    db.refresh(club)
    club_id = club.id
    yield club
    # Tests hit real endpoints that create rows (events, ...) beyond what
    # the make_* fixtures below track — clear anything left over for this
    # club before deleting it, or the FK constraint trips. Rows that carry
    # a member_id/created_by FK are cleaned up in make_member's own
    # teardown instead, since that runs (and deletes the members) before
    # this one does. Uses club_id (captured above) rather than club.id — a
    # test may have deleted the club itself (e.g. via DELETE /admin/clubs),
    # and re-reading an attribute off that now-expired, gone row raises
    # ObjectDeletedError instead of just telling us it's gone.
    db.query(models.ClubDuesSetting).filter(
        models.ClubDuesSetting.club_id == club_id
    ).delete()
    db.query(models.Event).filter(models.Event.club_id == club_id).delete()
    db.query(models.Member).filter(models.Member.club_id == club_id).delete()
    db.commit()
    row = db.get(models.Club, club_id)
    if row:
        db.delete(row)
        db.commit()


@pytest.fixture()
def make_member(db, test_club):
    """Factory fixture: make_member(role="President", suffix="001", pin="1234")
    creates a member in test_club and cleans it up after the test."""
    created_ids = []

    def _make(role="Member", suffix="001", pin="1234", is_board=False, name="Pytest Member"):
        member = models.Member(
            club_id=test_club.id,
            member_number=f"PYTEST-{suffix}",
            name=name,
            role=role,
            is_board=is_board,
            status="active",
            email="",
            phone=f"25670099{suffix}",
            dob="",
            pin_hash=security.hash_pin(pin),
        )
        db.add(member)
        db.commit()
        db.refresh(member)
        # Capture the id now rather than re-reading member.id at teardown —
        # a test may delete the member itself (e.g. via DELETE
        # /admin/members), at which point that attribute access would
        # raise ObjectDeletedError on the now-expired, gone row.
        created_ids.append(member.id)
        return member

    yield _make
    # Rows a test created that reference these members (votes, apologies,
    # dues payments, minutes/milestones authored by them, transactions they
    # recorded) must go first, or deleting the member trips the FK.
    member_ids = created_ids
    if member_ids:
        poll_ids = [
            p.id for p in db.query(models.Poll).filter(models.Poll.created_by.in_(member_ids))
        ]
        if poll_ids:
            db.query(models.PollVote).filter(models.PollVote.poll_id.in_(poll_ids)).delete(
                synchronize_session=False
            )
        db.query(models.PollVote).filter(models.PollVote.member_id.in_(member_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Poll).filter(models.Poll.created_by.in_(member_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Apology).filter(models.Apology.member_id.in_(member_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Transaction).filter(
            models.Transaction.created_by.in_(member_ids)
        ).delete(synchronize_session=False)
        db.query(models.DuesPayment).filter(
            models.DuesPayment.member_id.in_(member_ids)
        ).delete(synchronize_session=False)
        db.query(models.Minute).filter(models.Minute.created_by.in_(member_ids)).delete(
            synchronize_session=False
        )
        db.query(models.Milestone).filter(models.Milestone.created_by.in_(member_ids)).delete(
            synchronize_session=False
        )
        db.query(models.ClubDocument).filter(
            models.ClubDocument.created_by.in_(member_ids)
        ).delete(synchronize_session=False)
    for member_id in created_ids:
        # A test may have deleted the member itself (e.g. via DELETE
        # /admin/members) — nothing left to do then.
        row = db.get(models.Member, member_id)
        if row:
            db.delete(row)
    db.commit()


@pytest.fixture()
def make_event(db, test_club):
    created = []

    def _make(dow="WED", name="Pytest Fellowship", meta="6:00 PM - Hall"):
        event = models.Event(club_id=test_club.id, dow=dow, name=name, meta=meta)
        db.add(event)
        db.commit()
        db.refresh(event)
        created.append(event)
        return event

    yield _make
    for event in created:
        row = db.get(models.Event, event.id)
        if row:
            db.delete(row)
    db.commit()
