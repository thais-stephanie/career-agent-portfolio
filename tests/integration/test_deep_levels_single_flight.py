"""One derivation of the whole deep-level map per key at a time, per profile.

The Resume Tailor asks `/api/jobs` for its two lists at once. Each request
derived the whole map (`deep_api._whole_map`) beside the other, so a first
opening assessed every reading twice (2 x 2,367 on the real profile, 10-12 s).
Now a request waits only for a derivation of its own key and reads the map
it remembered. What must hold:

- requests asked together under one key derive the map once, and each gets
  the answer it got before (the same cards, in the same order);
- a remembered hit never waits, even while another key derives;
- another key never waits: it derives beside the first, as before;
- a derivation that fails releases its waiters, which derive themselves;
- Home's parts (`deep_levels_within`) never wait;
- two profiles never wait for each other;
- a later request, and a caller with a plain namespace for an app
  (`evaluation/ranking.py`), are unchanged; no flight is left behind.

Deterministic: the first derivation is held on an Event until the test lets
it go, so "at once" never depends on timing. Synthetic throughout: the
invented corpus of the Home deep-level tests.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from tests.integration.test_home_deep_levels_within import build_corpus, fresh, read_ids, vary

from career_agent.semantic import deep_assess
from career_agent.storage.db import connect
from career_agent.web import deep_api

LIST = 8
SAVED = {
    "saved_only": ["true"],
    "group_duplicates": ["1"],
    "limit": [str(LIST * 3)],
    "narrowings": ["later"],
}
BEST = {"group_duplicates": ["1"], "limit": [str(LIST)], "narrowings": ["later"]}
WAIT = 60


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path)
    vary(app)
    return app


@pytest.fixture
def assessed(monkeypatch):
    """How many readings were derived (`deep_assess.assess` calls)."""
    seen: list[int] = []
    real = deep_assess.assess
    monkeypatch.setattr(deep_assess, "assess", lambda *a, **k: seen.append(1) or real(*a, **k))
    return seen


class Gate:
    """Holds the FIRST derivation of the whole map until `release()`; counts
    every derivation of the whole map; can make the held one fail."""

    def __init__(self, monkeypatch) -> None:
        self.inside = threading.Event()
        self._go = threading.Event()
        self.calls = 0
        self.fail = False
        self._lock = threading.Lock()
        real = deep_api._review_rows

        def gated(app, conn, job_ids=None, **k):
            if job_ids is None:
                with self._lock:
                    self.calls += 1
                    first = self.calls == 1
                if first:
                    self.inside.set()
                    assert self._go.wait(timeout=WAIT)
                    if self.fail:
                        raise RuntimeError("the held derivation failed")
            return real(app, conn, job_ids, **k)

        monkeypatch.setattr(deep_api, "_review_rows", gated)

    def release(self) -> None:
        self._go.set()


@pytest.fixture
def gate(monkeypatch):
    g = Gate(monkeypatch)
    yield g
    g.release()


class Call(threading.Thread):
    def __init__(self, fn) -> None:
        super().__init__(daemon=True)
        self.fn, self.result, self.error = fn, None, None
        self.finished = threading.Event()

    def run(self) -> None:
        try:
            self.result = self.fn()
        except Exception as exc:  # noqa: BLE001 -- asserted by the tests
            self.error = exc
        finally:
            self.finished.set()


def _start(fn) -> Call:
    call = Call(fn)
    call.start()
    return call


def _cards(answer: dict) -> list:
    return [(i["job_id"], (i.get("deep") or {}).get("level")) for i in answer["items"]]


def _cold(app) -> None:
    fresh(app)
    with app._aggregates_lock:
        app._aggregates.clear()


def _flights(app) -> dict:
    return getattr(app, "_deep_levels_flights", None) or {}


def _queued(app, key_count: int = 1) -> None:
    """Give the second caller time to reach its wait (it can only wait or hit)."""
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and len(_flights(app)) < key_count:
        time.sleep(0.01)
    time.sleep(0.3)


def test_the_tailors_two_lists_at_once_derive_the_map_once(corpus, assessed, monkeypatch) -> None:
    app = corpus
    _cold(app)  # first, the answers each list gives on its own
    alone = [
        _cards(app.handle_api("GET", "/api/jobs", SAVED, {})),
        _cards(app.handle_api("GET", "/api/jobs", BEST, {})),
    ]
    _cold(app)
    once_before = len(assessed)
    app.deep_levels()
    once = len(assessed) - once_before
    assert once > 0 and any(level for _job, level in alone[1])

    held = Gate(monkeypatch)  # then the two at once, the first one held
    try:
        _cold(app)
        before = len(assessed)
        saved = _start(lambda: app.handle_api("GET", "/api/jobs", SAVED, {}))
        assert held.inside.wait(timeout=WAIT)
        best = _start(lambda: app.handle_api("GET", "/api/jobs", BEST, {}))
        _queued(app)
        assert not best.finished.is_set(), "the second list did not wait for the map"
        held.release()
        assert saved.finished.wait(WAIT) and best.finished.wait(WAIT)
        assert saved.error is None and best.error is None
        assert held.calls == 1
        assert len(assessed) - before == once
        assert [_cards(saved.result), _cards(best.result)] == alone
        assert _flights(app) == {}
    finally:
        held.release()


def test_concurrent_calls_of_one_key_share_one_derivation(corpus, gate) -> None:
    app = corpus
    first = _start(app.deep_levels)
    assert gate.inside.wait(timeout=WAIT)
    others = [_start(app.deep_levels) for _ in range(3)]
    _queued(app)
    assert not any(o.finished.is_set() for o in others)
    gate.release()
    for call in (first, *others):
        assert call.finished.wait(WAIT) and call.error is None
    assert gate.calls == 1
    assert all(o.result == first.result for o in others) and first.result
    assert _flights(app) == {}


def _moved_key(monkeypatch, suffix: str) -> threading.local:
    """Callers that set `.on` read their key with `suffix` appended: another state."""
    real = deep_api._levels_key
    mark = threading.local()

    def key(app, conn):
        k = real(app, conn)
        return None if k is None else (*k, suffix) if getattr(mark, "on", False) else k

    monkeypatch.setattr(deep_api, "_levels_key", key)
    return mark


def test_another_key_derives_beside_and_never_waits(corpus, gate, monkeypatch) -> None:
    app = corpus
    mark = _moved_key(monkeypatch, "moved")
    first = _start(app.deep_levels)
    assert gate.inside.wait(timeout=WAIT)

    def moved():
        mark.on = True
        return app.deep_levels()

    second = _start(moved)
    assert second.finished.wait(WAIT), "another key waited for the held derivation"
    assert second.error is None and second.result
    gate.release()
    assert first.finished.wait(WAIT) and first.error is None
    assert first.result == second.result  # the same data under two keys
    assert gate.calls == 2
    assert _flights(app) == {}


def test_a_hit_never_waits_while_another_key_derives(corpus, monkeypatch) -> None:
    app = corpus
    fresh(app)
    remembered = app.deep_levels()  # the map is now remembered under the current key
    mark = _moved_key(monkeypatch, "older")
    held = Gate(monkeypatch)
    try:

        def older():
            mark.on = True
            return app.deep_levels()

        a = _start(older)
        assert held.inside.wait(timeout=WAIT)
        # An older snapshot derives; a request of the current key that finds
        # the map remembered returns at once.
        hit = _start(app.deep_levels)
        assert hit.finished.wait(5), "a remembered hit waited for another key"
        assert hit.result == remembered
    finally:
        held.release()
    assert a.finished.wait(WAIT) and a.error is None


def test_a_failed_derivation_releases_its_waiters(corpus, gate) -> None:
    app = corpus
    gate.fail = True
    first = _start(app.deep_levels)
    assert gate.inside.wait(timeout=WAIT)
    waiter = _start(app.deep_levels)
    _queued(app)
    gate.release()
    assert first.finished.wait(WAIT) and isinstance(first.error, RuntimeError)
    assert waiter.finished.wait(WAIT) and waiter.error is None and waiter.result
    assert gate.calls == 2  # the waiter derived under its own key
    assert _flights(app) == {}


def test_homes_parts_never_wait_for_the_whole_map(corpus, gate) -> None:
    app = corpus
    first = _start(app.deep_levels)
    assert gate.inside.wait(timeout=WAIT)

    def part():
        conn = connect(app.config.db_path)
        try:
            return deep_api.deep_levels_within(app, conn, set(read_ids(app)[:4]))
        finally:
            conn.close()

    home_part = _start(part)
    assert home_part.finished.wait(WAIT), "Home's parts waited for the whole map"
    assert home_part.error is None and home_part.result
    gate.release()
    assert first.finished.wait(WAIT) and first.error is None
    conn = connect(app.config.db_path)
    try:
        whole = deep_api.deep_levels(app, conn)  # remembered by the first call
    finally:
        conn.close()
    assert all(whole[job] == level for job, level in home_part.result.items())


def test_two_profiles_never_wait_for_each_other(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    one = build_corpus(tmp_path / "one", postings=20, reads=(4, 3, 2), clones=4)
    two = build_corpus(tmp_path / "two", postings=20, reads=(4, 3, 2), clones=4)
    fresh(one)
    fresh(two)
    inside, release = threading.Event(), threading.Event()
    real = deep_api._review_rows

    def blocking(app, conn, job_ids=None, **k):
        if job_ids is None and app is one:
            inside.set()
            assert release.wait(timeout=WAIT)
        return real(app, conn, job_ids, **k)

    monkeypatch.setattr(deep_api, "_review_rows", blocking)
    held = _start(one.deep_levels)
    try:
        assert inside.wait(timeout=WAIT)
        other = _start(two.deep_levels)
        assert other.finished.wait(WAIT), "the second profile waited for the first"
        assert other.error is None
    finally:
        release.set()
    assert held.finished.wait(WAIT) and held.error is None


def test_a_later_request_derives_nothing(corpus, assessed) -> None:
    app = corpus
    fresh(app)
    app.deep_levels()
    before = len(assessed)
    app.handle_api("GET", "/api/jobs", SAVED, {})
    app.handle_api("GET", "/api/jobs", BEST, {})
    assert len(assessed) == before
    assert _flights(app) == {}


def test_a_plain_namespace_app_still_derives(corpus) -> None:
    """`evaluation/ranking.py` passes a namespace with `search_config` only."""
    app = corpus
    fresh(app)
    config = app.search_config()
    conn = connect(app.config.db_path)
    try:
        bare = deep_api.deep_levels(SimpleNamespace(search_config=lambda: config), conn)
        mine = deep_api.deep_levels(app, conn)
    finally:
        conn.close()
    assert bare == mine and bare
