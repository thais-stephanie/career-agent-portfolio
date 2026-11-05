"""Synthetic scenarios for the Workable closing model (research, not product)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from workable_lifecycle_sim import (  # noqa: E402
    CLOSED,
    GONE,
    OPEN,
    PUBLISHED,
    SUSPECTED,
    UNKNOWN,
    Catalogue,
    Policy,
    Read,
    apply_read,
)

BASE = frozenset(f"p{i}" for i in range(100))
C = Policy(name="C", misses=2, min_hours=72)
B = Policy(name="B", misses=2, min_hours=72)


def feed(version: str, hours: float, present=BASE, **kw) -> Read:
    return Read(version=version, at_hours=hours, present=frozenset(present), **kw)


def start(policy=C) -> Catalogue:
    cat = Catalogue()
    apply_read(cat, feed("v0", 0), policy)
    return cat


def without(*ids: str) -> frozenset[str]:
    return BASE - set(ids)


def test_one_absence_never_closes():
    cat = start(B)
    apply_read(cat, feed("v1", 100, without("p1")), B)
    assert cat.postings["p1"].status == SUSPECTED


def test_temporary_disappearance_recovers():
    cat = start()
    apply_read(cat, feed("v1", 48, without("p1")), C, lambda _: GONE)
    apply_read(cat, feed("v2", 96), C, lambda _: GONE)
    p = cat.postings["p1"]
    assert p.status == OPEN and p.misses == 0
    assert [e[1] for e in p.events] == [SUSPECTED, OPEN]


def test_confirmed_closure_needs_misses_time_and_employer():
    cat = start()
    apply_read(cat, feed("v1", 24, without("p1")), C, lambda _: GONE)
    apply_read(cat, feed("v2", 48, without("p1")), C, lambda _: GONE)
    assert cat.postings["p1"].status == SUSPECTED  # 2 misses but only 24 h apart
    apply_read(cat, feed("v3", 100, without("p1")), C, lambda _: GONE)
    assert cat.postings["p1"].status == CLOSED
    assert cat.postings["p1"].events[-1][2] == "EMPLOYER_SAYS_GONE"


def test_reopened_after_closing_keeps_history():
    cat = start(B)
    apply_read(cat, feed("v1", 24, without("p1")), B)
    apply_read(cat, feed("v2", 100, without("p1")), B)
    assert cat.postings["p1"].status == CLOSED
    apply_read(cat, feed("v3", 130), B)
    p = cat.postings["p1"]
    assert p.status == OPEN and [e[1] for e in p.events] == [SUSPECTED, CLOSED, OPEN]


@pytest.mark.parametrize(
    "read",
    [
        feed("cut", 200, without(*[f"p{i}" for i in range(60)]), complete=False),  # truncated
        feed("timeout", 200, frozenset(), complete=False),  # timeout, 429, 5xx: the read raised
        feed("v0", 200, frozenset(), not_modified=True),  # 304
    ],
)
def test_incomplete_or_unchanged_reads_change_nothing(read):
    cat = start(B)
    assert apply_read(cat, read, B).startswith("IGNORED")
    assert all(p.status == OPEN and p.misses == 0 for p in cat.postings.values())


def test_empty_feed_is_held():
    cat = start(B)
    assert apply_read(cat, feed("v1", 100, frozenset()), B) == "HELD_FEED_SHRANK"
    assert all(p.status == OPEN for p in cat.postings.values())


def test_sudden_large_reduction_is_held():
    cat = start(B)
    gone = [f"p{i}" for i in range(40)]
    assert apply_read(cat, feed("v1", 100, without(*gone)), B) == "HELD_FEED_SHRANK"


def test_churn_with_stable_size_is_held_too():
    cat = start(B)
    swapped = (BASE - {f"p{i}" for i in range(20)}) | {f"n{i}" for i in range(20)}
    assert apply_read(cat, feed("v1", 100, swapped), B) == "HELD_TOO_MANY_NEW_MISSES"


def test_employer_check_unavailable_keeps_it_open_to_ask_again():
    cat = start()
    for v, h in (("v1", 24), ("v2", 100), ("v3", 130)):
        apply_read(cat, feed(v, h, without("p1")), C, lambda _: UNKNOWN)
    assert cat.postings["p1"].status == SUSPECTED
    assert cat.confirmations == 2  # asked on each due read, closed on none


def test_employer_still_publishing_wins_over_the_feed():
    cat = start()
    apply_read(cat, feed("v1", 24, without("p1")), C, lambda _: PUBLISHED)
    apply_read(cat, feed("v2", 100, without("p1")), C, lambda _: PUBLISHED)
    p = cat.postings["p1"]
    assert p.status == OPEN and p.misses == 0
    assert p.events[-1][2] == "EMPLOYER_STILL_LISTS"


def test_republished_under_a_new_id_is_two_postings():
    cat = start(B)
    moved = without("p1") | {"p1-new"}
    apply_read(cat, feed("v1", 24, moved), B)
    apply_read(cat, feed("v2", 100, moved), B)
    assert cat.postings["p1"].status == CLOSED
    assert cat.postings["p1-new"].status == OPEN  # no fuzzy merge (ADR-0008)


def test_the_same_feed_version_counts_once():
    cat = start(B)
    for _ in range(3):
        apply_read(cat, feed("v1", 100, without("p1")), B)
    assert cat.postings["p1"].misses == 1


def test_policy_a_never_closes():
    a = Policy(name="A")
    cat = start(a)
    for v, h in (("v1", 100), ("v2", 200), ("v3", 300)):
        apply_read(cat, feed(v, h, frozenset()), a)
    assert all(p.status == OPEN for p in cat.postings.values())


def test_confirmation_budget_defers_the_rest():
    capped = Policy(name="C", misses=1, min_hours=0, confirm_cap=3)
    cat = start(capped)
    same_size = without("p1", "p2", "p3", "p4", "p5") | {f"n{i}" for i in range(5)}
    apply_read(cat, feed("v1", 1, same_size), capped, lambda _: GONE)
    closed = [k for k, p in cat.postings.items() if p.status == CLOSED]
    assert len(closed) == 3 and cat.confirmations == 3


CS = Policy(name="CS", misses=2, min_hours=24, confirm_cap=2)


def _two_misses(policy, confirm):
    cat = start(policy)
    shifted = without("p1", "p2", "p3") | {"n1", "n2", "n3"}
    apply_read(cat, feed("v1", 10, shifted), policy, confirm)
    return cat, apply_read(cat, feed("v2", 40, shifted), policy, confirm)


def test_sampled_audit_closes_the_batch_when_every_answer_is_gone():
    cat, outcome = _two_misses(CS, lambda _: GONE)
    assert outcome == "COUNTED" and cat.confirmations == 2
    assert all(cat.postings[p].status == CLOSED for p in ("p1", "p2", "p3"))


@pytest.mark.parametrize("answer", [PUBLISHED, UNKNOWN])
def test_sampled_audit_holds_the_batch_on_any_other_answer(answer):
    every = Policy(name="CS", misses=2, min_hours=24, confirm_cap=3)  # samples all three
    cat, outcome = _two_misses(every, lambda pid: answer if pid == "p2" else GONE)
    assert outcome == "HELD_AUDIT_SAMPLE"
    assert not any(cat.postings[p].status == CLOSED for p in ("p1", "p2", "p3"))
    assert cat.postings["p2"].status == (OPEN if answer == PUBLISHED else SUSPECTED)
