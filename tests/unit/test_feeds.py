"""The shared feed-walking machinery.

Every trap encoded in `providers/feeds.py` cost a real measurement somewhere in
this repository's history. These tests are that history, made executable, so
the next adapter inherits the answers instead of rediscovering them.

The distinction the whole module exists to protect: **a walk that stopped
because we said so, and a walk that stopped because the feed broke, are
different outcomes.** Reporting either as "collected" is silent truncation.
"""

from __future__ import annotations

import pytest

from career_agent.providers.feeds import (
    DEFAULT_MAX_PAGES,
    MAX_PAGES_CAP,
    BoundedWalk,
    FeedPage,
    PageBudget,
    PageBudgetError,
    deduplicate_by,
    walk_feed,
)


def page(n: int, count: int, total: int | None = None) -> FeedPage:
    return FeedPage(
        url=f"https://example.test/feed?page={n}",
        entries=tuple({"id": f"{n}-{i}"} for i in range(count)),
        page=n,
        claimed_total=total,
    )


# -- the budget ------------------------------------------------------------


def test_an_unconfigured_budget_takes_the_small_default() -> None:
    """A first run should be cheap. Defaulting high turns a preflight into a crawl."""
    assert PageBudget.resolve(50, None).max_pages == DEFAULT_MAX_PAGES


def test_there_is_no_way_to_ask_for_unlimited() -> None:
    """`None` means "the caller did not say", never "as far as you like"."""
    with pytest.raises(PageBudgetError):
        PageBudget(page_size=50, max_pages=0)


def test_a_runaway_page_count_is_refused() -> None:
    """The cost of a runaway walk lands on the vendor's server, not on ours."""
    with pytest.raises(PageBudgetError, match="cap"):
        PageBudget(page_size=50, max_pages=MAX_PAGES_CAP + 1)


def test_a_zero_page_size_is_refused() -> None:
    """A wrong page size turns a complete walk into a permanent one."""
    with pytest.raises(PageBudgetError):
        PageBudget(page_size=0)


# -- termination -----------------------------------------------------------


def test_a_short_page_ends_the_walk_and_the_walk_is_complete() -> None:
    budget = PageBudget(page_size=10, max_pages=5)
    asked: list[int] = []

    def read(n: int) -> FeedPage:
        asked.append(n)
        return page(n, 3)

    walk = walk_feed("scope", read, budget)

    assert asked == [1]
    assert walk.complete is True
    assert walk.hit_page_limit is False
    assert walk.truncated is False


def test_a_full_page_continues_the_walk_to_the_budget() -> None:
    budget = PageBudget(page_size=10, max_pages=3)
    asked: list[int] = []

    def read(n: int) -> FeedPage:
        asked.append(n)
        return page(n, 10, total=999)

    walk = walk_feed("scope", read, budget)

    assert asked == [1, 2, 3]
    assert walk.hit_page_limit is True
    assert walk.complete is False
    assert len(walk.entries) == 30


def test_the_first_page_number_is_the_vendors_convention() -> None:
    """Feeds disagree about zero or one, and guessing skips or repeats a page."""
    asked: list[int] = []

    def read(n: int) -> FeedPage:
        asked.append(n)
        return page(n, 1)

    walk_feed("scope", read, PageBudget(page_size=10, max_pages=2), first_page=0)
    assert asked == [0]


# -- the two ways a walk ends badly ----------------------------------------


def test_an_empty_page_short_of_the_stated_total_is_a_truncation() -> None:
    """A 200 carrying nothing on page 3 of 40 was once reported as a full pass."""
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 10, total=100), page(2, 0, total=100)),
        budget=PageBudget(page_size=10, max_pages=5),
    )
    assert walk.truncated is True
    assert walk.complete is False


def test_a_page_limit_is_a_bound_and_never_a_truncation() -> None:
    """Stopping because we said two pages is a decision, not a failure."""
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 10, total=100),),
        budget=PageBudget(page_size=10, max_pages=1),
    )
    assert walk.truncated is False
    assert walk.hit_page_limit is True


def test_an_empty_feed_with_no_stated_total_is_not_a_truncation() -> None:
    """A feed that says nothing about its size and returns nothing is empty."""
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 0),),
        budget=PageBudget(page_size=10, max_pages=3),
    )
    assert walk.truncated is False
    assert walk.complete is True


def test_a_deliberate_early_stop_is_neither_complete_nor_truncated() -> None:
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 10, total=100),),
        budget=PageBudget(page_size=10, max_pages=5),
        stopped_early=True,
    )
    assert walk.truncated is False
    assert walk.complete is False


# -- the ceiling -----------------------------------------------------------


def test_the_reachable_total_is_a_ceiling_and_not_a_promise() -> None:
    """ "We retrieved 500 of 48,129" and "we retrieved everything" differ."""
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 10, total=48_129),),
        budget=PageBudget(page_size=10, max_pages=5),
    )
    assert walk.claimed_total == 48_129
    assert walk.reachable_total == 50


def test_a_feed_that_states_no_total_has_no_reachable_total() -> None:
    walk = BoundedWalk(
        scope="scope",
        pages=(page(1, 3),),
        budget=PageBudget(page_size=10, max_pages=5),
    )
    assert walk.reachable_total is None


def test_a_claimed_total_is_never_used_to_stop_the_walk() -> None:
    """Gupy returns `total: 100` when `limit=100` and 82,958 when `limit=10`.

    A walk that trusted a paginated response's own total would end on page one.
    Here the total is a small lie and the walk keeps going on full pages.
    """
    budget = PageBudget(page_size=10, max_pages=3)
    asked: list[int] = []

    def read(n: int) -> FeedPage:
        asked.append(n)
        return page(n, 10, total=10)

    walk_feed("scope", read, budget)
    assert asked == [1, 2, 3]


# -- failures --------------------------------------------------------------


def test_a_failing_page_raises_rather_than_ending_the_walk_quietly() -> None:
    """A swallowed error would report two pages of postings as the whole feed."""

    def read(n: int) -> FeedPage:
        if n == 2:
            raise RuntimeError("upstream 503")
        return page(n, 10)

    with pytest.raises(RuntimeError, match="503"):
        walk_feed("scope", read, PageBudget(page_size=10, max_pages=3))


# -- within-walk deduplication ---------------------------------------------


def test_first_sighting_wins_over_an_exact_key() -> None:
    rows = [{"id": "a"}, {"id": "b"}, {"id": "a"}, {"id": "c"}]
    kept = deduplicate_by(rows, lambda r: r["id"])
    assert [r["id"] for r in kept] == ["a", "b", "c"]


def test_a_row_with_no_key_is_kept_rather_than_discarded() -> None:
    """Dropping a posting for lacking an id would be tidying a list by losing data."""
    rows = [{"id": None}, {"id": None}, {"id": "a"}]
    kept = deduplicate_by(rows, lambda r: r["id"])
    assert len(kept) == 3
