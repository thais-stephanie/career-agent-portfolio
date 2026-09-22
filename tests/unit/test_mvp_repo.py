"""Migration 0011 and the three repositories that read and write it.

The two tests that carry real weight here are the round trip -- a fully
populated `MatchResult` must come back as itself, enums as enums and tuples as
tuples, or the "why 68?" panel is showing a different result from the one that
was scored -- and `count(f) == len(page(f))`, which is the machine-checkable
form of "the Cards view and the Table view are one query".
"""

import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from career_agent.clock import is_valid_id
from career_agent.domain.application import ApplicationStatus
from career_agent.domain.enums import (
    AnalysisConfidence,
    EligibilityStatus,
    FitBand,
    GateResult,
    Prominence,
    ResponsibilityCategory,
    ScreeningState,
    Seniority,
    SenioritySource,
)
from career_agent.domain.matching import (
    ConfidenceItem,
    GateOutcome,
    MatchResult,
    ObservedSignal,
    Penalty,
    ScoreComponent,
    ScoreContribution,
    SeniorityReading,
    SignalHit,
    TitleAdjustment,
    TitleClass,
    TitleClassification,
)
from career_agent.storage.db import (
    MIGRATIONS_DIR,
    applied_versions,
    connect,
    migrate,
    schema_version,
    table_names,
)
from career_agent.storage.mvp_repo import (
    MAX_SIBLING_LOCATIONS,
    SALARY_CONFIDENCE_ITEM,
    SET_VALUED_FACETS,
    ApplicationRepo,
    JobFilter,
    MatchRepo,
    ScoredJobQuery,
    SortKeyError,
    deserialise_match,
    serialise_match,
)
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

CONFIG_ID = "demo_candidate_v1"
CONFIG_VERSION = 3
MVP_TABLES = {"job_match", "job_application", "job_application_event"}


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    yield connection
    connection.close()


# =====================================================================
# FIXTURE BUILDERS
# =====================================================================


#: `MatchRepo.store` now REQUIRES the configuration bytes a score was
#: computed from. A default let a caller silently record "UNRECORDED",
#: which defeats the drift check the digest exists for.
DIGEST = "a" * 64


def seed_job(
    conn: sqlite3.Connection, *, slug: str = "acme", external_id: str = "1", **job_kwargs
) -> tuple[str, str]:
    """Insert the minimum real rows a `job_match` foreign key needs.

    Built through the existing repositories rather than by hand-written INSERTs,
    so this test cannot pass against a schema the production write path would
    be rejected by.
    """
    company_id = CompanyRepo(conn).upsert(CompanyRecord(slug=slug, name=slug.title()))
    board_id = SourceBoardRepo(conn).upsert(
        SourceBoardRecord(
            company_id=company_id, provider="greenhouse", board_identifier=f"{slug}-board"
        )
    )
    text = job_kwargs.pop("description_text", f"Own the {slug} HubSpot instance end to end.")
    content_hash = JobRawRepo(conn).put(text)
    job_id = JobRepo(conn).upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id=external_id,
            url=f"https://example.test/{slug}/{external_id}",
            title=job_kwargs.pop("title", "Revenue Operations Engineer"),
            location_raw=job_kwargs.pop("location_raw", "Remote - Brazil"),
            posted_at=job_kwargs.pop("posted_at", "2026-09-01T00:00:00Z"),
            content_hash=content_hash,
        )
    )
    return job_id, content_hash


def a_full_result(**overrides: object) -> MatchResult:
    """A `MatchResult` with every optional structure populated.

    Deliberately not a minimal one: the round trip is only worth asserting over
    a value that exercises every nested dataclass, every enum and every tuple.
    """
    hit = SignalHit(
        signal_id="crm_administration",
        label="CRM administration",
        field_name="description",
        pattern="own our salesforce",
        quote="You will own our Salesforce instance, end to end.",
        char_start=120,
        char_end=169,
        negated=False,
        section="Responsibilities",
    )
    negated_hit = SignalHit(
        signal_id="people_management",
        label="People management",
        field_name="description",
        pattern="direct reports",
        quote="This role has no direct reports.",
        char_start=400,
        char_end=432,
        negated=True,
        section=None,
    )
    defaults: dict[str, object] = {
        "config_id": CONFIG_ID,
        "config_version": CONFIG_VERSION,
        "schema_version": 1,
        "match_score": 68,
        "data_confidence": 74,
        "eligibility_status": EligibilityStatus.LIKELY_ELIGIBLE,
        "screening_state": ScreeningState.NOT_BLOCKED,
        "screening_reason": "no blocker fired",
        "fit_band": FitBand.GOOD,
        "analysis_confidence": AnalysisConfidence.MEDIUM,
        "title": TitleClassification(
            base_class=TitleClass.CONDITIONAL,
            resolved_class=TitleClass.PRIMARY,
            adjustment=TitleAdjustment.PROMOTED,
            rule_id="ops_engineer",
            rule_label="Operations engineer",
            ambiguity_rule="requires_systems_ownership",
            reason="Promoted: the description owns a CRM.",
            supporting_signals=("crm_administration", "workflow_automation"),
        ),
        "components": (
            ScoreComponent(
                component_id="responsibility_alignment",
                label="Responsibility alignment",
                points=20.0,
                max_points=20.0,
                contributions=(
                    ScoreContribution(
                        signal_id="crm_administration",
                        label="CRM administration",
                        prominence=Prominence.PRIMARY,
                        weight=8.0,
                        points=12.0,
                        quote=hit.quote,
                    ),
                    ScoreContribution(
                        signal_id="workflow_automation",
                        label="Workflow automation",
                        prominence=Prominence.SECONDARY,
                        weight=6.0,
                        points=8.0,
                        quote=None,
                    ),
                ),
                capped=True,
                note="capped at 20/20",
            ),
        ),
        "penalties": (
            Penalty(
                signal_id="people_management",
                label="People management",
                prominence=Prominence.INCIDENTAL,
                weight=4.0,
                points=-1.5,
                quote=negated_hit.quote,
            ),
        ),
        "penalty_total": -1.5,
        "gates": (
            GateOutcome(
                gate="geography",
                result=GateResult.UNRESOLVED,
                reason="The posting never says where it hires.",
                blocker_id=None,
                quote=None,
                char_start=None,
                char_end=None,
            ),
            GateOutcome(
                gate="language",
                result=GateResult.PASS,
                reason="English stated as the working language.",
                blocker_id="lang_en",
                quote="Our working language is English.",
                char_start=10,
                char_end=42,
            ),
        ),
        "confidence_items": (
            ConfidenceItem(
                item_id=SALARY_CONFIDENCE_ITEM,
                label="Compensation stated",
                points=10,
                awarded=True,
                note="range given in BRL",
            ),
            ConfidenceItem(
                item_id="hiring_scope_stated",
                label="Hiring scope stated",
                points=15,
                awarded=False,
                note=None,
            ),
        ),
        "signals": (
            ObservedSignal(
                signal_id="crm_administration",
                label="CRM administration",
                responsibility=ResponsibilityCategory.CRM_ADMINISTRATION,
                prominence=Prominence.PRIMARY,
                hits=(hit,),
                negated_hits=(),
            ),
            ObservedSignal(
                signal_id="people_management",
                label="People management",
                responsibility=ResponsibilityCategory.PEOPLE_MANAGEMENT,
                prominence=Prominence.INCIDENTAL,
                hits=(),
                negated_hits=(negated_hit,),
            ),
        ),
        "unknowns": ("The posting never states where it hires.",),
        "seniority": SeniorityReading(
            value=Seniority.SENIOR,
            source=SenioritySource.TITLE_GRADE,
            confidence=AnalysisConfidence.HIGH,
            evidence="Senior Systems Analyst",
        ),
        "computed_at": "2026-09-04T12:00:00Z",
    }
    defaults.update(overrides)
    return MatchResult(**defaults)  # type: ignore[arg-type]


