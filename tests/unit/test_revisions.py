"""Editing a preference must not empty the product.

WHAT HAPPENED, ON THE OWNER'S OWN MACHINE
-----------------------------------------
2026-09-08, 13:16. She opened Settings and set her seniority preferences to
Mid-level and Senior, excluding Intern, Staff, Principal and Lead. The edit was
correct and it was entirely hers.

Her configuration went from revision 4 to revision 6. All 19,469 of her stored
scores answer revision 4. The product asked for revision 6, found nothing, and
showed an empty Jobs list.

Nothing was lost. The product was being precise -- a score is only true
relative to the preferences that produced it -- and it was precise in a way
that reads as "this has stopped working", with a seven-minute terminal command
as the remedy.

THE RULE THESE TESTS PIN
------------------------
Serve the current revision, unless an older one covers strictly more of the
corpus.

It is exact rather than a heuristic because a rescore only ever writes the
CURRENT configuration version. Every older version is immutable: it was
complete when it was current and nothing can be appending to it now. The only
population that can be half-built is the one being built.
"""

from __future__ import annotations

import sqlite3

import pytest

from career_agent.storage.revisions import Revision, resolve, revisions, scoreable_count

CONFIG = "personal-alpha"


@pytest.fixture
def conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE job (id TEXT PRIMARY KEY, content_hash TEXT, closed_at TEXT);
        CREATE TABLE job_raw (content_hash TEXT PRIMARY KEY);
        CREATE TABLE job_match (job_id TEXT, config_id TEXT, config_version INTEGER);
        CREATE TABLE compute_revision(id TEXT PRIMARY KEY, population_revision INTEGER);
        INSERT INTO compute_revision VALUES ('singleton',0);
        """
    )
    for table in ("job", "job_match"):
        for operation in ("INSERT", "UPDATE", "DELETE"):
            connection.execute(
                f"CREATE TRIGGER rev_{table}_{operation} AFTER {operation} ON {table} BEGIN "
                "UPDATE compute_revision SET population_revision=population_revision+1; END"
            )
    return connection


def corpus(conn: sqlite3.Connection, *, readable: int, textless: int = 0, closed: int = 0) -> None:
    conn.execute("INSERT INTO job_raw VALUES ('h')")
    for n in range(readable):
        conn.execute("INSERT INTO job VALUES (?, 'h', NULL)", (f"r{n}",))
    for n in range(textless):
        conn.execute("INSERT INTO job VALUES (?, NULL, NULL)", (f"t{n}",))
    for n in range(closed):
        conn.execute("INSERT INTO job VALUES (?, 'h', '2026-01-01')", (f"c{n}",))


def score(conn: sqlite3.Connection, version: int, count: int) -> None:
    for n in range(count):
        conn.execute("INSERT INTO job_match VALUES (?, ?, ?)", (f"r{n}", CONFIG, version))


# =========================================================================
# 1. WHAT COUNTS AS THE CORPUS
# =========================================================================


def test_a_posting_with_no_text_is_not_a_gap_in_the_population(conn) -> None:
    """The owner's corpus holds 19,485 open postings and 16 with no stored
    description. A scorer with nothing to read is not an unfinished job, and
    counting those 16 would make a complete population look permanently short.
    """
    corpus(conn, readable=19469, textless=16)

    assert scoreable_count(conn) == 19469


def test_a_closed_posting_is_not_part_of_the_corpus_either(conn) -> None:
    corpus(conn, readable=100, closed=25)

    assert scoreable_count(conn) == 100


def test_unchanged_population_does_not_recount_on_a_cache_hit(conn, monkeypatch):
    import career_agent.storage.revisions as module

    corpus(conn, readable=5)
    score(conn, 1, 5)
    first = resolve(conn, CONFIG, 1)
    original = module.scoreable_count
    calls = []

    def counted(connection):
        calls.append(True)
        return original(connection)

    monkeypatch.setattr(module, "scoreable_count", counted)
    assert resolve(conn, CONFIG, 1) == first
    assert not calls
    conn.execute("UPDATE job SET closed_at='closed' WHERE id='r0'")
    assert resolve(conn, CONFIG, 1).current.scoreable == 4
    assert calls == [True]


# =========================================================================
# 2. THE ORDINARY CASE
# =========================================================================


def test_a_complete_current_revision_is_what_gets_served(conn) -> None:
    corpus(conn, readable=100)
    score(conn, 6, 100)

    decision = resolve(conn, CONFIG, 6)

    assert decision.is_current
    assert not decision.is_stale
    assert decision.serving is not None
    assert decision.serving.config_version == 6
    assert decision.serving.complete


def test_an_empty_database_is_honestly_empty(conn) -> None:
    """A fresh install has nothing, and saying so is correct."""
    corpus(conn, readable=100)

    decision = resolve(conn, CONFIG, 1)

    assert decision.has_nothing
    assert decision.serving is None


# =========================================================================
# 3. THE FAILURE, AS IT ACTUALLY HAPPENED
# =========================================================================


def test_the_moment_after_a_preference_edit_serves_the_previous_answer(conn) -> None:
    """**Her exact situation.** 19,469 scored at v4, nothing at v6.

    The old behaviour showed zero jobs. This shows 19,469 and says which
    question they answer.
    """
    corpus(conn, readable=19469, textless=16)
    score(conn, 4, 19469)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None
    assert decision.serving.config_version == 4
    assert decision.serving.scored == 19469
    assert decision.is_stale, "the reader must be told this answers the old preferences"
    assert not decision.is_current
    assert not decision.has_nothing


def test_a_half_built_population_is_never_served(conn) -> None:
    """**The one that would be worse than an empty list.**

    A rescore in progress holds a thousand rows of nineteen thousand. Serving
    it would show a list that grows while she reads it, with every count
    wrong, and would look like the product had lost her corpus.
    """
    corpus(conn, readable=19469)
    score(conn, 4, 19469)
    score(conn, 6, 1000)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None
    assert decision.serving.config_version == 4
    assert decision.is_building, "it must still say that work is outstanding"
    assert decision.current.scored == 1000
    assert not decision.current.complete


def test_an_interrupted_rescore_looks_the_same_after_a_restart(conn) -> None:
    """Because it IS the same: work outstanding, previous answer intact.

    No runner state survives a restart, so the decision cannot depend on any.
    It is derived from the rows, which do survive.
    """
    corpus(conn, readable=19469)
    score(conn, 4, 19469)
    score(conn, 6, 12000)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None and decision.serving.config_version == 4
    assert decision.is_building


def test_the_new_revision_takes_over_the_moment_it_is_complete(conn) -> None:
    """Atomic activation, with no lock, no flag and no second table.

    One read serves the old population; the read after the last row lands
    serves the new one. There is no state in between that anybody can see.
    """
    corpus(conn, readable=19469)
    score(conn, 4, 19469)
    score(conn, 6, 19468)
    assert resolve(conn, CONFIG, 6).serving.config_version == 4

    conn.execute("INSERT INTO job_match VALUES ('r19468', ?, 6)", (CONFIG,))

    decision = resolve(conn, CONFIG, 6)
    assert decision.serving is not None and decision.serving.config_version == 6
    assert decision.is_current
    assert not decision.is_stale


# =========================================================================
# 4. THE CASES A NAIVE RULE GETS WRONG
# =========================================================================


def test_new_postings_do_not_send_the_reader_back_to_an_older_revision(conn) -> None:
    """**Why the tie goes to the current revision.**

    A collection adds thirty postings. Both revisions are now short by the
    same thirty. A rule that demanded completeness would fall back to the
    previous preferences because some jobs arrived, which is the original bug
    in a new costume.
    """
    corpus(conn, readable=19499)
    score(conn, 4, 19469)
    score(conn, 6, 19469)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None and decision.serving.config_version == 6
    assert decision.is_current
    assert not decision.serving.complete, "and it is honest about not being finished"


def test_the_newest_older_revision_is_not_automatically_the_best_one(conn) -> None:
    """Coverage decides, not recency.

    A v5 that was abandoned after two hundred rows must not beat a v4 that
    covers the corpus, however much newer it is.
    """
    corpus(conn, readable=19469)
    score(conn, 4, 19469)
    score(conn, 5, 200)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None and decision.serving.config_version == 4


def test_a_bigger_older_population_does_not_beat_a_newer_complete_one(conn) -> None:
    """**Found by running this against the real corpus.**

    Revision 2 holds 21,293 rows, scored when more postings were open.
    Revision 4 holds 19,469 and is her most recent complete answer. A rule
    that took the biggest population served revision 2 -- a question she
    stopped asking two revisions ago, answered over postings that have since
    closed.

    Completeness first, then recency. Size is only a tie-breaker, and only
    when nothing older is complete at all.
    """
    corpus(conn, readable=19469)
    score(conn, 2, 19469)
    conn.executemany(
        "INSERT INTO job_match VALUES (?, ?, 2)",
        [(f"gone{n}", CONFIG) for n in range(1824)],
    )
    score(conn, 4, 19469)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None
    assert decision.serving.config_version == 4, "the newest complete answer, not the biggest"


def test_the_first_rescore_ever_serves_what_it_has(conn) -> None:
    """There is no previous answer to fall back to, so a growing list beats an
    empty one -- and `is_building` is what tells the screen to say so."""
    corpus(conn, readable=19469)
    score(conn, 1, 1000)

    decision = resolve(conn, CONFIG, 1)

    assert decision.serving is not None and decision.serving.config_version == 1
    assert decision.is_building
    assert decision.is_current


def test_two_revisions_are_never_mixed_into_one_population(conn) -> None:
    """The reader sees one answer to one question, and is told which.

    Blending them would produce a list where some rows answer her new
    preferences and some answer her old ones, with nothing distinguishing
    them -- which is worse than either alone.
    """
    corpus(conn, readable=100)
    score(conn, 4, 100)
    score(conn, 6, 40)

    decision = resolve(conn, CONFIG, 6)

    assert decision.serving is not None
    assert decision.serving.scored == 100, "one revision's rows, not 140"


# =========================================================================
# 5. WHAT ROLLBACK CAN REACH
# =========================================================================


def test_every_older_population_is_reported_for_rollback(conn) -> None:
    """They are already on disk. The product only had to look."""
    corpus(conn, readable=100)
    for version, rows in ((1, 60), (2, 100), (4, 100)):
        score(conn, version, rows)

    decision = resolve(conn, CONFIG, 6)

    assert [r.config_version for r in decision.previous] == [4, 2, 1]
    assert decision.as_dict()["is_stale"] is True


def test_the_payload_a_screen_reads_carries_no_surprise(conn) -> None:
    corpus(conn, readable=100)
    score(conn, 4, 100)

    payload = resolve(conn, CONFIG, 6).as_dict()

    assert payload["current"]["config_version"] == 6
    assert payload["current"]["scored"] == 0
    assert payload["serving"]["config_version"] == 4
    assert payload["serving"]["complete"] is True
    assert payload["is_stale"] is True
    assert payload["has_nothing"] is False


def test_revisions_are_listed_newest_first(conn) -> None:
    corpus(conn, readable=10)
    for version in (1, 3, 7):
        score(conn, version, 10)

    assert [r.config_version for r in revisions(conn, CONFIG)] == [7, 3, 1]


def test_completeness_is_meaningless_without_a_corpus() -> None:
    """Zero scoreable postings is not a complete population; it is no corpus."""
    assert not Revision(CONFIG, 1, scored=0, scoreable=0).complete


def test_source_growth_does_not_reactivate_old_preferences_using_closed_scores(conn) -> None:
    corpus(conn, readable=120, closed=30)
    score(conn, 4, 100)
    score(conn, 6, 100)
    conn.executemany(
        "INSERT INTO job_match VALUES (?, ?, 4)",
        [(f"c{n}", CONFIG) for n in range(30)],
    )
    # Twenty newly collected jobs are unscored. Closed historical jobs must
    # not make the old preferences appear to cover more of the current corpus.
    decision = resolve(conn, CONFIG, 6)
    assert decision.serving is not None
    assert decision.serving.config_version == 6
    assert decision.serving.scored == 100
    assert decision.is_building
