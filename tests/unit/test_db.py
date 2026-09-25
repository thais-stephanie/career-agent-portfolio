"""Migration runner, pragmas, and schema shape.

Acceptance criteria 4, 5 and 17.
"""

import sqlite3
from pathlib import Path

import pytest

from career_agent.storage.db import (
    MIGRATIONS_DIR,
    MigrationError,
    applied_ledger,
    applied_versions,
    connect,
    discover_migrations,
    migrate,
    pending_migrations,
    schema_version,
    split_statements,
    table_names,
    transaction,
)

EXPECTED_TABLES = {
    "schema_migration",
    "candidate",
    "search_profile_version",
    "verified_claim",
    "company",
    "source_board",
    "job_raw",
    "job",
    "job_provider_payload",
    "pipeline_run",
    # M2: migration 0003. The document is stored whole in
    # fingerprint.data_json; the observation tables exist so M3/M4/M5 can ask
    # questions across jobs without parsing thousands of JSON blobs.
    "fingerprint",
    "evidence",
    "fp_responsibility",
    "fp_software",
    "fp_language",
    "fp_eligibility",
    "provider_observation",
    "llm_call",
    # M3 -- migration 0011. The deterministic score, and where the PERSON is
    # with a job. Separate from `fingerprint` because a score is only true
    # relative to one configuration version, while a fingerprint is
    # candidate-independent forever (ADR-0005, ADR-0011).
    "job_match",
    "job_application",
    "job_application_event",
    # M3 -- migration 0012. What a model running on THIS machine observed.
    # Deliberately not `fingerprint` and deliberately not `llm_call`: a local
    # answer costs nothing, so it has no budget row, and it must never be
    # servable in place of a hosted extraction (ADR-0010).
    "job_enrichment",
    # M3 -- migration 0015. Derived search data, not a source of truth: both
    # are rebuilt from `job`, `company` and `job_raw` and can be dropped and
    # regenerated without losing anything. `job_search` is the FTS5 index;
    # `search_index_state` records what it was built from, so "this index is
    # behind the corpus" is answerable rather than silently wrong.
    #
    # FTS5 creates shadow tables of its own (`job_search_data`, `_idx`,
    # `_content`, `_docsize`, `_config`). They are an implementation detail of
    # the virtual table, so they are filtered out rather than listed -- naming
    # them here would pin us to one SQLite version's internals.
    "job_search",
    "search_index_state",
    # M3 -- migration 0016. Which population this file belongs to, stated by
    # the file rather than guessed from its name. A demo database copied to
    # `personal.db` still says DEMO, which is the whole point: personal mode
    # refuses it instead of serving invented postings to someone who asked
    # for their own.
    "database_identity",
    # V3 -- migration 0018. Where ELSE a posting we already hold was seen.
    # `job` keeps one authoritative origin, because a posting belongs to the
    # system the employer published it in; an aggregator that republishes the
    # same posting gets a row here instead of a second `job`, so the corpus
    # does not double-count and the employer's own record is never overwritten
    # by a poorer copy.
    "job_discovery_source",
    # V1.3 -- migration 0019. The candidate's half of the workspace. A staged
    # CV read and its proposals, so a review survives a closed tab; what she
    # thinks of one requirement/evidence pair, stored apart from both because
    # neither may be edited to agree with her; and where she got to reading,
    # which is a fact about a reader and never about a posting.
    "cv_import",
    "cv_proposal",
    "requirement_review",
    "candidate_state",
    # V1.5 -- migration 0023. Proposed evidence that arrived as a FILE, which
    # somebody else's model may have written. Deliberately not `cv_import`:
    # that stages a document THIS PROGRAM read, with deterministic code whose
    # behaviour is covered by tests, and this stages a stranger's reading of
    # the candidate's own documents. Nothing in either table can produce a
    # `verified_claim`; only a review can.
    "intake_package",
    "intake_claim",
    # V1.5 -- migration 0024. One answer to a disagreement, rather than one
    # answer per sentence about it. A resolution names WHICH CLAIM'S dates
    # she accepted, never dates of its own, so every confirmed fact stays
    # traceable to a document that states it.
    "intake_conflict_resolution",
    # Career Evidence V2: candidate organization, never a rewrite of claims.
    "career_company",
    "career_experience",
    "career_evidence_link",
    "career_company_decision",
    "career_history_event",
    # 0039: the jobs a CV read describes -- company, role, dates, and the
    # source lines that stated them. Structure, never a claim.
    "cv_entry",
    # 0041: published semantic evaluations and the runs that made them.
    "semantic_evaluation",
    "semantic_run",
    # 0042: which retrieval lane and query found a posting. Provenance only.
    "job_retrieval_lane",
    # 0031: where a query-scoped walk got to, slice by slice, across runs.
    "source_slice_state",
    # 0033: where a board-discovery walk over an aggregator index got to,
    # employer by employer, and which boards it registered.
    "board_discovery_lead",
    # 0034: which postings may hold a different answer now, marked by trigger
    # so a rescore reads the change and not the corpus; and where each
    # posting's row sits in the full-text index, so it can be replaced.
    "job_dirty",
    "search_index_map",
    "compute_revision",
    "job_input_revision",
    "job_score_revision",
}


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "nested" / "career.db")
    yield conn
    conn.close()


