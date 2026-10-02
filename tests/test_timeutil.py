from datetime import datetime, timezone

from zoneinfo import ZoneInfo

from notification_dispatch import timeutil

NY = ZoneInfo("America/New_York")
SCL = ZoneInfo("America/Santiago")


def utc(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


def test_window_basic_and_cross_midnight():
    wall = datetime(2026, 3, 1, 23, 30)
    assert timeutil.local_wall_time_in_window(wall, "22:00", "23:45")
    assert not timeutil.local_wall_time_in_window(wall, "22:00", "23:30")
    assert timeutil.local_wall_time_in_window(wall, "22:00", "07:00")
    early = datetime(2026, 3, 1, 6, 0)
    assert timeutil.local_wall_time_in_window(early, "22:00", "07:00")
    assert not timeutil.local_wall_time_in_window(early, "22:00", "06:00")
    assert not timeutil.local_wall_time_in_window(wall, "08:00", "08:00")


def test_dnd_exit_end_inside_spring_gap():
    # 2026-03-08 NY: 02:00-03:00 local does not exist; DND 00:00-02:30.
    # Exit must be the transition instant 07:00:00Z (wall jumps to 03:00).
    ts = utc(2026, 3, 8, 5, 0)  # 00:00 local, inside DND
    assert timeutil.dnd_exit(NY, ts, "00:00", "02:30") == utc(2026, 3, 8, 7, 0)


def test_dnd_exit_end_exactly_at_gap_start():
    ts = utc(2026, 3, 8, 5, 0)
    # DND ends 02:00 == gap start; first wall >= 02:00 is 03:00 local.
    assert timeutil.dnd_exit(NY, ts, "00:00", "02:00") == utc(2026, 3, 8, 7, 0)


def test_dnd_exit_normal_day():
    ts = utc(2026, 3, 1, 3, 0)  # 2026-02-28 22:00 local
    assert timeutil.dnd_exit(NY, ts, "21:00", "23:30") == utc(2026, 3, 1, 4, 30)


def test_dnd_exit_cross_midnight_late_and_early():
    late = utc(2026, 3, 2, 3, 0)   # 2026-03-01 22:00 local
    early = utc(2026, 3, 2, 10, 0)  # 2026-03-02 05:00 local
    assert timeutil.dnd_exit(NY, late, "22:00", "07:00") == utc(2026, 3, 2, 12, 0)
    assert timeutil.dnd_exit(NY, early, "22:00", "07:00") == utc(2026, 3, 2, 12, 0)


def test_dnd_exit_ambiguous_end_first_and_second_pass():
    # 2026-11-01 NY: 01:00-02:00 happens twice; DND 00:30-01:30.
    # First pass event: exit at first 01:30 local == 05:30 UTC.
    first_pass = utc(2026, 11, 1, 5, 15)  # 01:15 EDT
    assert timeutil.dnd_exit(NY, first_pass, "00:30", "01:30") == utc(2026, 11, 1, 5, 30)
    # Second pass event: must be postponed again, to second 01:30 == 06:30 UTC.
    second_pass = utc(2026, 11, 1, 6, 15)  # 01:15 EST
    assert timeutil.dnd_exit(NY, second_pass, "00:30", "01:30") == utc(2026, 11, 1, 6, 30)


def test_next_local_date_midnight_springs_forward():
    # 2026-09-06 Santiago: local 00:00 does not exist (23:59:59 -> 01:00).
    # First instant of 2026-09-06 local is 04:00:00 UTC.
    ts = utc(2026, 9, 5, 12, 0)  # local date 2026-09-05
    assert timeutil.first_utc_of_next_local_date(SCL, ts) == utc(2026, 9, 6, 4, 0)


def test_next_local_date_midnight_falls_back():
    # 2026-04-04 Santiago: 23:00-24:00 repeats; 2026-04-05 has 25 hours.
    ts = utc(2026, 4, 4, 12, 0)  # local date 2026-04-04
    assert timeutil.first_utc_of_next_local_date(SCL, ts) == utc(2026, 4, 5, 4, 0)
    ts2 = utc(2026, 4, 5, 12, 0)  # local date 2026-04-05
    assert timeutil.first_utc_of_next_local_date(SCL, ts2) == utc(2026, 4, 6, 4, 0)


def test_next_local_date_normal():
    ts = utc(2026, 1, 15, 12, 0)
    assert timeutil.first_utc_of_next_local_date(NY, ts) == utc(2026, 1, 16, 5, 0)
