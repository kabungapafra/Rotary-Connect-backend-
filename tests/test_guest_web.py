"""The printed club QR links to /visit/{club_id}, so a guest who scans it
with their phone's own camera (no app) gets a check-in form in the browser
instead of a screen of raw QR text. It must log the visit exactly like the
app's guest check-in — same meeting-time window, same one-per-phone-per-day
rule — or the web form would become a back door around those rules."""

from datetime import datetime, timedelta, timezone

import pytest

from app import models


@pytest.fixture()
def meeting_now(make_event):
    now_local = datetime.now(timezone.utc) + timedelta(hours=3)  # fixed EAT
    return make_event(
        dow=now_local.strftime("%a").upper(),
        meta=f"{now_local.hour}:{now_local.minute:02d} - Hall",
    )


@pytest.fixture()
def guest_cleanup(db, test_club):
    yield
    db.query(models.GuestVisit).filter(models.GuestVisit.club_id == test_club.id).delete()
    db.commit()


def _visits(db, club):
    return db.query(models.GuestVisit).filter(models.GuestVisit.club_id == club.id).all()


def test_scanning_the_qr_opens_a_form_naming_the_club(client, test_club):
    res = client.get(f"/visit/{test_club.id}")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert test_club.name in res.text
    assert "<form" in res.text


def test_an_unknown_club_shows_a_not_found_page(client):
    res = client.get("/visit/999999999")
    assert res.status_code == 404
    assert "Club not found" in res.text


def test_submitting_the_form_logs_the_guest(client, db, test_club, meeting_now, guest_cleanup):
    res = client.post(
        f"/visit/{test_club.id}",
        data={"name": "Web Guest", "phone": "0772 000 551",
              "attendee_type": "Prospective member", "host_name": "Jane"},
    )
    assert res.status_code == 200
    assert "checked in, Web" in res.text
    [visit] = _visits(db, test_club)
    assert visit.phone == "256772000551"
    assert visit.guest_type == "Prospective member"
    assert visit.host_name == "Jane"


def test_a_second_submit_the_same_day_is_not_a_duplicate(
    client, db, test_club, meeting_now, guest_cleanup
):
    for _ in range(2):
        client.post(f"/visit/{test_club.id}", data={"name": "Twice", "phone": "0772000552"})
    assert len(_visits(db, test_club)) == 1


def test_visiting_rotarian_must_name_their_club(client, db, test_club, meeting_now, guest_cleanup):
    res = client.post(
        f"/visit/{test_club.id}",
        data={"name": "Rtn X", "phone": "0772000553", "attendee_type": "Visiting Rotarian"},
    )
    assert res.status_code == 422
    assert _visits(db, test_club) == []


def test_the_form_is_held_to_the_meeting_window(client, db, test_club, make_event, guest_cleanup):
    """No meeting on right now: the web form must refuse, same as the app."""
    now_local = datetime.now(timezone.utc) + timedelta(hours=3)
    far = now_local + timedelta(hours=4)
    if far.date() != now_local.date():
        far = now_local.replace(hour=23, minute=59)
    make_event(dow=now_local.strftime("%a").upper(), meta=f"{far.hour}:{far.minute:02d} - Hall")
    res = client.post(f"/visit/{test_club.id}", data={"name": "Early", "phone": "0772000554"})
    assert res.status_code == 422
    assert _visits(db, test_club) == []
