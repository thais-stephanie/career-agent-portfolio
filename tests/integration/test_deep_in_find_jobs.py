"""Find jobs reads the deep reading BESIDE the local score, never inside it.

A synthetic corpus of 30 scored postings; no provider is reached. What must
hold: a posting never read shows nothing and is found by "not read yet"; a
current reading shows its level, an out-of-date one says so and keeps the level
it had; the filter narrows in the query and its counts sum to the list; the
deep sort orders current readings by level, then out-of-date ones, then the
unread, the local score breaking ties and only ties; every other order is
unchanged; nothing writes `job_match` or `deep_review`; readings stored by a
run are shown at once; and a new job still skips what was read.
"""

from __future__ import annotations

import pytest
from tests.integration.test_deep_api_provider import api  # noqa: F401 - fixture
from tests.integration.test_deep_run import FakeCli

from career_agent.semantic.deep_run import run_deep
from career_agent.storage.db import connect, transaction
from career_agent.web import deep_api

WIDE = {
    "include_ineligible": ["true"],
    "include_unresolved": ["true"],
    "include_off_target": ["true"],
    "group_duplicates": ["false"],
}


def page(app, **query) -> dict:
    params = {**WIDE}
    for key, value in query.items():
        params[key] = [str(v) for v in value] if isinstance(value, list) else [str(value)]
    return app.handle_api("GET", "/api/jobs", params, {})


def ids(app) -> dict[str, str]:
    """external id -> job id."""
    conn = connect(app.config.db_path)
    found = {str(r[0]): str(r[1]) for r in conn.execute("SELECT external_id, id FROM job")}
    conn.close()
    return found


def set_scores(app, scores: dict[str, int]) -> None:
    conn = connect(app.config.db_path)
    by = ids(app)
    with transaction(conn):
        for external, score in scores.items():
            conn.execute(
                "UPDATE job_match SET match_score = ? WHERE job_id = ?", (score, by[external])
            )
    conn.close()


def readings(monkeypatch, app, levels: dict[str, tuple[str, bool]]) -> None:
    """The deep levels as the reading would show them, by external id."""
    by = ids(app)
    mapped = {by[external]: value for external, value in levels.items()}
    monkeypatch.setattr(deep_api, "deep_levels", lambda _app, _conn: dict(mapped))


def table(app, name: str) -> list[tuple]:
    conn = connect(app.config.db_path)
    rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()]
    conn.close()
    return rows


def external(app, items) -> list[str]:
    back = {v: k for k, v in ids(app).items()}
    return [back[i["job_id"]] for i in items]


LEVELS = {
    "p-0": ("LOW", False),
    "p-1": ("STRONG", False),
    "p-2": ("STRONG", False),
    "p-3": ("OTHER_WORK", False),
    "p-4": ("PREPARE", True),  # out of date
}


@pytest.fixture
def read(api, monkeypatch):  # noqa: F811
    set_scores(api, {"p-0": 90, "p-1": 40, "p-2": 70, "p-3": 99, "p-4": 95, "p-5": 98})
    readings(monkeypatch, api, LEVELS)
    return api


def test_a_posting_never_read_shows_nothing_and_is_found_as_unread(read) -> None:
    items = page(read, limit=60)["items"]
    by = dict(zip(external(read, items), items, strict=True))
    assert by["p-5"]["deep"] is None
    unread = external(read, page(read, deep="UNREAD", limit=60)["items"])
    assert "p-5" in unread and not set(unread) & set(LEVELS)


def test_a_current_reading_shows_its_level_and_a_stale_one_says_so(read) -> None:
    items = page(read, limit=60)["items"]
    by = dict(zip(external(read, items), items, strict=True))
    assert by["p-1"]["deep"] == {"level": "STRONG", "stale": False}
    assert by["p-4"]["deep"] == {"level": "PREPARE", "stale": True}


def test_the_filter_narrows_by_level_state_and_their_union(read) -> None:
    assert sorted(external(read, page(read, deep="STRONG")["items"])) == ["p-1", "p-2"]
    assert sorted(external(read, page(read, deep="READ")["items"])) == ["p-0", "p-1", "p-2", "p-3"]
    assert external(read, page(read, deep="STALE")["items"]) == ["p-4"]
    # A stale reading is never counted as its old level.
    assert "p-4" not in external(read, page(read, deep="PREPARE")["items"])
    union = page(read, deep=["STRONG", "STALE"])["items"]
    assert sorted(external(read, union)) == ["p-1", "p-2", "p-4"]
    assert page(read, deep="STRONG")["total"] == 2