# =====================================================================
# THE MIGRATION
# =====================================================================


def _staged_up_to(tmp_path: Path, highest: int) -> Path:
    """A migrations directory holding 0001..`highest` and nothing after it."""
    staged = tmp_path / f"migrations_{highest}"
    staged.mkdir()
    for migration in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(migration.stem.split("_", 1)[0]) <= highest:
            shutil.copy(migration, staged / migration.name)
    return staged


def test_0011_applies_on_a_fresh_database(conn: sqlite3.Connection) -> None:
    """Asserted as membership rather than as the head version. Pinning the
    literal would make every future migration break this test for no reason --
    the same argument test_db.py makes about ALL_VERSIONS."""
    assert 11 in applied_versions(conn)
    assert schema_version(conn) >= 11
    assert set(table_names(conn)) >= MVP_TABLES


def test_0011_applies_on_a_database_already_at_10(tmp_path: Path) -> None:
    """The real upgrade path: an existing corpus gains three tables and loses
    nothing. Replayed against directories cut at 10 and at 11, so the assertion
    stays exact however many migrations land after this one."""
    connection = connect(tmp_path / "career.db")
    try:
        migrate(connection, _staged_up_to(tmp_path, 10))
        assert schema_version(connection) == 10
        assert not MVP_TABLES & set(table_names(connection))

        applied = migrate(connection, _staged_up_to(tmp_path, 11))
        assert [m.version for m in applied] == [11]
        assert schema_version(connection) == 11
        assert set(table_names(connection)) >= MVP_TABLES
        # Nothing that already existed was rewritten away.
        assert {"job", "job_raw", "company", "fingerprint", "llm_call"} <= set(
            table_names(connection)
        )
    finally:
        connection.close()


