"""Local wall-clock helpers built on zoneinfo.

All public timestamps in the library are UTC integer seconds.  This module
converts between UTC instants and local wall clocks without ever fixing a
timezone to a single UTC offset and without fixed-step scanning of the
timeline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def utc_seconds_to_local(tz: ZoneInfo, ts: int) -> datetime:
    """Return the aware local datetime for UTC integer second ``ts``."""
    return datetime.fromtimestamp(ts, UTC).astimezone(tz)


def _hhmm_to_time(hhmm: str):
    hour, minute = int(hhmm[:2]), int(hhmm[3:5])
    return hour, minute


def local_wall_time_in_window(wall: datetime, start: str, end: str) -> bool:
    """True if local wall time falls in [start, end); start == end means no DND.

    start > end denotes a window crossing midnight.
    """
    if start == end:
        return False
    sh, sm = _hhmm_to_time(start)
    eh, em = _hhmm_to_time(end)
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    cur = wall.hour * 60 + wall.minute
    if start_min < end_min:
        return start_min <= cur < end_min
    return cur >= start_min or cur < end_min


def _offset_at(tz: ZoneInfo, naive: datetime, fold: int) -> timedelta:
    return naive.replace(tzinfo=tz, fold=fold).utcoffset()


def _first_utc_with_wall_ge(tz: ZoneInfo, target: datetime, after: int) -> int:
    """Earliest UTC second >= ``after`` whose local wall clock >= ``target``.

    ``target`` is a naive local datetime.  Wall clock as a function of UTC is
    non-decreasing except across fall-back transitions; the cases below keep
    every search inside a region where it is monotonic.
    """
    off0 = _offset_at(tz, target, 0)
    off1 = _offset_at(tz, target, 1)
    if off0 == off1:
        # Unambiguous local time: wall clock is strictly increasing nearby.
        u = int((target.replace(tzinfo=UTC) - off0).timestamp())
        return max(u, after) if u < after else u
    if off0 > off1:
        # Ambiguous (repeated) local time: two valid occurrences, u0 < u1.
        u0 = int((target.replace(tzinfo=UTC) - off0).timestamp())
        u1 = int((target.replace(tzinfo=UTC) - off1).timestamp())
        if after <= u0:
            return u0
        if after <= u1:
            return u1
        return after
    # Nonexistent local time (spring-forward gap).  The transition instant
    # lies in (target - off1, target - off0); wall clock is non-decreasing
    # across a forward jump, so binary-search for the first UTC second whose
    # wall clock reaches ``target``.
    lo = int((target.replace(tzinfo=UTC) - off1).timestamp())  # wall < target
    hi = int((target.replace(tzinfo=UTC) - off0).timestamp())  # wall >= target
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if utc_seconds_to_local(tz, mid).replace(tzinfo=None) >= target:
            hi = mid
        else:
            lo = mid
    return max(hi, after)


def first_utc_with_wall_ge(tz: ZoneInfo, target: datetime, after: int) -> int:
    return _first_utc_with_wall_ge(tz, target, after)


def dnd_exit(tz: ZoneInfo, ts: int, start: str, end: str) -> int:
    """Earliest UTC second >= ``ts`` that is outside the DND window.

    Precondition: ``ts`` is inside the DND window for ``tz``.
    """
    wall = utc_seconds_to_local(tz, ts)
    sh, sm = _hhmm_to_time(start)
    eh, em = _hhmm_to_time(end)
    start_min = sh * 60 + sm
    end_min = eh * 60 + em
    cur = wall.hour * 60 + wall.minute
    if start_min < end_min:
        target_date = wall.date()
    else:
        # Cross-midnight window: if we are in the late-evening part, the exit
        # is ``end`` on the next local date; otherwise it is ``end`` today.
        if cur >= start_min:
            target_date = wall.date() + timedelta(days=1)
        else:
            target_date = wall.date()
    target = datetime(target_date.year, target_date.month, target_date.day, eh, em)
    return _first_utc_with_wall_ge(tz, target, ts)


def first_utc_of_next_local_date(tz: ZoneInfo, ts: int) -> int:
    """First UTC instant whose local date is strictly after the local date of ``ts``."""
    wall = utc_seconds_to_local(tz, ts)
    next_date = wall.date() + timedelta(days=1)
    target = datetime(next_date.year, next_date.month, next_date.day)
    return _first_utc_with_wall_ge(tz, target, ts)
