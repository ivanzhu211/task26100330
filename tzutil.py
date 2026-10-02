"""Timezone helpers: resolve local wall-clock times to UTC instants.

Handles DST gaps (nonexistent wall times) and folds (repeated wall times)
without fixed-step scanning of the timeline.
"""

from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

UTC = timezone.utc


def _wall_of(tz: ZoneInfo, ts: int) -> datetime:
    return datetime.fromtimestamp(ts, tz).replace(tzinfo=None)


def wall_utc_candidates(tz: ZoneInfo, day: date, wall: dtime) -> list[int]:
    """UTC timestamps whose local wall time equals `wall` on local date `day`.

    Returns 0 values if the wall time is nonexistent (spring-forward gap),
    1 normally, 2 if ambiguous (fall-back fold). Sorted ascending.
    """
    naive = datetime.combine(day, wall)
    out: list[int] = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=tz, fold=fold)
        ts = int(aware.timestamp())
        if _wall_of(tz, ts) == naive and ts not in out:
            out.append(ts)
    out.sort()
    return out


def first_instant_wall_ge(tz: ZoneInfo, day: date, wall: dtime) -> int:
    """First UTC instant on local date `day` whose wall time is >= `wall`.

    If `wall` falls inside a gap, returns the gap-end instant (the first
    instant whose wall clock has jumped past `wall`).
    """
    candidates = wall_utc_candidates(tz, day, wall)
    if candidates:
        return candidates[0]
    # `wall` is nonexistent: the UTC instants computed with fold=0 and fold=1
    # bracket the transition. Binary-search the first second whose local wall
    # time reaches or passes `wall` (log-time, not fixed-step scanning).
    naive = datetime.combine(day, wall)
    ts0 = int(naive.replace(tzinfo=tz, fold=0).timestamp())
    ts1 = int(naive.replace(tzinfo=tz, fold=1).timestamp())
    lo, hi = min(ts0, ts1), max(ts0, ts1)
    # Invariant: wall(lo) < naive <= wall(hi). Widen defensively if violated.
    while _wall_of(tz, lo) >= naive:
        lo -= 7200
    while _wall_of(tz, hi) < naive:
        hi += 7200
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if _wall_of(tz, mid) >= naive:
            hi = mid
        else:
            lo = mid
    return hi


def first_instant_of_date(tz: ZoneInfo, day: date) -> int:
    """First UTC instant whose local date equals `day` (midnight may be
    ambiguous or nonexistent, e.g. America/Santiago switches at midnight)."""
    return first_instant_wall_ge(tz, day, dtime(0, 0))