def test_the_deep_counts_sum_to_the_list(read) -> None:
    answer = page(read, limit=1)
    buckets = answer["facets"]["deep"]
    assert buckets == {
        "STRONG": 2,
        "LOW": 1,
        "OTHER_WORK": 1,
        "STALE": 1,
        "UNREAD": answer["total"] - 5,
    }
    assert sum(buckets.values()) == answer["total"]


def test_the_deep_sort_orders_level_then_stale_then_unread_and_scores_break_ties(read) -> None:
    order = external(read, page(read, sort="deep", direction="desc", limit=60)["items"])
    # Same level: higher local score first (p-2 70 before p-1 40). Different
    # levels: the level decides, whatever the score (p-3 at 99 stays last of
    # the read ones). Then the stale reading, then everything unread, whose
    # best local score (p-5, 98) leads.
    assert order[:6] == ["p-2", "p-1", "p-0", "p-3", "p-4", "p-5"]
    assert len(order) == page(read, limit=1)["total"]


def test_ascending_puts_the_lowest_level_first_and_the_unread_still_last(read) -> None:
    order = external(read, page(read, sort="deep", direction="asc", limit=60)["items"])
    assert order[:5] == ["p-3", "p-0", "p-2", "p-1", "p-4"]
    assert order[5] == "p-5"


def test_the_deep_sort_pages_without_dropping_or_repeating(read) -> None:
    whole = external(read, page(read, sort="deep", limit=60)["items"])
    paged: list[str] = []
    for offset in range(0, len(whole), 7):
        paged += external(read, page(read, sort="deep", limit=7, offset=offset)["items"])
    assert paged == whole


@pytest.mark.parametrize("sort", ["score", "confidence", "posted", "title"])
def test_every_other_order_is_unchanged_by_a_reading(api, monkeypatch, sort) -> None:  # noqa: F811
    set_scores(api, {"p-0": 90, "p-1": 40, "p-2": 70, "p-3": 99, "p-4": 95, "p-5": 98})
    before = external(api, page(api, sort=sort, limit=60)["items"])
    readings(monkeypatch, api, LEVELS)
    assert external(api, page(api, sort=sort, limit=60)["items"]) == before


def test_listing_writes_neither_the_local_score_nor_the_reading(read) -> None:
    scores, reviews = table(read, "job_match"), table(read, "deep_review")
    for query in (
        {"deep": "STRONG"},
        {"sort": "deep"},
        {"deep": ["UNREAD", "STALE"], "sort": "deep"},
    ):
        page(read, **query)
    assert table(read, "job_match") == scores
    assert table(read, "deep_review") == reviews
    # The levels lived in a TEMP table of the connection that read them.
    conn = connect(read.config.db_path)
    assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'deep_level'").fetchone()
    conn.close()


def test_stored_readings_are_shown_at_once_and_a_new_job_skips_them(api) -> None:  # noqa: F811
    """No level is injected: a fake run stores real rows, the list derives them."""
    conn = connect(api.config.db_path)
    config = api.search_config()
    provider = FakeCli(id="claude_code", model="sonnet", answers_with="claude-sonnet-5-5")
    first = run_deep(conn, config, provider, share=False, limit=4, include_unresolved=True)
    conn.close()
    assert first.published == 4
    reviews = table(api, "deep_review")
    read_now = [i for i in page(api, deep="READ", limit=60)["items"]]
    assert len(read_now) == 4 and all(i["deep"] and not i["deep"]["stale"] for i in read_now)
    assert len(page(api, sort="deep", limit=4)["items"]) == 4
    assert table(api, "deep_review") == reviews, "listing changed no reading"
    conn = connect(api.config.db_path)
    again = run_deep(conn, config, provider, share=False, limit=4, include_unresolved=True)
    conn.close()
    read_ids = {i["job_id"] for i in read_now}
    assert again.published == 4, "four more, never the four already read"
    assert len({i["job_id"] for i in page(api, deep="READ", limit=60)["items"]} - read_ids) == 4
    assert len(table(api, "deep_review")) == len(reviews) + 4
