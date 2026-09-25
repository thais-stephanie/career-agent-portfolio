"""The shared public job catalogue: one copy of public job data, private
state per profile, nothing private crossing.

Synthetic throughout: the demo postings, invented LinkedIn cards and invented
search phrases, all under a temporary folder. No real profile is read.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import PipelineRunStatus
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.linkedin_collect import LinkedInCollector
from career_agent.pipeline.rescore import RescoreMode, plan, rescore
from career_agent.providers.linkedin_jobspy import LinkedInJobSpyProvider
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage import catalogue as cat
from career_agent.storage import catalogue_split
from career_agent.storage.db import (
    MIGRATIONS_DIR,
    MigrationError,
    connect,
    discover_migrations,
    migrate,
    table_names,
    transaction,
)
from career_agent.storage.repositories import PipelineRunRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
LINKEDIN = json.loads(
    (REPO_ROOT / "tests" / "fixtures" / "providers" / "linkedin" / "search.json").read_text(
        encoding="utf-8"
    )
)
#: B's private search phrase. Invented, and unlike any posting's words, so
#: finding it anywhere outside B's own database is a leak, not a coincidence.
SECRET_PHRASE = "Zyxquark Pipeline Whisperer"


def _config(tmp_path: Path, name: str, template: str) -> Path:
    config = tmp_path / name
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / template, config / "search.local.yaml")
    return config


@pytest.fixture
def two_profiles(tmp_path: Path):  # noqa: ANN201
    """A: the original profile, seeded and then split. B: created afterwards,
    linked to the same catalogue, with a different Search Intent."""
    root = tmp_path / "install"
    a_config = _config(tmp_path, "config-a", "search.worked-example.yaml")
    b_config = _config(tmp_path, "config-b", "search.starter.yaml")
    a_db = root / "data" / "personal.db"
    a_db.parent.mkdir(parents=True)
    conn = connect(a_db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "A")
        search, _ = load_search_config(a_config)
        seed_demo(conn, search, source=DEMO_FILE)
    finally:
        conn.close()
    source_jobs = _count_single(a_db, "job")
    profile, shared, _ = catalogue_split.build(a_db, tmp_path / "staging")
    assert catalogue_split.verify(a_db, profile, shared).ok
    installed = catalogue_split.install(a_db, profile, shared)

    b_db = root / "data" / "profiles" / "prof-B" / "personal.db"
    b_db.parent.mkdir(parents=True)
    conn = connect(b_db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "B")
    finally:
        conn.close()
    assert cat.link_new_profile(b_db, create=False) is not None
    return {
        "a_db": a_db,
        "b_db": b_db,
        "a_config": a_config,
        "b_config": b_config,
        "catalogue": installed.catalogue,
        "legacy": installed.legacy,
        "source_jobs": source_jobs,
    }


def _count_single(db: Path, table: str) -> int:
    raw = sqlite3.connect(db)
    try:
        return int(raw.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    finally:
        raw.close()


def _main_tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _scores(db: Path) -> dict[str, tuple]:
    conn = connect(db)
    try:
        return {
            str(r["job_id"]): (r["config_id"], r["match_score"], r["fit_band"], r["content_hash"])
            for r in conn.execute("SELECT * FROM main.job_match")
        }
    finally:
        conn.close()


def _bytes_of(db: Path) -> bytes:
    """The whole file set of a closed database, WAL included."""
    raw = sqlite3.connect(db)
    try:
        raw.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        raw.close()
    data = db.read_bytes()
    wal = db.with_name(db.name + "-wal")
    return data + (wal.read_bytes() if wal.exists() else b"")


class _Fake:
    def __init__(self, answers, pages=None):  # noqa: ANN001
        self.answers = list(answers)
        self.pages = pages or {}

    def scrape(self, **kwargs):  # noqa: ANN003, ANN201
        return self.answers.pop(0) if self.answers else []

    def details(self, job_id):  # noqa: ANN001, ANN201
        return self.pages.get(job_id, {})


def _collect(conn: sqlite3.Connection, results, phrase: str, pages=None):  # noqa: ANN001, ANN201
    from career_agent.discovery.plan import Query, QueryTerm
    from career_agent.discovery.scopes import MarketScope

    fake = _Fake([results], pages)
    provider = LinkedInJobSpyProvider(scrape=fake.scrape, details=fake.details)
    collector = LinkedInCollector(conn, provider, sleep=lambda s: None, pause=lambda span: 0.0)
    query = Query(QueryTerm(phrase, "anchor"), MarketScope("country:BR", "Brazil", "BR"))
    return collector.collect((query,), max_enrich=5)


# =========================================================================
# what goes where
# =========================================================================


def test_every_table_is_classified_exactly_once(tmp_path: Path) -> None:
    conn = connect(tmp_path / "fresh.db")
    try:
        migrate(conn)
        tables = set(table_names(conn))
    finally:
        conn.close()
    assert not cat.SHARED_TABLES & cat.PRIVATE_TABLES
    unclassified = tables - cat.SHARED_TABLES - cat.PRIVATE_TABLES - cat.LEDGER_TABLES
    assert not unclassified, f"classify these in storage/catalogue.py: {sorted(unclassified)}"
    assert tables <= cat.SHARED_TABLES | cat.PRIVATE_TABLES | cat.LEDGER_TABLES


def test_the_catalogue_holds_only_public_tables_and_the_profile_only_private_ones(
    two_profiles: dict,
) -> None:
    shared = sqlite3.connect(two_profiles["catalogue"])
    try:
        catalogue_tables = _main_tables(shared)
    finally:
        shared.close()
    assert not catalogue_tables & cat.PRIVATE_TABLES
    assert "job" in catalogue_tables and "job_match" not in catalogue_tables
    for db in (two_profiles["a_db"], two_profiles["b_db"]):
        conn = connect(db)
        try:
            assert cat.role(conn) == "profile"
            assert not _main_tables(conn) & cat.SHARED_TABLES
            # No foreign key may name a table SQLite cannot see from this file.
            for table in _main_tables(conn):
                parents = {r[2] for r in conn.execute(f'PRAGMA main.foreign_key_list("{table}")')}
                assert not parents & cat.SHARED_TABLES, table
        finally:
            conn.close()


def test_both_profiles_read_one_copy_of_each_posting(two_profiles: dict) -> None:
    conns = [connect(two_profiles[k]) for k in ("a_db", "b_db")]
    try:
        seen = [{r[0] for r in c.execute("SELECT id FROM job")} for c in conns]
        assert seen[0] == seen[1] and len(seen[0]) == two_profiles["source_jobs"]
    finally:
        for c in conns:
            c.close()
    assert _count_single(two_profiles["catalogue"], "job") == two_profiles["source_jobs"]


# =========================================================================
# each profile keeps its own interpretation of a shared posting
# =========================================================================


def test_each_profile_scores_the_same_posting_its_own_way(two_profiles: dict) -> None:
    a_before = _scores(two_profiles["a_db"])
    assert a_before, "A was scored by the demo seed"
    b_config, _ = load_search_config(two_profiles["b_config"])
    conn = connect(two_profiles["b_db"])
    try:
        stats = rescore(conn, b_config)
        assert stats.jobs_scored == two_profiles["source_jobs"] - stats.jobs_skipped_no_description
    finally:
        conn.close()
    b_scores = _scores(two_profiles["b_db"])
    assert set(b_scores) == set(a_before), "B scored the same shared postings"
    assert {v[0] for v in b_scores.values()} == {b_config.config_id}
    assert any(b_scores[j][1:3] != a_before[j][1:3] for j in b_scores), "B's intent is its own"
    assert _scores(two_profiles["a_db"]) == a_before, "B's scoring changed A's"


def test_a_public_refresh_reaches_both_profiles_and_each_rescores_on_its_own(
    two_profiles: dict,
) -> None:
    a_config, _ = load_search_config(two_profiles["a_config"])
    b_config, _ = load_search_config(two_profiles["b_config"])
    b = connect(two_profiles["b_db"])
    try:
        rescore(b, b_config)
    finally:
        b.close()
    a = connect(two_profiles["a_db"])
    try:
        # A public posting changes: its text is rewritten by the source.
        job = a.execute(
            "SELECT id, content_hash FROM job WHERE content_hash IS NOT NULL ORDER BY id LIMIT 1"
        ).fetchone()
        from career_agent.storage.repositories import JobRawRepo

        with transaction(a):
            new_hash = JobRawRepo(a).put("An entirely rewritten public description.", None)
            a.execute("UPDATE job SET content_hash = ? WHERE id = ?", (new_hash, job["id"]))
        # And a new public posting arrives through A's collection.
        _collect(a, [LINKEDIN[0]], "Marketing Operations")
        new_id = a.execute(
            "SELECT id FROM job WHERE external_id = ?", ("li-4000000001",)
        ).fetchone()
        assert new_id is not None
        a_plan = plan(a, a_config, mode=RescoreMode.TARGETED)
        assert job["id"] in a_plan.targets
        rescore(a, a_config)
        assert job["id"] not in plan(a, a_config, mode=RescoreMode.TARGETED).targets
    finally:
        a.close()
    b = connect(two_profiles["b_db"])
    try:
        assert b.execute("SELECT content_hash FROM job WHERE id = ?", (job["id"],)).fetchone()[
            0
        ] == (new_hash)
        assert b.execute("SELECT 1 FROM job WHERE id = ?", (new_id[0],)).fetchone()
        # A's pass cleared the shared refresh queue; B still knows its own
        # score of that posting is stale, from its own receipts.
        assert job["id"] in plan(b, b_config, mode=RescoreMode.TARGETED).targets
        # Nothing of A's run or provenance is in B.
        assert b.execute("SELECT COUNT(*) FROM main.job_retrieval_lane").fetchone()[0] == 0
        assert b.execute(
            "SELECT COUNT(*) FROM main.pipeline_run WHERE stage LIKE 'collect%'"
        ).fetchone()[0] == (0)
    finally:
        b.close()


def test_a_targeted_search_shares_the_posting_but_never_the_query(two_profiles: dict) -> None:
    b = connect(two_profiles["b_db"])
    try:
        stats = _collect(b, [LINKEDIN[1]], SECRET_PHRASE)
        assert stats.jobs_new == 1
        job_id = b.execute("SELECT id FROM job WHERE external_id = 'li-4000000002'").fetchone()[0]
        lanes = b.execute(
            "SELECT query_key, term_origin FROM main.job_retrieval_lane WHERE job_id = ?",
            (job_id,),
        ).fetchall()
        assert lanes and lanes[0]["term_origin"] == "anchor"
    finally:
        b.close()
    a = connect(two_profiles["a_db"])
    try:
        assert a.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone(), "public"
        assert a.execute("SELECT COUNT(*) FROM main.job_retrieval_lane").fetchone()[0] == 0
        assert a.execute(
            "SELECT COUNT(*) FROM main.pipeline_run WHERE stage = 'collect-linkedin'"
        ).fetchone()[0] == (0)
    finally:
        a.close()
    needle = SECRET_PHRASE.casefold().encode("utf-8")
    # The detector works: B's own file records its phrase (its query key).
    assert needle in _bytes_of(two_profiles["b_db"]).lower()
    assert needle not in _bytes_of(two_profiles["a_db"]).lower(), "B's phrase reached A's file"
    assert needle not in _bytes_of(two_profiles["catalogue"]).lower(), "the catalogue kept it"


# =========================================================================
# one collection at a time
# =========================================================================


def test_a_collection_is_refused_while_another_process_collects(two_profiles: dict) -> None:
    other = cat.CatalogueWriteLock(two_profiles["catalogue"])
    handle = other._take()  # what another process holds
    try:
        b = connect(two_profiles["b_db"])
        try:
            with pytest.raises(cat.CatalogueBusy), transaction(b):
                PipelineRunRepo(b).start("collect-linkedin")
            assert b.execute("SELECT COUNT(*) FROM main.pipeline_run").fetchone()[0] == 0
            # Scoring takes no collection lock.
            with transaction(b):
                run = PipelineRunRepo(b).start("rescore")
                PipelineRunRepo(b).finish(run, PipelineRunStatus.OK)
        finally:
            b.close()
    finally:
        other._handle = handle
        other._drop()
    b = connect(two_profiles["b_db"])
    try:
        with transaction(b):
            run = PipelineRunRepo(b).start("collect-linkedin")
        lock = cat.CatalogueWriteLock.for_catalogue(two_profiles["catalogue"])
        assert lock.held
        with transaction(b):
            PipelineRunRepo(b).finish(run, PipelineRunStatus.OK)
        assert not lock.held
    finally:
        b.close()


def test_a_run_that_never_finishes_does_not_keep_the_lock(two_profiles: dict) -> None:
    from career_agent.pipeline.retrieval import RetrievalRunner

    runner = RetrievalRunner()

    def work(state, cancel) -> None:  # noqa: ANN001
        conn = connect(two_profiles["b_db"])
        try:
            with transaction(conn):
                PipelineRunRepo(conn).start("collect")
            raise RuntimeError("the collector crashed before finishing its run")
        finally:
            conn.close()

    runner.start(work, "run")
    runner.join(10)
    assert not cat.CatalogueWriteLock.for_catalogue(two_profiles["catalogue"]).held


# =========================================================================
# migrations know which file they change
# =========================================================================


def _migrations_with(tmp_path: Path, name: str, body: str) -> Path:
    folder = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS_DIR, folder)
    (folder / name).write_text(body, encoding="utf-8")
    return folder


def test_a_new_migration_must_name_the_file_it_changes(tmp_path: Path) -> None:
    folder = _migrations_with(tmp_path, "0044_unscoped.sql", "CREATE TABLE x (id TEXT);\n")
    with pytest.raises(MigrationError, match="scope"):
        discover_migrations(folder)


def test_a_scoped_migration_runs_on_its_side_and_is_recorded_on_the_other(
    two_profiles: dict, tmp_path: Path
) -> None:
    folder = _migrations_with(
        tmp_path,
        "0044_profile_note.sql",
        "-- scope: profile\nCREATE TABLE private_note (id TEXT);\n",
    )
    conn = connect(two_profiles["b_db"])
    try:
        migrate(conn, folder)
        assert "private_note" in _main_tables(conn)
    finally:
        conn.close()
    shared = connect(two_profiles["catalogue"])
    try:
        assert "private_note" not in _main_tables(shared)
        assert shared.execute("SELECT name FROM schema_migration WHERE version = 44").fetchone()[
            0
        ] == ("profile_note")
    finally:
        shared.close()


# =========================================================================
# backups say what they hold
# =========================================================================


def test_a_profile_backup_holds_no_postings_and_a_catalogue_backup_no_person(
    two_profiles: dict, tmp_path: Path
) -> None:
    from career_agent.storage.backup import create_backup, create_catalogue_backup

    profile_zip = tmp_path / "profile.zip"
    result = create_backup(
        db=two_profiles["a_db"],
        config_dir=two_profiles["a_config"],
        destination=profile_zip,
        staging=tmp_path / "stage-p",
    )
    assert result.split and result.jobs == 0
    with zipfile.ZipFile(profile_zip) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
        archive.extract("personal.db", tmp_path / "out-p")
    assert manifest["contains_public_jobs"].startswith("No.")
    held = sqlite3.connect(tmp_path / "out-p" / "personal.db")
    try:
        assert not _main_tables(held) & cat.SHARED_TABLES
    finally:
        held.close()

    shared_zip = tmp_path / "catalogue.zip"
    shared = create_catalogue_backup(
        catalogue=two_profiles["catalogue"], destination=shared_zip, staging=tmp_path / "stage-c"
    )
    assert shared.jobs == two_profiles["source_jobs"]
    assert not set(shared.tables) & cat.PRIVATE_TABLES
    with zipfile.ZipFile(shared_zip) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
        names = archive.namelist()
    assert manifest["contains_profile_data"].startswith("No.")
    assert names == ["catalogue.db", "MANIFEST.json"]


# =========================================================================
# the split itself: never in place, verified, reversible
# =========================================================================


def test_the_split_keeps_the_original_and_can_be_rolled_back(two_profiles: dict) -> None:
    legacy = two_profiles["legacy"]
    assert legacy.exists() and "legacy" in legacy.parts
    assert _count_single(legacy, "job") == two_profiles["source_jobs"]
    aside = catalogue_split.rollback(two_profiles["a_db"], legacy)
    conn = connect(two_profiles["a_db"])
    try:
        assert cat.role(conn) == "single"
        assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == two_profiles["source_jobs"]
    finally:
        conn.close()
    assert aside.exists(), "the split profile is kept, not deleted"


def test_the_split_refuses_a_database_already_split(two_profiles: dict, tmp_path: Path) -> None:
    with pytest.raises(cat.CatalogueError):
        catalogue_split.build(two_profiles["a_db"], tmp_path / "again")


def test_a_new_profile_is_not_linked_while_it_holds_postings(tmp_path: Path) -> None:
    db = tmp_path / "data" / "profiles" / "prof-X" / "personal.db"
    db.parent.mkdir(parents=True)
    conn = connect(db)
    try:
        migrate(conn)
        search, _ = load_search_config(_config(tmp_path, "c", "search.worked-example.yaml"))
        seed_demo(conn, search, source=DEMO_FILE)
    finally:
        conn.close()
    with pytest.raises(cat.CatalogueError):
        cat.link_new_profile(db, create=True)


def test_a_missing_or_foreign_catalogue_is_refused(two_profiles: dict, tmp_path: Path) -> None:
    shared = two_profiles["catalogue"]
    aside = shared.with_name("aside.db")
    shared.rename(aside)
    try:
        with pytest.raises(cat.CatalogueError, match="missing"):
            connect(two_profiles["b_db"])
        cat.create_empty(shared)  # another installation's catalogue
        with pytest.raises(cat.CatalogueMismatch):
            connect(two_profiles["b_db"])
    finally:
        shared.unlink()
        for suffix in ("-wal", "-shm"):
            shared.with_name(shared.name + suffix).unlink(missing_ok=True)
        aside.rename(shared)


def test_integrity_reports_a_private_row_whose_posting_is_gone(two_profiles: dict) -> None:
    from career_agent.storage.integrity import audit

    conn = connect(two_profiles["b_db"])
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO job_retrieval_lane VALUES"
                " ('no-such-job', 'targeted', 'linkedin', 'k', 'anchor', 'x', 'x')"
            )
        findings = {f.check: f for f in audit(conn)}
        assert findings["catalogue_reference"].examples == ("no-such-job",)
    finally:
        conn.close()


# =========================================================================
# from the adversarial review
# =========================================================================


def test_another_profiles_pass_never_hides_work_in_dirty_mode(two_profiles: dict) -> None:
    a_config, _ = load_search_config(two_profiles["a_config"])
    b_config, _ = load_search_config(two_profiles["b_config"])
    b = connect(two_profiles["b_db"])
    try:
        rescore(b, b_config)
    finally:
        b.close()
    a = connect(two_profiles["a_db"])
    try:
        job = a.execute("SELECT id FROM job WHERE content_hash IS NOT NULL ORDER BY id").fetchone()[
            0
        ]
        from career_agent.storage.repositories import JobRawRepo

        with transaction(a):
            new_hash = JobRawRepo(a).put("A rewritten public description, once more.", None)
            a.execute("UPDATE job SET content_hash = ? WHERE id = ?", (new_hash, job))
        rescore(a, a_config, mode=RescoreMode.DIRTY)
        assert a.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0] == 0, "A cleared the mark"
    finally:
        a.close()
    b = connect(two_profiles["b_db"])
    try:
        assert job in plan(b, b_config, mode=RescoreMode.DIRTY).targets
    finally:
        b.close()


def test_a_profiles_own_rescore_requests_stay_private(two_profiles: dict) -> None:
    from career_agent.storage import invalidation

    a_config, _ = load_search_config(two_profiles["a_config"])
    b_config, _ = load_search_config(two_profiles["b_config"])
    b = connect(two_profiles["b_db"])
    try:
        rescore(b, b_config)
        assert plan(b, b_config, mode=RescoreMode.TARGETED).targets == []
    finally:
        b.close()
    a = connect(two_profiles["a_db"])
    try:
        job = a.execute("SELECT job_id FROM main.job_match ORDER BY job_id").fetchone()[0]
        revision = a.execute("SELECT revision FROM compute_revision").fetchone()[0]
        with transaction(a):
            invalidation.request(a, [job])
        assert (
            a.execute("SELECT COUNT(*) FROM job_dirty WHERE reason='REQUESTED'").fetchone()[0] == 0
        )
        assert a.execute("SELECT revision FROM compute_revision").fetchone()[0] == revision
        assert job in plan(a, a_config, mode=RescoreMode.DIRTY).targets
        rescore(a, a_config, mode=RescoreMode.DIRTY)
        assert a.execute("SELECT COUNT(*) FROM main.profile_request").fetchone()[0] == 0
    finally:
        a.close()
    b = connect(two_profiles["b_db"])
    try:
        assert plan(b, b_config, mode=RescoreMode.TARGETED).targets == [], (
            "A's request cost B nothing"
        )
    finally:
        b.close()


def test_only_whole_public_table_names_lose_their_foreign_keys() -> None:
    stripped = cat._strip_shared_references(
        "CREATE TABLE x (a TEXT REFERENCES job_match(id),"
        " b TEXT REFERENCES job(id) ON DELETE CASCADE,"
        " FOREIGN KEY (a) REFERENCES job_application(job_id))"
    )
    assert "REFERENCES job_match(id)" in stripped
    assert "REFERENCES job(id)" not in stripped
    assert "REFERENCES job_application(job_id)" in stripped


def _single(tmp_path: Path, name: str) -> Path:
    db = tmp_path / name / "data" / "personal.db"
    db.parent.mkdir(parents=True)
    conn = connect(db)
    try:
        migrate(conn)
        search, _ = load_search_config(
            _config(tmp_path, f"{name}-config", "search.worked-example.yaml")
        )
        seed_demo(conn, search, source=DEMO_FILE)
    finally:
        conn.close()
    return db


def test_the_source_is_held_exclusively_while_it_is_split(tmp_path: Path) -> None:
    db = _single(tmp_path, "held")
    with catalogue_split.SourceHold(db):
        other = sqlite3.connect(db, timeout=0.2)
        try:
            with pytest.raises(sqlite3.OperationalError, match="locked"):
                other.execute(
                    "INSERT INTO pipeline_run (id, stage, started_at, status) VALUES"
                    " ('x', 'collect', 'x', 'RUNNING')"
                )
        finally:
            other.close()


def test_a_database_behind_the_schema_is_refused(tmp_path: Path) -> None:
    db = _single(tmp_path, "behind")
    raw = sqlite3.connect(db)
    try:
        raw.execute(
            "DELETE FROM schema_migration"
            " WHERE version = (SELECT MAX(version) FROM schema_migration)"
        )
        raw.commit()
    finally:
        raw.close()
    with pytest.raises(cat.CatalogueError, match="schema"):
        catalogue_split.build(db, tmp_path / "staging-behind")


def test_a_failed_install_puts_every_file_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    db = _single(tmp_path, "undo")
    profile, shared, _ = catalogue_split.build(db, tmp_path / "staging-undo")
    real = os.replace
    calls = {"n": 0}

    def flaky(a, b):  # noqa: ANN001, ANN202
        calls["n"] += 1
        if calls["n"] == 3:
            raise PermissionError("in use")
        return real(a, b)

    monkeypatch.setattr(catalogue_split.os, "replace", flaky)
    with pytest.raises(cat.CatalogueError, match="put back"):
        catalogue_split.install(db, profile, shared)
    monkeypatch.setattr(catalogue_split.os, "replace", real)
    assert db.exists() and profile.exists() and shared.exists()
    assert not cat.catalogue_path(db).exists()
    conn = connect(db)
    try:
        assert cat.role(conn) == "single"
    finally:
        conn.close()
