"""One vocabulary over eleven collectors, and the arithmetic that must hold.

The funnel reads `pipeline_run.stats_json` and arranges it. It invents no
counter and changes no collector, so everything here is about whether the
arrangement is HONEST -- which is a narrower question than whether it runs, and
the one that got answered wrong twice while this was written.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from career_agent.pipeline.funnel import (
    KNOWN_STAGES,
    funnel_from,
    latest_funnels,
    missing_stages,
)


def _funnel(**stats: object):
    return funnel_from(
        stage="collect-example",
        status="OK",
        started_at="2026-09-09T00:00:00Z",
        finished_at="2026-09-09T00:01:00Z",
        error=None,
        stats=stats,
    )


# -- not measured is not zero ------------------------------------------------


def test_a_stage_a_collector_does_not_measure_is_none_and_never_zero() -> None:
    """The distinction the whole module turns on.

    Most sources never say how many postings they hold. `discovered: 0` reads
    as a catastrophe and `discovered = fetched` reads as success, and both are
    inventions. Working Nomads genuinely publishes no total, and its row must
    say so rather than pick one of those.
    """
    funnel = _funnel(postings_seen=32, jobs_new=32)
    assert funnel.discovered is None
    assert funnel.parsed == 32
    assert funnel.fetched is None, "no http block means no request count, not no requests"


def test_a_source_that_holds_nothing_reports_zero_and_not_none() -> None:
    """The other direction, and it must not collapse into the first."""
    funnel = _funnel(claimed_total=0, postings_seen=0)
    assert funnel.discovered == 0
    assert funnel.parsed == 0


# -- the arithmetic ----------------------------------------------------------


def test_a_changed_posting_is_not_counted_twice() -> None:
    """`jobs_changed` is a SUBSET of `jobs_seen_again` -- the ones whose
    description moved. Adding it to the accepted total would count those
    postings twice and stop the funnel summing."""
    funnel = _funnel(postings_seen=100, jobs_new=60, jobs_seen_again=40, jobs_changed=7)
    assert funnel.accepted == 100
    assert funnel.jobs_changed == 7
    assert funnel.unaccounted == 0


def test_a_record_never_read_sits_outside_the_parsed_count() -> None:
    """THE REGRESSION THIS TEST EXISTS FOR: `unaccounted: -6`.

    Programathor's `postings_seen` increments only when the detail page
    ANSWERS, so its six unavailable postings never became parsed ones. Treating
    them like `postings_unaddressable` -- which is decided about a record that
    already had been read -- subtracted them from a total they were never in,
    and the first run of this report printed a negative number of postings.
    """
    funnel = _funnel(
        postings_listed=112,
        postings_unavailable=6,
        postings_seen=106,
        jobs_new=106,
    )
    assert funnel.parsed == 106
    assert funnel.rejected == 0
    assert funnel.unaccounted == 0
    assert [r.count for r in funnel.lost_before_parsing] == [6]
    assert "never" in funnel.lost_before_parsing[0].reason or "nothing was read" in (
        funnel.lost_before_parsing[0].reason
    )


def test_a_rejection_carries_its_reason_and_not_only_a_count() -> None:
    """A count with no reason is a rumour, and every one of these used to be
    exactly that: `postings_unaddressable: 34` says thirty-four postings were
    dropped and does not say what was wrong with them."""
    funnel = _funnel(
        postings_seen=100,
        postings_unaddressable=3,
        postings_without_company=2,
        jobs_new=95,
    )
    assert funnel.rejected == 5
    reasons = {r.reason for r in funnel.rejections}
    assert any("identity" in r for r in reasons)
    assert any("employer" in r for r in reasons)
    assert funnel.unaccounted == 0


def test_a_posting_dropped_with_no_reason_shows_up_as_unaccounted() -> None:
    """The number worth looking at, and the reason it is computed rather than
    reported: a collector that drops a posting without counting why cannot be
    the thing that tells you it did."""
    funnel = _funnel(postings_seen=100, jobs_new=90)
    assert funnel.unaccounted == 10


def test_the_gupy_partition_probes_add_up_to_what_the_source_holds() -> None:
    """It cannot use a single key: it cuts the feed into slices and probes each
    one against the live feed, so what the source holds for what was asked is
    the sum of those probes."""
    funnel = _funnel(
        partition_totals={"remote": 2115, "hybrid": 5970, "on-site": 72682},
        postings_seen=100,
    )
    assert funnel.discovered == 80767


# -- a bound is not a failure ------------------------------------------------


@pytest.mark.parametrize(
    "bound",
    ["stopped_early", "truncated", "window_full"],
)
def test_a_bounded_walk_is_reported_as_a_bound_and_not_as_a_failure(bound: str) -> None:
    """V1.6 separated "we chose to stop" from "they refused" on purpose, and a
    report that printed either as a failure would undo that in one line."""
    funnel = _funnel(postings_seen=10, jobs_new=10, **{bound: True})
    assert funnel.failed == 0
    assert bound in funnel.bounds


def test_a_vendor_ceiling_is_named_with_how_many_slices_hit_it() -> None:
    funnel = _funnel(postings_seen=10, jobs_new=10, slices_over_ceiling=["a", "b", "c"])
    assert "slices_over_ceiling=3" in funnel.bounds
    assert funnel.failed == 0


def test_a_false_bound_is_not_reported_at_all() -> None:
    funnel = _funnel(postings_seen=10, jobs_new=10, stopped_early=False)
    assert funnel.bounds == ()


# -- failures ----------------------------------------------------------------


def test_failures_count_the_transport_the_source_and_the_collector() -> None:
    """Three different units, deliberately summed only into a headline and
    named separately underneath. Summing `boards_failed` with `feeds_failed`
    into a labelled figure would invent a number whose unit is nothing."""
    funnel = _funnel(
        postings_seen=10,
        jobs_new=10,
        failures=["slice on-site/SP: timed out"],
        boards_failed=2,
        http={"requests": 40, "failure_count": 1},
    )
    assert funnel.failed == 4
    assert funnel.fetched == 40
    assert funnel.failures == ("slice on-site/SP: timed out",)


# -- reading the database ----------------------------------------------------


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE pipeline_run ("
        " id TEXT PRIMARY KEY, stage TEXT, started_at TEXT, finished_at TEXT,"
        " status TEXT, stats_json TEXT, error TEXT)"
    )
    return conn


def _row(conn: sqlite3.Connection, run_id: str, stage: str, started: str, **stats: object) -> None:
    conn.execute(
        "INSERT INTO pipeline_run VALUES (?, ?, ?, ?, 'OK', ?, NULL)",
        (run_id, stage, started, started, json.dumps(stats)),
    )


def test_only_the_most_recent_run_of_each_stage_is_reported() -> None:
    """A stage run nightly for a week holds seven rows, six of which answer a
    question nobody asked. What a person wants to know is whether the last one
    worked."""
    conn = _db()
    _row(conn, "1", "collect-gupy", "2026-09-01T00:00:00Z", postings_seen=1)
    _row(conn, "2", "collect-gupy", "2026-09-08T00:00:00Z", postings_seen=999)
    _row(conn, "3", "collect-wwr", "2026-09-05T00:00:00Z", postings_seen=90)

    funnels = latest_funnels(conn)
    assert [f.stage for f in funnels] == ["collect-gupy", "collect-wwr"]
    assert funnels[0].parsed == 999


def test_a_rescore_is_not_a_collection_and_does_not_appear() -> None:
    conn = _db()
    _row(conn, "1", "rescore", "2026-09-08T00:00:00Z", jobs_scored=28195)
    assert latest_funnels(conn) == ()


def test_unreadable_stored_stats_do_not_end_the_report() -> None:
    """A diagnostic that dies on one bad row is a diagnostic nobody can run at
    the moment they need it most."""
    conn = _db()
    conn.execute(
        "INSERT INTO pipeline_run VALUES ('1','collect-wwr','2026-09-08T00:00:00Z',"
        "NULL,'FAILED','{not json',NULL)"
    )
    funnels = latest_funnels(conn)
    assert len(funnels) == 1
    assert funnels[0].parsed is None


def test_a_stage_that_never_ran_is_named_rather_than_omitted() -> None:
    """A report listing only what ran answers "did the collections work" and
    silently drops "and which ones never happened", which is what a corpus
    missing a whole market looks like from the inside."""
    conn = _db()
    _row(conn, "1", "collect-gupy", "2026-09-08T00:00:00Z", postings_seen=1)
    never = {n.stage for n in missing_stages(KNOWN_STAGES, latest_funnels(conn))}
    assert "collect-remoteok" in never
    assert "collect-gupy" not in never


def test_every_known_stage_is_a_stage_the_reader_would_find() -> None:
    """`KNOWN_STAGES` is what SHOULD have run. A name here that no command ever
    writes would be permanently reported as never-run, which is a false alarm
    that trains somebody to ignore the list."""
    for stage in KNOWN_STAGES:
        assert stage == "collect" or stage.startswith("collect-")


# -- against the collectors' own shapes --------------------------------------


def test_every_collectors_own_stats_shape_produces_a_coherent_funnel() -> None:
    """The structural half: each collector's `as_dict` at its defaults, through
    the reader. It catches the case where somebody renames a counter and the
    funnel silently starts reporting `not measured` forever.
    """
    from career_agent.pipeline.arbeitnow_collect import ArbeitnowStats
    from career_agent.pipeline.getonbrd_collect import GetonbrdStats
    from career_agent.pipeline.gupy_collect import GupyStats
    from career_agent.pipeline.himalayas_collect import HimalayasStats
    from career_agent.pipeline.jobicy_collect import JobicyStats
    from career_agent.pipeline.programathor_collect import ProgramathorStats
    from career_agent.pipeline.remoteok_collect import RemoteOkStats
    from career_agent.pipeline.speedrun_collect import SpeedrunStats
    from career_agent.pipeline.workingnomads_collect import WorkingNomadsStats
    from career_agent.pipeline.wwr_collect import WwrStats

    for stats_class in (
        ArbeitnowStats,
        GetonbrdStats,
        GupyStats,
        HimalayasStats,
        ProgramathorStats,
        JobicyStats,
        RemoteOkStats,
        SpeedrunStats,
        WorkingNomadsStats,
        WwrStats,
    ):
        stored = json.loads(json.dumps(stats_class().as_dict()))
        funnel = funnel_from(
            stage="collect-example",
            status="OK",
            started_at="2026-09-09T00:00:00Z",
            finished_at=None,
            error=None,
            stats=stored,
        )
        assert funnel.parsed == 0, f"{stats_class.__name__} no longer reports what it parsed"
        assert funnel.accepted == 0, f"{stats_class.__name__} no longer reports what it stored"
        assert funnel.unaccounted == 0


# -- the counter that was declared and never incremented ---------------------


def test_an_ats_adapter_counts_the_entries_it_could_not_read() -> None:
    """THE PROMISE THAT WAS WRITTEN DOWN AND NOT KEPT.

    `greenhouse._to_stub` has said since it was written that "a single unusable
    entry is skipped rather than failing the whole board... the skipped count
    surfaces in the run statistics, so it is never silent".
    `CollectionStats.postings_skipped_malformed` was declared beside it and
    incremented nowhere, so every ATS adapter dropped malformed entries in
    total silence, under a docstring saying it did not.

    Found by `career-agent ingestion-report`, which reads that counter -- the
    funnel's whole reason for existing is that a rejection with no reason is a
    rumour, and this one had no count either.
    """
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.providers.base import BoardRef
    from career_agent.providers.greenhouse import GreenhouseProvider

    body = {
        "jobs": [
            {"id": 1, "title": "Real", "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"},
            {"title": "No id at all"},
            "not even an object",
            {"id": 3, "absolute_url": "https://boards.greenhouse.io/acme/jobs/3"},
        ]
    }
    fetcher = HttpFetcher(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda _r: httpx.Response(200, json=body))
        )
    )
    try:
        provider = GreenhouseProvider(fetcher)
        stubs = list(provider.list_postings(BoardRef("acme", "greenhouse", "acme")))
    finally:
        fetcher.close()

    assert len(stubs) == 1
    assert provider.postings_skipped == 3, "three entries were read and could not be used"


def test_the_counter_starts_at_zero_on_every_adapter() -> None:
    """Per INSTANCE, because `pipeline.collect` builds one adapter per board
    and reads the counter as that board's."""
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.providers.registry import available_providers, get_provider

    fetcher = HttpFetcher()
    try:
        for name in available_providers():
            if name == "jooble":
                continue  # refuses to exist without JOOBLE_DOMAIN, on purpose
            assert get_provider(name, fetcher).postings_skipped == 0, name
    finally:
        fetcher.close()


