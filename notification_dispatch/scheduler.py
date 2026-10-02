"""Delivery scheduling for learner in-app notifications, persisted in SQLite."""

from __future__ import annotations

import bisect
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import timezone
from typing import Optional

from zoneinfo import ZoneInfo

from . import timeutil

_SCHEMA = """
CREATE TABLE IF NOT EXISTS preferences (
    learner_id TEXT NOT NULL,
    effective_after INTEGER NOT NULL,  -- applies to instants strictly > this UTC second
    seq INTEGER NOT NULL,              -- tie-break: monotonic insertion order
    tz TEXT NOT NULL,
    dnd_start TEXT NOT NULL,
    dnd_end TEXT NOT NULL,
    caps_json TEXT NOT NULL,
    PRIMARY KEY (learner_id, effective_after, seq)
);
CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner_id TEXT NOT NULL,
    category TEXT NOT NULL,
    occurred_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS pending (
    event_id INTEGER PRIMARY KEY REFERENCES events(event_id)
);
CREATE TABLE IF NOT EXISTS inbox (
    learner_id TEXT NOT NULL,
    event_id INTEGER NOT NULL REFERENCES events(event_id),
    category TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    delivered_at INTEGER NOT NULL,
    local_date TEXT NOT NULL,          -- local date at delivery, per tz effective then
    deliver_seq INTEGER PRIMARY KEY AUTOINCREMENT
);
CREATE INDEX IF NOT EXISTS idx_inbox_cap
    ON inbox (learner_id, category, local_date);
CREATE INDEX IF NOT EXISTS idx_inbox_order
    ON inbox (learner_id, delivered_at, event_id);
"""

_HHMM = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

_DEFAULT_PREF = None  # built lazily


@dataclass(frozen=True)
class Preference:
    tz: ZoneInfo
    dnd_start: str
    dnd_end: str
    caps: dict


def _default_pref() -> Preference:
    global _DEFAULT_PREF
    if _DEFAULT_PREF is None:
        _DEFAULT_PREF = Preference(ZoneInfo("UTC"), "00:00", "00:00", {})
    return _DEFAULT_PREF


