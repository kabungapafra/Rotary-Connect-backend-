"""Pure-function tests for the time parsing and next-occurrence math behind
fellowship reminders and the Next Meeting card — no DB, no client, just
the arithmetic that has to be right or members get reminded (or not
reminded) at the wrong time.
"""

from datetime import datetime, timezone

from app.event_announcements import next_occurrence_utc, parse_event_time, venue_from_meta


def test_parse_event_time_variants():
    assert parse_event_time("6:00 PM · Mbalwa Gardens Hall") == (18, 0)
    assert parse_event_time("6:30pm - Hall") == (18, 30)
    assert parse_event_time("18:00, Community Hall") == (18, 0)
    assert parse_event_time("6 PM") == (18, 0)
    assert parse_event_time("9:00 AM - Boardroom") == (9, 0)


def test_parse_event_time_returns_none_when_unparseable():
    assert parse_event_time("") is None
    assert parse_event_time("Community Hall") is None


def test_venue_from_meta_strips_the_time_prefix():
    assert venue_from_meta("6:00 PM · Gardens Hall") == "Gardens Hall"
    assert venue_from_meta("Gardens Hall") == "Gardens Hall"  # no time prefix at all


def test_next_occurrence_rolls_to_next_week_once_todays_time_has_passed():
    # A Wednesday 10:00 UTC "now", asking for a Wednesday 09:00 UTC slot —
    # that slot already happened today, so it must roll to next Wednesday.
    now = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)  # Wed
    result = next_occurrence_utc("WED", 9, 0, now=now)
    assert result.date().isoformat() == "2026-07-15"
    assert result.weekday() == 2  # Wednesday


def test_next_occurrence_stays_today_when_the_time_hasnt_passed_yet():
    now = datetime(2026, 7, 8, 6, 0, tzinfo=timezone.utc)  # Wed, 6am
    result = next_occurrence_utc("WED", 9, 0, now=now)
    assert result.date().isoformat() == "2026-07-08"


def test_next_occurrence_finds_a_different_weekday():
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)  # Thursday
    result = next_occurrence_utc("MON", 15, 0, now=now)
    assert result.date().isoformat() == "2026-07-13"  # the following Monday
