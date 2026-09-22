"""The full-text index, and the thing between a search box and FTS5 syntax.

`to_match_query` is the load-bearing part. FTS5's MATCH takes an expression,
not a string: `AND`/`OR`/`NOT`/`NEAR` are operators, an unbalanced quote is a
syntax error, and bare punctuation raises `sqlite3.OperationalError` from
inside the query. A person typing `C++` into a search box is not writing a
query language and must never see a 500.
"""

from __future__ import annotations

import pathlib
import sqlite3

import pytest

from career_agent.storage.db import split_statements
from career_agent.storage.search_index import is_current, rebuild, to_match_query

MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / "src"
    / "career_agent"
    / "storage"
    / "migrations"
    / "0015_search_index.sql"
)
#: The rowid map `refresh` needs, declared beside the dirty ledger in 0034.
#: Only its CREATE TABLE is read here: the rest of that migration is triggers
#: over columns this minimal corpus does not declare.
MAP_MIGRATION = MIGRATION.with_name("0034_job_dirty_ledger.sql")


# =========================================================================
# Turning prose into a query language
# =========================================================================


@pytest.mark.parametrize(
    "typed",
    [
        "C++",
        ".NET",
        "C++ / .NET",
        "node.js",
        'a "quoted" phrase',
        "AND",
        "OR",
        "NOT",
        "NEAR",
        "*",
        "()",
        "-",
        "engineer OR NOT (",
        "salário R$ 15.000",
        "n8n / Zapier -- iPaaS",
    ],
)
def test_anything_a_person_can_type_produces_a_query_that_runs(typed: str) -> None:
    """The whole point. Every one of these is a syntax error raw."""
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE VIRTUAL TABLE t USING fts5(body)")
    conn.execute("INSERT INTO t (body) VALUES ('integration engineer hubspot n8n')")

    match = to_match_query(typed)
    if match is None:
        return  # nothing searchable survived; the caller matches nothing
    # Must not raise. That is the assertion.
    conn.execute("SELECT COUNT(*) FROM t WHERE t MATCH ?", (match,)).fetchone()


def test_only_punctuation_selects_nothing_rather_than_everything() -> None:
    """A search for `***` must not quietly become "no filter".

    That is the same defect as an unknown query parameter returning the whole
    corpus, which this repository shipped once and now refuses at the API.
    """
    assert to_match_query("***") is None
    assert to_match_query("   ") is None
    assert to_match_query("") is None


def test_operator_words_are_searched_for_not_executed() -> None:
    """`NOT` is a word people put in job searches, not an instruction."""
    match = to_match_query("NOT")
    assert match is not None
    assert '"NOT"' in match


def test_the_last_token_is_a_prefix_so_typing_finds_things() -> None:
    match = to_match_query("integrat")
    assert match is not None
    assert match.endswith("*")


def test_several_words_mean_all_of_them() -> None:
    match = to_match_query("integration engineer")
    assert match is not None
    assert " AND " in match


# =========================================================================
# The index itself
# =========================================================================


def _corpus(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE company (id TEXT PRIMARY KEY, name TEXT);
        CREATE TABLE job (
            id TEXT PRIMARY KEY, company_id TEXT, title TEXT,
            content_hash TEXT, last_seen_at TEXT
        );
        CREATE TABLE job_raw (content_hash TEXT PRIMARY KEY, description_text TEXT);
        """
    )
    conn.execute("INSERT INTO company VALUES ('c1', 'Northwind Systems')")
    conn.execute("INSERT INTO job_raw VALUES ('h1', 'We run n8n and HubSpot pipelines.')")
    conn.execute("INSERT INTO job_raw VALUES ('h2', 'Bioengineering research team.')")
    conn.execute("INSERT INTO job VALUES ('j1', 'c1', 'Integration Engineer', 'h1', '2026-09-01')")
    conn.execute("INSERT INTO job VALUES ('j2', 'c1', 'Lab Lead', 'h2', '2026-09-02')")


@pytest.fixture()
def indexed() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _corpus(conn)
    # Read the real migration rather than restating its DDL. A fixture that
    # declares its own copy of a schema is two hand-maintained definitions
    # that must agree with nothing checking -- which is how the first version
    # of this file kept an `INTEGER PRIMARY KEY` the migration had already
    # dropped, and failed five tests for a reason that had nothing to do with
    # what they were testing.
    conn.executescript(MIGRATION.read_text(encoding="utf-8"))
    for statement in split_statements(MAP_MIGRATION.read_text(encoding="utf-8")):
        if statement.startswith("CREATE TABLE search_index_map"):
            conn.execute(statement)
    return conn


def test_rebuild_indexes_every_job_and_reports_how_many(
    indexed: sqlite3.Connection,
) -> None:
    assert rebuild(indexed) == 2
    assert is_current(indexed)


def test_the_index_searches_the_description_not_only_the_title(
    indexed: sqlite3.Connection,
) -> None:
    """The product's whole description-led discovery channel rests on this."""
    rebuild(indexed)
    match = to_match_query("hubspot")
    rows = indexed.execute(
        "SELECT job_id FROM job_search WHERE job_search MATCH ?", (match,)
    ).fetchall()
    assert [r["job_id"] for r in rows] == ["j1"]


def test_a_word_is_not_found_inside_a_longer_word(
    indexed: sqlite3.Connection,
) -> None:
    """FTS is word-aware where the old LIKE was substring, and that is a fix.

    On the real corpus, `search=engineer` under LIKE returned five postings
    whose only match was the word **bioengineering** -- a Technical Account
    Manager and a Head of EMEA Solution Consulting among them. FTS returns
    12,241 against LIKE's 12,246, and every one of the five it drops is that
    same false positive. Nothing is lost in the other direction: the FTS-only
    set is empty.
    """
    rebuild(indexed)
    match = to_match_query("engineer")
    rows = indexed.execute(
        "SELECT job_id FROM job_search WHERE job_search MATCH ?", (match,)
    ).fetchall()
    found = {r["job_id"] for r in rows}
    assert "j1" in found, "the real Integration Engineer must still be found"
    assert "j2" not in found, "bioengineering is not a match for engineer"


def test_a_corpus_that_moved_makes_the_index_stale(
    indexed: sqlite3.Connection,
) -> None:
    """Staleness is answerable, so search can degrade honestly instead of
    quietly returning yesterday's corpus."""
    rebuild(indexed)
    assert is_current(indexed)

    indexed.execute(
        "INSERT INTO job VALUES ('j3', 'c1', 'Automation Engineer', 'h1', '2026-09-03')"
    )
    assert not is_current(indexed), "a new posting must invalidate the index"

    rebuild(indexed)
    assert is_current(indexed)


def test_a_posting_renormalised_in_place_also_makes_it_stale(
    indexed: sqlite3.Connection,
) -> None:
    """Count alone is not enough, which is why `max_seen` is in the key."""
    rebuild(indexed)
    indexed.execute("UPDATE job SET last_seen_at = '2026-09-09' WHERE id = 'j1'")
    assert not is_current(indexed)


def test_a_missing_index_reads_as_stale_rather_than_raising() -> None:
    """A damaged or absent index must degrade search, never break the page."""
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    _corpus(bare)
    assert is_current(bare) is False