class NotificationDispatcher:
    """Schedules and delivers in-app notifications into a SQLite-backed inbox."""

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self._pref_cache: dict[str, list] = {}

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------ prefs

    def set_preferences(
        self,
        learner_id: str,
        tz: str,
        dnd_start: str,
        dnd_end: str,
        caps: dict,
        now: int,
    ) -> None:
        ZoneInfo(tz)  # validate IANA name
        if not _HHMM.match(dnd_start) or not _HHMM.match(dnd_end):
            raise ValueError("dnd_start/dnd_end must be HH:MM")
        clean_caps = {str(k): int(v) for k, v in caps.items()}
        if any(v < 1 for v in clean_caps.values()):
            raise ValueError("daily caps must be >= 1")
        row = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM preferences WHERE learner_id = ?",
            (learner_id,),
        ).fetchone()
        with self._conn:
            self._conn.execute(
                "INSERT INTO preferences VALUES (?, ?, ?, ?, ?, ?, ?)",
                (learner_id, now, row[0], tz, dnd_start, dnd_end,
                 json.dumps(clean_caps, sort_keys=True)),
            )
        self._pref_cache.pop(learner_id, None)

    def _prefs_for(self, learner_id: str) -> list:
        """Cached (keys, prefs): effective_after keys and Preference entries."""
        cached = self._pref_cache.get(learner_id)
        if cached is not None:
            return cached
        rows = self._conn.execute(
            "SELECT effective_after, seq, tz, dnd_start, dnd_end, caps_json"
            " FROM preferences WHERE learner_id = ? ORDER BY effective_after, seq",
            (learner_id,),
        ).fetchall()
        prefs = [
            (r[0], r[1], Preference(ZoneInfo(r[2]), r[3], r[4], json.loads(r[5])))
            for r in rows
        ]
        entry = ([p[0] for p in prefs], [p[2] for p in prefs])
        self._pref_cache[learner_id] = entry
        return entry

    def _pref_at(self, learner_id: str, ts: int) -> Preference:
        """Preference in effect at instant ``ts`` (strictly after effective_after)."""
        keys, prefs = self._prefs_for(learner_id)
        idx = bisect.bisect_left(keys, ts) - 1
        # bisect_left gives first key >= ts; we need the last key < ts.
        if idx < 0:
            return _default_pref()
        return prefs[idx]

    # ------------------------------------------------------------------- emit

    def emit(self, learner_id: str, category: str, now: int) -> int:
        with self._conn:
            cur = self._conn.execute(
                "INSERT INTO events (learner_id, category, occurred_at)"
                " VALUES (?, ?, ?)",
                (learner_id, category, now),
            )
            event_id = cur.lastrowid
            self._conn.execute("INSERT INTO pending (event_id) VALUES (?)", (event_id,))
        return event_id

    # --------------------------------------------------------------- schedule

    def _last_delivered_at(self, learner_id: str, category: str) -> Optional[int]:
        row = self._conn.execute(
            "SELECT delivered_at FROM inbox"
            " WHERE learner_id = ? AND category = ?"
            " ORDER BY deliver_seq DESC LIMIT 1",
            (learner_id, category),
        ).fetchone()
        return row[0] if row else None

    def _delivered_count_on(self, learner_id: str, category: str, local_date: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM inbox"
            " WHERE learner_id = ? AND category = ? AND local_date = ?",
            (learner_id, category, local_date),
        ).fetchone()
        return row[0]

    def _schedule(self, learner_id: str, category: str, occurred_at: int,
                  prev_delivered_at: Optional[int]) -> tuple[int, str]:
        """Earliest deliverable instant for one notification and its local date."""
        t = occurred_at if prev_delivered_at is None else max(occurred_at, prev_delivered_at)
        while True:
            pref = self._pref_at(learner_id, t)
            wall = timeutil.utc_seconds_to_local(pref.tz, t)
            if timeutil.local_wall_time_in_window(wall, pref.dnd_start, pref.dnd_end):
                t = timeutil.dnd_exit(pref.tz, t, pref.dnd_start, pref.dnd_end)
                continue
            local_date = wall.date().isoformat()
            cap = pref.caps.get(category)
            if cap is not None and self._delivered_count_on(learner_id, category, local_date) >= cap:
                t = timeutil.first_utc_of_next_local_date(pref.tz, t)
                continue
            return t, local_date

    # --------------------------------------------------------------- dispatch

    def dispatch(self, now: int) -> list:
        rows = self._conn.execute(
            "SELECT e.event_id, e.learner_id, e.category, e.occurred_at"
            " FROM pending p JOIN events e ON e.event_id = p.event_id"
            " ORDER BY e.event_id"
        ).fetchall()
        delivered = []
        blocked: set[tuple[str, str]] = set()
        prev_delivered: dict[tuple[str, str], Optional[int]] = {}
        with self._conn:
            for event_id, learner_id, category, occurred_at in rows:
                key = (learner_id, category)
                if key in blocked:
                    continue
                if key not in prev_delivered:
                    prev_delivered[key] = self._last_delivered_at(learner_id, category)
                t, local_date = self._schedule(
                    learner_id, category, occurred_at, prev_delivered[key])
                if t > now:
                    blocked.add(key)
                    continue
                self._conn.execute(
                    "INSERT INTO inbox"
                    " (learner_id, event_id, category, occurred_at, delivered_at, local_date)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (learner_id, event_id, category, occurred_at, t, local_date),
                )
                self._conn.execute("DELETE FROM pending WHERE event_id = ?", (event_id,))
                prev_delivered[key] = t
                delivered.append({
                    "learner_id": learner_id,
                    "event_id": event_id,
                    "category": category,
                    "occurred_at": occurred_at,
                    "delivered_at": t,
                })
        delivered.sort(key=lambda d: (d["delivered_at"], d["event_id"]))
        return delivered

    # ------------------------------------------------------------------ inbox

    def inbox(self, learner_id: str) -> list:
        rows = self._conn.execute(
            "SELECT event_id, category, occurred_at, delivered_at FROM inbox"
            " WHERE learner_id = ? ORDER BY delivered_at, event_id",
            (learner_id,),
        ).fetchall()
        return [
            {
                "event_id": r[0],
                "category": r[1],
                "occurred_at": r[2],
                "delivered_at": r[3],
            }
            for r in rows
        ]