def test_job_application_has_no_applied_boolean(conn: sqlite3.Connection) -> None:
    """`has_applied` is derived in domain/application.py. A column here would
    be a third fact able to disagree with the two it is derived from."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_application)")}
    assert "applied" not in columns
    assert {"status", "applied_at", "saved", "notes"} <= columns


def test_check_constraints_refuse_an_invented_status(conn: sqlite3.Connection) -> None:
    job_id, _ = seed_job(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO job_application"
            " (id, job_id, status, saved, created_at, updated_at)"
            " VALUES ('a', ?, 'MAYBE_LATER', 0, '2026-09-04T00:00:00Z', '2026-09-04T00:00:00Z')",
            (job_id,),
        )


# =====================================================================
# SERIALISATION
# =====================================================================


def test_match_result_survives_a_round_trip_unchanged() -> None:
    """Every nested structure, every enum, every tuple. Equality on frozen
    dataclasses is structural, so this compares the whole graph."""
    original = a_full_result()
    restored = deserialise_match(serialise_match(original))

    assert restored == original
    assert isinstance(restored.eligibility_status, EligibilityStatus)
    assert isinstance(restored.seniority, SeniorityReading)
    # The provenance survives the round trip, not just the level: a stored
    # result that forgot WHERE its level came from would be a DEFAULT and an
    # evidenced reading looking identical on the way back out.
    assert restored.seniority.source is SenioritySource.TITLE_GRADE
    assert restored.seniority.evidence == "Senior Systems Analyst"
    assert restored.title is not None
    assert isinstance(restored.title.base_class, TitleClass)
    assert isinstance(restored.components, tuple)
    assert isinstance(restored.components[0].contributions, tuple)
    assert isinstance(restored.components[0].contributions[0].prominence, Prominence)
    assert isinstance(restored.signals[0].hits, tuple)
    assert restored.signals[0].responsibility is ResponsibilityCategory.CRM_ADMINISTRATION
    assert isinstance(restored.gates[0].result, GateResult)
    assert restored.unknowns == original.unknowns

    # The derived properties, which is what the UI actually renders.
    assert restored.blockers == original.blockers
    assert restored.matched_strengths == original.matched_strengths


def test_serialisation_is_byte_stable() -> None:
    assert serialise_match(a_full_result()) == serialise_match(a_full_result())


def test_a_minimal_result_round_trips_too() -> None:
    """Every optional structure empty. The defaults are the common case for a
    posting that said almost nothing."""
    minimal = MatchResult(config_id=CONFIG_ID, config_version=CONFIG_VERSION)
    assert deserialise_match(serialise_match(minimal)) == minimal


# =====================================================================
# MatchRepo
# =====================================================================


def test_store_returns_a_ulid_and_reads_back_equal(conn: sqlite3.Connection) -> None:
    job_id, content_hash = seed_job(conn)
    row_id = MatchRepo(conn).store(job_id, content_hash, a_full_result(), config_digest=DIGEST)

    assert is_valid_id(row_id)
    assert MatchRepo(conn).get(job_id, CONFIG_ID, CONFIG_VERSION) == a_full_result()


def test_storing_the_same_job_and_version_twice_updates_rather_than_duplicates(
    conn: sqlite3.Connection,
) -> None:
    job_id, content_hash = seed_job(conn)
    repo = MatchRepo(conn)
    first = repo.store(job_id, content_hash, a_full_result(), config_digest=DIGEST)
    second = repo.store(
        job_id,
        content_hash,
        a_full_result(match_score=91, fit_band=FitBand.STRONG),
        config_digest=DIGEST,
    )

    assert first == second, "the upsert must keep the row identity stable"
    assert repo.count_for(CONFIG_ID, CONFIG_VERSION) == 1
    stored = repo.get(job_id, CONFIG_ID, CONFIG_VERSION)
    assert stored is not None
    assert stored.match_score == 91
    scalar = conn.execute("SELECT match_score, fit_band FROM job_match").fetchone()
    assert (scalar["match_score"], scalar["fit_band"]) == (91, "STRONG")


def test_a_new_config_version_is_a_new_row(conn: sqlite3.Connection) -> None:
    job_id, content_hash = seed_job(conn)
    repo = MatchRepo(conn)
    repo.store(job_id, content_hash, a_full_result(), config_digest=DIGEST)
    repo.store(
        job_id, content_hash, a_full_result(config_version=4, match_score=12), config_digest=DIGEST
    )

    assert repo.count_for(CONFIG_ID, CONFIG_VERSION) == 1
    assert repo.count_for(CONFIG_ID, 4) == 1
    older = repo.get(job_id, CONFIG_ID, CONFIG_VERSION)
    assert older is not None and older.match_score == 68


def test_delete_for_removes_one_version_and_leaves_the_other(conn: sqlite3.Connection) -> None:
    job_id, content_hash = seed_job(conn)
    repo = MatchRepo(conn)
    repo.store(job_id, content_hash, a_full_result(), config_digest=DIGEST)
    repo.store(job_id, content_hash, a_full_result(config_version=4), config_digest=DIGEST)

    assert repo.delete_for(CONFIG_ID, CONFIG_VERSION) == 1
    assert repo.count_for(CONFIG_ID, CONFIG_VERSION) == 0
    assert repo.count_for(CONFIG_ID, 4) == 1


def test_get_returns_none_for_an_unscored_job(conn: sqlite3.Connection) -> None:
    job_id, _ = seed_job(conn)
    assert MatchRepo(conn).get(job_id, CONFIG_ID, CONFIG_VERSION) is None


# =====================================================================
# ApplicationRepo
# =====================================================================


def test_get_is_none_until_the_job_is_edited(conn: sqlite3.Connection) -> None:
    """None means 'nobody has touched this', which is not the same fact as
    'this is at DISCOVERED with no date'."""
    job_id, _ = seed_job(conn)
    assert ApplicationRepo(conn).get(job_id) is None


def test_set_status_appends_exactly_one_event_per_move(conn: sqlite3.Connection) -> None:
    job_id, _ = seed_job(conn)
    repo = ApplicationRepo(conn)

    repo.set_status(job_id, ApplicationStatus.SHORTLISTED, now="2026-09-01T09:00:00Z")
    repo.set_status(job_id, ApplicationStatus.TO_APPLY, now="2026-09-02T09:00:00Z")
    repo.set_status(job_id, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z", note="sent")

    history = repo.history(job_id)
    assert [(e["from_status"], e["to_status"]) for e in history] == [
        (None, "SHORTLISTED"),
        ("SHORTLISTED", "TO_APPLY"),
        ("TO_APPLY", "APPLIED"),
    ]
    assert history[-1]["note"] == "sent"
    assert history[-1]["applied_at"] == "2026-09-03"
    assert conn.execute("SELECT COUNT(*) FROM job_application").fetchone()[0] == 1

    state = repo.get(job_id)
    assert state == (ApplicationStatus.APPLIED, "2026-09-03", False, None)


def test_stepping_back_keeps_the_date_and_the_history_says_so(
    conn: sqlite3.Connection,
) -> None:
    """The history is what makes this checkable later, so it carries the date.

    Replaces a test that asserted the date became NULL on a backward move.
    ADR-0012: the status says where this is now, `applied_at` says an
    application was sent, and only `set_applied_at` can retract the second.
    """
    job_id, _ = seed_job(conn)
    repo = ApplicationRepo(conn)
    repo.set_status(job_id, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")
    repo.set_status(job_id, ApplicationStatus.SHORTLISTED, now="2026-09-04T09:00:00Z")

    assert repo.get(job_id) == (ApplicationStatus.SHORTLISTED, "2026-09-03", False, None)
    assert repo.history(job_id)[-1]["applied_at"] == "2026-09-03"


def test_an_explicit_clear_removes_the_date_without_moving_the_status(
    conn: sqlite3.Connection,
) -> None:
    """The other half: a date can still be removed, by the action that means it."""
    job_id, _ = seed_job(conn)
    repo = ApplicationRepo(conn)
    repo.set_status(job_id, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")
    repo.set_status(job_id, ApplicationStatus.SHORTLISTED, now="2026-09-04T09:00:00Z")
    repo.set_applied_at(job_id, None, now="2026-09-05T09:00:00Z")

    assert repo.get(job_id) == (ApplicationStatus.SHORTLISTED, None, False, None)
    last = repo.history(job_id)[-1]
    assert last["applied_at"] is None
    assert last["from_status"] == last["to_status"] == "SHORTLISTED"


def test_set_saved_creates_no_event_and_disturbs_no_status(conn: sqlite3.Connection) -> None:
    job_id, _ = seed_job(conn)
    repo = ApplicationRepo(conn)
    repo.set_status(job_id, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")

    repo.set_saved(job_id, True, now="2026-09-04T09:00:00Z")
    repo.set_notes(job_id, "recruiter replied", now="2026-09-04T10:00:00Z")

    assert repo.get(job_id) == (ApplicationStatus.APPLIED, "2026-09-03", True, "recruiter replied")
    assert len(repo.history(job_id)) == 1, "bookmarking is not a workflow move"


def test_set_saved_on_an_untouched_job_starts_it_at_discovered(conn: sqlite3.Connection) -> None:
    job_id, _ = seed_job(conn)
    repo = ApplicationRepo(conn)
    repo.set_saved(job_id, True, now="2026-09-04T09:00:00Z")

    assert repo.get(job_id) == (ApplicationStatus.DISCOVERED, None, True, None)
    assert repo.history(job_id) == []


def test_counts_by_status_counts_edited_jobs_only(conn: sqlite3.Connection) -> None:
    first, _ = seed_job(conn, slug="acme", external_id="1")
    second, _ = seed_job(conn, slug="beta", external_id="2")
    seed_job(conn, slug="gamma", external_id="3")
    repo = ApplicationRepo(conn)
    repo.set_status(first, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")
    repo.set_status(second, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")

    assert repo.counts_by_status() == {"APPLIED": 2}


# =====================================================================
# ScoredJobQuery
# =====================================================================


@pytest.fixture
def scored(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Four scored postings across three companies, varied on every axis the
    filters read."""
    matches = MatchRepo(conn)

    job_a, hash_a = seed_job(conn, slug="acme", external_id="1", title="RevOps Engineer")
    matches.store(
        job_a, hash_a, a_full_result(match_score=90, fit_band=FitBand.STRONG), config_digest=DIGEST
    )

    job_b, hash_b = seed_job(
        conn,
        slug="beta",
        external_id="2",
        title="Salesforce Administrator",
        location_raw="Sao Paulo, Brazil",
    )
    matches.store(
        job_b,
        hash_b,
        a_full_result(
            match_score=55,
            fit_band=FitBand.MODERATE,
            data_confidence=30,
            eligibility_status=EligibilityStatus.UNRESOLVED,
            confidence_items=(),
            signals=(),
        ),
        config_digest=DIGEST,
    )

    job_c, hash_c = seed_job(conn, slug="gamma", external_id="3", title="Data Engineer")
    matches.store(
        job_c,
        hash_c,
        a_full_result(match_score=20, fit_band=FitBand.WEAK, title=None),
        config_digest=DIGEST,
    )

    job_d, hash_d = seed_job(conn, slug="acme", external_id="4", title="Systems Analyst")
    matches.store(job_d, hash_d, a_full_result(match_score=70), config_digest=DIGEST)
    ApplicationRepo(conn).set_status(job_d, ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z")
    ApplicationRepo(conn).set_saved(job_d, True, now="2026-09-03T09:00:00Z")
    return conn


#: Filters that must agree between the counter and the list. Chosen to cover an
#: indexed predicate, a joined one, a LEFT JOIN default and the two containment
#: filters that no index can serve.
AGREEMENT_FILTERS = [
    JobFilter(limit=500),
    JobFilter(min_score=50, limit=500),
    JobFilter(companies=("acme",), limit=500),
    JobFilter(statuses=("DISCOVERED",), limit=500),
    JobFilter(saved_only=True, limit=500),
    JobFilter(signals=("crm_administration",), limit=500),
    JobFilter(has_salary=True, limit=500),
    JobFilter(search="salesforce", limit=500),
    JobFilter(fit_bands=("STRONG", "GOOD"), eligibility=("LIKELY_ELIGIBLE",), limit=500),
]


@pytest.mark.parametrize("job_filter", AGREEMENT_FILTERS, ids=lambda f: f.sort + repr(hash(f))[:6])
def test_count_and_page_agree(scored: sqlite3.Connection, job_filter: JobFilter) -> None:
    """One WHERE clause, two readers. This is the whole point of `_where`."""
    query = ScoredJobQuery(scored)
    assert query.count(CONFIG_ID, CONFIG_VERSION, job_filter) == len(
        query.page(CONFIG_ID, CONFIG_VERSION, job_filter)
    )


def test_the_filters_actually_select_something_different(scored: sqlite3.Connection) -> None:
    """A guard on the guard above: count == len(page) is trivially true if
    every filter returns everything."""
    query = ScoredJobQuery(scored)
    counts = {
        f.search or f.sort + str(f.min_score) + str(f.saved_only) + str(f.has_salary): query.count(
            CONFIG_ID, CONFIG_VERSION, f
        )
        for f in AGREEMENT_FILTERS
    }
    assert len(set(counts.values())) > 1
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter()) == 4
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(saved_only=True)) == 1
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(statuses=("APPLIED",))) == 1
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(statuses=("DISCOVERED",))) == 3
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(signals=("crm_administration",))) == 3
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(has_salary=False)) == 1


