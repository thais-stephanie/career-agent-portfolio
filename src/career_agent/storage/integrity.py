"""Invariants checked against a real database, rather than assumed.

Every check here asks a question the schema cannot answer on its own. SQLite
enforces types and, when asked, foreign keys; it does not know that a posting
cannot be applied to before it was seen, that a match row belongs to the text
it was computed from, or that `NO` is Norway and not the boolean false.

**Nothing here writes.** A finding is reported with enough detail to act on and
never repaired in passing, because two of the categories below are raw
observations -- `job_raw`, `job_provider_payload` -- and an observation that
this system edits is no longer an observation. Derived rows may be rebuilt, and
rebuilding them is a rescore, which is a separate command the owner runs.

Severity is a claim about consequence, not about tidiness:

``BROKEN``   something references a row that is not there, or a value the
             vocabulary does not contain. A reader will see wrong output.
``STALE``    correct when it was written, computed by an older build. A
             rescore fixes it and until then the interface should say so.
``NOTE``     worth knowing, wrong only in context.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field

#: Statuses an application may hold. Read from the enum rather than repeated,
#: so a vocabulary change cannot leave this file quietly checking last year's.
from career_agent.domain.application import ApplicationStatus
from career_agent.domain.matching import MATCH_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class Finding:
    """One failed invariant, with enough detail to act on it."""

    check: str
    severity: str
    count: int
    detail: str
    #: A few offending identifiers. Bounded on purpose: a finding with 12,000
    #: ids is a wall, and the first handful is what anybody actually opens.
    examples: tuple[str, ...] = field(default=())

    @property
    def is_broken(self) -> bool:
        return self.severity == "BROKEN"


BROKEN = "BROKEN"
STALE = "STALE"
NOTE = "NOTE"

#: How many offending rows a finding carries.
_EXAMPLES = 5


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return list(conn.execute(sql, params))


def _finding(
    check: str, severity: str, rows: list[sqlite3.Row], detail: str, key: str = "id"
) -> Finding | None:
    if not rows:
        return None
    return Finding(
        check=check,
        severity=severity,
        count=len(rows),
        detail=detail,
        examples=tuple(str(row[key]) for row in rows[:_EXAMPLES]),
    )


def _current_versions(conn: sqlite3.Connection) -> list[tuple[str, int]]:
    """The newest configuration version of each configuration in the database.

    Every check below that asks "is this row correct" has to ask it of the
    CURRENT rows only. `job_match` is versioned on purpose: v1 and v2 sit side
    by side so a past evaluation stays reproducible, the read path scopes every
    query to one version, and a rescore never touches the others.

    Not knowing that produced a false alarm the first time this module ran. It
    reported 21 broken country codes and 23,765 stale scores; all 21 and most
    of the 23,765 were `config_version = 1`, which no screen has read since the
    Post-V3 reconciliation. History is not a defect.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT config_id, MAX(config_version) AS v FROM job_match GROUP BY config_id"
    ).fetchall()
    return [(str(row["config_id"]), int(row["v"])) for row in rows]


def _current_clause(conn: sqlite3.Connection) -> tuple[str, tuple]:
    """A WHERE fragment restricting to the current version of each config."""
    versions = _current_versions(conn)
    if not versions:
        return "0", ()
    ors = " OR ".join("(config_id = ? AND config_version = ?)" for _ in versions)
    params = tuple(value for pair in versions for value in pair)
    return f"({ors})", params


def check_orphan_matches(conn: sqlite3.Connection) -> Finding | None:
    """A score whose posting is gone, or whose text is gone.

    `job_match.content_hash` points at the exact text the score was computed
    from. If that row is missing the score cannot be explained -- the drawer
    has no description to quote evidence out of -- so this is BROKEN rather
    than merely untidy.
    """
    rows = _rows(
        conn,
        "SELECT m.id FROM job_match m"
        " LEFT JOIN job j ON j.id = m.job_id"
        " LEFT JOIN job_raw r ON r.content_hash = m.content_hash"
        " WHERE j.id IS NULL OR r.content_hash IS NULL",
    )
    return _finding(
        "orphan_match", BROKEN, rows, "a score whose posting or whose original text is missing"
    )


