"""Saying "I am interested" must not make a job fit better.

`SHORTLISTED` is rendered as **Interested** / **Tenho interesse**, and it is
the one status a person sets while EXPLORING rather than while applying. That
makes it the status most likely to be read, by a future feature or by a
reader, as a signal about the job. It is not one. It is a fact about her.

WHY THIS FILE EXISTS RATHER THAN A COMMENT SAYING SO
----------------------------------------------------
The brief for this session asked for an exploration preference that
"contributes ZERO DIRECT COMPATIBILITY POINTS", and the honest first move was
to find out whether the current runtime already had that property rather than
to assume it did or to build a second mechanism beside it. It does, and this
file is the proof, in three independent forms:

  STRUCTURAL   `JobFacts` is the complete list of what the matcher may know,
               and it names no workflow state at all.
  ARCHITECTURAL `career_agent.match` imports nothing that could reach one.
  EMPIRICAL    the same posting, scored before and after being marked
               Interested, produces a byte-identical row.

The empirical one is the one that would catch a regression; the two above are
what make the regression hard to write in the first place.

WHAT INTEREST DOES CHANGE, AND WHY THAT IS NOT FIT
---------------------------------------------------
It changes WHERE THE POSTING IS REACHABLE. A posting she is tracking is exempt
from the automatic narrowings in the views that are ABOUT her decisions --
Saved, the Applications board, the digest's tracking section -- so a later
rescore deciding it is off target, or that an employer ruled her out, cannot
make it vanish from under a decision she already took.

It does NOT put the posting back among the recommendations. That was the
unconditional behaviour until 2026-09-07 and it was wrong: a list of jobs this
product is putting forward is not a record of her decisions, and one posting
an employer had verifiably ruled her out of was sitting in the default top 50
of the real corpus because she had shortlisted it. See
`JobFilter.exempt_tracked`.

Either way it moves no number: the score, the confidence, the eligibility
verdict and the band are all exactly what they were.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir, comparable_score

from career_agent.config.search_config import load_search_config
from career_agent.domain.application import ApplicationStatus
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import ApplicationRepo, JobFilter, ScoredJobQuery
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
MATCH_PACKAGE = REPO_ROOT / "src" / "career_agent" / "match"

#: The columns a score is made of. Every one is read back after the status
#: moves, because a defect that moved only the band would be invisible in the
#: score and would still change what the reader is told.
SCORE_COLUMNS = (
    "match_score",
    "data_confidence",
    "eligibility_status",
    "fit_band",
    "screening_state",
    "title_class",
    "result_json",
)


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="interest-not-fit")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


# =========================================================================
# 1. STRUCTURAL: the matcher is not told
# =========================================================================


def test_the_matchers_whole_input_names_no_workflow_state() -> None:
    """`JobFacts` is the complete list of what the deterministic matcher may
    know about a posting. A field it does not have is a fact it cannot use."""
    from career_agent.match.engine import JobFacts

    fields = set(JobFacts.__dataclass_fields__)
    forbidden = {"application_status", "status", "saved", "hidden", "hidden_at", "notes"}
    assert not (fields & forbidden), sorted(fields & forbidden)


def test_the_match_package_imports_nothing_that_knows_where_she_is() -> None:
    """The same discipline `career_agent.local_ai` is held to: a property made
    true by construction rather than by everyone remembering.

    `ApplicationStatus` lives in `career_agent.domain.application`, and the
    workflow columns live in `career_agent.storage.mvp_repo`. The matcher may
    import neither, so no scoring code can reach a status even by accident.
    """
    offenders: list[str] = []
    for path in sorted(MATCH_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if "domain.application" in name or "storage" in name:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == [], offenders


# =========================================================================
# 2. EMPIRICAL: the row does not move
# =========================================================================


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.APPLIED,
        ApplicationStatus.REJECTED,
    ],
)
def test_a_status_change_and_a_rescore_leave_the_score_byte_identical(
    api: JobsApi, status: ApplicationStatus
) -> None:
    """The assertion that would actually catch a regression.

    Marked Interested -- and, for contrast, three other statuses -- then
    rescored with `force`, so nothing is skipped for already existing. Every
    column that makes up a score is compared, `result_json` included: a defect
    that moved only the evidence inside the blob would leave the number alone
    and still change what the drawer says.
    """
    config, _ = load_search_config(CONFIG_DIR)
    conn = connect(api.config.db_path)
    try:
        job_id = _any_job(conn)
        # RESCORE FIRST, and take the baseline from that.
        #
        # `seed_demo` builds its facts from the corpus FILE and `rescore`
        # builds them from what was PERSISTED, and on the demo corpus those
        # two are not the same input: the provider it imitates publishes no
        # employment type and no pay period, so a forced rescore moves 18 of
        # 19 rows. That is a real and separate finding about the demo; it is
        # not what this test is about, and starting from a seeded row would
        # let it masquerade as one.
        rescore(conn, config, force=True)
        conn.commit()
        before = _score_row(conn, job_id)
        assert before is not None, "the fixture posting was never scored"

        ApplicationRepo(conn).set_status(job_id, status, now="2026-09-06T10:00:00Z")
        conn.commit()
        rescore(conn, config, force=True)
        conn.commit()

        after = _score_row(conn, job_id)
    finally:
        conn.close()

    assert after == before, f"marking {status.value} moved the score"


def test_marking_interested_changes_reach_and_nothing_else(api: JobsApi) -> None:
    """What interest DOES do, stated so it is not mistaken for the above.

    Two filters over the same corpus, differing in one field. The DISCOVERY
    one keeps hiding the posting, because interest is not eligibility and a
    recommendation is not a record. The TRACKING one stops hiding it, because
    that view exists to hold decisions she already took.

    And the number beside it is untouched in both. Asserting all three in one
    place is what stops any of them being quietly traded for another.
    """
    conn = connect(api.config.db_path)
    try:
        query = ScoredJobQuery(conn)
        identity = api._identity()
        discovery = JobFilter(include_off_target=False, limit=500)
        tracking = JobFilter(include_off_target=False, exempt_tracked=True, limit=500)

        hidden_ids = _hidden_by(query, identity, discovery)
        assert hidden_ids, "nothing is set aside as off target, so this proves nothing"
        job_id = sorted(hidden_ids)[0]
        before = _score_row(conn, job_id)

        ApplicationRepo(conn).set_status(
            job_id, ApplicationStatus.SHORTLISTED, now="2026-09-06T10:00:00Z"
        )
        conn.commit()

        assert job_id in _hidden_by(query, identity, discovery), (
            "shortlisting a posting put it back among the recommendations"
        )
        assert job_id not in _hidden_by(query, identity, tracking), (
            "a posting she is tracking was hidden from the view that holds her decisions"
        )
        assert _score_row(conn, job_id) == before, "changing where it shows moved the score"
    finally:
        conn.close()


def test_the_card_reports_interest_and_the_score_from_different_fields(
    api: JobsApi,
) -> None:
    """ADR-0004 keeps three measurements and never blends them.

    `application_status` is a fourth thing entirely -- where SHE is -- and it
    arrives on the card as its own field. Nothing may fold it into one of the
    three, and the way to be sure is to change it and watch the three not
    move.
    """
    job_id = _first_job_id(api)
    before = api.get_job(job_id=job_id, query={}, body={})

    api.patch_status(job_id=job_id, query={}, body={"status": ApplicationStatus.SHORTLISTED.value})
    after = api.get_job(job_id=job_id, query={}, body={})

    assert after["application_status"] == ApplicationStatus.SHORTLISTED.value
    for field in ("match_score", "data_confidence", "eligibility_status", "fit_band"):
        assert after[field] == before[field], f"{field} moved when the status did"


# =========================================================================
# helpers
# =========================================================================


def _any_job(conn) -> str:
    row = conn.execute("SELECT job_id FROM job_match ORDER BY job_id LIMIT 1").fetchone()
    assert row is not None, "the fixture database holds no scored jobs"
    return str(row["job_id"])


def _score_row(conn, job_id: str) -> tuple[object, ...] | None:
    columns = ", ".join(SCORE_COLUMNS)
    row = conn.execute(
        f"SELECT {columns} FROM job_match WHERE job_id = ? ORDER BY config_version DESC",
        (job_id,),
    ).fetchone()
    # `comparable_score` sets aside the audit timestamp INSIDE `result_json`,
    # which a second computation is supposed to change. Without it this test
    # passed only while both rescores landed in the same second.
    return (
        None if row is None else tuple(comparable_score(name, row[name]) for name in SCORE_COLUMNS)
    )


def _hidden_by(query: ScoredJobQuery, identity, narrow: JobFilter) -> set[str]:
    everything = {row.job_id for row in query.page(*identity, JobFilter(limit=500))}
    visible = {row.job_id for row in query.page(*identity, narrow)}
    return everything - visible


def _first_job_id(api: JobsApi) -> str:
    payload = api.list_jobs(query={}, body={})
    assert payload["items"], "the fixture served no jobs"
    return str(payload["items"][0]["job_id"])
