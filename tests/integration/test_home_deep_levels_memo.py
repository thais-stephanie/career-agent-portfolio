"""The memo of parts: levels Home derived for some postings, kept for the next miss.

`deep_levels_within` keeps what it derives under the whole map's key
(`_levels_key`) in `_deep_levels_part`, separate from the whole map's cache.
What must hold:

- a later Home miss (the 600 s memo expired, a save, a hide, a moved
  checkpoint) derives only postings never asked under the current key;
- a posting asked that has no level is remembered as asked, not derived again;
- another key (a reading, the reading instructions, the profile's context, the
  catalogue) starts the memo empty, and nothing derived under one key is ever
  added under another;
- the whole map is never written from a part, and Find jobs still gets it;
- every answer is the one the whole map gives.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime

import pytest
from tests.integration.test_home_deep_levels_within import (
    assert_same_home,
    build_corpus,
    fresh,
    home,
    ids,
    read_ids,
    vary,
)

from career_agent.semantic import deep_assess
from career_agent.storage.db import connect, transaction
from career_agent.web import deep_api, workspace_api


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
def asked(monkeypatch):
    """The postings each `_review_rows` call was asked for."""
    calls: list[list[str] | None] = []
    real = deep_api._review_rows

    def spy(app, conn, job_ids=None, **k):
        calls.append(None if job_ids is None else list(job_ids))
        return real(app, conn, job_ids, **k)

    monkeypatch.setattr(deep_api, "_review_rows", spy)
    return calls


def whole_map(app) -> dict:
    """The whole map, derived now, without touching any remembered answer."""
    kept = (app._deep_levels_cache, getattr(app, "_deep_relations_cache", None))
    part = getattr(app, "_deep_levels_part", None)
    app._deep_levels_cache = None
    conn = connect(app.config.db_path)
    whole = deep_api.deep_levels(app, conn)
    conn.close()
    app._deep_levels_cache, app._deep_relations_cache = kept
    app._deep_levels_part = part
    return whole


def miss(app) -> dict:
    """Home with its own memo gone (as after 600 s), every level memo kept."""
    app._home_lists = None
    return home(app)


def write(app, sql: str, params=()) -> None:
    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute(sql, params)
    conn.close()


def assert_memo_agrees(app) -> None:
    """Every remembered part is the whole map's entry, and none is invented."""
    part = app._deep_levels_part
    assert part is not None
    whole = whole_map(app)
    assert set(part[1]) <= part[2]
    assert part[1] == {j: whole[j] for j in part[2] if j in whole}


def test_a_later_home_miss_derives_nothing_already_derived(corpus, assessed) -> None:
    """Fails on Step 1 (`1727033`), which derived the part again on every miss."""
    fresh(corpus)
    first = home(corpus)
    assert assessed, "the first Home derives its part"
    assessed.clear()
    assert miss(corpus) == first
    assert assessed == []
    assert corpus._deep_levels_cache is None, "a part is never the whole map"
    assert_memo_agrees(corpus)


def test_the_600_second_memo_expiring_keeps_the_levels(corpus, assessed, monkeypatch) -> None:
    fresh(corpus)
    first = home(corpus)
    assessed.clear()
    monkeypatch.setattr(workspace_api, "HOME_MEMO_SECONDS", 0)
    time.sleep(0.01)
    assert home(corpus) == first
    assert assessed == []


def test_saving_or_hiding_a_posting_keeps_the_levels(corpus, assessed, monkeypatch) -> None:
    fresh(corpus)
    home(corpus)
    shown = [i["job_id"] for s in home(corpus)["sections"] for i in s["items"]]
    read = set(read_ids(corpus))
    now = datetime.now(UTC).isoformat()
    for n, job in enumerate([j for j in shown if j in read][:2]):
        write(
            corpus,
            "INSERT OR REPLACE INTO job_application (id, job_id, status, saved, hidden_at,"
            " created_at, updated_at) VALUES (?, ?, 'SHORTLISTED', ?, ?, ?, ?)",
            (f"memo-{job}", job, 1 if n == 0 else 0, None if n == 0 else now, now, now),
        )
    assessed.clear()
    answer = miss(corpus)
    assert assessed == [], "a save or a hide changes no reading's level"
    assert answer == assert_same_home(corpus, monkeypatch)


