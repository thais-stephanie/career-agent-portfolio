"""One derivation of the whole deep-level map at a time, per profile.

The Resume Tailor asks `/api/jobs` for its two lists at once. Each request
derived the whole map (`deep_api._whole_map`) beside the other, so a first
opening assessed every reading twice (2 x 2,367 on the real profile, 10-12 s).
Now the second request waits for the first and reads the map it remembered.
What must hold:

- requests asked together derive the map once, and each gets the answer it
  got before (the same cards in the same order);
- a request whose key moved while it waited derives again, as before;
- two profiles never wait for each other;
- a later request, and a caller with a plain namespace for an app
  (`evaluation/ranking.py`), are unchanged.

Synthetic throughout: the invented corpus of the Home deep-level tests.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from tests.integration.test_home_deep_levels_within import build_corpus, fresh, vary

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


@pytest.fixture
def whole_rows(monkeypatch):
    """Each derivation of the WHOLE map (`_review_rows` without ids), slowed so
    that requests asked together really overlap."""
    calls: list[int] = []
    real = deep_api._review_rows

    def slow(app, conn, job_ids=None, **k):
        if job_ids is None:
            calls.append(1)
            time.sleep(0.3)
        return real(app, conn, job_ids, **k)

    monkeypatch.setattr(deep_api, "_review_rows", slow)
    return calls


def _cards(answer: dict) -> list:
    return [(i["job_id"], (i.get("deep") or {}).get("level")) for i in answer["items"]]


def _together(*calls):
    """Run the calls at the same moment; their answers, in order."""
    start = threading.Barrier(len(calls))
    out: list = [None] * len(calls)
    errors: list = []

    def run(n, call):
        start.wait()
        try:
            out[n] = call()
        except Exception as exc:  # noqa: BLE001 -- reported below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(n, c)) for n, c in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    return out


def _one_derivation(app, assessed) -> int:
    fresh(app)
    before = len(assessed)
    app.deep_levels()
    return len(assessed) - before


def test_the_tailors_two_lists_at_once_derive_the_map_once(corpus, assessed, whole_rows) -> None:
    app = corpus
    once = _one_derivation(app, assessed)
    assert once > 0
    fresh(app)
    alone = [
        _cards(app.handle_api("GET", "/api/jobs", SAVED, {})),
        _cards(app.handle_api("GET", "/api/jobs", BEST, {})),
    ]
    fresh(app)
    with app._aggregates_lock:
        app._aggregates.clear()
    before, derived = len(assessed), len(whole_rows)
    saved, best = _together(
        lambda: app.handle_api("GET", "/api/jobs", SAVED, {}),
        lambda: app.handle_api("GET", "/api/jobs", BEST, {}),
    )
    assert len(whole_rows) - derived == 1
    assert len(assessed) - before == once
    assert [_cards(saved), _cards(best)] == alone
    assert any(level for _job, level in alone[1]), "the best list shows read postings"


def test_concurrent_calls_share_one_derivation_and_one_answer(corpus, assessed, whole_rows) -> None:
    app = corpus
    once = _one_derivation(app, assessed)
    expected = app.deep_levels()
    fresh(app)
    before, derived = len(assessed), len(whole_rows)
    answers = _together(*([app.deep_levels] * 4))
    assert len(whole_rows) - derived == 1
    assert len(assessed) - before == once
    assert all(a == expected for a in answers)


def test_a_key_that_moved_while_waiting_is_derived_again(corpus, whole_rows, monkeypatch) -> None:
    """The waiter's key is the one its own snapshot read: when it differs from
    the key the first derivation stored, the waiter derives under its own."""
    app = corpus
    fresh(app)
    real_key = deep_api._levels_key
    moved = threading.local()

    def key(app_, conn):
        k = real_key(app_, conn)
        return None if k is None else (*k, "moved") if getattr(moved, "on", False) else k

    monkeypatch.setattr(deep_api, "_levels_key", key)

    def moved_call():
        moved.on = True
        time.sleep(0.05)  # ask after the first has taken the derivation
        return app.deep_levels()

    first, second = _together(app.deep_levels, moved_call)
    assert len(whole_rows) == 2
    assert first == second  # the same data under two keys gives the same levels
    assert app._deep_levels_cache[0][-1] == "moved"


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
            assert release.wait(timeout=60)
        return real(app, conn, job_ids, **k)

    monkeypatch.setattr(deep_api, "_review_rows", blocking)
    held = threading.Thread(target=one.deep_levels)
    held.start()
    try:
        assert inside.wait(timeout=60)
        done = threading.Event()
        other = threading.Thread(target=lambda: (two.deep_levels(), done.set()))
        other.start()
        assert done.wait(timeout=60), "the second profile waited for the first"
        other.join()
    finally:
        release.set()
        held.join(timeout=60)


def test_a_later_request_derives_nothing(corpus, assessed) -> None:
    app = corpus
    _one_derivation(app, assessed)
    before = len(assessed)
    app.handle_api("GET", "/api/jobs", SAVED, {})
    app.handle_api("GET", "/api/jobs", BEST, {})
    assert len(assessed) == before


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
