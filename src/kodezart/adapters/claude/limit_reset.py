"""The reset time a Claude usage-limit message states in plain words.

The CLI ends a session it stopped on a usage limit with a result whose
text names the reset: ``You've hit your session limit · resets 3:20pm
(Europe/Berlin)`` for the five-hour window, and ``You've hit your weekly
limit · resets Oct 3 at 10pm (Europe/Berlin)`` for the weekly one.  That
wording is this vendor's, so it is read here, in the adapter, and reaches
the rest of the system only as an instant on the result event.
"""

import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

#: "resets 3:20pm (Europe/Berlin)", "resets 15:20 (UTC)",
#: "resets Oct 3 at 10pm (Europe/Berlin)".
_RESET = re.compile(
    r"resets\s+(?:([a-z]{3})[a-z]*\.?\s+(\d{1,2})\s+at\s+)?"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*\(([^)]+)\)",
    re.IGNORECASE,
)

_MONTHS = "jan feb mar apr may jun jul aug sep oct nov dec".split()


def stated_reset(text: str | None, *, now: datetime) -> datetime | None:
    """The instant a limit message says the limit resets, or ``None``.

    A message with a date (``Oct 3 at 10pm``) resets on the next such date:
    this year when that is still ahead of *now*, next year otherwise.  A
    message with a wall-clock time only resets the next time that clock
    reads it: today when still ahead of *now*, tomorrow otherwise.  A
    message with no reset, an impossible date or time, or a zone name the
    zone database does not know is ``None``.
    """
    if not text:
        return None
    match = _RESET.search(text)
    if match is None:
        return None
    month_name, day, hour_text, minute_text, meridiem, zone_name = match.groups()
    hour = int(hour_text)
    minute = int(minute_text or 0)
    if meridiem is not None:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem.lower() == "pm" else 0)
    if hour > 23 or minute > 59:
        return None
    try:
        zone = ZoneInfo(zone_name.strip())
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return None
    local_now = now.astimezone(zone)
    if month_name is None:
        candidate = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= local_now:
            candidate += timedelta(days=1)
        return candidate
    if month_name.lower() not in _MONTHS:
        return None
    month = _MONTHS.index(month_name.lower()) + 1
    for year in (local_now.year, local_now.year + 1):
        try:
            candidate = datetime(year, month, int(day), hour, minute, tzinfo=zone)
        except ValueError:
            return None
        if candidate > local_now:
            return candidate
    return None
