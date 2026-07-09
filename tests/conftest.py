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
    yield club
    # Tests hit real endpoints that create rows (events, members, ...)
    # beyond what the make_* fixtures below track — clear anything left
    # over for this club before deleting it, or the FK constraint trips.
    db.query(models.Event).filter(models.Event.club_id == club.id).delete()
    db.query(models.Member).filter(models.Member.club_id == club.id).delete()
    db.commit()
    db.delete(club)
    db.commit()


@pytest.fixture()
def make_member(db, test_club):
    """Factory fixture: make_member(role="President", suffix="001", pin="1234")
    creates a member in test_club and cleans it up after the test."""
    created = []

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
        created.append(member)
        return member

    yield _make
    for member in created:
        db.delete(member)
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