def test_page_is_sorted_and_truncates_the_description(scored: sqlite3.Connection) -> None:
    query = ScoredJobQuery(scored)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter())

    assert [row.result.match_score for row in rows if row.result] == [90, 70, 55, 20]
    assert all(len(row.description_text) <= 400 for row in rows)
    assert rows[0].result is not None, "the list carries the full result, not just the scalars"
    assert rows[0].company_slug == "acme"


def test_get_one_returns_the_untruncated_description(conn: sqlite3.Connection) -> None:
    long_text = "A" * 900
    job_id, content_hash = seed_job(conn, description_text=long_text)
    MatchRepo(conn).store(job_id, content_hash, a_full_result(), config_digest=DIGEST)
    query = ScoredJobQuery(conn)

    listed = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter())[0]
    detail = query.get_one(job_id, CONFIG_ID, CONFIG_VERSION)

    assert len(listed.description_text) == 400
    assert detail is not None
    assert detail.description_text == long_text
    assert detail.result == a_full_result()


def test_an_unedited_job_reads_as_discovered(scored: sqlite3.Connection) -> None:
    rows = ScoredJobQuery(scored).page(CONFIG_ID, CONFIG_VERSION, JobFilter(companies=("gamma",)))
    assert rows[0].application_status == "DISCOVERED"
    assert rows[0].saved is False
    assert rows[0].applied_at is None


#: Facets whose buckets partition the result set, so they must sum to
#: `count()`. That property is what lets the interface print a number beside a
#: chip and promise it.
#:
#: `country`, `region`, `signal` and `technology` are SETS: one posting can be
#: in two countries and fire six signals, so their buckets overlap by
#: construction and summing them is meaningless rather than wrong. They are
#: held to a different property -- every bucket equals what its own filter
#: returns -- in `tests/integration/test_filter_contract.py`.