def check_jobs_without_text(conn: sqlite3.Connection) -> Finding | None:
    """A posting with no stored description.

    NOTE, not BROKEN. A board that published a title and no body is a real
    thing and the product handles it; this counts them so a sudden jump is
    visible.
    """
    rows = _rows(
        conn,
        "SELECT j.id FROM job j LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE r.content_hash IS NULL",
    )
    return _finding("job_without_text", NOTE, rows, "a posting with no stored description")


def check_orphan_applications(conn: sqlite3.Connection) -> Finding | None:
    rows = _rows(
        conn,
        "SELECT a.id FROM job_application a LEFT JOIN job j ON j.id = a.job_id WHERE j.id IS NULL",
    )
    return _finding("orphan_application", BROKEN, rows, "tracking state for a posting that is gone")


def check_unknown_application_status(conn: sqlite3.Connection) -> Finding | None:
    """A status outside the vocabulary.

    Worth checking even with a CHECK constraint in place, because a constraint
    added by a later migration does not validate the rows already there.
    """
    known = {status.value for status in ApplicationStatus}
    rows = [
        row
        for row in _rows(conn, "SELECT id, status FROM job_application")
        if str(row["status"]) not in known
    ]
    return _finding(
        "unknown_application_status",
        BROKEN,
        rows,
        "a tracking status the vocabulary has no name for",
    )


def check_applied_before_seen(conn: sqlite3.Connection) -> Finding | None:
    """An application dated before the posting was ever collected.

    Not impossible in life -- somebody may apply on a company site and record
    it here afterwards -- so this is a NOTE. It is checked because the reverse
    reading, treating `applied_at` as evidence of when a posting appeared, is
    a mistake this codebase has made once already.
    """
    rows = _rows(
        conn,
        "SELECT a.id FROM job_application a JOIN job j ON j.id = a.job_id"
        " WHERE a.applied_at IS NOT NULL AND j.first_seen_at IS NOT NULL"
        " AND a.applied_at < j.first_seen_at",
    )
    return _finding(
        "applied_before_seen", NOTE, rows, "an application dated before the posting was collected"
    )


def check_applied_without_date(conn: sqlite3.Connection) -> Finding | None:
    """A posting past APPLIED with no date recorded.

    ADR-0012: the date records that an application was SENT, and no later move
    makes that untrue. A row in INTERVIEW with no `applied_at` is the defect
    that ADR exists to prevent, so it is BROKEN.
    """
    past = ("APPLIED", "INTERVIEW", "OFFER", "HIRED")
    marks = ",".join("?" for _ in past)
    rows = _rows(
        conn,
        f"SELECT id FROM job_application WHERE status IN ({marks}) AND applied_at IS NULL",
        past,
    )
    return _finding(
        "applied_without_date", BROKEN, rows, "past the applied stage with no date of application"
    )


def check_event_without_application(conn: sqlite3.Connection) -> Finding | None:
    rows = _rows(
        conn,
        "SELECT e.id FROM job_application_event e LEFT JOIN job j ON j.id = e.job_id"
        " WHERE j.id IS NULL",
    )
    return _finding("orphan_event", BROKEN, rows, "a lifecycle event for a posting that is gone")


def check_boolean_country_codes(conn: sqlite3.Connection) -> Finding | None:
    """`norway: NO` written unquoted, read by YAML 1.1 as the boolean false.

    This shipped once and put the string `FALSE` in 21 rows where a country
    code belongs. The generator and the guard are fixed; this is how the
    DATABASE says whether it still carries any.
    """
    clause, params = _current_clause(conn)
    rows = _rows(
        conn,
        "SELECT id FROM job_match WHERE (countries LIKE '%FALSE%' OR countries LIKE '%TRUE%')"
        f" AND {clause}",
        params,
    )
    return _finding(
        "boolean_country_code", BROKEN, rows, "a country column holding a YAML boolean, not a code"
    )


