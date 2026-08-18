"""Placeholder numbers in the demo clubs sit inside live Ugandan ranges, so
an automated sweep would text whoever really owns them. Blocking is done per
number rather than per club, because the demo clubs also contain real people
whose SMS must still go out."""

from unittest.mock import patch

from app import config
from app.sms import send_sms


def _sent(phone):
    """Returns whether send_sms got as far as calling the gateway."""
    with patch("app.sms.requests.post") as post, \
         patch.object(config, "SMS_ENABLED", True), \
         patch.object(config, "YOOLA_API_KEY", "test-key"):
        post.return_value.status_code = 200
        post.return_value.text = "ok"
        send_sms(phone, "hello")
        return post.called


def test_demo_numbers_are_never_texted():
    for demo in ["256700000001", "256700000010", "256780000101", "256780000104"]:
        assert _sent(demo) is False, f"{demo} should be blocked"


def test_demo_numbers_blocked_in_local_format_too():
    """Blocking happens after normalisation, so the local form is caught."""
    assert _sent("0700000001") is False


def test_real_numbers_still_receive_sms():
    """The whole point: a real member inside a demo club must still be texted."""
    assert _sent("0757029368") is True
    assert _sent("256772123456") is True