def test_facets_totals_equal_the_row_count(scored: sqlite3.Connection) -> None:
    query = ScoredJobQuery(scored)
    total = query.count(CONFIG_ID, CONFIG_VERSION, JobFilter())
    facets = query.facets(CONFIG_ID, CONFIG_VERSION, JobFilter())

    # Named exhaustively rather than by pattern: a facet the interface draws a
    # control for and the repository stops emitting is a control that silently
    # goes blank, and this list is what makes that a failure.
    assert set(facets) == {
        "company",
        "provider",
        "title_class",
        "eligibility_status",
        "status",
        "fit_band",
        "worksite",
        "seniority",
        "employment_type",
        # Added V1.7, the day the column stopped being NULL on every row in
        # the real corpus: Gupy publishes the employer's own contract type,
        # and 37,368 postings resolved to CLT against 621 to PJ. It was an
        # accepted API parameter with a closed vocabulary and NO FACET for
        # four migrations, so no screen could offer it.
        "contract_regime",
        "salary_currency",
        "salary_period",
        "country",
        "region",
        "signal",
        "technology",
        # Added by migration 0027, on the day the product learned to tell
        # "3 years required" from "no experience necessary". Both are here
        # from the first commit rather than later: a control the panel draws
        # and the repository does not tally is a control that silently goes
        # blank, which is what the `technology` line above records.
        "experience_requirement",
        "entry_signal",
    }
    for name, buckets in facets.items():
        if name in SET_VALUED_FACETS:
            continue
        assert sum(buckets.values()) == total, name
    assert facets["company"]["acme"] == 2
    assert facets["status"]["DISCOVERED"] == 3


def test_posted_within_days_uses_a_python_cutoff(scored: sqlite3.Connection) -> None:
    """No SQL date function is involved: the cutoff is computed in Python and
    bound, and the timestamps are text that already sorts correctly."""
    query = ScoredJobQuery(scored)
    query.now = "2026-09-04T00:00:00Z"
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(posted_within_days=7)) == 4
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(posted_within_days=1)) == 0


def test_remote_only_reads_the_boards_own_location_string(scored: sqlite3.Connection) -> None:
    query = ScoredJobQuery(scored)
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(remote_only=True)) == 3


def test_a_bogus_sort_key_raises_rather_than_reaching_sql(scored: sqlite3.Connection) -> None:
    """A whitelist, not a sanitiser. The rejected value never becomes SQL at
    all -- including one shaped like an injection."""
    query = ScoredJobQuery(scored)
    with pytest.raises(SortKeyError, match="unknown sort key"):
        query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(sort="salary"))
    with pytest.raises(SortKeyError, match="unknown sort key"):
        query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(sort="j.title; DROP TABLE job"))
    with pytest.raises(SortKeyError, match="unknown sort direction"):
        query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(direction="sideways"))


def test_a_search_term_full_of_wildcards_is_taken_literally(scored: sqlite3.Connection) -> None:
    """`%` and `_` are LIKE metacharacters. A user typing them means them."""
    query = ScoredJobQuery(scored)
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(search="%")) == 0
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(search="_")) == 0


def test_limit_and_offset_page_without_dropping_or_repeating(scored: sqlite3.Connection) -> None:
    query = ScoredJobQuery(scored)
    first = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(limit=2, offset=0))
    second = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(limit=2, offset=2))

    ids = [row.job_id for row in first] + [row.job_id for row in second]
    assert len(set(ids)) == 4


# =====================================================================
# DUPLICATE GROUPING
#
# Employers publish ONE role as SEVERAL postings, one per location. Each is a
# genuinely distinct posting at the provider, so collection-level deduplication
# -- which keys on (provider, external_id) -- is correct and does not move.
# What follows tests the presentation-layer answer: at the review surface,
# collapse them to one representative row that SAYS what it stands for.
#
# Measured on the corpus at >= 55: 163 rows, 132 distinct (company, title),
# 31 surplus. Only 8 of those 31 are byte-identical in description text, which
# is why `content_hash` is not the key and `(company_id, title)` is.
# =====================================================================


@pytest.fixture
def duplicated(conn: sqlite3.Connection) -> tuple[sqlite3.Connection, list[str]]:
    """One role published four times, one published twice, one singleton.

    The scores are deliberately uneven in the first group and deliberately TIED
    in the second, so the two halves of the election rule -- highest score, then
    lowest `j.id` -- are each exercised by a group that needs it.

    Returns the tied ids alongside the connection, because "which of the two
    won" is the only fact a test cannot re-derive from the rows it reads back.
    """
    matches = MatchRepo(conn)
    for external_id, score, location in (
        ("w1", 61, "New York, NY"),
        ("w2", 88, "Austin, TX"),
        ("w3", 74, "Remote - US"),
        ("w4", 88, "Austin, TX"),  # same place as w2: locations must dedupe
    ):
        job_id, content_hash = seed_job(
            conn,
            slug="workato",
            external_id=external_id,
            title="Forward Deployed Engineer",
            location_raw=location,
            description_text=f"Deploy for customers in {location}. Own the integration stack.",
        )
        matches.store(job_id, content_hash, a_full_result(match_score=score), config_digest=DIGEST)

    # A tie on score, so only the id can break it.
    tied: list[str] = []
    for external_id in ("b1", "b2"):
        job_id, content_hash = seed_job(
            conn,
            slug="brex",
            external_id=external_id,
            title="Software Engineer, Agent Builder",
            location_raw="Remote - Brazil",
            description_text=f"Build agent tooling ({external_id}). Own the integration stack.",
        )
        matches.store(job_id, content_hash, a_full_result(match_score=70), config_digest=DIGEST)
        tied.append(job_id)

    job_id, content_hash = seed_job(
        conn, slug="xero", external_id="x1", title="Senior Engineer", location_raw="Melbourne, AU"
    )
    matches.store(job_id, content_hash, a_full_result(match_score=52), config_digest=DIGEST)
    return conn, sorted(tied)


def _ids(rows: list) -> list[str]:
    return [row.job_id for row in rows]


def test_grouping_is_off_by_default(duplicated: tuple[sqlite3.Connection, list[str]]) -> None:
    """The regression test that makes this change safe.

    Every caller written before grouping existed -- the CLI, the table view,
    every other test in this file -- must keep the population it has always
    had. A default of True would silently change what `rescore`, the API and
    the counters mean, with nothing on screen saying so.
    """
    conn, _ = duplicated
    assert JobFilter().group_duplicates is False
    query = ScoredJobQuery(conn)
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter()) == 7
    rows = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(limit=500))
    assert len(rows) == 7
    # And nothing was looked up: an ungrouped row is its own group, and paying
    # for a sibling query to be told so would be pure cost on the default path.
    assert {row.duplicate_count for row in rows} == {1}
    assert {row.sibling_locations for row in rows} == {()}