def check_stale_analysis(conn: sqlite3.Connection) -> Finding | None:
    """Scores computed by an older analysis build.

    STALE is exactly right: each row was correct when written. A rescore
    replaces them, and until it runs the interface should say so rather than
    letting four filters quietly answer zero.
    """
    clause, params = _current_clause(conn)
    rows = _rows(
        conn,
        f"SELECT id FROM job_match WHERE schema_version < ? AND {clause}",
        (MATCH_SCHEMA_VERSION, *params),
    )
    return _finding(
        "stale_analysis",
        STALE,
        rows,
        f"scored under an analysis schema older than {MATCH_SCHEMA_VERSION};"
        " a rescore replaces them",
    )


def check_duplicate_current_match(conn: sqlite3.Connection) -> Finding | None:
    """More than one score for one posting under one configuration version.

    A UNIQUE constraint says this cannot happen. It is checked anyway, because
    a constraint introduced by a migration does not retroactively validate
    what was already there, and because the read path assumes exactly one row.
    """
    rows = _rows(
        conn,
        "SELECT job_id AS id, COUNT(*) AS n FROM job_match"
        " GROUP BY job_id, config_id, config_version HAVING n > 1",
    )
    return _finding(
        "duplicate_match", BROKEN, rows, "two scores for one posting under one configuration"
    )


def check_sighting_without_job(conn: sqlite3.Connection) -> Finding | None:
    """An aggregator sighting pointing at nothing.

    ADR-0013 keeps ONE authoritative origin per posting and files a second
    appearance as a sighting. A sighting whose posting is gone is a claim
    about a job this database cannot show.
    """
    rows = _rows(
        conn,
        "SELECT d.id FROM job_discovery_source d LEFT JOIN job j ON j.id = d.job_id"
        " WHERE j.id IS NULL",
    )
    return _finding("orphan_sighting", BROKEN, rows, "a sighting of a posting that is gone")


def check_job_without_company(conn: sqlite3.Connection) -> Finding | None:
    rows = _rows(
        conn,
        "SELECT j.id FROM job j LEFT JOIN company c ON c.id = j.company_id WHERE c.id IS NULL",
    )
    return _finding("job_without_company", BROKEN, rows, "a posting whose company row is gone")


def check_closed_without_date(conn: sqlite3.Connection) -> Finding | None:
    """A posting marked closed with no date, or dated closed without the mark.

    Freshness and the daily digest both read these two fields, and they
    disagree silently when only one is set.
    """
    rows = _rows(
        conn,
        "SELECT id FROM job WHERE (collection_status = 'CLOSED' AND closed_at IS NULL)"
        " OR (closed_at IS NOT NULL AND collection_status != 'CLOSED')",
    )
    return _finding(
        "closed_state_disagrees", BROKEN, rows, "closed_at and collection_status disagree"
    )


def check_foreign_keys(conn: sqlite3.Connection) -> Finding | None:
    """SQLite's own answer, which it only gives when asked.

    Foreign keys are off by default per connection, so a database can accumulate
    violations for years and report none. `PRAGMA foreign_key_check` looks
    regardless of whether enforcement was ever on.
    """
    rows = _rows(conn, "PRAGMA foreign_key_check")
    if not rows:
        return None
    tables = sorted({str(row[0]) for row in rows})
    return Finding(
        check="foreign_key",
        severity=BROKEN,
        count=len(rows),
        detail=f"SQLite reports violations in: {', '.join(tables)}",
        examples=tuple(tables[:_EXAMPLES]),
    )


def check_superseded_versions(conn: sqlite3.Connection) -> Finding | None:
    """Scores kept from an earlier configuration version.

    A NOTE, and never anything stronger. These rows are why `job_match` is
    versioned: a past evaluation stays reproducible, and the read path has
    scoped every query to one version since the beginning. They are reported so
    that a person reading a size on disk knows what it is, and so that nobody
    mistakes them for the current answer -- which is exactly the mistake this
    module made about them the first time it ran.
    """
    versions = _current_versions(conn)
    if not versions:
        return None
    ors = " OR ".join("(config_id = ? AND config_version = ?)" for _ in versions)
    params = tuple(value for pair in versions for value in pair)
    rows = _rows(conn, f"SELECT id FROM job_match WHERE NOT ({ors})", params)
    return _finding(
        "superseded_version",
        NOTE,
        rows,
        "scores from an earlier configuration version, kept on purpose and read by nothing",
    )


