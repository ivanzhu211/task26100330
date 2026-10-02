"""Tests for the notification delivery scheduler."""

from __future__ import annotations

import random
from datetime import datetime, timezone

import pytest

from notify_scheduler import Scheduler


def ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).replace(tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------- DST edges


def test_ny_spring_dnd_end_inside_gap():
    """America/New_York 2026-03-08: 02:00-03:00 is skipped. DND ends 02:30,
    which does not exist; delivery must wait for the gap end (03:00 EDT)."""
    s = Scheduler(":memory:")
    s.set_preferences("L", "America/New_York", "22:00", "02:30", {}, ts("2026-03-01T00:00:00"))
    # 2026-03-08 06:15 UTC == 01:15 EST, inside DND.
    s.emit("L", "streak", ts("2026-03-08T06:15:00"))
    out = s.dispatch(ts("2026-03-09T00:00:00"))
    assert [d["deliver_at"] for d in out] == [ts("2026-03-08T07:00:00")]


def test_ny_fall_event_in_repeated_hour_deferred_again():
    """America/New_York 2026-11-01: 01:00-02:00 happens twice. DND 00:30-01:30
    covers both passes; an event in the second pass must wait for the second
    01:30 (06:30 UTC), not the first (05:30 UTC)."""
    s = Scheduler(":memory:")
    s.set_preferences("L", "America/New_York", "00:30", "01:30", {}, ts("2026-10-01T00:00:00"))
    s.emit("L", "review", ts("2026-11-01T05:15:00"))  # first 01:15 (EDT)
    s.emit("L", "review", ts("2026-11-01T06:15:00"))  # second 01:15 (EST)
    out = s.dispatch(ts("2026-11-02T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2026-11-01T05:30:00"),  # first 01:30
        ts("2026-11-01T06:30:00"),  # second 01:30
    ]


def test_santiago_spring_midnight_gap_next_day_start():
    """America/Santiago 2025-09-07: clocks jump 00:00 -> 01:00, so the local
    date 2025-09-07 begins at 01:00 (04:00 UTC). A capped notification must
    roll to that first instant, not to a nonexistent midnight."""
    s = Scheduler(":memory:")
    s.set_preferences("L", "America/Santiago", "00:00", "00:00", {"lvl": 1}, ts("2025-09-01T00:00:00"))
    s.emit("L", "lvl", ts("2025-09-06T20:00:00"))  # local 2025-09-06 17:00 (-03? no: -04)
    s.emit("L", "lvl", ts("2025-09-06T21:00:00"))  # same local date, cap full
    out = s.dispatch(ts("2025-09-08T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2025-09-06T20:00:00"),
        ts("2025-09-07T04:00:00"),  # first instant of 2025-09-07 (wall 01:00)
    ]


def test_santiago_fall_midnight_next_day_start():
    """America/Santiago 2025-04-06: clocks fall back at midnight; the local
    date 2025-04-06 begins at a single, existing midnight (04:00 UTC)."""
    s = Scheduler(":memory:")
    s.set_preferences("L", "America/Santiago", "00:00", "00:00", {"lvl": 1}, ts("2025-04-01T00:00:00"))
    s.emit("L", "lvl", ts("2025-04-05T20:00:00"))  # local 2025-04-05 17:00 (-03)
    s.emit("L", "lvl", ts("2025-04-05T21:00:00"))  # same local date, cap full
    out = s.dispatch(ts("2025-04-07T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2025-04-05T20:00:00"),
        ts("2025-04-06T04:00:00"),  # first instant of 2025-04-06 (wall 00:00)
    ]


# ------------------------------------------------------------- basic rules


def test_default_preferences_immediate_delivery():
    s = Scheduler(":memory:")
    s.emit("L", "streak", ts("2026-01-01T10:00:00"))
    out = s.dispatch(ts("2026-01-01T10:00:00"))
    assert [d["deliver_at"] for d in out] == [ts("2026-01-01T10:00:00")]


def test_equal_dnd_bounds_mean_no_dnd():
    s = Scheduler(":memory:")
    s.set_preferences("L", "UTC", "03:00", "03:00", {}, ts("2026-01-01T00:00:00"))
    s.emit("L", "streak", ts("2026-01-02T03:00:00"))
    out = s.dispatch(ts("2026-01-02T03:00:00"))
    assert [d["deliver_at"] for d in out] == [ts("2026-01-02T03:00:00")]


def test_overnight_dnd():
    s = Scheduler(":memory:")
    s.set_preferences("L", "UTC", "22:00", "07:00", {}, ts("2026-01-01T00:00:00"))
    s.emit("L", "a", ts("2026-01-02T21:59:00"))   # before DND
    s.emit("L", "b", ts("2026-01-02T22:00:00"))   # DND start (inclusive)
    s.emit("L", "c", ts("2026-01-03T06:59:00"))   # still DND
    s.emit("L", "d", ts("2026-01-03T07:00:00"))   # DND end (exclusive)
    out = s.dispatch(ts("2026-01-04T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2026-01-02T21:59:00"),
        ts("2026-01-03T07:00:00"),
        ts("2026-01-03T07:00:00"),
        ts("2026-01-03T07:00:00"),
    ]


def test_preferences_apply_only_strictly_after_now():
    s = Scheduler(":memory:")
    # Pref set at now=T must not affect an event occurring exactly at T.
    s.set_preferences("L", "UTC", "00:00", "23:59", {}, ts("2026-01-01T12:00:00"))
    s.emit("L", "a", ts("2026-01-01T12:00:00"))  # default pref: no DND
    s.emit("L", "b", ts("2026-01-01T12:00:01"))  # new pref: inside DND
    out = s.dispatch(ts("2026-01-03T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2026-01-01T12:00:00"),
        ts("2026-01-01T23:59:00"),
    ]


def test_same_category_queue_order():
    """A notification is never delivered before the previous one in its
    category, even if the previous one is held back by a cap."""
    s = Scheduler(":memory:")
    s.set_preferences("L", "UTC", "00:00", "00:00", {"c": 1}, ts("2026-01-01T00:00:00"))
    s.emit("L", "c", ts("2026-01-02T10:00:00"))
    s.emit("L", "c", ts("2026-01-02T11:00:00"))
    s.emit("L", "c", ts("2026-01-02T12:00:00"))
    out = s.dispatch(ts("2026-01-10T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2026-01-02T10:00:00"),
        ts("2026-01-03T00:00:00"),
        ts("2026-01-04T00:00:00"),
    ]


def test_cap_change_applies_to_later_instants():
    s = Scheduler(":memory:")
    s.set_preferences("L", "UTC", "00:00", "00:00", {"c": 1}, ts("2026-01-01T00:00:00"))
    s.emit("L", "c", ts("2026-01-02T10:00:00"))
    # Raise the cap; takes effect for instants after 2026-01-02T11:00:00.
    s.set_preferences("L", "UTC", "00:00", "00:00", {"c": 5}, ts("2026-01-02T11:00:00"))
    s.emit("L", "c", ts("2026-01-02T12:00:00"))
    out = s.dispatch(ts("2026-01-10T00:00:00"))
    assert [d["deliver_at"] for d in out] == [
        ts("2026-01-02T10:00:00"),
        ts("2026-01-02T12:00:00"),
    ]


def test_unlisted_category_uncapped():
    s = Scheduler(":memory:")
    s.set_preferences("L", "UTC", "00:00", "00:00", {"capped": 1}, ts("2026-01-01T00:00:00"))
    for i in range(5):
        s.emit("L", "free", ts("2026-01-02T10:00:00") + i)
    out = s.dispatch(ts("2026-01-03T00:00:00"))
    assert len(out) == 5


def test_reopen_database_keeps_state(tmp_path):
    db = str(tmp_path / "n.db")
    s = Scheduler(db)
    s.set_preferences("L", "UTC", "22:00", "07:00", {}, ts("2026-01-01T00:00:00"))
    s.emit("L", "a", ts("2026-01-02T23:00:00"))
    s.close()
    s2 = Scheduler(db)
    out = s2.dispatch(ts("2026-01-04T00:00:00"))
    assert [d["deliver_at"] for d in out] == [ts("2026-01-03T07:00:00")]
    s2.close()


# --------------------------------------- dispatch-timing independence


def _random_ops(seed: int) -> list[tuple]:
    """Random preference changes (tz, dnd, caps) and emits over a monotonic
    timeline crossing several DST transitions."""
    rng = random.Random(seed)
    tzs = ["UTC", "America/New_York", "America/Santiago", "Australia/Sydney", "Asia/Shanghai"]
    categories = ["streak", "level", "review"]
    learners = ["L1", "L2", "L3", "L4"]
    ops: list[tuple] = []
    now = ts("2026-03-01T00:00:00")
    end = ts("2026-04-20T00:00:00")
    while now < end:
        now += rng.randint(600, 20000)
        if rng.random() < 0.18:
            learner = rng.choice(learners)
            start_h, start_m = rng.randint(0, 23), rng.choice([0, 15, 30, 45])
            if rng.random() < 0.15:
                dnd = ("00:00", "00:00")  # no DND
            else:
                span = rng.randint(1, 14)
                end_h = (start_h + span) % 24
                dnd = (f"{start_h:02d}:{start_m:02d}", f"{end_h:02d}:{start_m:02d}")
            caps = {c: rng.randint(1, 3) for c in categories if rng.random() < 0.6}
            ops.append(("pref", learner, rng.choice(tzs), dnd[0], dnd[1], caps, now))
        else:
            ops.append(("emit", rng.choice(learners), rng.choice(categories), now))
    return ops, now


def _run(ops, final_now, db_path, strategy: str, seed: int = 0) -> dict[str, list]:
    rng = random.Random(seed)
    s = Scheduler(db_path)
    dispatched = []
    reopen_done = False
    for i, op in enumerate(ops):
        if op[0] == "pref":
            _, learner, tz, dnd_start, dnd_end, caps, now = op
            s.set_preferences(learner, tz, dnd_start, dnd_end, caps, now)
        else:
            _, learner, category, now = op
            s.emit(learner, category, now)
        if strategy == "every":
            dispatched.extend(s.dispatch(now))
        elif strategy == "random":
            if strategy == "random" and not reopen_done and i == len(ops) // 2:
                s.close()
                s = Scheduler(db_path)
                reopen_done = True
            if rng.random() < 0.25:
                dispatched.extend(s.dispatch(now))
    dispatched.extend(s.dispatch(final_now + 1))
    inboxes = {learner: s.inbox(learner) for learner in ("L1", "L2", "L3", "L4")}
    s.close()
    return inboxes


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_dispatch_timing_does_not_change_results(tmp_path, seed):
    ops, final_now = _random_ops(seed)
    a = _run(ops, final_now, str(tmp_path / "a.db"), "every")
    b = _run(ops, final_now, str(tmp_path / "b.db"), "final")
    c = _run(ops, final_now, str(tmp_path / "c.db"), "random", seed=seed + 1000)
    assert a == b == c
