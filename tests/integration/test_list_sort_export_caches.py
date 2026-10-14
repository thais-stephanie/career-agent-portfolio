"""Find jobs sorting and export, and the remembered answers behind three screens.

* A header sort orders the WHOLE filtered list before a page is cut, so the
  pages of one order join up with no repeat and no gap; empty values go last
  both ways; going back to the default restores best match first.
* The export carries the whole stored advert, unchanged.
* Home, the deep reading's counts and the reader count are remembered, and
  each is read again when something it depends on moves: a score written by
  another connection, a hidden posting, the reading identity, another profile.
"""

from __future__ import annotations

import csv
import io

import pytest
from tests.integration.test_deep_api_provider import POSTINGS, api, call  # noqa: F401 - fixture

from career_agent.storage.db import connect, transaction

WIDE = {
    "include_ineligible": ["true"],
    "include_unresolved": ["true"],
    "include_off_target": ["true"],
    "group_duplicates": ["false"],
}


def page(app, **query) -> dict:
    return app.handle_api(
        "GET", "/api/jobs", {**WIDE, **{k: [str(v)] for k, v in query.items()}}, {}
    )


def every_page(app, sort: str, direction: str, size: int = 7) -> list[dict]:
    seen: list[dict] = []
    offset = 0
    while True:
        items = page(app, sort=sort, direction=direction, limit=size, offset=offset)["items"]
        if not items:
            return seen
        seen.extend(items)
        offset += size


# ------------------------------------------------------------------ sorting


@pytest.mark.parametrize("direction", ["asc", "desc"])
def test_a_title_sort_orders_the_whole_list_before_the_pages(api, direction) -> None:  # noqa: F811
    rows = every_page(api, "title", direction)
    titles = [r["title"] for r in rows]
    assert len(rows) == POSTINGS and len({r["job_id"] for r in rows}) == POSTINGS
    assert titles == sorted(titles, key=str.casefold, reverse=direction == "desc")


def test_an_empty_value_sorts_last_in_both_directions(api) -> None:  # noqa: F811
    conn = connect(api.config.db_path)
    with transaction(conn):
        conn.execute("UPDATE job SET location_raw = NULL WHERE external_id IN ('p-1', 'p-2')")
    conn.close()
    for direction in ("asc", "desc"):
        rows = every_page(api, "place", direction)
        empties = [i for i, r in enumerate(rows) if not r.get("location_raw")]
        assert empties == [len(rows) - 2, len(rows) - 1], direction


def test_the_default_order_is_best_match_first(api) -> None:  # noqa: F811
    rows = every_page(api, "score", "desc")
    scores = [r["match_score"] for r in rows]
    assert scores == sorted(scores, reverse=True)


def test_a_sort_key_nobody_offered_is_refused(api) -> None:  # noqa: F811
    from career_agent.web.server import ApiError

    with pytest.raises(ApiError):
        page(api, sort="favourite_colour")


# ------------------------------------------------------------------ export


def test_the_page_export_reads_each_whole_advert_unchanged(api) -> None:  # noqa: F811
    conn = connect(api.config.db_path)
    stored = dict(
        conn.execute(
            "SELECT j.id, r.description_text FROM job j JOIN job_raw r USING (content_hash)"
        ).fetchall()
    )
    conn.close()
    ids = list(stored)[:5]
    got = api.handle_api("GET", "/api/jobs/descriptions", {"ids": [",".join(ids)]}, {})
    assert got["descriptions"] == {i: stored[i] for i in ids}


def test_the_descriptions_route_is_bounded(api) -> None:  # noqa: F811
    from career_agent.web.server import ApiError

    with pytest.raises(ApiError):
        api.handle_api("GET", "/api/jobs/descriptions", {"ids": [""]}, {})
    with pytest.raises(ApiError):
        api.handle_api("GET", "/api/jobs/descriptions", {"ids": [",".join(["x"] * 501)]}, {})


