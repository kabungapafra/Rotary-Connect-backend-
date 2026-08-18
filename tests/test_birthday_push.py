"""Birthdays went out by SMS only, unlike the dues reminder which already
pushed as well. A member with the app installed should be wished in-app too
— which also reaches them where their club has SMS withheld."""

import uuid
from datetime import date
from unittest.mock import patch

from app import models
from app.birthdays import wish_if_due


def _add_device(db, member):
    """Returns a cleanup callable — make_member's teardown deletes the member
    but not its device tokens, and the FK would trip on the way out."""
    row = models.DeviceToken(
        member_id=member.id, token=f"tok-{uuid.uuid4().hex}", platform="android"
    )
    db.add(row)
    db.commit()

    def cleanup():
        db.query(models.DeviceToken).filter(
            models.DeviceToken.member_id == member.id
        ).delete()
        db.commit()

    return cleanup


def _member_with_birthday_today(db, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    today = date.today()
    member.dob = f"{today.day:02d} {today.strftime('%b')} 1990"
    member.last_birthday_wished = None
    db.commit()
    return member


def test_birthday_push_goes_to_the_members_devices(db, test_club, make_member):
    member = _member_with_birthday_today(db, make_member)
    cleanup = _add_device(db, member)
    try:
        with patch("app.birthdays.send_push", return_value=True) as push, \
             patch("app.birthdays.send_sms", return_value=False):
            wish_if_due(db, member)

        assert push.call_count == 1
        title = push.call_args.args[1]
        assert "birthday" in title.lower()
        assert push.call_args.kwargs["data"] == {"type": "birthday"}
    finally:
        cleanup()


def test_push_alone_is_enough_to_mark_them_wished(db, test_club, make_member):
    """Otherwise a club with SMS withheld would push the same member daily."""
    member = _member_with_birthday_today(db, make_member)
    cleanup = _add_device(db, member)
    try:
        with patch("app.birthdays.send_push", return_value=True), \
             patch("app.birthdays.send_sms", return_value=False):
            wish_if_due(db, member)
        db.refresh(member)
        assert member.last_birthday_wished == date.today()
    finally:
        cleanup()


def test_nothing_is_sent_when_it_is_not_their_birthday(db, test_club, make_member):
    member = make_member(suffix=uuid.uuid4().hex[:8])
    member.dob = "01 Jan 1990" if date.today().strftime("%d %b") != "01 Jan" else "02 Feb 1990"
    member.last_birthday_wished = None
    db.commit()

    with patch("app.birthdays.send_push") as push, patch("app.birthdays.send_sms") as sms:
        wish_if_due(db, member)
    push.assert_not_called()
    sms.assert_not_called()


def test_sms_still_sent_and_still_marks_wished(db, test_club, make_member):
    """The existing SMS behaviour must be untouched."""
    member = _member_with_birthday_today(db, make_member)
    with patch("app.birthdays.send_push", return_value=False), \
         patch("app.birthdays.send_sms", return_value=True) as sms:
        wish_if_due(db, member)
    assert sms.call_count == 1
    db.refresh(member)
    assert member.last_birthday_wished == date.today()
