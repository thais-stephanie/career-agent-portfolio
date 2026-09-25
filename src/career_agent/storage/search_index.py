"""The full-text index over title, company and description.

One module because there are exactly three questions to answer and they
belong together: build it, is it current, and what does a person's typed
phrase become in FTS5's query language.

The last one is the part that bites. FTS5's MATCH takes an expression, not a
string: bare punctuation is a syntax error, `AND`/`OR`/`NOT`/`NEAR` are
operators, and a stray quote raises `sqlite3.OperationalError` from inside the
query. A person typing `C++ / .NET` into a search box is not writing a query
language, and must never see a 500. :func:`to_match_query` is what stands
between the two.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Sequence
from datetime import UTC, datetime

#: Rebuilt in chunks so a corpus that outgrows memory still indexes. 2,000
#: rows of ~6 KB is roughly 12 MB per batch, which is comfortable.
_BATCH = 2000

#: Everything FTS5 treats as syntax. A person's search box contains prose, so
#: every one of these is stripped rather than escaped -- escaping them would
#: preserve an operator the person did not mean to type.
_FTS_SYNTAX = re.compile(r"[^\w\s]", flags=re.UNICODE)


#: A run of word characters, after folding. Everything else separates words.
_WORD = re.compile(r"\w+", flags=re.UNICODE)

#: The SQLite name :func:`fold_text` is registered under. A connection that
#: runs the free-text clause must have it (see `ScoredJobQuery.__init__`).
FOLD_FUNCTION = "ca_fold"


def fold_text(value: object) -> str:
    """Casefolded, without diacritics, as space-separated words.

    `São Paulo, BR` becomes `sao paulo br`. SQLite's own LOWER folds ASCII
    only, so "sao paulo" never found "São Paulo"; this is the deterministic
    fold both sides of a place comparison go through. The same fold the FTS
    index applies (`unicode61 remove_diacritics 2`), so a token folded here
    also matches the index.
    """
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value).casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(_WORD.findall(plain))


def register_fold(conn: sqlite3.Connection) -> None:
    """Make :func:`fold_text` callable from SQL on this connection."""
    conn.create_function(FOLD_FUNCTION, 1, fold_text, deterministic=True)


def search_tokens(text: str) -> list[str]:
    """The words of a free-text search, folded. Empty when nothing is searchable."""
    return fold_text(text).split()


def to_match_query(text: str) -> str | None:
    """Turn what a person typed into a safe FTS5 MATCH expression.

    Returns ``None`` when nothing searchable survives, which the caller must
    treat as "this filter selects nothing" rather than as "no filter" -- a
    search for `***` finding every posting would be the same class of defect
    as an unknown parameter returning the whole corpus.

    Every token is quoted, so FTS5 reads it as a literal rather than as an
    operator: searching for the word ``NOT`` finds postings containing "not",
    it does not negate the next term. Tokens are ANDed, because a person
    typing two words means both -- and a trailing ``*`` is added to the last
    token so ``integrat`` matches ``integration`` while typing.
    """
    cleaned = _FTS_SYNTAX.sub(" ", text or "")
    tokens = [token for token in cleaned.split() if token]
    if not tokens:
        return None
    quoted = [f'"{token}"' for token in tokens[:-1]]
    # The final token gets a prefix match; the quotes still keep it literal.
    quoted.append(f'"{tokens[-1]}"*')
    return " AND ".join(quoted)


def current_state(conn: sqlite3.Connection) -> tuple[int, str | None]:
    """What the corpus looks like right now: how many jobs, newest sighting.

    The pair is the staleness key. Count alone misses a posting that was
    re-normalised in place; `max(last_seen_at)` alone misses a deletion.
    """
    row = conn.execute("SELECT COUNT(*) AS n, MAX(last_seen_at) AS m FROM job").fetchone()
    return int(row["n"]), row["m"]


def is_current(conn: sqlite3.Connection) -> bool:
    """Does the index describe the corpus as it stands?

    False when the index has never been built, when the table is missing
    entirely, or when the corpus has moved since. Never raises: a damaged or
    absent index must degrade search to the slow-but-correct path, not break
    the page.
    """
    try:
        row = conn.execute(
            "SELECT job_count, max_seen FROM search_index_state WHERE id = 'singleton'"
        ).fetchone()
    except sqlite3.Error:
        return False
    if row is None:
        return False
    count, seen = current_state(conn)
    return int(row["job_count"]) == count and row["max_seen"] == seen


def rebuild(conn: sqlite3.Connection) -> int:
    """Rebuild the index from the corpus. Returns how many rows it holds.

    A full rebuild is the path for a corpus whose index was never built, or
    whose `search_index_map` cannot be trusted; :func:`refresh` is the path
    for a known set of changed postings. Both leave the index describing the
    same corpus; the difference is what they cost. Measured 2026-09-12 on a
    copy of the 249,632-posting corpus, a rebuild rewrites 1.5 GB of text and
    is the single largest fixed cost a rescore used to pay -- for a change
    to one posting as much as for a change to all of them.

    Rows are inserted with EXPLICIT rowids, 1..N, and the map is written
    beside them, so that :func:`refresh` can later replace one posting's
    row by rowid instead of scanning the content table for its id.

    The caller owns the transaction.
    """
    conn.execute("DELETE FROM job_search")
    conn.execute("DELETE FROM search_index_map")
    inserted = 0
    cursor = conn.execute(_SOURCE_SQL)
    while True:
        rows = cursor.fetchmany(_BATCH)
        if not rows:
            break
        numbered = [
            (inserted + i + 1, r["job_id"], r["title"] or "", r["company"] or "", r["description"])
            for i, r in enumerate(rows)
        ]
        conn.executemany(
            "INSERT INTO job_search (rowid, job_id, title, company, description)"
            " VALUES (?, ?, ?, ?, ?)",
            numbered,
        )
        conn.executemany(
            "INSERT INTO search_index_map (job_id, fts_rowid) VALUES (?, ?)",
            [(job_id, rowid) for rowid, job_id, *_ in numbered],
        )
        inserted += len(rows)
    _record_state(conn)
    return inserted


def can_refresh(conn: sqlite3.Connection) -> bool:
    """May the index be brought up to date for a KNOWN set of postings?

    Only when it was built by a version of :func:`rebuild` that wrote the
    rowid map. An index built before migration 0034 has rows and no map; a
    database whose index was never built has neither. Both answer False and
    get the full rebuild, which is the one path that is always correct.

    The map is NOT required to name every posting. The first version asked
    for `COUNT(map) == state.job_count`, and on 2026-09-12 that failed the
    moment two collectors ran at once: one pass recorded the corpus count
    while the other was still inserting postings whose marks it had not
    read, so the next pass found the map short by those rows and REBUILT
    THE WHOLE INDEX -- fifteen minutes under one write lock, and three
    collectors in other processes lost the boards they had just fetched to
    "database is locked". A posting without a map row is simply one the
    refresh has not reached yet; it is still in the ledger, and `refresh`
    inserts it when its mark is read.
    """
    try:
        state = conn.execute("SELECT 1 FROM search_index_state WHERE id = 'singleton'").fetchone()
        mapped = conn.execute("SELECT 1 FROM search_index_map LIMIT 1").fetchone()
    except sqlite3.Error:
        return False
    return state is not None and mapped is not None


def refresh(conn: sqlite3.Connection, job_ids: Sequence[str]) -> int:
    """Replace the index rows of exactly these postings. Returns how many.

    Two point operations per posting: delete the old row by its mapped
    rowid, insert the current text under a fresh one. A posting that no
    longer exists loses its row; a posting that was never indexed gains
    one. The caller has established, through `job_dirty`, that these are
    the postings whose title, text or employer name may have moved since
    the index last described the corpus -- so after this the state row is
    brought to the corpus as it stands, and `is_current` says so honestly.

    The caller owns the transaction, and must have checked :func:`can_refresh`.
    """
    ids = list(dict.fromkeys(job_ids))
    if not ids:
        _record_state(conn)
        return 0
    row = conn.execute("SELECT COALESCE(MAX(fts_rowid), 0) FROM search_index_map").fetchone()
    next_rowid = int(row[0]) + 1
    refreshed = 0
    for start in range(0, len(ids), _CHUNK):
        chunk = ids[start : start + _CHUNK]
        marks = ",".join("?" for _ in chunk)
        mapped = conn.execute(
            f"SELECT job_id, fts_rowid FROM search_index_map WHERE job_id IN ({marks})", chunk
        ).fetchall()
        if mapped:
            conn.executemany(
                "DELETE FROM job_search WHERE rowid = ?", [(int(m["fts_rowid"]),) for m in mapped]
            )
            conn.executemany(
                "DELETE FROM search_index_map WHERE job_id = ?", [(m["job_id"],) for m in mapped]
            )
        rows = conn.execute(_SOURCE_SQL + f" WHERE j.id IN ({marks})", chunk).fetchall()
        numbered = [
            (next_rowid + i, r["job_id"], r["title"] or "", r["company"] or "", r["description"])
            for i, r in enumerate(rows)
        ]
        next_rowid += len(rows)
        conn.executemany(
            "INSERT INTO job_search (rowid, job_id, title, company, description)"
            " VALUES (?, ?, ?, ?, ?)",
            numbered,
        )
        conn.executemany(
            "INSERT INTO search_index_map (job_id, fts_rowid) VALUES (?, ?)",
            [(job_id, rowid) for rowid, job_id, *_ in numbered],
        )
        refreshed += len(rows)
    _record_state(conn)
    return refreshed


#: What one index row is made of. Shared by the rebuild and the refresh so
#: the two can never disagree about which text a posting is searched by.
_SOURCE_SQL = (
    "SELECT j.id AS job_id, j.title AS title, c.name AS company,"
    " COALESCE(jr.description_text, '') AS description"
    " FROM job j"
    " JOIN company c ON c.id = j.company_id"
    " LEFT JOIN job_raw jr ON jr.content_hash = j.content_hash"
)

#: Ids per `IN (...)`, well under the SQLite variable ceiling.
_CHUNK = 500


def _record_state(conn: sqlite3.Connection) -> None:
    """Stamp the index with the corpus as it stands.

    After a `refresh`, a posting inserted by ANOTHER process since the pass
    read its ledger is counted here and not yet indexed; its mark is in
    `job_dirty` and the next pass indexes it. Between the two the index is
    short by those rows and `is_current` says current -- a smaller
    dishonesty than the alternative, which is a corpus-wide LIKE fallback
    for as long as any collection runs.
    """
    count, seen = current_state(conn)
    conn.execute(
        "INSERT INTO search_index_state (id, job_count, max_seen, built_at)"
        " VALUES ('singleton', ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        " job_count = excluded.job_count,"
        " max_seen = excluded.max_seen,"
        " built_at = excluded.built_at",
        (count, seen, datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")),
    )
