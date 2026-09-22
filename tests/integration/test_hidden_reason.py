"""Why she set a posting aside, counted -- and never turned into a rule.

WHAT THIS IS FOR
----------------
The recommendation audit on 2026-09-08 measured that 92 of her top 100 sit in
the WEAK band and that only 101 postings of 19,469 are her specialty AND open
to her AND at her level.

Those are measurements of the corpus. They cannot tell a posting that is wrong
because of WHERE it is from one that is wrong because of WHAT THE WORK IS, and
those two want completely different corrections. Only she knows which, and she
knows it one posting at a time, at the moment she decides not to look at it
again.

THE LINE THIS FILE DEFENDS
--------------------------
**Evidence for a conversation, never an input to one.** A product that learned
"she rejects Germany" from one hidden posting would be inventing a candidate
fact, which invariant 2 forbids and `config/ownership.py` places squarely with
her. A reason changes no score, no eligibility and no preference; if a pattern
in them is ever turned into a rule, it goes through the preference editor as a
proposal she confirms.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.domain.enums import HiddenReason
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import ApplicationRepo
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api() -> Iterator[JobsApi]:
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.demo_seed import seed_demo

    root = Path(tempfile.mkdtemp(prefix="hidden-reason"))
    config = root / "config"
    config.mkdir()
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", config)
    shutil.copy(config / "search.worked-example.yaml", config / "search.local.yaml")
    shutil.copy(ROOT / "config" / "places.yaml", config)
    db = root / "career.db"
    conn = connect(db)
    migrate(conn)
    loaded, _ = load_search_config(config)
    seed_demo(conn, loaded, source=DEMO)
    conn.close()
    yield JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True)


def a_job(api: JobsApi) -> str:
    return api.list_jobs(query={"limit": ["1"]}, body={})["items"][0]["job_id"]


def reasons(api: JobsApi) -> dict[str, int]:
    with connect(api.config.db_path) as conn:
        return ApplicationRepo(conn).hidden_reasons()


# =========================================================================
# 1. RECORDING ONE
# =========================================================================


def test_a_reason_is_stored_with_the_hide_it_belongs_to(api) -> None:
    job = a_job(api)

    api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": "WRONG_LEVEL"})

    assert reasons(api) == {"WRONG_LEVEL": 1}


def test_hiding_without_a_reason_stays_unremarkable(api) -> None:
    """The commonest case. A control that interrogates somebody for setting one
    job aside is a control they stop using."""
    job = a_job(api)

    api.patch_hidden(job_id=job, query={}, body={"hidden": True})

    assert reasons(api) == {}, "an unexplained hide invented an explanation"


def test_undoing_the_hide_clears_the_reason(api) -> None:
    """A reason attached to a posting that is no longer hidden is a note about
    a decision that was reversed. Keeping it would invite a later query to read
    "she once said wrong country about this" as a signal. It is not one."""
    job = a_job(api)
    api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": "WRONG_PLACE"})

    api.patch_hidden(job_id=job, query={}, body={"hidden": False})

    assert reasons(api) == {}


def test_they_are_counted_rather_than_listed(api) -> None:
    """ "Eleven of the last twenty were the wrong seniority" is actionable.
    "Eleven notes" is not."""
    jobs = [item["job_id"] for item in api.list_jobs(query={"limit": ["5"]}, body={})["items"]]
    for job, reason in zip(jobs, ["WRONG_LEVEL", "WRONG_LEVEL", "WRONG_PLACE"], strict=False):
        api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": reason})

    assert reasons(api) == {"WRONG_LEVEL": 2, "WRONG_PLACE": 1}


# =========================================================================
# 2. WHAT IT REFUSES
# =========================================================================


def test_a_reason_outside_the_vocabulary_is_refused(api) -> None:
    """Free text cannot be counted, and counting is the whole point. `notes` is
    already there for anything that does not fit."""
    job = a_job(api)

    with pytest.raises(ApiError) as refused:
        api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": "too far away"})

    assert refused.value.status == 400
    assert "WRONG_PLACE" in refused.value.message


def test_a_reason_may_not_ride_along_with_a_restore(api) -> None:
    job = a_job(api)
    api.patch_hidden(job_id=job, query={}, body={"hidden": True})

    with pytest.raises(ApiError) as refused:
        api.patch_hidden(job_id=job, query={}, body={"hidden": False, "reason": "PAY"})

    assert refused.value.status == 400


def test_the_database_refuses_a_value_the_enum_does_not_have(api) -> None:
    """The CHECK constraint is the second spelling of `HiddenReason`, and
    `tests/unit/test_enum_vocabularies.py` exists because two spellings drifting
    apart once cost a live call."""
    job = a_job(api)

    with connect(api.config.db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_application (id, job_id, status, saved, hidden_at,"
            " hidden_reason, created_at, updated_at)"
            " VALUES ('x', ?, 'DISCOVERED', 0, '2026-01-01', 'NOT_A_REASON',"
            " '2026-01-01', '2026-01-01')",
            (job,),
        )


def test_every_enum_member_is_accepted_by_the_column(api) -> None:
    """The other direction, and the one that actually breaks in production: a
    member the enum has and the constraint does not."""
    jobs = [item["job_id"] for item in api.list_jobs(query={"limit": ["60"]}, body={})["items"]]
    assert len(jobs) >= len(HiddenReason), "not enough postings to try every member"

    for job, member in zip(jobs, HiddenReason, strict=False):
        api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": member.value})

    assert set(reasons(api)) == {member.value for member in HiddenReason}


# =========================================================================
# 3. AND IT CHANGES NOTHING
# =========================================================================


def test_a_reason_moves_no_score(api) -> None:
    """**The line this whole file defends.**

    Hiding removes a posting from a view. Saying why must not touch what the
    matcher concluded about it.
    """
    job = a_job(api)
    before = api.get_job(job_id=job, query={}, body={})

    api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": "WRONG_WORK"})
    after = api.get_job(job_id=job, query={}, body={})

    for field in ("match_score", "data_confidence", "eligibility_status", "seniority"):
        assert before.get(field) == after.get(field), field


def test_a_reason_moves_no_preference(api) -> None:
    """A product that learned "she rejects Germany" from one hidden posting
    would be inventing a candidate fact."""
    from career_agent.config.candidate_writer import current_candidate_fields

    job = a_job(api)
    before = current_candidate_fields(api.config.config_dir)
    version_before = api.search_config().config_version

    api.patch_hidden(job_id=job, query={}, body={"hidden": True, "reason": "WRONG_PLACE"})

    api._search_config = None
    assert current_candidate_fields(api.config.config_dir) == before
    assert api.search_config().config_version == version_before


def test_the_matcher_cannot_reach_a_reason_at_all(api) -> None:
    """Structural rather than behavioural, and the stronger claim of the two.

    The same shape as `test_interest_is_not_fit`: a rule nothing can break is
    better than a rule nothing currently breaks.
    """
    import ast

    matcher = ROOT / "src" / "career_agent" / "match"
    for path in matcher.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
            node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
        }
        assert "hidden_reason" not in names, path
        assert "HiddenReason" not in names, path
        source = path.read_text(encoding="utf-8")
        assert "hidden_reason" not in source, f"{path} mentions the column"
