"""Pure-function tests for the time parsing and next-occurrence math behind
fellowship reminders and the Next Meeting card — no DB, no client, just
the arithmetic that has to be right or members get reminded (or not
reminded) at the wrong time.
"""

from datetime import datetime, timezone

from app.event_announcements import (
    _shifted_cron,
    is_registration_open,
    next_occurrence_utc,
    parse_event_end_time,
    parse_event_time,
    venue_from_meta,
)


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


def test_parse_event_end_time_reads_the_second_time_of_a_range():
    assert parse_event_end_time("6:00 PM to 8:00 PM · Gardens Hall") == (20, 0)
    assert parse_event_end_time("18:00 to 20:30, Hall") == (20, 30)
    # Legacy metas — a dash separates time from VENUE, never an end time.
    assert parse_event_end_time("6:00 PM - Gardens Hall") is None
    assert parse_event_end_time("6:00 PM · Hall") is None
    assert parse_event_end_time("") is None
    # Both times stay parseable together: start still comes from the front.
    assert parse_event_time("6:00 PM to 8:00 PM · Hall") == (18, 0)
    assert venue_from_meta("6:00 PM to 8:00 PM · Hall") == "Hall"


def test_registration_closes_15_minutes_before_the_end_time():
    # 2026-07-21 is a Tuesday. Event 6-8 PM EAT (= 15:00-17:00 UTC);
    # registration closes 16:45 UTC (7:45 PM EAT).
    meta = "6:00 PM to 8:00 PM · Hall"
    before = datetime(2026, 7, 21, 16, 30, tzinfo=timezone.utc)
    inside = datetime(2026, 7, 21, 16, 50, tzinfo=timezone.utc)
    after_end = datetime(2026, 7, 21, 18, 30, tzinfo=timezone.utc)
    assert is_registration_open("TUE", meta, now=before) is True
    assert is_registration_open("TUE", meta, now=inside) is False
    assert is_registration_open("TUE", meta, now=after_end) is False
    # A different day of the week: this occurrence isn't today — open.
    assert is_registration_open("WED", meta, now=inside) is True
    # No end time at all (legacy meta): never closes.
    assert is_registration_open("TUE", "6:00 PM - Hall", now=inside) is True


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


def test_reminder_fires_4_hours_before_the_local_event_time():
    # WED 6:00 PM EAT (Africa/Kampala, UTC+3) = WED 15:00 UTC; 4 hours
    # before that is still the same UTC day.
    dow, hour, minute = _shifted_cron("WED", 18, 0, -4)
    assert (dow, hour, minute) == ("wed", 11, 0)


def test_thank_you_fires_1_hour_after_the_local_event_time():
    dow, hour, minute = _shifted_cron("WED", 18, 0, 1)
    assert (dow, hour, minute) == ("wed", 16, 0)


def test_reminder_rolls_the_weekday_back_across_midnight():
    # MON 1:00 AM EAT is SUN 22:00 UTC the day before; 4 hours before that
    # local time must land on SUN in UTC terms, not MON.
    dow, hour, minute = _shifted_cron("MON", 1, 0, -4)
    assert dow == "sun"


def test_thank_you_stays_on_the_same_utc_day_even_when_local_day_rolls_over():
    # FRI 11:30 PM EAT rolling into SAT locally an hour later is still
    # FRI in UTC (Kampala is ahead of UTC).
    dow, hour, minute = _shifted_cron("FRI", 23, 30, 1)
    assert dow == "fri"
