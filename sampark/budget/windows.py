"""Window arithmetic — Design Lock §3.1, §14.3.

A "window" is an IST calendar date. Quiet hours already carve the day
at 09:00/21:00 IST, so one IST day contains exactly one contiguous
sendable band — this is why the window and the calendar date coincide.

Pure functions only: every one of these takes its instant explicitly
and reads no clock (Design Lock §3.5).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sampark.allocator.constants import IST, QUIET_HOURS_END_HOUR, QUIET_HOURS_START_HOUR


def window_id_for(instant: datetime) -> date:
    """The IST calendar date `instant` falls on."""
    return instant.astimezone(IST).date()


def window_start_for(window_id: date) -> datetime:
    """09:00 IST on `window_id` — the first sendable instant of that window."""
    return datetime.combine(window_id, time(QUIET_HOURS_END_HOUR, 0, 0), tzinfo=IST)


def next_window_start(window_id: date) -> datetime:
    """09:00 IST on the day after `window_id` — used as `next_eligible_at`
    for a candidate that lost its window's allocation, exhausted a
    contact cap, or was blocked by an active claim."""
    return window_start_for(window_id + timedelta(days=1))


def is_quiet_hours(instant: datetime) -> bool:
    """True if `instant` falls in [21:00, 09:00) Asia/Kolkata."""
    local = instant.astimezone(IST)
    return local.hour >= QUIET_HOURS_START_HOUR or local.hour < QUIET_HOURS_END_HOUR


def next_quiet_hours_boundary(instant: datetime) -> datetime:
    """The next 09:00 IST at or after `instant`, given `instant` is
    inside quiet hours. An instant in the EVENING half [21:00, 24:00)
    defers to the NEXT calendar day's 09:00; an instant in the EARLY
    half [00:00, 09:00) defers to the SAME day's 09:00 — it is already
    on the correct side of the boundary."""
    local = instant.astimezone(IST)
    if not is_quiet_hours(instant):
        raise ValueError(f"{instant!r} is not inside quiet hours")
    target_date = local.date() if local.hour < QUIET_HOURS_END_HOUR else local.date() + timedelta(days=1)
    return window_start_for(target_date)
