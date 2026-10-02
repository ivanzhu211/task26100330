"""Notification delivery scheduling library (SQLite-backed).

All times are UTC integer seconds. Delivery times are computed deterministically
at emit time from stored preferences and the per-category queue, so results are
independent of when (and how often) dispatch() is called, and survive closing
and reopening the database file.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

from tzutil import first_instant_of_date, first_instant_wall_ge, wall_utc_candidates

UTC = timezone.utc

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prefs (
    learner_id TEXT NOT NULL,
    effective_from INTEGER NOT NULL,
    tz TEXT NOT NULL,
    dnd_start TEXT NOT NULL,
    dnd_end TEXT NOT NULL,
    caps TEXT NOT NULL,
    PRIMARY KEY (learner_id, effective_from)
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    category TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    deliver_at INTEGER NOT NULL,
    local_date TEXT NOT NULL,
    delivered INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_queue ON events(learner_id, category, id);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(learner_id, category, local_date);
CREATE INDEX IF NOT EXISTS idx_events_dispatch ON events(delivered, deliver_at);
"""


@dataclass(frozen=True)
class Preference:
    tz: str
    dnd_start: dtime
    dnd_end: dtime
    caps: dict[str, int]


_DEFAULT_PREF = Preference("UTC", dtime(0, 0), dtime(0, 0), {})


def parse_hhmm(value: str) -> dtime:
    parts = value.split(":")
    if len(parts) != 2:
        raise ValueError(f"invalid HH:MM time: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"invalid HH:MM time: {value!r}")
    return dtime(hour, minute)


def _hhmm_pair(t: dtime) -> tuple[int, int]:
    return (t.hour, t.minute)


class Scheduler:
    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Scheduler":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ prefs

    def set_preferences(
        self,
        learner_id: str,
        tz: str,
        dnd_start: str,
        dnd_end: str,
        caps: dict[str, int],
        now: int,
    ) -> None:
        ZoneInfo(tz)  # validate IANA name
        parse_hhmm(dnd_start)
        parse_hhmm(dnd_end)
        for category, cap in caps.items():
            if not isinstance(cap, int) or cap < 1:
                raise ValueError(f"cap for {category!r} must be a positive integer")
        self._conn.execute(
            "INSERT OR REPLACE INTO prefs"
            " (learner_id, effective_from, tz, dnd_start, dnd_end, caps)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (learner_id, now, tz, dnd_start, dnd_end, json.dumps(caps)),
        )
        self._conn.commit()

    def _pref_at(self, learner_id: str, ts: int) -> Preference:
        """Preference in effect at instant `ts`: the one set with the latest
        `now` strictly before `ts`; default (UTC, no DND, no caps) if none."""
        row = self._conn.execute(
            "SELECT tz, dnd_start, dnd_end, caps FROM prefs"
            " WHERE learner_id = ? AND effective_from < ?"
            " ORDER BY effective_from DESC LIMIT 1",
            (learner_id, ts),
        ).fetchone()
        if row is None:
            return _DEFAULT_PREF
        return Preference(
            tz=row["tz"],
            dnd_start=parse_hhmm(row["dnd_start"]),
            dnd_end=parse_hhmm(row["dnd_end"]),
            caps=json.loads(row["caps"]),
        )

    # ------------------------------------------------------------------- dnd

    @staticmethod
    def _in_dnd(pref: Preference, ts: int) -> bool:
        if pref.dnd_start == pref.dnd_end:
            return False
        local = datetime.fromtimestamp(ts, ZoneInfo(pref.tz))
        w = (local.hour, local.minute)
        s, e = _hhmm_pair(pref.dnd_start), _hhmm_pair(pref.dnd_end)
        if s < e:
            return s <= w < e
        return w >= s or w < e

    @staticmethod
    def _dnd_exit(pref: Preference, ts: int) -> int:
        """First instant >= ts that is outside the DND window (caller must
        ensure ts is inside DND)."""
        tz = ZoneInfo(pref.tz)
        local = datetime.fromtimestamp(ts, tz)
        w = (local.hour, local.minute)
        s, e = _hhmm_pair(pref.dnd_start), _hhmm_pair(pref.dnd_end)
        if s < e:
            exit_date = local.date()
        else:
            exit_date = local.date() + (timedelta(days=1) if w >= s else timedelta(0))
        # The exit is the next instant whose wall clock reaches dnd_end.
        candidates = [c for c in wall_utc_candidates(tz, exit_date, pref.dnd_end) if c >= ts]
        if candidates:
            return min(candidates)
        t_exit = first_instant_wall_ge(tz, exit_date, pref.dnd_end)
        if t_exit >= ts:
            return t_exit
        return first_instant_wall_ge(tz, exit_date + timedelta(days=1), pref.dnd_end)

    # ------------------------------------------------------------- scheduling

    def _count_on_date(self, learner_id: str, category: str, local_date: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM events"
            " WHERE learner_id = ? AND category = ? AND local_date = ?",
            (learner_id, category, local_date),
        ).fetchone()
        return row["n"]

    def _compute_delivery(self, learner_id: str, category: str, start: int) -> tuple[int, str]:
        """Earliest instant >= start that is outside DND and under the daily
        cap for its local date. Returns (deliver_at, local_date)."""
        t = start
        while True:
            pref = self._pref_at(learner_id, t)
            if self._in_dnd(pref, t):
                t = self._dnd_exit(pref, t)
                continue
            tz = ZoneInfo(pref.tz)
            day = datetime.fromtimestamp(t, tz).date()
            local_date = day.isoformat()
            cap = pref.caps.get(category)
            if cap is not None and self._count_on_date(learner_id, category, local_date) >= cap:
                t = first_instant_of_date(tz, day + timedelta(days=1))
                continue
            return t, local_date

    # ------------------------------------------------------------------- api

    def emit(self, learner_id: str, category: str, now: int) -> int:
        row = self._conn.execute(
            "SELECT deliver_at FROM events"
            " WHERE learner_id = ? AND category = ? ORDER BY id DESC LIMIT 1",
            (learner_id, category),
        ).fetchone()
        prev_deliver_at = row["deliver_at"] if row else 0
        deliver_at, local_date = self._compute_delivery(
            learner_id, category, max(now, prev_deliver_at)
        )
        cur = self._conn.execute(
            "INSERT INTO events (learner_id, category, occurred_at, deliver_at, local_date)"
            " VALUES (?, ?, ?, ?, ?)",
            (learner_id, category, now, deliver_at, local_date),
        )
        self._conn.commit()
        return cur.lastrowid

    def dispatch(self, now: int) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, learner_id, category, occurred_at, deliver_at FROM events"
            " WHERE delivered = 0 AND deliver_at <= ? ORDER BY deliver_at, id",
            (now,),
        ).fetchall()
        if rows:
            self._conn.execute(
                "UPDATE events SET delivered = 1 WHERE delivered = 0 AND deliver_at <= ?",
                (now,),
            )
            self._conn.commit()
        return [
            {
                "event_id": r["id"],
                "learner_id": r["learner_id"],
                "category": r["category"],
                "occurred_at": r["occurred_at"],
                "deliver_at": r["deliver_at"],
            }
            for r in rows
        ]

    def inbox(self, learner_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT id, category, occurred_at, deliver_at FROM events"
            " WHERE learner_id = ? AND delivered = 1 ORDER BY deliver_at, id",
            (learner_id,),
        ).fetchall()
        return [
            {
                "event_id": r["id"],
                "category": r["category"],
                "occurred_at": r["occurred_at"],
                "deliver_at": r["deliver_at"],
            }
            for r in rows
        ]
