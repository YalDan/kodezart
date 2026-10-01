"""The Claude adapter reads the reset its limit message states, and only it."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from claude_agent_sdk import ResultMessage

from kodezart.adapters.claude.limit_reset import stated_reset
from kodezart.adapters.claude.sdk_mapping import map_message
from kodezart.types.domain.agent import ResultEvent

BERLIN = ZoneInfo("Europe/Berlin")
SESSION_LIMIT = "You've hit your session limit · resets 3:20pm (Europe/Berlin)"
#: The text a recorded weekly-limit death carried as its result.
WEEKLY_LIMIT = "You've hit your weekly limit · resets Oct 3 at 10pm (Europe/Berlin)"
#: 12:00 in Berlin on the day of the recorded deaths.
NOON_BERLIN = datetime(2026, 9, 29, 12, 0, tzinfo=BERLIN)


def test_a_twelve_hour_reset_in_a_named_zone_is_today_when_still_ahead() -> None:
    at = stated_reset(SESSION_LIMIT, now=NOON_BERLIN.astimezone(UTC))

    assert at == datetime(2026, 9, 29, 15, 20, tzinfo=BERLIN)
    assert at is not None
    assert (at - NOON_BERLIN).total_seconds() == 3 * 3600 + 20 * 60


@pytest.mark.parametrize(
    ("text", "hour", "minute"),
    [
        ("resets 9am (Europe/Berlin)", 9, 0),
        ("resets 12am (Europe/Berlin)", 0, 0),
        ("resets 12pm (Europe/Berlin)", 12, 0),
        ("resets 11:45PM (Europe/Berlin)", 23, 45),
        ("resets 15:20 (Europe/Berlin)", 15, 20),
    ],
)
def test_the_clock_reading_is_read_on_both_clocks(
    text: str, hour: int, minute: int
) -> None:
    at = stated_reset(text, now=NOON_BERLIN)

    assert at is not None
    assert (at.hour, at.minute) == (hour, minute)
    assert at > NOON_BERLIN


def test_a_reset_already_past_today_rolls_over_to_tomorrow() -> None:
    four_pm = datetime(2026, 9, 29, 16, 0, tzinfo=BERLIN)

    at = stated_reset(SESSION_LIMIT, now=four_pm)

    assert at == datetime(2026, 9, 30, 15, 20, tzinfo=BERLIN)


def test_the_zone_is_the_named_one_not_the_hosts() -> None:
    at = stated_reset("resets 3:20pm (America/New_York)", now=NOON_BERLIN)

    assert at is not None
    assert at.utcoffset() == timedelta(hours=-4)
    assert at.astimezone(UTC) == datetime(2026, 9, 29, 19, 20, tzinfo=UTC)


def test_the_recorded_weekly_limit_resets_on_its_stated_date() -> None:
    at = stated_reset(WEEKLY_LIMIT, now=NOON_BERLIN)

    assert at == datetime(2026, 10, 3, 22, 0, tzinfo=BERLIN)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("resets Oct 3 at 10:30pm (Europe/Berlin)", datetime(2026, 10, 3, 22, 30)),
        ("resets October 3 at 22:00 (Europe/Berlin)", datetime(2026, 10, 3, 22, 0)),
        ("resets Sep 29 at 3pm (Europe/Berlin)", datetime(2026, 9, 29, 15, 0)),
        # Passed this year already: the next one is next year's.
        ("resets Sep 1 at 9am (Europe/Berlin)", datetime(2027, 9, 1, 9, 0)),
    ],
)
def test_a_dated_reset_is_the_next_such_date(text: str, expected: datetime) -> None:
    assert stated_reset(text, now=NOON_BERLIN) == expected.replace(tzinfo=BERLIN)


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "Claude API error: rate_limit",
        "resets 3:20pm (Mars/Olympus_Mons)",
        "resets 13pm (Europe/Berlin)",
        "resets 25:00 (Europe/Berlin)",
        "resets 12:60pm (Europe/Berlin)",
        "resets 3:20pm",
        "resets 3:20pm (../../etc/passwd)",
        "resets Foo 3 at 10pm (Europe/Berlin)",
        "resets Feb 30 at 10pm (Europe/Berlin)",
        "resets Oct 3 at 13pm (Europe/Berlin)",
    ],
)
def test_an_unreadable_reset_is_none(text: str | None) -> None:
    assert stated_reset(text, now=NOON_BERLIN) is None


def _mapped(*, is_error: bool, result: str) -> ResultEvent:
    (event,) = map_message(
        ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=is_error,
            num_turns=1,
            session_id="session-1",
            result=result,
        )
    )
    assert isinstance(event, ResultEvent)
    return event


def test_an_error_result_carries_the_reset_its_text_states() -> None:
    event = _mapped(is_error=True, result=WEEKLY_LIMIT)

    assert event.rate_limit_resets_at is not None
    assert event.rate_limit_resets_at.tzinfo is not None
    assert "rate_limit_resets_at" not in event.model_dump()


def test_a_delivered_result_carries_no_reset_whatever_its_text_says() -> None:
    event = _mapped(is_error=False, result=SESSION_LIMIT)

    assert event.rate_limit_resets_at is None