def test_the_best_fits_export_has_the_whole_job_description(api) -> None:  # noqa: F811
    conn = connect(api.config.db_path)
    with transaction(conn):
        # A multi-line advert with characters a spreadsheet must keep.
        conn.execute(
            "UPDATE job_raw SET description_text = description_text || ?",
            ('\n\nLinha dois: São Paulo, "aspas", vírgula.',),
        )
        conn.execute("UPDATE job_match SET fit_band = 'STRONG'")
    stored = dict(
        conn.execute(
            "SELECT j.id, r.description_text FROM job j JOIN job_raw r USING (content_hash)"
        ).fetchall()
    )
    conn.close()
    download = api.handle_api("GET", "/api/jobs/export.csv", dict(WIDE), {})
    text = download.body.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text[1:])))
    assert rows and all(row["Job description"] == stored[row["Job id"]] for row in rows)


# ------------------------------------------------------------------ caches


def write_score(app) -> None:
    """Another process rescoring: a score row changes behind the server's back."""
    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute(
            "UPDATE job_match SET eligibility_status = 'VERIFIED_NOT_ELIGIBLE'"
            " WHERE job_id = (SELECT id FROM job WHERE external_id = 'p-0')"
        )
    conn.close()


def test_the_deep_counts_are_remembered_and_read_again_when_a_score_moves(api, monkeypatch) -> None:  # noqa: F811
    from career_agent.semantic import deep_run

    reads: list[int] = []
    real = deep_run.population_rows

    def counting(*args, **kwargs):
        reads.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(deep_run, "population_rows", counting)
    first = call(api, "GET", "/api/deep/coverage")
    again = call(api, "GET", "/api/deep/coverage")
    assert len(reads) == 1 and again == first, "a return reads nothing again"
    write_score(api)
    after = call(api, "GET", "/api/deep/coverage")
    assert len(reads) == 2
    assert after["main_list_roles"] == first["main_list_roles"] - 1


def test_the_deep_counts_are_read_again_after_a_hide(api) -> None:  # noqa: F811
    before = call(api, "GET", "/api/deep/coverage")["main_list_roles"]
    job_id = page(api, limit=1)["items"][0]["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/hidden", {}, {"hidden": True})
    assert call(api, "GET", "/api/deep/coverage")["main_list_roles"] == before - 1


def test_home_is_remembered_and_read_again_when_a_score_moves(api, monkeypatch) -> None:  # noqa: F811
    from career_agent.pipeline import rescore as rescore_module

    plans: list[int] = []
    real = rescore_module.plan

    def counting(*args, **kwargs):
        plans.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(rescore_module, "plan", counting)
    first = call(api, "GET", "/api/home")
    assert call(api, "GET", "/api/home") == first
    # The outdated count is asked apart (2026-10-13): Home itself plans nothing.
    assert plans == [] and first["scores"]["outdated"] is None
    counted = call(api, "GET", "/api/home/outdated")
    assert call(api, "GET", "/api/home/outdated") == counted and len(plans) == 1
    write_score(api)
    call(api, "GET", "/api/home/outdated")
    assert len(plans) == 2


def test_the_reader_count_follows_the_reading_identity_and_new_scores(api, monkeypatch) -> None:  # noqa: F811
    from career_agent.match import identity

    assert call(api, "GET", "/api/health")["stale_scores"] == 0
    monkeypatch.setattr(identity, "READER_IDENTITY", "readers-next")
    assert call(api, "GET", "/api/health")["stale_scores"] == POSTINGS
    # Scores rewritten by another connection under the new identity: the
    # remembered count is not served past them.
    from career_agent.pipeline.rescore import rescore

    conn = connect(api.config.db_path)
    rescore(conn, api.search_config())
    conn.close()
    assert call(api, "GET", "/api/health")["stale_scores"] == 0


def test_a_remembered_answer_belongs_to_one_profile(tmp_path) -> None:
    from tests.support import career_profile

    _p1, first = career_profile(tmp_path, "prof-one", "One")
    _p2, second = career_profile(tmp_path, "prof-two", "Two")
    conn = connect(second.config.db_path)
    with transaction(conn):
        conn.execute("DELETE FROM job_match")
    conn.close()
    mine = call(first, "GET", "/api/home")
    theirs = call(second, "GET", "/api/home")
    assert mine["total"] > 0 and theirs["total"] == 0
    assert call(first, "GET", "/api/home") == mine