def test_a_section_admitting_new_postings_derives_only_those(corpus, asked, monkeypatch) -> None:
    """The sections first admit fewer read postings (as before time, a
    checkpoint or a person's marks move them), then all: only the newcomers
    are derived. The narrower population is simulated at `ids_among`, the one
    place Home learns it, so the key does not move."""
    from career_agent.storage.mvp_repo import ScoredJobQuery

    fresh(corpus)
    real = ScoredJobQuery.ids_among
    held = set(sorted(set(read_ids(corpus)))[::2])

    def narrower(self, *a, **k):
        return real(self, *a, **k) - held

    monkeypatch.setattr(ScoredJobQuery, "ids_among", narrower)
    home(corpus)
    before = set(corpus._deep_levels_part[2])
    monkeypatch.setattr(ScoredJobQuery, "ids_among", real)
    asked.clear()
    miss(corpus)
    after = set(corpus._deep_levels_part[2])
    assert after > before, "the corpus must admit new postings for this test to mean anything"
    assert len(asked) == 1 and set(asked[0]) == after - before
    assert_memo_agrees(corpus)
    assert_same_home(corpus, monkeypatch)


def test_overlapping_sets_derive_only_the_postings_not_yet_asked(corpus, asked) -> None:
    fresh(corpus)
    everyone = sorted(ids(corpus).values())
    one, two = set(everyone[: len(everyone) * 2 // 3]), set(everyone[len(everyone) // 3 :])
    conn = connect(corpus.config.db_path)
    whole = whole_map(corpus)
    asked.clear()
    for wanted in (one, two, one | two, one):
        assert deep_api.deep_levels_within(corpus, conn, wanted) == {
            j: v for j, v in whole.items() if j in wanted
        }
    conn.close()
    assert [set(a) for a in asked] == [one, two - one]


def test_a_posting_without_a_level_is_not_derived_again(corpus, asked) -> None:
    fresh(corpus)
    unread = set(ids(corpus).values()) - set(whole_map(corpus))
    wanted = set(sorted(unread)[:5]) | {"no-such-posting"}
    asked.clear()
    conn = connect(corpus.config.db_path)
    assert deep_api.deep_levels_within(corpus, conn, wanted) == {}
    assert deep_api.deep_levels_within(corpus, conn, wanted) == {}
    conn.close()
    assert len(asked) == 1, "asked once, remembered as asked without a level"


@pytest.mark.parametrize(
    "change",
    [
        "UPDATE deep_review SET created_at = '2999-01-01T00:00:00Z'"
        " WHERE id = (SELECT MIN(id) FROM deep_review)",
        "DELETE FROM deep_review WHERE id = (SELECT MAX(id) FROM deep_review)",
        "UPDATE verified_claim SET revision = revision + 1",
        "UPDATE job SET title = title || ' (moved)'"
        " WHERE id = (SELECT job_id FROM deep_review ORDER BY id LIMIT 1)",
        "UPDATE job_match SET match_score = match_score + 1"
        " WHERE job_id = (SELECT job_id FROM deep_review ORDER BY id LIMIT 1)",
    ],
    ids=["reading-replaced", "reading-removed", "evidence", "advert-retitled", "score"],
)
def test_another_key_reuses_nothing(corpus, asked, monkeypatch, change) -> None:
    fresh(corpus)
    home(corpus)
    old_key = corpus._deep_levels_part[0]
    write(corpus, change)
    asked.clear()
    miss(corpus)
    part = corpus._deep_levels_part
    assert part[0] != old_key
    assert asked and set(asked[0]) == part[2], "every posting derived again under the new key"
    assert_memo_agrees(corpus)
    assert_same_home(corpus, monkeypatch)


def test_another_reading_contract_reuses_nothing(corpus, asked, monkeypatch) -> None:
    fresh(corpus)
    home(corpus)
    from career_agent.semantic import deep

    real = deep.contract_identity
    monkeypatch.setattr(deep, "contract_identity", lambda: (*real(), "another"))
    asked.clear()
    miss(corpus)
    assert asked and set(asked[0]) == corpus._deep_levels_part[2]


def test_the_whole_map_is_never_written_from_a_part_and_supersedes_it(corpus) -> None:
    fresh(corpus)
    home(corpus)
    assert corpus._deep_levels_cache is None
    assert getattr(corpus, "_deep_relations_cache", None) is None
    whole = whole_map(corpus)
    # Find jobs asks for the whole map: it is derived whole, and the parts go.
    corpus.deep_levels()
    assert corpus._deep_levels_part is None
    assert corpus._deep_levels_cache[1] == whole


def test_a_part_under_another_key_survives_the_whole_map(corpus) -> None:
    fresh(corpus)
    corpus._deep_levels_part = (("another",), {"x": ("APPLY", False, "current")}, {"x"})
    corpus.deep_levels()
    assert corpus._deep_levels_part[0] == ("another",), "only a part under the same key goes"
    corpus._deep_levels_cache = None  # so the part path answers, not the whole map
    conn = connect(corpus.config.db_path)
    assert "x" not in deep_api.deep_levels_within(corpus, conn, {"x"})
    conn.close()


def test_two_profiles_keep_their_own_parts(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    one = build_corpus(tmp_path / "one", postings=24, reads=(8, 0, 0))
    two = build_corpus(tmp_path / "two", postings=24, reads=(4, 0, 0))
    for app in (one, two):
        fresh(app)
        home(app)
    assert one._deep_levels_part is not two._deep_levels_part
    assert not set(one._deep_levels_part[2]) & set(two._deep_levels_part[2])
    for app in (one, two):
        assert_memo_agrees(app)
        assert_same_home(app, monkeypatch)


def test_a_derivation_under_an_old_key_is_never_added_under_a_new_one(corpus, monkeypatch) -> None:
    """Another call installs a newer key while this one derives: what this one
    derived answers it, and is not added to the newer memo."""
    fresh(corpus)
    real = deep_api._review_rows

    def meanwhile(app, conn, job_ids=None, **k):
        rows = real(app, conn, job_ids, **k)
        app._deep_levels_part = (("newer",), {}, set())
        return rows

    monkeypatch.setattr(deep_api, "_review_rows", meanwhile)
    everyone = set(ids(corpus).values())
    conn = connect(corpus.config.db_path)
    got = deep_api.deep_levels_within(corpus, conn, everyone)
    conn.close()
    monkeypatch.setattr(deep_api, "_review_rows", real)
    assert got == whole_map(corpus)
    assert corpus._deep_levels_part == (("newer",), {}, set())


def test_concurrent_overlapping_calls_agree_with_the_whole_map(corpus) -> None:
    fresh(corpus)
    whole = whole_map(corpus)
    everyone = sorted(ids(corpus).values())
    sets = [set(everyone[i::3]) | set(everyone[: len(everyone) // 2]) for i in range(3)] * 2
    answers: list[dict | BaseException] = [None] * len(sets)  # type: ignore[list-item]
    start = threading.Barrier(len(sets))

    def run(n: int) -> None:
        conn = connect(corpus.config.db_path)
        try:
            start.wait()
            answers[n] = deep_api.deep_levels_within(corpus, conn, sets[n])
        except BaseException as exc:  # noqa: BLE001 - reported below
            answers[n] = exc
        finally:
            conn.close()

    threads = [threading.Thread(target=run, args=(n,)) for n in range(len(sets))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for wanted, got in zip(sets, answers, strict=True):
        assert got == {j: v for j, v in whole.items() if j in wanted}
    assert_memo_agrees(corpus)


def test_every_home_with_the_memo_warm_is_the_whole_maps(corpus, monkeypatch) -> None:
    """A sequence of states, the memo of parts kept across all of them."""
    fresh(corpus)
    checks = [
        lambda: None,
        lambda: write(
            corpus,
            "UPDATE job SET closed_at = '2026-01-01T00:00:00Z' WHERE id NOT IN"
            " (SELECT job_id FROM deep_review) AND rowid % 5 = 0",
        ),
        lambda: write(corpus, "UPDATE verified_claim SET revision = revision + 1"),
        lambda: setattr(corpus, "retrieval", type("Busy", (), {"running": True})()),
    ]
    for step in checks:
        step()
        corpus._home_lists = None
        new = home(corpus)
        corpus._home_lists = None
        kept = corpus._deep_levels_part
        from tests.integration.test_home_deep_levels_within import home_whole_map

        corpus._deep_levels_cache = None
        assert new == home_whole_map(corpus, monkeypatch)
        corpus._deep_levels_cache = None
        corpus._deep_levels_part = kept
        assert_memo_agrees(corpus)


def test_a_write_between_the_key_and_the_derivation_is_not_published_under_the_key(
    corpus, monkeypatch
) -> None:
    """A direct caller (no snapshot of its own): the key and the derivation
    still read one snapshot, so the memo never holds the newer state under
    the older key."""
    fresh(corpus)
    before = whole_map(corpus)
    target = next(j for j, (_level, stale, _state) in sorted(before.items()) if not stale)
    real = deep_api._review_rows

    def then_a_writer(app, conn, job_ids=None, **k):
        write(corpus, "UPDATE job SET title = title || ' (moved)' WHERE id = ?", (target,))
        monkeypatch.setattr(deep_api, "_review_rows", real)
        return real(app, conn, job_ids, **k)

    monkeypatch.setattr(deep_api, "_review_rows", then_a_writer)
    conn = connect(corpus.config.db_path)
    got = deep_api.deep_levels_within(corpus, conn, set(before))
    conn.close()
    assert got == before, "the snapshot the key was read in"
    key, levels, _asked = corpus._deep_levels_part
    assert levels == before
    conn = connect(corpus.config.db_path)
    now = deep_api.deep_levels_within(corpus, conn, {target})
    conn.close()
    assert corpus._deep_levels_part[0] != key
    assert now[target][1] is True, "the next call, under the new key, sees the new title"
