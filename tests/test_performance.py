"""Backlog and history must not force full replays on every dispatch."""

import time
from datetime import datetime, timezone

from notification_dispatch import NotificationDispatcher


def utc(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


def test_large_backlog_dispatches_quickly(tmp_path):
    disp = NotificationDispatcher(str(tmp_path / "n.db"))
    disp.set_preferences("a", "America/New_York", "01:00", "05:00",
                         {"streak": 2000}, utc(2026, 1, 1, 0))
    base = utc(2026, 3, 1, 0)
    for i in range(10_000):
        disp.emit("a", "streak", base + i * 60)
    t0 = time.monotonic()
    out = disp.dispatch(utc(2026, 4, 1, 0))
    elapsed = time.monotonic() - t0
    assert len(out) == 10_000
    assert elapsed < 20
    # A no-op dispatch afterwards touches nothing.
    t0 = time.monotonic()
    assert disp.dispatch(utc(2026, 4, 1, 1)) == []
    assert time.monotonic() - t0 < 1
    disp.close()
