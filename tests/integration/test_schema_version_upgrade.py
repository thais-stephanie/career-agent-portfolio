"""A score written under older semantics must be visibly stale, and repairable.

`MATCH_SCHEMA_VERSION` is documented as existing so that "a row computed under
older semantics is visibly stale rather than silently reinterpreted". Migration
0017 changed those semantics -- ten filterable values moved onto `job_match` --
and the constant was not bumped, so a database scored before it looked complete
and answered nothing for four filters.

Found by running `serve` against `data/demo.db` rather than by a test: the
country, region, workplace and salary filters returned 0 while seniority,
technology and keyword worked. An empty result and an unpopulated column are
indistinguishable from the outside, which is the whole reason this constant is
supposed to be bumped.

Three properties, and the second is the one that makes the first useful:

  * a stale row is COUNTED, so the interface can say so;
  * a PLAIN `rescore` repairs it -- not `--force`, which nobody would know to
    run because nothing told them anything was wrong;
  * a current row is still skipped, so resuming an interrupted pass over 18,000
    postings does not become a full recompute.
"""

from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.matching import MATCH_SCHEMA_VERSION
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import rescore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import JobFilter, MatchRepo, ScoredJobQuery

#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = pathlib.Path("evaluation") / "demo" / "demo_postings.yaml"

#: The columns migration 0017 added that a version-1 row cannot have.
_FACET_COLUMNS = (
    "countries = ''",
    "regions = ''",
    "work_model = NULL",
    "employment_type = NULL",
    "salary_currency = NULL",
    "salary_annual_max = NULL",
)


@pytest.fixture
def aged(tmp_path: pathlib.Path) -> Iterator[tuple]:
    """A seeded database, then aged back to look pre-migration.

    Aged rather than checked in, because a fixture file would freeze one
    schema version forever and this test is about the transition between two.
    """
    db_path = tmp_path / "aged.db"
    conn = connect(db_path)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.DEMO, "demo")
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)

    with transaction(conn):
        conn.execute(
            "UPDATE job_match SET schema_version = ?, " + ", ".join(_FACET_COLUMNS),
            (MATCH_SCHEMA_VERSION - 1,),
        )
    try:
        yield conn, config
    finally:
        conn.close()


def test_a_row_from_an_older_schema_is_counted_as_stale(aged: tuple) -> None:
    conn, config = aged
    repo = MatchRepo(conn)
    stale = repo.stale_schema_count(config.config_id, config.config_version)
    assert stale > 0
    assert stale == repo.count_for(config.config_id, config.config_version)


def test_a_stale_row_answers_nothing_for_the_filters_it_predates(aged: tuple) -> None:
    """The symptom, asserted so the cure has something to be measured against.

    This is what a person actually saw: four filters returning zero, with the
    database looking fully scored and nothing anywhere saying why.
    """
    conn, config = aged
    query = ScoredJobQuery(conn)
    everything = query.count(config.config_id, config.config_version, JobFilter())
    assert everything > 0, "the corpus is not scored at all, so this proves nothing"

    for name, job_filter in (
        ("country", JobFilter(countries=("US",))),
        ("region", JobFilter(regions=("LATAM",))),
        ("worksite", JobFilter(worksites=("REMOTE",))),
        ("min_salary", JobFilter(min_salary=1, salary_currencies=("USD",))),
    ):
        assert query.count(config.config_id, config.config_version, job_filter) == 0, name


def test_a_plain_rescore_repairs_it_without_force(aged: tuple) -> None:
    """`--force` would also work, and is not the answer.

    Nobody runs `--force` on a database that reports itself fully scored. The
    resume set now excludes rows below the current schema version, so the
    ordinary command a person already knows is the one that fixes this.
    """
    conn, config = aged
    stats = rescore(conn, config)

    assert stats.jobs_scored > 0, "a plain rescore skipped the rows that needed rewriting"
    assert stats.jobs_skipped_already_scored == 0

    repo = MatchRepo(conn)
    assert repo.stale_schema_count(config.config_id, config.config_version) == 0

    query = ScoredJobQuery(conn)
    assert query.count(config.config_id, config.config_version, JobFilter(countries=("US",))) > 0
    remote = JobFilter(worksites=("REMOTE",))
    assert query.count(config.config_id, config.config_version, remote) > 0


def test_a_current_row_is_still_skipped_so_a_resume_stays_a_resume(
    tmp_path: pathlib.Path,
) -> None:
    """The other half. Making the schema version part of "already scored" must
    not turn every interrupted pass over 18,000 postings into a full recompute.
    """
    db_path = tmp_path / "current.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)

        # A legacy seed score has no revision-checked processing receipt.
        # Establish currency through the safe path before testing a resume.
        assert rescore(conn, config).jobs_scored > 0
        stats = rescore(conn, config)
        assert stats.jobs_scored == 0
        assert stats.jobs_skipped_already_scored == stats.jobs_considered
    finally:
        conn.close()
