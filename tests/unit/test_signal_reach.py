"""A phrase editor that cannot say what a phrase reaches is operated blind.

`/api/preferences` lets the owner edit the phrase groups that decide what
counts as the work she wants. It showed her the phrases and nothing else, so
every edit was a guess: add a phrase, remove a phrase, and find out what
happened by rescoring nineteen thousand postings and reading the list.

Measured on her corpus 2026-09-08, the blindness was hiding real facts:

    documentation_practice   6,506 postings   33.4%
    scripting                5,986            30.8%
    ...
    crm_architecture            81             0.42%
    selling_hubspot              0             0%

None of those is a verdict. "No posting says this" and "this work does not
exist" are different statements and only she can tell them apart -- but she
cannot tell them apart without the number.
"""

from __future__ import annotations

import sqlite3

import pytest

from career_agent.storage.signals import (
    MEASURED_CATEGORIES,
    reach_summary,
    signal_reach,
    signal_reach_details,
)

CONFIG = "personal-alpha"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.execute(
        "CREATE TABLE job_match (job_id TEXT, config_id TEXT, config_version INTEGER,"
        " membership TEXT, content_hash TEXT DEFAULT 'body')"
    )
    connection.execute("CREATE TABLE job (id TEXT PRIMARY KEY, content_hash TEXT, closed_at TEXT)")
    return connection


def scored(conn: sqlite3.Connection, *memberships: str, version: int = 6) -> None:
    for index, membership in enumerate(memberships):
        conn.execute(
            "INSERT OR IGNORE INTO job VALUES (?, 'body', NULL)",
            (f"job{index}",),
        )
        conn.execute(
            "INSERT INTO job_match (job_id,config_id,config_version,membership) "
            "VALUES (?, ?, ?, ?)",
            (f"job{index}", CONFIG, version, membership),
        )


def fired(*signals: str) -> str:
    """`membership` as the scorer writes it: pipe-delimited, with awards too."""
    return "|" + "|".join(f"fired:{s}" for s in signals) + "||award:description_present|"


# =========================================================================
# 1. COUNTING
# =========================================================================


def test_it_counts_the_postings_each_signal_matched(conn) -> None:
    scored(
        conn,
        fired("api_integration", "scripting"),
        fired("scripting"),
        fired("api_integration", "scripting", "ipaas"),
    )

    reach = signal_reach(conn, CONFIG, 6)

    assert reach == {"api_integration": 2, "scripting": 3, "ipaas": 1}


def test_an_award_is_not_a_signal(conn) -> None:
    """`membership` carries both. Only the `fired:` half is a phrase group."""
    scored(conn, fired("scripting"))

    assert "description_present" not in signal_reach(conn, CONFIG, 6)


def test_a_signal_name_is_never_read_as_a_prefix_of_another(conn) -> None:
    """The pipe delimiter is what makes that true, and it is worth pinning:
    `data_sync` and `data_sync_v2` would otherwise be one number."""
    scored(conn, fired("data_sync_v2"))

    reach = signal_reach(conn, CONFIG, 6)

    assert reach == {"data_sync_v2": 1}
    assert "data_sync" not in reach


def test_only_the_asked_for_population_is_counted(conn) -> None:
    """The count describes the list she is looking at, not every row stored.

    Counting across revisions would mix an answer to her current preferences
    with an answer to preferences she has changed.
    """
    scored(conn, fired("scripting"), version=6)
    scored(conn, fired("scripting"), fired("scripting"), version=4)

    assert signal_reach(conn, CONFIG, 6) == {"scripting": 1}


def test_an_empty_population_counts_nothing_rather_than_failing(conn) -> None:
    assert signal_reach(conn, CONFIG, 99) == {}


def test_reach_excludes_closed_and_changed_content(conn) -> None:
    scored(conn, fired("scripting"), fired("scripting"), fired("scripting"))
    conn.execute("UPDATE job SET closed_at='closed' WHERE id='job1'")
    conn.execute("UPDATE job SET content_hash='changed' WHERE id='job2'")
    reading = signal_reach_details(conn, CONFIG, 6)
    assert reading.population == 1
    assert reading.lexical == {"scripting": 1}


def test_title_only_reach_is_not_positive_body_scoring(conn) -> None:
    scored(
        conn,
        fired("scripting") + "|body_scoring_measured:v1|",
        fired("scripting") + "|body_positive:scripting|body_scoring_measured:v1|",
    )
    reading = signal_reach_details(conn, CONFIG, 6)
    assert reading.lexical == {"scripting": 2}
    assert reading.positive_body == {"scripting": 1}


def test_old_or_partially_upgraded_scores_are_unmeasured_not_zero(conn) -> None:
    scored(conn, fired("scripting"), fired("scripting") + "|body_scoring_measured:v1|")
    assert signal_reach_details(conn, CONFIG, 6).positive_body is None


# =========================================================================
# 2. NOT MEASURED IS NOT ZERO
# =========================================================================


def test_a_signal_that_matched_nothing_reports_zero(conn) -> None:
    """**The finding, not a gap.** `selling_hubspot` matches nothing across
    19,469 postings, and that is exactly what she needs to see."""
    summary = reach_summary({"scripting": 10}, [("selling_hubspot", "desired")], population=100)

    assert summary[0]["postings"] == 0
    assert summary[0]["measured"] is True
    assert summary[0]["share"] == 0.0


def test_a_hard_exclusion_reports_none_rather_than_a_false_zero(conn) -> None:
    """**The bug the first version shipped.**

    A hard exclusion is a gate outcome decided by `match/gates.py`; it writes
    no token into `membership`. Reporting it as zero said "this phrase matches
    nothing" about five of her blockers -- including the one carrying
    `we do not sponsor`, which demonstrably does refuse postings.
    """
    summary = reach_summary({}, [("us_residence_required", "excluded")], population=19469)

    assert summary[0]["measured"] is False
    assert summary[0]["postings"] is None, "an unmeasured signal must not read as zero"
    assert summary[0]["share"] is None


def test_the_measured_categories_are_the_lexicon_ones() -> None:
    """Desired and negative are phrase groups the scorer records. Excluded is
    a gate, and a gate leaves no trace in this column."""
    assert {"desired", "negative"} == MEASURED_CATEGORIES


def test_a_share_needs_a_real_denominator(conn) -> None:
    """A percentage over an unscored corpus is a number with no meaning
    printed beside ones that have some."""
    summary = reach_summary({"scripting": 0}, [("scripting", "desired")], population=0)

    assert summary[0]["share"] is None


def test_every_declared_signal_appears_even_when_it_matched_nothing() -> None:
    """Built from the DECLARED list, because a dictionary of what matched can
    only ever describe what matched -- and zero is the interesting number."""
    summary = reach_summary(
        {"scripting": 5},
        [("scripting", "desired"), ("ipaas", "desired"), ("blocker", "excluded")],
        population=50,
    )

    assert [row["signal"] for row in summary] == ["scripting", "ipaas", "blocker"]
    assert [row["postings"] for row in summary] == [5, 0, None]