def test_grouping_keeps_the_highest_scoring_posting_of_each_role(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    grouped = JobFilter(group_duplicates=True, limit=500)

    assert query.count(CONFIG_ID, CONFIG_VERSION, grouped) == 3
    rows = query.page(CONFIG_ID, CONFIG_VERSION, grouped)
    assert [row.title for row in rows] == [
        "Forward Deployed Engineer",
        "Software Engineer, Agent Builder",
        "Senior Engineer",
    ]
    assert rows[0].result is not None
    assert rows[0].result.match_score == 88, "the group must be represented by its best row"


def test_a_tie_on_score_is_broken_by_the_lowest_job_id(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """Determinism, not preference. Two postings of one role usually carry the
    same text and therefore the same score; without a tiebreak SQLite could
    return a different representative between two runs of the same query, and
    the card would change identity under a reload with nothing having happened.
    ULIDs are time-sortable, so the lowest id is the one collected first."""
    conn, tied = duplicated
    query = ScoredJobQuery(conn)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500))
    brex = next(row for row in rows if row.company_slug == "brex")
    assert brex.job_id == tied[0]

    # And it is stable: the same query asked again elects the same row.
    again = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500))
    assert _ids(rows) == _ids(again)


def test_the_representative_carries_the_siblings_it_stands_for(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """Grouping HIDES NOTHING. A card that silently dropped three siblings
    would be worse than four cards, because the reader could not tell."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500))
    by_slug = {row.company_slug: row for row in rows}

    workato = by_slug["workato"]
    assert workato.duplicate_count == 4, "the count INCLUDES the representative"
    # Its own location leads; "Austin, TX" appears once although two postings
    # carry it, because these are the PLACES the role is open in.
    assert workato.sibling_locations == ("Austin, TX", "New York, NY", "Remote - US")

    assert by_slug["brex"].duplicate_count == 2
    assert by_slug["brex"].sibling_locations == ("Remote - Brazil",)


def test_the_location_list_is_capped_and_the_count_still_tells_the_truth(
    conn: sqlite3.Connection,
) -> None:
    """More siblings than `MAX_SIBLING_LOCATIONS`: the list is cut, the count is not.

    This is the arithmetic the card's "+N more" is made of, and it is asserted
    here rather than in the demo corpus on purpose. Reaching it on screen needs
    either thirteen postings of one role or a sibling with no location at all,
    and neither is something `evaluation/demo/` should carry as scenery -- a
    thirteen-city group would dominate every screenshot to prove one integer.

    The invariant the interface depends on is exactly this: `duplicate_count`
    counts postings, `sibling_locations` names as many as fit, and the
    difference is what the badge is allowed to call "+N more". If the count
    were capped too, the badge would say "12 locations" about a role published
    in fifteen and nothing on screen would admit it.
    """
    matches = MatchRepo(conn)
    places = [f"City {n:02d}" for n in range(MAX_SIBLING_LOCATIONS + 3)]
    for index, place in enumerate(places):
        job_id, content_hash = seed_job(
            conn,
            slug="everywhere",
            external_id=f"e{index}",
            title="Integration Engineer",
            location_raw=place,
            description_text=f"Own the integration stack from {place}.",
        )
        matches.store(job_id, content_hash, a_full_result(match_score=70), config_digest=DIGEST)

    rows = ScoredJobQuery(conn).page(
        CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500)
    )
    assert len(rows) == 1
    row = rows[0]

    assert row.duplicate_count == len(places), "the COUNT is never capped"
    assert len(row.sibling_locations) == MAX_SIBLING_LOCATIONS, "the LIST is"
    assert row.duplicate_count - len(row.sibling_locations) == 3, (
        "three postings are unnamed, which is what the badge reports as '+3 more'"
    )
    assert row.sibling_locations[0] == row.location_raw, "its own place survives the cut"


def test_a_sibling_with_no_location_is_counted_and_not_invented(
    conn: sqlite3.Connection,
) -> None:
    """The other way "+N more" is reached: a posting the board gave no place.

    Absence is never permission, and it is never a plausible guess either. The
    posting counts -- it exists, and a reader looking for every listing of this
    role needs to know it is there -- but nothing is put in the location list
    on its behalf.
    """
    matches = MatchRepo(conn)
    for external_id, location in (("s1", "Lisbon, PT"), ("s2", None)):
        job_id, content_hash = seed_job(
            conn,
            slug="quiet",
            external_id=external_id,
            title="Automation Engineer",
            location_raw=location,
            description_text=f"Own the integration stack ({external_id}).",
        )
        matches.store(job_id, content_hash, a_full_result(match_score=70), config_digest=DIGEST)

    rows = ScoredJobQuery(conn).page(
        CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500)
    )
    assert len(rows) == 1
    assert rows[0].duplicate_count == 2
    assert rows[0].sibling_locations == ("Lisbon, PT",)


def test_a_singleton_reports_itself_and_no_siblings(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """`1` and `()`, never `0`: a posting always counts itself, and "this
    stands for nothing else" has to be readable by the interface without a
    second field to consult.

    The tuple is empty even though this posting HAS a location, because the
    locations are what a grouped row discloses and a singleton discloses
    nothing -- its own place is already on the card under "Where"."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    rows = query.page(
        CONFIG_ID, CONFIG_VERSION, JobFilter(companies=("xero",), group_duplicates=True)
    )
    assert len(rows) == 1
    assert rows[0].location_raw == "Melbourne, AU"
    assert rows[0].duplicate_count == 1
    assert rows[0].sibling_locations == ()


