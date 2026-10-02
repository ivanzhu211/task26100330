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


def test_default_prefs_deliver_immediately(disp):
    e1 = disp.emit("a", "streak", utc(2026, 1, 1, 10))
    e2 = disp.emit("a", "streak", utc(2026, 1, 1, 11))
    out = disp.dispatch(utc(2026, 1, 1, 12))
    assert [d["event_id"] for d in out] == [e1, e2]
    assert [d["delivered_at"] for d in out] == [utc(2026, 1, 1, 10), utc(2026, 1, 1, 11)]
    inbox = disp.inbox("a")
    assert [i["event_id"] for i in inbox] == [e1, e2]


def test_dispatch_returns_only_new_entries(disp):
    disp.emit("a", "streak", utc(2026, 1, 1, 10))
    assert len(disp.dispatch(utc(2026, 1, 1, 10))) == 1
    assert disp.dispatch(utc(2026, 1, 1, 11)) == []
    e2 = disp.emit("a", "streak", utc(2026, 1, 1, 12))
    out = disp.dispatch(utc(2026, 1, 1, 12))
    assert [d["event_id"] for d in out] == [e2]


def test_dnd_postpones(disp):
    disp.set_preferences("a", "UTC", "22:00", "07:00", {}, utc(2026, 1, 1, 0))
    e = disp.emit("a", "review", utc(2026, 1, 2, 23, 0))
    disp.dispatch(utc(2026, 1, 3, 12))
    assert disp.inbox("a")[0]["delivered_at"] == utc(2026, 1, 3, 7, 0)


def test_pref_applies_only_strictly_after_now(disp):
    disp.set_preferences("a", "UTC", "10:00", "11:00", {}, utc(2026, 1, 1, 9, 0))
    # Event exactly at the boundary instant 09:00: pref does not apply there,
    # but the event occurs after, at 10:30, where it does.
    e1 = disp.emit("a", "review", utc(2026, 1, 1, 10, 30))
    disp.dispatch(utc(2026, 1, 2, 0))
    assert disp.inbox("a")[0]["delivered_at"] == utc(2026, 1, 1, 11, 0)


def test_daily_cap_rolls_to_next_day(disp):
    disp.set_preferences("a", "UTC", "00:00", "00:00", {"streak": 2}, utc(2026, 1, 1, 0))
    ids = [disp.emit("a", "streak", utc(2026, 1, 2, 8, 0)) for _ in range(3)]
    disp.dispatch(utc(2026, 1, 5, 0))
    inbox = disp.inbox("a")
    assert [i["delivered_at"] for i in inbox] == [
        utc(2026, 1, 2, 8, 0),
        utc(2026, 1, 2, 8, 0),
        utc(2026, 1, 3, 0, 0),
    ]


def test_unlisted_category_unlimited(disp):
    disp.set_preferences("a", "UTC", "00:00", "00:00", {"streak": 1}, utc(2026, 1, 1, 0))
    ids = [disp.emit("a", "other", utc(2026, 1, 2, 8, 0)) for _ in range(5)]
    disp.dispatch(utc(2026, 1, 2, 9, 0))
    assert len(disp.inbox("a")) == 5


def test_same_category_queue_never_inverts(disp):
    # Second event occurs earlier in the day than the first's DND exit;
    # it must not be delivered before the first.
    disp.set_preferences("a", "UTC", "00:00", "12:00", {}, utc(2026, 1, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 1, 2, 6, 0))
    e2 = disp.emit("a", "streak", utc(2026, 1, 2, 7, 0))
    disp.dispatch(utc(2026, 1, 3, 0))
    inbox = disp.inbox("a")
    assert [i["event_id"] for i in inbox] == [e1, e2]
    assert inbox[0]["delivered_at"] == utc(2026, 1, 2, 12, 0)
    assert inbox[1]["delivered_at"] == utc(2026, 1, 2, 12, 0)


def test_categories_are_independent(disp):
    disp.set_preferences("a", "UTC", "00:00", "12:00", {}, utc(2026, 1, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 1, 2, 6, 0))
    e2 = disp.emit("a", "level", utc(2026, 1, 2, 6, 0))
    disp.dispatch(utc(2026, 1, 3, 0))
    inbox = disp.inbox("a")
    assert inbox[0]["delivered_at"] == utc(2026, 1, 2, 12, 0)
    assert inbox[1]["delivered_at"] == utc(2026, 1, 2, 12, 0)


def test_inbox_ordering_by_delivery_then_emit(disp):
    disp.set_preferences("a", "UTC", "00:00", "12:00", {}, utc(2026, 1, 1, 0))
    e1 = disp.emit("a", "streak", utc(2026, 1, 2, 6, 0))   # delivered 12:00
    e2 = disp.emit("a", "level", utc(2026, 1, 2, 13, 0))   # delivered 13:00
    e3 = disp.emit("a", "review", utc(2026, 1, 2, 8, 0))   # delivered 12:00
    disp.dispatch(utc(2026, 1, 3, 0))
    assert [i["event_id"] for i in disp.inbox("a")] == [e1, e3, e2]


def test_reopen_database_keeps_state(tmp_path):
    path = str(tmp_path / "n.db")
    d = NotificationDispatcher(path)
    d.set_preferences("a", "UTC", "00:00", "12:00", {}, utc(2026, 1, 1, 0))
    e = d.emit("a", "streak", utc(2026, 1, 2, 6, 0))
    d.close()
    d2 = NotificationDispatcher(path)
    d2.dispatch(utc(2026, 1, 3, 0))
    assert d2.inbox("a")[0]["delivered_at"] == utc(2026, 1, 2, 12, 0)
    d2.close()