# -- one stage, four families ------------------------------------------------


def test_the_ats_runner_reports_each_family_separately() -> None:
    """One stage drives Greenhouse, Lever, Ashby and Recruiterflow in a single
    pass, and one set of totals for all four cannot answer the question this
    module exists for: which source is losing postings, and where."""
    funnel = _funnel(
        postings_observed=300,
        jobs_new=290,
        by_provider={
            "greenhouse": {"postings_observed": 200, "jobs_new": 200},
            "recruiterflow": {"postings_observed": 100, "jobs_new": 90},
        },
    )
    split = dict(funnel.per_provider)
    assert set(split) == {"greenhouse", "recruiterflow"}
    assert split["greenhouse"].parsed == 200
    assert split["greenhouse"].unaccounted == 0
    assert split["recruiterflow"].unaccounted == 10


def test_a_stage_that_is_one_source_reports_no_split_at_all() -> None:
    """Empty rather than a single row repeating the totals, which would be a
    breakdown that breaks nothing down."""
    assert _funnel(postings_seen=10, jobs_new=10).per_provider == ()


def test_the_split_and_the_totals_cannot_drift_apart() -> None:
    """THE REASON `tally` IS ONE METHOD.

    A per-provider breakdown maintained beside the totals is a breakdown that
    disagrees with them the first time somebody adds a counter and updates one
    of the two places -- silently, and in the direction that makes a report
    look fine. Every counter goes through `CollectionStats.tally`, which writes
    both, and this asserts the property that buys.
    """
    from career_agent.pipeline.collect import CollectionStats

    stats = CollectionStats()
    stats.tally("greenhouse", "jobs_new")
    stats.tally("greenhouse", "jobs_new")
    stats.tally("recruiterflow", "jobs_new")
    stats.tally("recruiterflow", "postings_observed", 5)

    assert stats.jobs_new == 3
    assert stats.postings_observed == 5
    for counter in ("jobs_new", "postings_observed"):
        split = sum(b.get(counter, 0) for b in stats.by_provider.values())
        assert split == getattr(stats, counter), counter
    assert stats.as_dict()["by_provider"]["greenhouse"]["jobs_new"] == 2


def test_every_counter_the_ats_runner_moves_goes_through_the_tally() -> None:
    """A structural guard, because the drift this prevents is invisible.

    A bare `stats.<counter> += 1` anywhere in the runner would move the total
    and not the split, and nothing would look wrong until somebody read a
    report and believed it.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "src" / "career_agent" / "pipeline" / "collect.py"
    ).read_text(encoding="utf-8")

    split_counters = {
        "boards_attempted",
        "boards_succeeded",
        "boards_failed",
        "postings_observed",
        "postings_skipped_malformed",
        "jobs_new",
        "jobs_seen_again",
        "jobs_changed",
        "jobs_closed",
        "descriptions_non_empty",
        "descriptions_empty",
    }
    bare = set(re.findall(r"stats\.(\w+)\s*\+=", source))
    assert not (bare & split_counters), f"these bypass tally(): {sorted(bare & split_counters)}"