def check_career_organization(conn: sqlite3.Connection) -> Finding | None:
    """Candidate-owned links must not cross identities or point at retired containers."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'career_experience'").fetchone():
        return None
    rows = _rows(
        conn,
        "SELECT e.id FROM career_experience e JOIN career_company c ON c.id = e.company_id"
        " WHERE e.archived = 0 AND (e.candidate_id != c.candidate_id OR c.archived != 0)"
        " UNION SELECT l.claim_key AS id FROM career_evidence_link l"
        " JOIN career_experience e ON e.id = l.experience_id"
        " WHERE l.candidate_id != e.candidate_id OR e.archived != 0"
        " UNION SELECT c.id FROM career_company c"
        " JOIN career_company target ON target.id = c.merged_into"
        " WHERE c.archived = 0 AND (c.candidate_id != target.candidate_id OR target.archived != 0)",
    )
    if rows:
        return _finding(
            "career_organization",
            BROKEN,
            rows,
            "career associations cross candidate identities or inactive containers",
        )
    parents = {
        row["id"]: row["merged_into"]
        for row in _rows(conn, "SELECT id, merged_into FROM career_company WHERE archived = 0")
    }
    cycles = []
    for key in parents:
        seen = set()
        current = key
        while current in parents:
            if current in seen:
                cycles.append(key)
                break
            seen.add(current)
            current = parents[current]
    if cycles:
        return Finding(
            check="career_alias_cycle",
            severity=BROKEN,
            count=len(cycles),
            detail="company alias associations contain a cycle",
            examples=tuple(cycles[:_EXAMPLES]),
        )
    return None


def check_catalogue_references(conn: sqlite3.Connection) -> Finding | None:
    """A private row naming a posting the shared catalogue does not hold.

    Only for a profile split from the catalogue (storage/catalogue.py). There,
    SQLite cannot enforce these references (a foreign key cannot cross into
    another file), so this is where they are checked instead. Scores and
    tracking rows have their own checks above; this covers the rest.
    """
    from career_agent.storage.catalogue import role
    from career_agent.storage.catalogue_split import JOB_REFERENCES

    if role(conn) != "profile":
        return None
    present = {
        str(r[0]) for r in conn.execute("SELECT name FROM main.sqlite_master WHERE type='table'")
    }
    rows: list[sqlite3.Row] = []
    for table, column in JOB_REFERENCES.items():
        if table in {"job_match", "job_application"} or table not in present:
            continue
        rows += _rows(
            conn,
            f"SELECT '{table}' AS source, p.{column} AS job_id FROM main.{table} p"
            f" WHERE NOT EXISTS (SELECT 1 FROM job j WHERE j.id = p.{column})",
        )
    return _finding(
        "catalogue_reference",
        BROKEN,
        rows,
        "a private row naming a posting the catalogue lacks",
        key="job_id",
    )


#: Every check, in reporting order. A list rather than a decorator registry:
#: the order is meaningful to a reader and a registry would hide it.
CHECKS = (
    check_foreign_keys,
    check_career_organization,
    check_orphan_matches,
    check_orphan_applications,
    check_catalogue_references,
    check_sighting_without_job,
    check_event_without_application,
    check_job_without_company,
    check_duplicate_current_match,
    check_unknown_application_status,
    check_applied_without_date,
    check_closed_without_date,
    check_boolean_country_codes,
    check_stale_analysis,
    check_jobs_without_text,
    check_applied_before_seen,
    check_superseded_versions,
)


def audit(conn: sqlite3.Connection) -> Iterator[Finding]:
    """Run every check. Yields only what failed."""
    for check in CHECKS:
        finding = check(conn)
        if finding is not None:
            yield finding
