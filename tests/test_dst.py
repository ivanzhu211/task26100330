"""DST boundary behaviour for America/New_York and America/Santiago."""

from datetime import datetime, timezone

import pytest

from notification_dispatch import NotificationDispatcher


def utc(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


@pytest.fixture()
def disp(tmp_path):
    d = NotificationDispatcher(str(tmp_path / "n.db"))
    yield d
    d.close()


# 2026-03-08 America/New_York: 01:59:59-05:00 -> 03:00:00-04:00 at 07:00:00Z.
# 2026-11-01 America/New_York: 01:59:59-04:00 -> 01:00:00-05:00 at 06:00:00Z.
# 2026-04-04/05 America/Santiago: 23:59:59-03:00 -> 23:00:00-04:00 at 03:00:00Z.
# 2026-09-05/06 America/Santiago: 23:59:59-04:00 -> 01:00:00-03:00 at 04:00:00Z.


def test_ny_spring_dnd_end_inside_skipped_period(disp):
    disp.set_preferences(
        "a", "America/New_York", "00:00", "02:30", {}, utc(2026, 3, 1, 0))
    # 2026-03-08 01:00 local == 06:00 UTC, inside DND.
    e = disp.emit("a", "streak", utc(2026, 3, 8, 6, 0))
    disp.dispatch(utc(2026, 3, 9, 0))
    # 02:30 local never happens; DND ends at the jump instant 07:00 UTC.
    assert disp.inbox("a") == [
        {"event_id": e, "category": "streak",
         "occurred_at": utc(2026, 3, 8, 6, 0),
         "delivered_at": utc(2026, 3, 8, 7, 0)}
    ]


def test_ny_fall_repeated_hour_second_pass_postponed_again(disp):
    disp.set_preferences(
        "a", "America/New_York", "00:30", "01:30", {}, utc(2026, 10, 1, 0))
    # First pass through 01:15 EDT == 05:15 UTC.
    e1 = disp.emit("a", "review", utc(2026, 11, 1, 5, 15))
    # Second pass through 01:15 EST == 06:15 UTC.
    e2 = disp.emit("a", "review", utc(2026, 11, 1, 6, 15))
    disp.dispatch(utc(2026, 11, 2, 0))
    inbox = disp.inbox("a")
    # First-pass event exits at first 01:30 == 05:30 UTC.
    assert inbox[0]["delivered_at"] == utc(2026, 11, 1, 5, 30)
    # Second-pass event is again inside DND and exits at second 01:30 == 06:30 UTC.
    assert inbox[1]["delivered_at"] == utc(2026, 11, 1, 6, 30)


def test_santiago_spring_midnight_cap_rolls_to_next_local_date(disp):
    # Cap 1/day; two events on 2026-09-05 local. The second must wait for the
    # first instant of 2026-09-06 local, which is 04:00 UTC because local
    # midnight 2026-09-06 00:00 does not exist (clocks jump to 01:00).
    disp.set_preferences(
        "a", "America/Santiago", "00:00", "00:00", {"streak": 1},
        utc(2026, 9, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 9, 5, 12, 0))  # 2026-09-05 08:00 local
    e2 = disp.emit("a", "streak", utc(2026, 9, 5, 13, 0))  # 2026-09-05 09:00 local
    disp.dispatch(utc(2026, 9, 7, 0))
    inbox = disp.inbox("a")
    assert inbox[0]["delivered_at"] == utc(2026, 9, 5, 12, 0)
    assert inbox[1]["delivered_at"] == utc(2026, 9, 6, 4, 0)


def test_santiago_fall_midnight_cap_rolls_to_next_local_date(disp):
    # 2026-04-04 local has 25 hours (23:00-24:00 repeats). Cap 1/day; the
    # second event on 2026-04-04 local rolls to 2026-04-05 00:00 local, which
    # exists and equals 04:00 UTC.
    disp.set_preferences(
        "a", "America/Santiago", "00:00", "00:00", {"streak": 1},
        utc(2026, 4, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 4, 4, 12, 0))  # 2026-04-04 09:00 local
    e2 = disp.emit("a", "streak", utc(2026, 4, 4, 13, 0))  # 2026-04-04 10:00 local
    disp.dispatch(utc(2026, 4, 6, 0))
    inbox = disp.inbox("a")
    assert inbox[0]["delivered_at"] == utc(2026, 4, 4, 12, 0)
    assert inbox[1]["delivered_at"] == utc(2026, 4, 5, 4, 0)


def test_santiago_spring_event_during_repeated_hour_not_double_counted(disp):
    # Events in both passes of the repeated 23:00-24:00 hour on 2026-04-04
    # share the local date 2026-04-04, so cap 2 allows both but not a third.
    disp.set_preferences(
        "a", "America/Santiago", "00:00", "00:00", {"streak": 2},
        utc(2026, 4, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 4, 5, 2, 30))  # 23:30-03:00 first pass
    e2 = disp.emit("a", "streak", utc(2026, 4, 5, 3, 30))  # 23:30-04:00 second pass
    e3 = disp.emit("a", "streak", utc(2026, 4, 5, 4, 30))  # 2026-04-05 00:30 local
    disp.dispatch(utc(2026, 4, 7, 0))
    inbox = disp.inbox("a")
    assert [i["delivered_at"] for i in inbox] == [
        utc(2026, 4, 5, 2, 30),
        utc(2026, 4, 5, 3, 30),
        utc(2026, 4, 5, 4, 30),
    ]
