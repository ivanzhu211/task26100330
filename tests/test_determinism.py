"""Inbox contents must not depend on when (or how often) dispatch runs,
including across database close/reopen."""

import random
from datetime import datetime, timezone

from notification_dispatch import NotificationDispatcher


def utc(y, mo, d, h, mi=0, s=0):
    return int(datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).timestamp())


START = utc(2026, 2, 20, 0)   # before NY spring forward
END = utc(2026, 4, 10, 0)     # after Santiago fall back
LEARNERS = ["ana", "bo", "cy", "di"]
CATEGORIES = ["streak", "level", "review"]
TZS = ["UTC", "America/New_York", "America/Santiago", "Australia/Sydney"]


def make_ops(seed):
    rng = random.Random(seed)
    ops = []
    now = START
    while now < END:
        now += rng.randint(600, 6 * 3600)
        now = min(now, END)
        kind = rng.random()
        if kind < 0.25:
            start_h = rng.randint(0, 23)
            end_h = rng.randint(0, 23)
            caps = {}
            for cat in CATEGORIES:
                if rng.random() < 0.5:
                    caps[cat] = rng.randint(1, 3)
            ops.append(("pref", rng.choice(LEARNERS), rng.choice(TZS),
                        f"{start_h:02d}:{rng.randint(0, 59):02d}",
                        f"{end_h:02d}:{rng.randint(0, 59):02d}",
                        caps, now))
        else:
            ops.append(("emit", rng.choice(LEARNERS), rng.choice(CATEGORIES), now))
    return ops


def run(ops, path, mode, seed=0):
    rng = random.Random(seed + 1)
    disp = NotificationDispatcher(path)
    try:
        for i, op in enumerate(ops):
            if op[0] == "pref":
                _, learner, tz, ds, de, caps, now = op
                disp.set_preferences(learner, tz, ds, de, caps, now)
            else:
                _, learner, category, now = op
                disp.emit(learner, category, now)
            if mode == "every":
                disp.dispatch(now)
            elif mode == "random":
                if rng.random() < 0.3:
                    disp.dispatch(now)
                if rng.random() < 0.05:
                    disp.close()
                    disp = NotificationDispatcher(path)
        disp.dispatch(END)
        return {learner: disp.inbox(learner) for learner in LEARNERS}
    finally:
        disp.close()


def test_dispatch_timing_does_not_change_inboxes(tmp_path):
    ops = make_ops(seed=42)
    every = run(ops, str(tmp_path / "every.db"), "every")
    once = run(ops, str(tmp_path / "once.db"), "once")
    rand = run(ops, str(tmp_path / "rand.db"), "random")
    assert every == once == rand
    # Sanity: the scenario actually delivered a non-trivial number of items.
    assert sum(len(v) for v in every.values()) > 50


def test_second_seed(tmp_path):
    ops = make_ops(seed=7)
    every = run(ops, str(tmp_path / "every.db"), "every")
    once = run(ops, str(tmp_path / "once.db"), "once")
    rand = run(ops, str(tmp_path / "rand.db"), "random")
    assert every == once == rand