def test_the_readable_count_counts_characters_not_bytes(api) -> None:  # noqa: F811
    """The length is taken from the record header where that is certain, and
    counted exactly near the threshold: 150 two-byte characters (300 bytes)
    are too short to read, 250 of them (500 bytes) are not."""
    before = call(api, "GET", "/api/deep/coverage")
    conn = connect(api.config.db_path)
    with transaction(conn):
        hashes = [r[0] for r in conn.execute("SELECT content_hash FROM job ORDER BY id LIMIT 2")]
        conn.execute(
            "UPDATE job_raw SET description_text = ? WHERE content_hash = ?", ("ç" * 150, hashes[0])
        )
        conn.execute(
            "UPDATE job_raw SET description_text = ? WHERE content_hash = ?", ("ç" * 250, hashes[1])
        )
        # A content change the triggers do not see: say so the way a writer would.
        conn.execute("UPDATE job SET updated_at = updated_at WHERE content_hash IN (?, ?)", hashes)
    conn.close()
    after = call(api, "GET", "/api/deep/coverage")
    assert after["main_list_roles"] == before["main_list_roles"]
    assert after["readable"] == before["readable"] - 1


def test_the_contract_filter_reads_the_engagement_with_the_default(api) -> None:  # noqa: F811
    """An American employer hiring in Latin America that states no engagement
    is filed under Contractor by the Contract filter, and counted there."""
    from career_agent.pipeline.rescore import rescore

    conn = connect(api.config.db_path)
    with transaction(conn):
        job_id, digest = conn.execute(
            "SELECT id, content_hash FROM job WHERE external_id = 'p-3'"
        ).fetchone()
        conn.execute(
            "UPDATE job_raw SET description_text = ? || description_text WHERE content_hash = ?",
            (
                "We are a US-based startup. This role is open to candidates in Latin America.\n\n",
                digest,
            ),
        )
        conn.execute("DELETE FROM job_match WHERE job_id = ?", (job_id,))
    rescore(conn, api.search_config())
    conn.close()
    found = page(api, engagement="CONTRACTOR_B2B", limit=50)
    assert [item["job_id"] for item in found["items"]] == [job_id]
    assert found["items"][0]["employment_default"] is True
    buckets = found["facets"]["engagement"]
    assert buckets.get("CONTRACTOR_B2B") == 1


def _engagement_rows(app) -> dict[str, str]:
    found = page(app, limit=50)
    return {item["job_id"]: item for item in found["items"]}


def test_card_table_and_filter_file_the_engagement_the_same_way(api) -> None:  # noqa: F811
    """Old, partly updated and new scores, before and after a recalculation:
    the Contract filter, its counts and the card agree, and a score made
    before the engagement was read is "not read yet", never "not stated"."""
    from career_agent.pipeline.rescore import rescore

    conn = connect(api.config.db_path)
    with transaction(conn):
        rows = conn.execute("SELECT id, external_id FROM job ORDER BY id").fetchall()
        explicit = next(r[0] for r in rows if r[1] == "p-1")
        old = next(r[0] for r in rows if r[1] == "p-2")
        digest = conn.execute("SELECT content_hash FROM job WHERE id = ?", (explicit,)).fetchone()[
            0
        ]
        conn.execute(
            "UPDATE job_raw SET description_text = ? || description_text WHERE content_hash = ?",
            ("Employment type: full-time employee (CLT).\n\n", digest),
        )
        conn.execute("DELETE FROM job_match WHERE job_id = ?", (explicit,))
    rescore(conn, api.search_config())
    with transaction(conn):
        # A score from before the engagement was read: schema 13, no column.
        conn.execute(
            "UPDATE job_match SET schema_version = 13, engagement = NULL,"
            " result_json = json_set(result_json, '$.schema_version', 13) WHERE job_id = ?",
            (old,),
        )
    conn.close()

    found = page(api, limit=50)
    buckets = found["facets"]["engagement"]
    cards = {item["job_id"]: item for item in found["items"]}
    assert buckets.get("NOT_READ") == 1
    assert cards[old]["engagement"] is None and cards[old]["engagement_read"] is False
    assert cards[explicit]["engagement"] == "EMPLOYEE" and cards[explicit]["engagement_read"]
    # Each bucket returns exactly the cards that show it.
    for value, count in buckets.items():
        listed = page(api, engagement=value, limit=50)["items"]
        assert len(listed) == count, value
        for item in listed:
            unread = not item["engagement_read"]
            shown = item["engagement"] or ("NOT_READ" if unread else "NOT_STATED")
            assert shown == value, (value, item["job_id"])
    # Hours stay apart: full-time is never the engagement.
    assert all(item["engagement"] != "FULL_TIME" for item in found["items"])

    # After a recalculation the old score is read and leaves "not read yet".
    conn = connect(api.config.db_path)
    with transaction(conn):
        conn.execute("DELETE FROM job_match WHERE job_id = ?", (old,))
    rescore(conn, api.search_config())
    conn.close()
    assert "NOT_READ" not in page(api, limit=50)["facets"]["engagement"]


