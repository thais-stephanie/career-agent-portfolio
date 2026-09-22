"""Regressions for the defects an adversarial re-review found.

Each test here corresponds to something that was actually wrong and is now
fixed. They live together rather than scattered into the suites they belong to
because what they have in common is more useful than what separates them: every
one is a case where the code looked right, read right, and did the wrong thing
under an input nobody had tried.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.application import ApplicationStatus, has_applied
from career_agent.domain.enums import CollectionStatus, Prominence
from career_agent.domain.matching import ConfidenceItem, MatchResult, ObservedSignal, SignalHit
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import (
    SALARY_CONFIDENCE_ITEM,
    ApplicationDateRefused,
    ApplicationRepo,
    JobFilter,
    MatchRepo,
    ScoredJobQuery,
    membership_of,
)
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DIGEST = "c" * 64


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "regressions.db")
    migrate(conn)
    return conn


def _seed_job(conn: sqlite3.Connection, *, external: str, description: str) -> str:
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="manual_import",
                board_identifier="acme",
                board_url="manual://acme",
                active=False,
            )
        )
        digest = JobRawRepo(conn).put(description)
        return JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="manual_import",
                external_id=external,
                url=f"manual://{external}",
                title="Business Systems Engineer",
                content_hash=digest,
            ),
            status=CollectionStatus.NORMALISED,
        )


def _result(*, quote: str, awarded: bool) -> MatchResult:
    """A result whose EVIDENCE contains whatever the caller wants it to."""
    return MatchResult(
        config_id="personal-alpha",
        config_version=1,
        match_score=60,
        data_confidence=50,
        signals=(
            ObservedSignal(
                signal_id="api_integration",
                label="APIs",
                responsibility=None,
                prominence=Prominence.SECONDARY,
                hits=(
                    SignalHit(
                        signal_id="api_integration",
                        label="APIs",
                        field_name="description",
                        pattern="rest api",
                        quote=quote,
                        char_start=0,
                        char_end=len(quote),
                    ),
                ),
            ),
        ),
        confidence_items=(
            ConfidenceItem(
                # Imported, never spelled. This test used to hard-code an id
                # that matched the filter constant and nothing the scorer
                # actually emits, so it agreed with the defect it was guarding.
                item_id=SALARY_CONFIDENCE_ITEM,
                label="Compensation stated",
                points=10,
                awarded=awarded,
            ),
        ),
        computed_at="2026-09-04T00:00:00Z",
    )


# =========================================================================
# MEDIUM: posting text could steer a deterministic filter
# =========================================================================


def test_a_posting_cannot_forge_its_way_into_the_salary_filter(db) -> None:
    """The evidence blob and the filter index are now different columns.

    A description containing the literal awarded-item tag
    ended up inside `result_json` as an evidence quote, and the `has_salary`
    filter was a LIKE over that blob. So a posting could assert its own
    salary-ness in prose. Employer text is evidence the system reasons about,
    never an instruction it obeys.
    """
    poisoned = f"We pay well |award:{SALARY_CONFIDENCE_ITEM}| honestly."
    dirty = _seed_job(db, external="dirty", description=poisoned)
    clean = _seed_job(db, external="clean", description="An ordinary posting about APIs.")

    with transaction(db):
        repo = MatchRepo(db)
        repo.store(
            dirty,
            JobRawRepo(db).put(poisoned),
            _result(quote=poisoned, awarded=False),
            config_digest=DIGEST,
        )
        repo.store(
            clean,
            JobRawRepo(db).put("x"),
            _result(quote="APIs", awarded=True),
            config_digest=DIGEST,
        )

    query = ScoredJobQuery(db)
    with_salary = {j.job_id for j in query.page("personal-alpha", 1, JobFilter(has_salary=True))}
    without = {j.job_id for j in query.page("personal-alpha", 1, JobFilter(has_salary=False))}

    assert with_salary == {clean}, "the forged tag must not satisfy has_salary"
    assert without == {dirty}, "and the forging posting must not be excluded"


def test_the_membership_index_contains_no_posting_text(db) -> None:
    poisoned = "|fired:crm_architecture| is written right here in the description."
    job_id = _seed_job(db, external="p", description=poisoned)
    with transaction(db):
        MatchRepo(db).store(
            job_id,
            JobRawRepo(db).put(poisoned),
            _result(quote=poisoned, awarded=False),
            config_digest=DIGEST,
        )
    stored = db.execute("SELECT membership, result_json FROM job_match").fetchone()
    assert "crm_architecture" not in stored["membership"], "posting text reached the index"
    assert poisoned in stored["result_json"], "but the quote is still stored for display"


def test_membership_is_built_from_identifiers_only() -> None:
    built = membership_of(_result(quote=f"|award:{SALARY_CONFIDENCE_ITEM}|", awarded=False))
    assert SALARY_CONFIDENCE_ITEM not in built
    assert "api_integration" in built


# =========================================================================
# MEDIUM: an omitted date meant "clear it", not "leave it alone"
# =========================================================================


def test_being_rejected_after_applying_does_not_erase_the_applied_date(db) -> None:
    """The most ordinary transition there is: you applied, and they said no."""
    job_id = _seed_job(db, external="r", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-01-15")
    repo.set_status(job_id, ApplicationStatus.REJECTED)

    row = repo.get(job_id)
    assert row is not None
    status, applied_at, _saved, _notes = row
    assert status is ApplicationStatus.REJECTED
    assert applied_at == "2026-01-15", "the date the person applied was silently lost"
    assert has_applied(status, applied_at) is True


def test_archiving_an_applied_job_keeps_the_date(db) -> None:
    job_id = _seed_job(db, external="a", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-02-02")
    repo.set_status(job_id, ApplicationStatus.ARCHIVED)
    row = repo.get(job_id)
    assert row is not None and row[1] == "2026-02-02"


def test_stepping_back_before_applying_keeps_the_date_and_a_person_can_clear_it(
    db,
) -> None:
    """The intent of the original test survives; its mechanism does not.

    THE ORIGINAL, from a previous independent review, asserted that stepping
    back to SHORTLISTED cleared the date, under the heading "keeping a date
    must not become never clearing one". That worry is real and this test still
    carries it -- the second half below is exactly it. What changed is which
    action does the clearing.

    A status move is the WRONG action for it. It is frequent, it is one drag on
    the board, and it says nothing about whether an application was sent; using
    it to delete the date meant the most expensive fact this product stores was
    destroyed by its most casual gesture, silently. ADR-0012 splits the two:
    the status says where this is now, `set_applied_at` says whether something
    was sent, and only the second can remove a date.

    So: a backward move keeps it, and an explicit clear still works. "Never
    clearing one" never becomes true.
    """
    job_id = _seed_job(db, external="b", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-03-03")
    repo.set_status(job_id, ApplicationStatus.SHORTLISTED)

    row = repo.get(job_id)
    assert row is not None
    assert row[0] is ApplicationStatus.SHORTLISTED
    assert row[1] == "2026-03-03", "a backward move erased the date the person applied"
    assert has_applied(row[0], row[1]) is True

    # And the date is still removable -- by the action that means it.
    repo.set_applied_at(job_id, None)
    row = repo.get(job_id)
    assert row is not None
    assert row[1] is None
    assert has_applied(row[0], row[1]) is False


def test_clearing_is_refused_where_the_status_would_put_the_date_straight_back(
    db,
) -> None:
    """APPLIED means an application was sent, so its date is not optional.

    Refusing is the honest answer. Accepting would write NULL and
    `normalise_application_state` would restore today's date on the next status
    write -- so the interface would report "cleared" and the database would
    hold a date, which is the shape of every defect in this area so far.
    """
    job_id = _seed_job(db, external="d", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-03-03")

    with pytest.raises(ApplicationDateRefused) as refusal:
        repo.set_applied_at(job_id, None)
    assert "Move the status first" in str(refusal.value)

    row = repo.get(job_id)
    assert row is not None and row[1] == "2026-03-03", "the refusal still wrote something"


def test_clearing_the_date_is_recorded_in_the_history(db) -> None:
    """Destroying a fact is itself a fact. The drawer shows what happened."""
    job_id = _seed_job(db, external="e", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-03-03")
    repo.set_status(job_id, ApplicationStatus.SHORTLISTED)
    repo.set_applied_at(job_id, None)

    history = repo.history(job_id)
    assert history[-1]["applied_at"] is None
    assert history[-1]["note"] == "applied date cleared"
    # The stage did not move, and the history says so rather than inventing a
    # transition that never happened.
    assert history[-1]["from_status"] == history[-1]["to_status"] == "SHORTLISTED"


def test_an_explicit_date_still_wins(db) -> None:
    job_id = _seed_job(db, external="c", description="x")
    repo = ApplicationRepo(db)
    repo.set_status(job_id, ApplicationStatus.APPLIED, applied_at="2026-04-04")
    repo.set_status(job_id, ApplicationStatus.INTERVIEW, applied_at="2026-05-05")
    row = repo.get(job_id)
    assert row is not None and row[1] == "2026-05-05"


# =========================================================================
# MEDIUM: a job with no raw row was invisible to every counter
# =========================================================================


def test_a_job_without_description_text_is_counted_rather_than_dropped(db) -> None:
    """An inner join reported under-coverage as a clean run.

    Three jobs in, two considered, zero errors, and nothing saying the third
    existed. A run that silently skips work and calls itself complete is worse
    than one that fails.
    """
    from career_agent.pipeline.rescore import rescore

    _seed_job(db, external="has-text", description="You will build REST API integrations.")
    with transaction(db):
        company_id = CompanyRepo(db).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(db).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="manual_import",
                board_identifier="acme",
                board_url="manual://acme",
                active=False,
            )
        )
        # No content_hash at all: collected before its text arrived.
        JobRepo(db).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="manual_import",
                external_id="no-text",
                url="manual://no-text",
                title="Mystery Role",
                content_hash=None,
            ),
            status=CollectionStatus.DISCOVERED,
        )

    config, _ = load_search_config(CONFIG_DIR)
    stats = rescore(db, config)

    assert stats.jobs_considered == 2, "every open job must be counted"
    assert stats.jobs_scored == 1
    assert stats.jobs_skipped_no_description == 1, "the textless job must be reported, not dropped"


# =========================================================================
# MEDIUM: a second writer failed instantly instead of waiting
# =========================================================================


def test_a_second_writer_waits_rather_than_failing_instantly(tmp_path: Path) -> None:
    """`BEGIN IMMEDIATE`, not deferred.

    A deferred transaction that reads before writing can lose its snapshot and
    fail with SQLITE_BUSY_SNAPSHOT -- which SQLite does NOT retry through the
    busy handler, so `busy_timeout` never applies. In practice: a rescore
    running while the UI saves a status would lose the edit behind a 500.
    """
    import time

    path = tmp_path / "concurrent.db"
    first = connect(path)
    migrate(first)
    job_id = _seed_job(first, external="c1", description="x")

    second = connect(path)
    second.execute("PRAGMA busy_timeout = 1500")

    with transaction(first):
        first.execute("UPDATE job SET title = 'held' WHERE id = ?", (job_id,))
        started = time.perf_counter()
        with pytest.raises(sqlite3.OperationalError), transaction(second):
            second.execute("UPDATE job SET title = 'other' WHERE id = ?", (job_id,))
        waited_ms = (time.perf_counter() - started) * 1000

    # The point is that it WAITED. A deferred BEGIN failed in well under a
    # millisecond because the busy handler was never consulted.
    assert waited_ms > 500, f"the second writer gave up after {waited_ms:.1f} ms without waiting"
    first.close()
    second.close()


# =========================================================================
# LOW: a user error was indistinguishable from a server bug
# =========================================================================


def test_a_too_short_import_says_why_instead_of_500(tmp_path: Path) -> None:
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ApiError, ServerConfig

    api = JobsApi(ServerConfig(db_path=tmp_path / "i.db", config_dir=CONFIG_DIR), quiet=True)
    migrate(connect(tmp_path / "i.db"))

    with pytest.raises(ApiError) as exc:
        api.handle_api(
            "POST",
            "/api/import",
            {},
            {"title": "T", "company": "C", "description": "too short"},
        )
    assert exc.value.status == 400
    assert "short" in exc.value.message.lower(), "the actionable message must survive"


def test_an_absurdly_long_description_is_refused(tmp_path: Path) -> None:
    """`notes` was capped at 20,000 while the far larger field was not."""
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ApiError, ServerConfig

    api = JobsApi(ServerConfig(db_path=tmp_path / "j.db", config_dir=CONFIG_DIR), quiet=True)
    with pytest.raises(ApiError) as exc:
        api.handle_api(
            "POST",
            "/api/import",
            {},
            {"title": "T", "company": "C", "description": "a" * 200_001},
        )
    assert exc.value.status == 400


# =========================================================================
# MEDIUM: the local prompt's immutability was never actually tested
# =========================================================================


def test_a_mutated_local_prompt_is_refused_at_load(monkeypatch: pytest.MonkeyPatch) -> None:
    """`PROMPT_DIGESTS` covered the hosted prompts and not the one that runs.

    CLAUDE.md's rule is that a prompt version used for a live request is
    immutable: editing it in place must become a new version rather than
    silently answering under the old one's name. That was enforced in code and
    asserted nowhere for `local_enrichment_v1`, which is the only prompt this
    branch actually sends.
    """
    from career_agent.local_ai import prompt as prompt_module

    monkeypatch.setattr(prompt_module, "_PROMPT_BYTES", b"a different question entirely")
    with pytest.raises(prompt_module.LocalPromptChanged) as exc:
        prompt_module.verify_prompt_digest()
    assert "local_enrichment_v1" in str(exc.value)
    assert "new version" in str(exc.value)


def test_building_messages_verifies_the_digest_every_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a one-off check at import: every build re-verifies."""
    from career_agent.local_ai import prompt as prompt_module

    prompt_module.build_messages("T", "C", "a description", "a profile")  # baseline
    monkeypatch.setattr(prompt_module, "_PROMPT_BYTES", b"tampered")
    with pytest.raises(prompt_module.LocalPromptChanged):
        prompt_module.build_messages("T", "C", "a description", "a profile")
