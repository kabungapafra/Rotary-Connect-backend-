"""Regression coverage for DELETE /admin/clubs/{id} and
DELETE /admin/members/{id} — both used to trip a Postgres FK violation
(caught nowhere, surfaced to the dashboard as a generic "can't reach the
server") on any club/member that had ever used a feature added after the
original cleanup was written: polls, dues, transactions, minutes,
milestones, gallery uploads, or event RSVPs."""

import uuid
from datetime import date

from app import models, security


def _admin_auth(db):
    admin = db.query(models.AdminUser).first()
    assert admin is not None, "seed_bootstrap_data should have created the admin account"
    token = security.create_admin_access_token(admin.id)
    return {"Authorization": f"Bearer {token}"}


def test_delete_club_with_every_dependent_row(client, db, test_club, make_member):
    """A club that has actually been used — one of everything the app can
    attach to it — must still delete cleanly."""
    president = make_member(role="President", suffix=uuid.uuid4().hex[:8], is_board=True)

    meeting = models.Meeting(club_id=test_club.id, date=date.today(), name="Weekly")
    db.add(meeting)
    db.flush()
    db.add(models.CheckIn(member_id=president.id, meeting_id=meeting.id))

    event = models.Event(club_id=test_club.id, dow="WED", name="Fellowship", meta="6pm")
    db.add(event)
    db.flush()
    db.add(models.EventRsvp(event_id=event.id, name="Guest", phone="256700000001"))

    db.add(models.Project(club_id=test_club.id, name="Borehole", area="", pct=0, desc="", deadline=""))
    db.add(models.GuestVisit(club_id=test_club.id, name="Visitor", phone="256700000002", visit_date=date.today()))
    db.add(models.GalleryPhoto(club_id=test_club.id, album="General", image="data:image/png;base64,x", uploaded_by=president.id))
    db.add(models.Apology(club_id=test_club.id, member_id=president.id, meeting_date=date.today(), reason="Travel"))
    db.add(models.ClubDuesSetting(club_id=test_club.id, amount=10000, period="quarterly"))
    db.add(models.DuesPayment(club_id=test_club.id, member_id=president.id, period_label="2026-Q3"))
    db.add(models.Transaction(club_id=test_club.id, kind="income", label="Dues", amount=10000, created_by=president.id))
    db.add(models.Minute(club_id=test_club.id, title="Minutes", meeting_date=date.today(), created_by=president.id))
    db.add(models.Milestone(club_id=test_club.id, year="2026", title="Founded", created_by=president.id))

    poll = models.Poll(
        club_id=test_club.id, type="motion", title="Approve budget",
        options="[\"Yes\", \"No\"]", created_by=president.id,
    )
    db.add(poll)
    db.flush()
    db.add(models.PollVote(poll_id=poll.id, member_id=president.id, choice="Yes"))
    db.commit()

    club_id, president_id = test_club.id, president.id
    res = client.delete(f"/admin/clubs/{club_id}", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": True}
    # The endpoint ran in its own DB session (FastAPI's Depends(get_db)) —
    # this session's identity map can still hold pre-delete copies, so
    # force a re-read from Postgres rather than trusting cached objects.
    db.expire_all()
    assert db.get(models.Club, club_id) is None
    assert db.get(models.Member, president_id) is None


def test_delete_member_with_every_dependent_row(client, db, test_club, make_member):
    """Same FK gap, one level down: deleting a single member who voted,
    recorded a transaction, etc. must not trip a FK violation either."""
    member = make_member(role="Treasurer", suffix=uuid.uuid4().hex[:8], is_board=True)

    db.add(models.GalleryPhoto(club_id=test_club.id, album="General", image="data:image/png;base64,x", uploaded_by=member.id))
    db.add(models.Apology(club_id=test_club.id, member_id=member.id, meeting_date=date.today(), reason="Travel"))
    db.add(models.DuesPayment(club_id=test_club.id, member_id=member.id, period_label="2026-Q3"))
    db.add(models.Transaction(club_id=test_club.id, kind="expense", label="Venue", amount=5000, created_by=member.id))
    db.add(models.Minute(club_id=test_club.id, title="Minutes", meeting_date=date.today(), created_by=member.id))
    db.add(models.Milestone(club_id=test_club.id, year="2026", title="Award", created_by=member.id))

    poll = models.Poll(
        club_id=test_club.id, type="motion", title="Approve venue",
        options="[\"Yes\", \"No\"]", created_by=member.id,
    )
    db.add(poll)
    db.flush()
    db.add(models.PollVote(poll_id=poll.id, member_id=member.id, choice="Yes"))
    db.commit()
    member_id = member.id

    res = client.delete(f"/admin/members/{member_id}", headers=_admin_auth(db))
    assert res.status_code == 200, res.text
    assert res.json() == {"deleted": True}
    db.expire_all()
    assert db.get(models.Member, member_id) is None
    # make_member's own teardown will find nothing left to clean up —
    # confirming the endpoint, not the fixture, did the deleting.