PAY = {
    # external id: (min, max, currency, period, annual min, annual max)
    "p-1": (3000, 3000, "USD", "MONTH", 36000, 36000),
    "p-2": (45000, 50000, "USD", "YEAR", 45000, 50000),
    "p-3": (45000, 60000, "USD", "YEAR", 45000, 60000),
    "p-4": (50, 50, "USD", "HOUR", 104000, 104000),
    "p-5": (60000, 60000, "EUR", "YEAR", 60000, 60000),
    "p-6": (160000, 220000, "USD", None, None, None),
}


def _pay(app) -> dict[str, str]:
    conn = connect(app.config.db_path)
    ids = {}
    with transaction(conn):
        conn.execute(
            "UPDATE job_match SET salary_min = NULL, salary_max = NULL, salary_currency = NULL,"
            " salary_period = NULL, salary_annual_min = NULL, salary_annual_max = NULL"
        )
        for external, values in PAY.items():
            job_id = conn.execute(
                "SELECT id FROM job WHERE external_id = ?", (external,)
            ).fetchone()[0]
            ids[job_id] = external
            conn.execute(
                "UPDATE job_match SET salary_min = ?, salary_max = ?, salary_currency = ?,"
                " salary_period = ?, salary_annual_min = ?, salary_annual_max = ? WHERE job_id = ?",
                (*values, job_id),
            )
    conn.close()
    return ids


def test_salary_sorts_comparable_pay_and_puts_the_rest_last(api) -> None:  # noqa: F811
    ids = _pay(api)
    up = [
        ids.get(i["job_id"]) for i in page(api, sort="salary", direction="asc", limit=50)["items"]
    ]
    down = [
        ids.get(i["job_id"]) for i in page(api, sort="salary", direction="desc", limit=50)["items"]
    ]
    # A month is a twelfth of a year; a band by its lower end, then its upper.
    assert [x for x in up if x][:3] == ["p-1", "p-2", "p-3"]
    assert [x for x in down if x][:3] == ["p-3", "p-2", "p-1"]
    # Hourly, another currency and no period are not comparable: last both ways.
    for order in (up, down):
        compared = order.index("p-3") if order is up else order.index("p-1")
        assert all(order.index(x) > compared for x in ("p-4", "p-5", "p-6"))
    answer = page(api, sort="salary", direction="asc", limit=50)
    assert answer["sort_currency"] == "USD"
    # The whole filtered list is ordered before a page is cut.
    whole = [i["job_id"] for i in answer["items"]]
    pages = [
        i["job_id"]
        for off in range(0, len(whole), 4)
        for i in page(api, sort="salary", direction="asc", limit=4, offset=off)["items"]
    ]
    assert pages == whole


def test_a_single_currency_filter_is_the_currency_compared(api) -> None:  # noqa: F811
    ids = _pay(api)
    answer = page(api, sort="salary", direction="desc", salary_currency="EUR", limit=50)
    assert answer["sort_currency"] == "EUR"
    assert [ids.get(i["job_id"]) for i in answer["items"]] == ["p-5"]