# --- migration discovery ---------------------------------------------------


def test_migrations_are_discovered_in_order() -> None:
    migrations = discover_migrations()
    assert migrations, "no migration files found"
    assert [m.version for m in migrations] == sorted(m.version for m in migrations)
    assert migrations[0].version == 1
    assert migrations[0].name == "init"


def test_badly_named_migration_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "init.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="NNNN_name.sql"):
        discover_migrations(tmp_path)


def test_duplicate_versions_are_rejected(tmp_path: Path) -> None:
    (tmp_path / "0001_a.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "0001_b.sql").write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(MigrationError, match="duplicate migration versions"):
        discover_migrations(tmp_path)


# --- applying migrations ---------------------------------------------------


#: Every migration on disk, so these tests describe "the schema is current"
#: rather than "the schema is version 1". Pinning a literal here means every
#: future migration breaks four unrelated tests for no reason.
ALL_VERSIONS = [m.version for m in discover_migrations()]


def test_migrate_creates_every_expected_table(db: sqlite3.Connection) -> None:
    """Acceptance criterion 4."""
    applied = migrate(db)
    assert [m.version for m in applied] == ALL_VERSIONS
    assert set(table_names(db)) == EXPECTED_TABLES


def test_migrate_records_each_applied_version(db: sqlite3.Connection) -> None:
    migrate(db)
    assert schema_version(db) == max(ALL_VERSIONS)

    rows = db.execute(
        "SELECT version, name, applied_at FROM schema_migration ORDER BY version"
    ).fetchall()
    assert [row["version"] for row in rows] == ALL_VERSIONS
    assert rows[0]["name"] == "init"
    assert all(row["applied_at"].endswith("Z") for row in rows)


def test_migrate_is_idempotent(db: sqlite3.Connection) -> None:
    """Acceptance criterion 5: running it twice changes nothing."""
    first = migrate(db)
    second = migrate(db)
    assert len(first) == len(ALL_VERSIONS)
    assert second == []
    assert pending_migrations(db) == []
    assert applied_versions(db) == set(ALL_VERSIONS)
    assert set(table_names(db)) == EXPECTED_TABLES


def test_the_m1d_identity_columns_exist(db: sqlite3.Connection) -> None:
    """Migration 0002 is additive: new columns, no table rewritten."""
    migrate(db)
    company = {row["name"] for row in db.execute("PRAGMA table_info(company)")}
    board = {row["name"] for row in db.execute("PRAGMA table_info(source_board)")}

    assert {"canonical_domain", "discovery_source", "priority_reason"} <= company
    assert {"slug", "name", "notes"} <= company, "nothing was dropped"
    assert {"discovery_method", "verified_at", "suspicious_since", "suspicious_observed"} <= board
    assert {"company_id", "provider", "board_identifier", "last_collected_at"} <= board


def test_canonical_domain_is_unique_where_present(db: sqlite3.Connection) -> None:
    """The identity anchor. Nullable, because the M1A-M1C engineering seed
    companies have no verified domain and must not be made to invent one."""
    migrate(db)
    insert = (
        "INSERT INTO company (id, slug, name, canonical_domain, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, '2026-08-26T00:00:00Z', '2026-08-26T00:00:00Z')"
    )
    db.execute(insert, ("1", "acme", "Acme", "acme.com"))
    db.execute(insert, ("2", "no-domain-a", "A", None))
    db.execute(insert, ("3", "no-domain-b", "B", None))

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(insert, ("4", "acme-duplicate", "Acme Again", "acme.com"))


def test_empty_database_reports_version_zero(db: sqlite3.Connection) -> None:
    assert schema_version(db) == 0
    assert applied_versions(db) == set()


def test_failed_migration_leaves_no_partial_schema(tmp_path: Path) -> None:
    """The ledger row and the tables commit together or not at all."""
    directory = tmp_path / "migrations"
    directory.mkdir()
    (directory / "0001_ok.sql").write_text(
        "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL, applied_at TEXT NOT NULL);"
        "CREATE TABLE good (id TEXT PRIMARY KEY);",
        encoding="utf-8",
    )
    (directory / "0002_broken.sql").write_text(
        "CREATE TABLE fine (id TEXT PRIMARY KEY); THIS IS NOT SQL;", encoding="utf-8"
    )
    conn = connect(tmp_path / "career.db")
    try:
        with pytest.raises(MigrationError, match="0002_broken.sql"):
            migrate(conn, directory)
        assert schema_version(conn) == 1
        assert "fine" not in set(table_names(conn))
    finally:
        conn.close()


# --- lineage: a migration is its version AND its name (ADR-0028) ----------
#
# On 2026-09-17 production ran the Search Fit lane's 0036 and 0037; on
# 2026-09-18 `main` merged score replay as its own 0036, and the runner, which
# then compared numbers only, reported it as applied on production and never
# created `input_digest`. These tests hold the rule that closed that: a row of
# the same number and another name is refused unless the file at that number
# is an empty reserved slot that names the row.

#: The two ledger rows production carries at 36 and 37, by name. They are
#: reconciled by the reserved slots on `main`; nothing else on `main` may
#: name what those migrations created.
PRODUCTION_LINEAGE_ROWS = {36: "searchfit_v4_beta", 37: "searchfit_reader_lifecycle"}
RESERVED_SLOTS = {
    36: "reserved_slot_searchfit_v4_beta",
    37: "reserved_slot_searchfit_reader_lifecycle",
}
SEARCHFIT_OBJECTS = (
    "job_duty_profile",
    "candidate_work_relation",
    "candidate_work_relation_reading",
    "functional_alignment",
    "idx_job_match_eligible_population",
)

LEDGER_DDL = (
    "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY,"
    " name TEXT NOT NULL, applied_at TEXT NOT NULL);"
)


def _lineage(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    directory = tmp_path / name
    directory.mkdir()
    for filename, sql in files.items():
        (directory / filename).write_text(sql, encoding="utf-8")
    return directory


def test_the_reserved_slots_reconcile_exactly_the_production_rows() -> None:
    by_version = {m.version: m for m in discover_migrations()}
    for version, foreign in PRODUCTION_LINEAGE_ROWS.items():
        slot = by_version[version]
        assert slot.name == RESERVED_SLOTS[version]
        assert slot.reconciles == frozenset({foreign})
        assert split_statements(slot.sql) == [], "a reserved slot must create nothing"
    others = [m for m in discover_migrations() if m.version not in PRODUCTION_LINEAGE_ROWS]
    assert all(m.reconciles == frozenset() for m in others), "only the two slots reconcile"


def test_score_replay_is_0038_because_production_spent_36_and_37() -> None:
    by_version = {m.version: m for m in discover_migrations()}
    assert by_version[38].name == "score_replay_input_digest"
    assert "input_digest" in by_version[38].sql


def test_main_names_no_object_of_the_unmerged_lineage() -> None:
    """The slots reconcile the ledger rows; they do not import the schema.
    A fresh install has none of these objects and nothing on `main` may
    read or write one (they exist only in a database the other lane wrote)."""
    root = MIGRATIONS_DIR.parent.parent
    offenders: list[str] = []
    for path in root.rglob("*"):
        if path.suffix not in {".py", ".sql"} or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for name in SEARCHFIT_OBJECTS:
            if name in text and not path.name.startswith(("0036_", "0037_")):
                offenders.append(f"{path.relative_to(root)}: {name}")
    assert offenders == [], offenders


def test_a_reserved_slot_with_statements_is_refused(tmp_path: Path) -> None:
    directory = _lineage(
        tmp_path,
        "bad",
        {"0001_reserved.sql": "-- reconciles: other\nCREATE TABLE t (id TEXT PRIMARY KEY);"},
    )
    with pytest.raises(MigrationError, match="reserved slot must be empty"):
        discover_migrations(directory)


def test_a_reconciles_header_is_read_only_at_line_start(tmp_path: Path) -> None:
    directory = _lineage(
        tmp_path,
        "prose",
        {
            "0001_init.sql": LEDGER_DDL,
            # Mentioned in a sentence, not declared: the file is an ordinary
            # migration and the runner must not read a directive into prose.
            "0002_plain.sql": (
                "-- the previous lane wrote reconciles: nothing here\n"
                "CREATE TABLE t (id TEXT PRIMARY KEY);"
            ),
        },
    )
    assert all(m.reconciles == frozenset() for m in discover_migrations(directory))


def test_a_ledger_row_of_another_name_is_a_collision_and_nothing_runs(tmp_path: Path) -> None:
    """The defect, reproduced and refused. Lane A ran its 0002; lane B's 0002
    has another name and creates something else. Before 2026-09-18 lane B's
    file was reported as applied. Now it is a named refusal, the ledger is as
    it was, and lane B's 0003 is not applied either -- nothing runs past a
    collision."""
    lane_a = _lineage(
        tmp_path,
        "a",
        {"0001_init.sql": LEDGER_DDL, "0002_alpha.sql": "CREATE TABLE alpha (id TEXT);"},
    )
    lane_b = _lineage(
        tmp_path,
        "b",
        {
            "0001_init.sql": LEDGER_DDL,
            "0002_beta.sql": "CREATE TABLE beta (id TEXT PRIMARY KEY);",
            "0003_gamma.sql": "CREATE TABLE gamma (id TEXT PRIMARY KEY);",
        },
    )
    conn = connect(tmp_path / "career.db")
    try:
        migrate(conn, lane_a)
        before = conn.execute("SELECT * FROM schema_migration ORDER BY version").fetchall()
        with pytest.raises(MigrationError, match="lineage collision at migration 0002") as excinfo:
            migrate(conn, lane_b)
        assert "'alpha'" in str(excinfo.value) and "'beta'" in str(excinfo.value)
        after = conn.execute("SELECT * FROM schema_migration ORDER BY version").fetchall()
        assert [tuple(r) for r in after] == [tuple(r) for r in before]
        assert "beta" not in table_names(conn) and "gamma" not in table_names(conn)
    finally:
        conn.close()


def test_a_reserved_slot_accepts_its_foreign_row_and_the_rest_applies(tmp_path: Path) -> None:
    """Production's path, in miniature: the other lane's 0002 and 0003 ran
    (through the runner, so the rows are its own), this build reserves both
    numbers and adds 0004. Only 0004 runs; the two foreign rows stand, byte
    for byte; a second run is a no-op."""
    lane = _lineage(
        tmp_path,
        "lane",
        {
            "0001_init.sql": LEDGER_DDL,
            "0002_searchfit_v4_beta.sql": "CREATE TABLE lane_probe (id TEXT PRIMARY KEY);",
            "0003_searchfit_reader_lifecycle.sql": "CREATE TABLE lane_probe_2 (id TEXT);",
        },
    )
    main = _lineage(
        tmp_path,
        "main",
        {
            "0001_init.sql": LEDGER_DDL,
            "0002_reserved_slot_searchfit_v4_beta.sql": "-- reconciles: searchfit_v4_beta\n",
            "0003_reserved_slot_searchfit_reader_lifecycle.sql": (
                "-- reconciles: searchfit_reader_lifecycle\n"
            ),
            "0004_score_replay_input_digest.sql": "CREATE TABLE digest (id TEXT PRIMARY KEY);",
        },
    )
    conn = connect(tmp_path / "career.db")
    try:
        migrate(conn, lane)
        before = [tuple(r) for r in conn.execute("SELECT * FROM schema_migration ORDER BY version")]
        assert [m.version for m in pending_migrations(conn, main)] == [4]
        applied = migrate(conn, main)
        assert [(m.version, m.name) for m in applied] == [(4, "score_replay_input_digest")]
        assert "digest" in table_names(conn)
        assert "lane_probe" in table_names(conn), "the foreign lane's objects are not touched"
        after = [tuple(r) for r in conn.execute("SELECT * FROM schema_migration ORDER BY version")]
        assert after[:3] == before, "the foreign rows are untouched, applied_at included"
        assert applied_ledger(conn) == {
            1: "init",
            2: "searchfit_v4_beta",
            3: "searchfit_reader_lifecycle",
            4: "score_replay_input_digest",
        }
        assert schema_version(conn) == 4
        assert migrate(conn, main) == []
    finally:
        conn.close()


def test_a_reserved_slot_applies_as_itself_where_nothing_ran(tmp_path: Path) -> None:
    """A fresh install, and a database at the version before the slots, both
    record the slot under its own name and create nothing for it."""
    main = _lineage(
        tmp_path,
        "main",
        {
            "0001_init.sql": LEDGER_DDL,
            "0002_reserved_slot_x.sql": "-- reconciles: x\n",
            "0003_real.sql": "CREATE TABLE real_thing (id TEXT PRIMARY KEY);",
        },
    )
    conn = connect(tmp_path / "career.db")
    try:
        applied = migrate(conn, main)
        assert [(m.version, m.name) for m in applied] == [
            (1, "init"),
            (2, "reserved_slot_x"),
            (3, "real"),
        ]
        assert applied_ledger(conn)[2] == "reserved_slot_x"
        assert set(table_names(conn)) == {"schema_migration", "real_thing"}
    finally:
        conn.close()


def test_the_other_lane_is_refused_against_a_database_this_build_wrote(tmp_path: Path) -> None:
    """The rule cuts both ways: a build carrying the experimental files as
    0002 / 0003 cannot open a database whose 0002 is the reserved slot. That
    is what makes 'port to new numbers' the only way the other lane merges."""
    main = _lineage(
        tmp_path,
        "main",
        {"0001_init.sql": LEDGER_DDL, "0002_reserved_slot_x.sql": "-- reconciles: x\n"},
    )
    lane = _lineage(
        tmp_path,
        "lane",
        {"0001_init.sql": LEDGER_DDL, "0002_x.sql": "CREATE TABLE x (id TEXT PRIMARY KEY);"},
    )
    conn = connect(tmp_path / "career.db")
    try:
        migrate(conn, main)
        with pytest.raises(MigrationError, match="lineage collision at migration 0002"):
            migrate(conn, lane)
        assert "x" not in table_names(conn)
    finally:
        conn.close()


def test_a_newer_database_is_not_a_collision_for_an_older_build(tmp_path: Path) -> None:
    """Rows beyond every file this build carries are an older build reading a
    newer database: it applies nothing and refuses nothing."""
    newer = _lineage(
        tmp_path,
        "newer",
        {"0001_init.sql": LEDGER_DDL, "0002_later.sql": "CREATE TABLE later (id TEXT);"},
    )
    older = _lineage(tmp_path, "older", {"0001_init.sql": LEDGER_DDL})
    conn = connect(tmp_path / "career.db")
    try:
        migrate(conn, newer)
        assert pending_migrations(conn, older) == []
        assert migrate(conn, older) == []
    finally:
        conn.close()


def test_a_fresh_install_records_the_slots_under_their_own_names(db: sqlite3.Connection) -> None:
    migrate(db)
    ledger = applied_ledger(db)
    for version, name in RESERVED_SLOTS.items():
        assert ledger[version] == name
    assert ledger[38] == "score_replay_input_digest"
    columns = {row["name"] for row in db.execute("PRAGMA table_info(job_match)")}
    assert "input_digest" in columns
    for name in SEARCHFIT_OBJECTS:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (name,)).fetchone() is None


# --- pragmas ---------------------------------------------------------------


def test_foreign_keys_are_enforced(db: sqlite3.Connection) -> None:
    """Acceptance criterion 17. SQLite defaults this to OFF; orphan rows would
    accumulate silently and block the PostgreSQL import later."""
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    migrate(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO search_profile_version"
            " (id, candidate_id, content_hash, yaml_snapshot, created_at)"
            " VALUES ('a', 'no-such-candidate', 'h', 'yaml', '2026-01-01T00:00:00Z')"
        )


def test_check_constraints_reject_invalid_enum_values(db: sqlite3.Connection) -> None:
    """The database is the last line of defence behind pydantic."""
    migrate(db)
    db.execute(
        "INSERT INTO pipeline_run (id, stage, started_at, status)"
        " VALUES ('r1', 'collect', '2026-01-01T00:00:00Z', 'OK')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, status)"
            " VALUES ('r2', 'collect', '2026-01-01T00:00:00Z', 'MAYBE')"
        )


# --- statement splitting ---------------------------------------------------
# This is the fiddly part of the migration runner, so it gets its own tests.


def test_comments_are_stripped_before_splitting() -> None:
    """An apostrophe in prose must not look like an unterminated string. This
    is a real bug that the first version of the runner had."""
    sql = "-- a company's jobs\nCREATE TABLE a (id TEXT);"
    assert split_statements(sql) == ["CREATE TABLE a (id TEXT)"]


def test_block_comments_are_stripped() -> None:
    sql = "/* it's fine */ CREATE TABLE a (id TEXT);"
    assert split_statements(sql) == ["CREATE TABLE a (id TEXT)"]


def test_string_literals_are_preserved() -> None:
    sql = "CREATE TABLE a (s TEXT CHECK (s IN ('X','Y')));"
    assert split_statements(sql) == ["CREATE TABLE a (s TEXT CHECK (s IN ('X','Y')))"]


def test_trailing_and_blank_fragments_are_ignored() -> None:
    assert split_statements("CREATE TABLE a (id TEXT);\n\n;\n") == ["CREATE TABLE a (id TEXT)"]


def test_semicolon_inside_a_literal_fails_loudly() -> None:
    """We would rather refuse than silently execute a truncated statement."""
    with pytest.raises(MigrationError, match="unbalanced quote"):
        split_statements("INSERT INTO a (s) VALUES ('one;two');")


# --- the transaction helper ------------------------------------------------


def test_transaction_rolls_back_ddl_on_failure(db: sqlite3.Connection) -> None:
    """The behaviour sqlite3's implicit handling does not give us: DDL inside a
    failed transaction is undone."""
    with pytest.raises(RuntimeError, match="boom"), transaction(db):
        db.execute("CREATE TABLE scratch (id TEXT PRIMARY KEY)")
        raise RuntimeError("boom")
    assert "scratch" not in table_names(db)


def test_transaction_commits_on_success(db: sqlite3.Connection) -> None:
    with transaction(db):
        db.execute("CREATE TABLE scratch (id TEXT PRIMARY KEY)")
    assert "scratch" in table_names(db)