def test_get_one_reports_the_group_even_though_it_took_no_filter(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The drawer is where the person asks what they are looking at. A posting
    that shares its title with three others is worth saying whether or not the
    list that led here was grouped."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    grouped = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500))
    workato = next(row for row in grouped if row.company_slug == "workato")

    detail = query.get_one(workato.job_id, CONFIG_ID, CONFIG_VERSION)
    assert detail is not None
    assert detail.duplicate_count == 4
    assert detail.sibling_locations == workato.sibling_locations


def test_count_page_and_facets_agree_under_grouping(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The invariant the `facets` docstring claims, asserted with the extra
    clause in play. All three derive from `_where`, which is exactly why the
    clause lives there and not in `page`."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    for base in (
        JobFilter(group_duplicates=True, limit=500),
        JobFilter(group_duplicates=True, min_score=60, limit=500),
        JobFilter(group_duplicates=True, companies=("workato", "brex"), limit=500),
        JobFilter(group_duplicates=True, search="integration stack", limit=500),
    ):
        total = query.count(CONFIG_ID, CONFIG_VERSION, base)
        assert total == len(query.page(CONFIG_ID, CONFIG_VERSION, base)), base
        for name, buckets in query.facets(CONFIG_ID, CONFIG_VERSION, base).items():
            if name in SET_VALUED_FACETS:
                continue
            assert sum(buckets.values()) == total, (name, base)


def test_grouping_actually_changes_the_population(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """A guard on the guard above: count == len(page) is trivially true if the
    grouped clause selects everything."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter()) == 7
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True)) == 3
    assert (
        query.count(
            CONFIG_ID, CONFIG_VERSION, JobFilter(companies=("workato",), group_duplicates=True)
        )
        == 1
    )


def test_pagination_under_grouping_neither_drops_nor_repeats(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    whole = _ids(query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500)))
    paged: list[str] = []
    for offset in (0, 1, 2):
        paged += _ids(
            query.page(
                CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=1, offset=offset)
            )
        )
    assert paged == whole
    assert len(set(paged)) == 3


def test_grouping_keys_on_company_and_title_not_on_content_hash(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The measurement that chose the key: on the corpus only 8 of 31 surplus
    rows are byte-identical, because the rest differ by a location line.

    The Workato group here has the same shape -- four postings, three distinct
    description hashes, because two of them happen to name the same city.
    Keying on `content_hash` would collapse that one accidental pair and leave
    the other three as separate cards, which is the wrong answer arrived at
    silently. `(company_id, title)` collapses all four."""
    conn, _ = duplicated
    hashes = {
        row["content_hash"]
        for row in conn.execute(
            "SELECT DISTINCT jm.content_hash AS content_hash FROM job_match jm"
            " JOIN job j ON j.id = jm.job_id WHERE j.title = 'Forward Deployed Engineer'"
        )
    }
    assert len(hashes) == 3, "four postings, three texts -- the corpus's own shape"
    assert (
        ScoredJobQuery(conn).count(
            CONFIG_ID, CONFIG_VERSION, JobFilter(companies=("workato",), group_duplicates=True)
        )
        == 1
    )


def test_the_same_title_at_a_different_company_is_a_different_role(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The key is the PAIR. Two employers hiring a "Senior Engineer" are two
    roles, and collapsing them would be the defect this change exists to fix,
    pointed the other way."""
    conn, _ = duplicated
    job_id, content_hash = seed_job(
        conn, slug="clickhouse", external_id="c1", title="Senior Engineer"
    )
    MatchRepo(conn).store(job_id, content_hash, a_full_result(match_score=51), config_digest=DIGEST)
    query = ScoredJobQuery(conn)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(group_duplicates=True, limit=500))
    titles = [row.title for row in rows]
    assert titles.count("Senior Engineer") == 2
    assert {row.duplicate_count for row in rows if row.title == "Senior Engineer"} == {1}


def test_a_filter_the_best_sibling_fails_still_shows_the_role(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """Grouping must never make a MATCHING posting disappear.

    The Workato group's best row (88) sits in Austin; a remote sibling scores
    74. Elect the representative over the whole configuration version and
    `remote_only` deletes the role outright: the Austin row is filtered away
    and every remote sibling loses the election to it, so no row survives. That
    is not a duplicate being collapsed, it is a result being lost, and the
    reader cannot tell.

    So the election runs inside the FILTERED population. Measured on the corpus
    before the fix: `min_score=55 + remote_only` showed 18 of 20 roles, the
    eligibility filter 815 of 825, `search="forward deployed"` 377 of 379.
    `min_score` alone lost nothing, because it is the one filter that is
    monotone in the election key.
    """
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    remote = JobFilter(companies=("workato",), remote_only=True, limit=500)

    ungrouped = query.page(CONFIG_ID, CONFIG_VERSION, remote)
    assert [row.location_raw for row in ungrouped] == ["Remote - US"]

    grouped = query.page(CONFIG_ID, CONFIG_VERSION, replace(remote, group_duplicates=True))
    assert len(grouped) == 1, "the role vanished: the best sibling is not remote"
    assert grouped[0].job_id == ungrouped[0].job_id
    assert grouped[0].result is not None
    assert grouped[0].result.match_score == 74, "the best row THAT MATCHES represents the group"

    # And the three numbers still agree, with the filter inside the election.
    assert query.count(CONFIG_ID, CONFIG_VERSION, replace(remote, group_duplicates=True)) == 1


def test_the_election_population_is_the_filtered_one_not_the_whole_version(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """The general form of the test above, over a filter on workflow state --
    which is the one a person changes by hand, on one posting, without any
    thought about which sibling they were looking at."""
    conn, tied = duplicated
    # Save the SECOND of the two tied Brex postings. The first wins the tie, so
    # a version-wide election would elect it and `saved_only` would then match
    # nothing at all.
    ApplicationRepo(conn).set_saved(tied[1], True, now="2026-09-04T00:00:00Z")

    query = ScoredJobQuery(conn)
    saved = JobFilter(saved_only=True, group_duplicates=True, limit=500)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, saved)
    assert [row.job_id for row in rows] == [tied[1]]
    assert query.count(CONFIG_ID, CONFIG_VERSION, saved) == 1


def test_the_migration_indexes_the_pair_the_grouping_probes(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """Migration 0014. Both readers -- the NOT EXISTS election and the sibling
    lookup -- correlate on (company_id, title), and `idx_job_company` alone
    leaves the title half to a row fetch per candidate."""
    conn, _ = duplicated
    assert 14 in applied_versions(conn)
    indexes = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'job'"
        )
    }
    assert "idx_job_company_title" in indexes
    plan = "\n".join(
        str(row["detail"])
        for row in conn.execute(
            "EXPLAIN QUERY PLAN SELECT j.id FROM job_match jm JOIN job j ON j.id = jm.job_id"
            " WHERE jm.config_id = ? AND jm.config_version = ?"
            " AND (j.company_id = ? AND j.title = ?)",
            (CONFIG_ID, CONFIG_VERSION, "x", "y"),
        )
    )
    # Migration 0028 also lets SQLite start from the small scored population
    # and probe jobs by primary key. Both plans avoid a full table scan.
    assert "SCAN j" not in plan and "SCAN jm" not in plan, plan
    assert "idx_job_company_title" in plan or (
        "idx_job_match_population" in plan and "sqlite_autoindex_job_1" in plan
    ), plan


def test_discovery_counts_do_not_fetch_large_score_payloads(duplicated):
    """Cold production reads timed out while a broad source was refreshing."""
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    base = JobFilter(
        exempt_tracked=False,
        include_ineligible=False,
        include_unresolved=False,
        include_off_target=False,
        include_user_hidden=False,
        include_excluded_seniority=False,
        excluded_seniorities=("SENIOR",),
        limit=500,
    )
    # The default list and each hidden-count disclosure use these same fields.
    for field in (
        None,
        "include_ineligible",
        "include_unresolved",
        "include_off_target",
        "include_excluded_seniority",
        "include_user_hidden",
    ):
        chosen = replace(base, **{field: True}) if field else base
        where, params = query._where(CONFIG_ID, CONFIG_VERSION, chosen)
        sql = "SELECT COUNT(*)" + query._from_for(query._joins_needed(chosen)) + where
        plan = "\n".join(
            str(row["detail"]) for row in conn.execute("EXPLAIN QUERY PLAN " + sql, params)
        )
        assert "USING COVERING INDEX idx_job_match_discovery_gates" in plan, plan
        assert query.count(CONFIG_ID, CONFIG_VERSION, chosen) == len(
            query.page(CONFIG_ID, CONFIG_VERSION, chosen)
        )


def test_duplicate_count_never_claims_a_sibling_the_filter_excluded(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """`sum(duplicate_count)` must equal the UNGROUPED count, under every filter.

    Grouping collapses rows; it never removes a posting from the tally and
    never adds one. So the badge on the cards, summed, has to reconstruct the
    list the person would have seen with grouping off.

    This failed when the election ran over the filtered rows -- as it must, or
    a filtered-out best sibling loses the whole role -- while the sibling
    counter still counted over the whole configuration version. On the real
    corpus `remote_only` then returned 20 postings in 20 groups, nothing
    collapsed, and the counts summed to 23: three cards announcing a sibling
    that was not in the list and could not be reached from it.

    The two populations are one population now, and this is what says so.
    """
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    for base in (
        JobFilter(limit=500),
        JobFilter(min_score=60, limit=500),
        JobFilter(remote_only=True, limit=500),
        JobFilter(companies=("workato", "brex"), limit=500),
        JobFilter(search="integration stack", limit=500),
        JobFilter(eligibility=("VERIFIED_ELIGIBLE",), limit=500),
        JobFilter(saved_only=True, limit=500),
    ):
        ungrouped = query.count(CONFIG_ID, CONFIG_VERSION, base)
        rows = query.page(CONFIG_ID, CONFIG_VERSION, replace(base, group_duplicates=True))
        assert sum(row.duplicate_count for row in rows) == ungrouped, base


def test_the_drawer_counts_over_the_corpus_not_over_the_list(
    duplicated: tuple[sqlite3.Connection, list[str]],
) -> None:
    """`get_one` has no list context, so it answers the other question.

    A card reached from a filtered list says how many of the group are in that
    list. The drawer says how many exist -- "this is published more than once"
    is a fact about the posting, not about the filter that led here. The two
    numbers differ on purpose, and each is right about its own question.
    """
    conn, _ = duplicated
    query = ScoredJobQuery(conn)
    narrow = JobFilter(remote_only=True, group_duplicates=True, limit=500)
    rows = query.page(CONFIG_ID, CONFIG_VERSION, narrow)
    grouped = [row for row in rows if row.duplicate_count >= 1]
    assert grouped, "the fixture must offer a remote posting to reach the drawer with"

    for row in grouped:
        detail = query.get_one(row.job_id, CONFIG_ID, CONFIG_VERSION)
        assert detail is not None
        # Never fewer: the corpus cannot hold less than the filtered view did.
        assert detail.duplicate_count >= row.duplicate_count


def test_the_facets_query_does_not_build_a_row_object_per_row(scored: sqlite3.Connection) -> None:
    """THE ONE QUERY THAT READS EVERY MATCHING ROW, and the only one that cares.

    Measured 2026-09-09 on the real corpus at 103,010 rows: fetching five
    columns took 16.0 seconds with the connection's `sqlite3.Row` factory and
    3.1 without it, while READING the values afterwards took 0.12 seconds
    either way. Nearly thirteen seconds went into constructing row objects each
    of which is indexed once and discarded.

    V1.2 took this from 9,411ms to 1,431ms over 21,202 rows by making it ONE
    statement. The corpus is five times bigger now and the row factory is the
    cost that statement was hiding: 8.30s -> 1.33s on the same data.

    Asserted structurally, because the property is invisible from the outside:
    a slow correct answer and a fast correct answer are the same answer.
    """
    import re
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2] / "src" / "career_agent" / "storage" / "mvp_repo.py"
    ).read_text(encoding="utf-8")
    # `facets` is the last method in its class, so the slice ends at the next
    # TOP-LEVEL definition rather than at a named neighbour: a neighbour that
    # moves is a test that starts checking the wrong function without saying
    # so, and running to end-of-file swept in module functions that legitimately
    # index rows by name.
    start = source.index("    def facets(")
    rest = source[start:]
    following = re.search(r"\n(?:    def |def |@)", rest[1:])
    body = rest[: following.start() + 1] if following else rest

    assert "cursor.row_factory = None" in body, "facets() went back to sqlite3.Row"
    # COMMENTS STRIPPED FIRST. The method's own comment quotes the shape it
    # replaced -- `str(row["bucket"])` -- and a search over the raw text
    # matches the explanation rather than any code.
    code = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r'row\["', code), "facets() indexes by NAME again, which needs the factory"


def test_facets_leaves_the_connections_own_row_factory_alone(
    scored: sqlite3.Connection,
) -> None:
    """A cursor carries its own factory, so nothing else in this class changes
    behaviour. Setting it on the CONNECTION would have made every later read in
    the same request return bare tuples."""
    query = ScoredJobQuery(scored)
    query.facets(CONFIG_ID, CONFIG_VERSION, JobFilter())
    row = scored.execute("SELECT 1 AS one").fetchone()
    assert row["one"] == 1
