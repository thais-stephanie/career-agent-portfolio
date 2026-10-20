"""A deep reading in two halves (migration 0059, ADR-0035 addendum 5).

What a reading was ASKED ABOUT (intent and levels) and what it was SHOWN about
the person (evidence) are recorded apart. When only the evidence changed, the
work fit stands, and the recommendation stands only when it cannot move with
the evidence; the planner skips exactly those and reads the rest again. Every
stale reason is exercised against the planner and the reading's own state.
A row whose halves were never proven stays fully out of date. No provider is
reached.
"""

from __future__ import annotations

import pytest
from tests.integration.test_deep_run import FakeCli, add_claims
from tests.integration.test_semantic_run import corpus  # noqa: F401 - fixture

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.semantic import deep, deep_run
from career_agent.semantic.deep_run import backfill_digests, context, run_deep, select
from career_agent.semantic.intent import SearchIntent
from career_agent.storage.db import transaction
from career_agent.storage.repositories import ClaimRepo


class _App:
    def __init__(self, config) -> None:
        self._config = config

    def search_config(self):
        return self._config


def readings(config, conn) -> dict[str, dict]:
    from career_agent.web.deep_api import _review_rows

    return {r["job_id"]: r for r in _review_rows(_App(config), conn)}


def planned(conn, config) -> set[str]:
    chosen = select(
        conn,
        config,
        provider_id="fake_cli",
        limit=1000,
        current_digest=context(conn, config, True).digest,
        include_unresolved=True,
        share=True,
    )
    return {c.job_id for c in chosen.candidates}


def read(conn, config, **fake) -> str:
    """One reading with evidence shared; returns its job id."""
    add_claims(conn)
    stats = run_deep(conn, config, FakeCli(**fake), share=True, limit=1, include_unresolved=True)
    assert stats.published == 1
    return str(conn.execute("SELECT job_id FROM deep_review").fetchone()[0])


def edit_cited_skill(conn) -> None:
    """`s1` is the first skill, so the alias E1 the fake reading cites."""
    with transaction(conn):
        ClaimRepo(conn).supersede(
            "c1",
            VerifiedClaim(
                claim_key="s1",
                revision=2,
                claim_type=ClaimType.SKILL,
                text="CRM and billing integrations",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )


def add_new_claim(conn) -> None:
    with transaction(conn):
        ClaimRepo(conn).add(
            "c1",
            VerifiedClaim(
                claim_key="cert-new",
                claim_type=ClaimType.CERTIFICATION,
                text="A certificate confirmed after the reading",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )


# --- current ------------------------------------------------------------------


def test_a_current_reading_is_current_and_not_planned(corpus):  # noqa: F811
    conn, config = corpus
    job = read(conn, config)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["stale_reasons"]) == ("current", False, [])
    assert job not in planned(conn, config)


def test_a_new_reading_records_both_halves(corpus):  # noqa: F811
    conn, config = corpus
    read(conn, config)
    stored = conn.execute("SELECT intent_digest, evidence_digest FROM deep_review").fetchone()
    ctx = context(conn, config, True)
    assert tuple(stored) == (ctx.intent_digest, ctx.evidence_digest)


# --- only the evidence changed --------------------------------------------------


def test_evidence_added_keeps_a_level_supported_by_unchanged_citations(corpus):  # noqa: F811
    """Rule 3: every counted requirement was supported by items still current."""
    conn, config = corpus
    job = read(conn, config, relation="central", support="supported")
    add_new_claim(conn)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["stale_reasons"]) == ("evidence", False, ["evidence"])
    assert row["level"] == row["level_then"] != "STALE"
    assert job not in planned(conn, config), "skipped: the level cannot move"


def test_a_cited_item_edited_makes_an_aligned_level_stale(corpus):  # noqa: F811
    """Rule 4: the support it relied on changed."""
    conn, config = corpus
    job = read(conn, config, relation="central", support="supported")
    edit_cited_skill(conn)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["level"]) == ("evidence", True, "STALE")
    assert job in planned(conn, config)


def test_a_requirement_not_shown_is_read_again_when_evidence_is_added(corpus):  # noqa: F811
    """Rule 4: new evidence may show what was not shown."""
    conn, config = corpus
    job = read(conn, config, relation="secondary", support="not_shown")
    add_new_claim(conn)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"]) == ("evidence", True)
    assert job in planned(conn, config)


@pytest.mark.parametrize("relation", ["neighbour", "unrelated", "unclear"])
def test_work_that_does_not_align_keeps_its_level_whatever_the_evidence(corpus, relation):  # noqa: F811
    """Rule 2: LOW, OTHER_WORK and TO_CONFIRM are decided before history."""
    conn, config = corpus
    job = read(conn, config, relation=relation, support="not_shown")
    edit_cited_skill(conn)
    add_new_claim(conn)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"]) == ("evidence", False)
    assert row["level"] in ("LOW", "OTHER_WORK", "TO_CONFIRM")
    assert job not in planned(conn, config)


def test_sharing_turned_off_keeps_only_levels_decided_before_history(corpus):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="central", support="supported")
    add_new_claim(conn)
    chosen = select(
        conn,
        config,
        provider_id="fake_cli",
        limit=1000,
        current_digest=context(conn, config, False).digest,
        include_unresolved=True,
        share=False,
    )
    assert job in {c.job_id for c in chosen.candidates}, "rule 3 needs evidence shared now"


# --- what was asked, the instructions, the posting -------------------------------


def test_changed_levels_make_everything_stale_and_planned(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    monkeypatch.setattr(deep_run, "preferred_levels", lambda _config: ["JUNIOR"])
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["stale_reasons"]) == ("stale", True, ["profile"])
    assert job in planned(conn, config)


def test_a_changed_intent_makes_everything_stale_and_planned(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    original = deep_run.search_intent
    monkeypatch.setattr(
        deep_run, "search_intent", lambda cfg: SearchIntent(items=original(cfg).items[:-1])
    )
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["stale_reasons"]) == ("stale", True, ["profile"])
    assert job in planned(conn, config)


def test_changed_instructions_make_everything_stale_and_planned(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    monkeypatch.setattr(deep, "contract_identity", lambda: "deep-9+changed")
    row = readings(config, conn)[job]
    assert row["state"] == "stale" and "instructions" in row["stale_reasons"]
    assert job in planned(conn, config)


@pytest.mark.parametrize("change", ["text", "title"])
def test_a_changed_posting_makes_everything_stale_and_planned(corpus, change):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    with transaction(conn):
        if change == "title":
            conn.execute("UPDATE job SET title = title || ' II' WHERE id = ?", (job,))
        else:
            other = conn.execute(
                "SELECT content_hash FROM job WHERE id != ? AND content_hash IS NOT NULL", (job,)
            ).fetchone()[0]
            conn.execute("UPDATE job SET content_hash = ? WHERE id = ?", (other, job))
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"]) == ("stale", True)
    assert "posting" in row["stale_reasons"]
    if change == "title":
        assert job in planned(conn, config)


def test_evidence_and_something_else_changed_is_fully_stale(corpus):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    add_new_claim(conn)
    with transaction(conn):
        conn.execute("UPDATE job SET title = title || ' II' WHERE id = ?", (job,))
    row = readings(config, conn)[job]
    assert row["state"] == "stale" and row["stale"]
    assert job in planned(conn, config)


# --- rows whose halves are not known ---------------------------------------------


def _forget_halves(conn) -> None:
    with transaction(conn):
        conn.execute("UPDATE deep_review SET intent_digest = NULL, evidence_digest = NULL")


def test_an_unproven_row_is_fully_stale_when_its_profile_changed(corpus):  # noqa: F811
    conn, config = corpus
    job = read(conn, config, relation="neighbour")
    _forget_halves(conn)
    add_new_claim(conn)
    row = readings(config, conn)[job]
    assert (row["state"], row["stale"], row["stale_reasons"]) == ("stale", True, ["profile"])
    assert job in planned(conn, config), "never reused without proof"


def test_an_unproven_row_whose_profile_did_not_change_is_still_current(corpus):  # noqa: F811
    conn, config = corpus
    job = read(conn, config)
    _forget_halves(conn)
    assert readings(config, conn)[job]["state"] == "current"
    assert job not in planned(conn, config)


def test_the_backfill_records_only_what_it_proves(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    read(conn, config, relation="neighbour")
    stored = tuple(
        conn.execute("SELECT intent_digest, evidence_digest FROM deep_review").fetchone()
    )
    _forget_halves(conn)
    add_new_claim(conn)  # after the reading: the rebuild must use the evidence AS OF it
    with transaction(conn):  # timestamps are to the second; place it clearly later
        conn.execute(
            "UPDATE verified_claim SET created_at = '2999-01-01T00:00:00Z'"
            " WHERE claim_key = 'cert-new'"
        )
    before = conn.execute("SELECT * FROM deep_review").fetchall()

    dry = backfill_digests(conn, config, execute=False)
    assert (dry.proven, dry.unproven, dry.already) == (1, 0, 0)
    assert conn.execute("SELECT * FROM deep_review").fetchall() == before, (
        "a dry run writes nothing"
    )

    done = backfill_digests(conn, config, execute=True)
    assert done.proven == 1
    after = conn.execute("SELECT * FROM deep_review").fetchone()
    assert (after["intent_digest"], after["evidence_digest"]) == stored
    columns = [d[0] for d in conn.execute("SELECT * FROM deep_review").description]
    untouched = [c for c in columns if c not in ("intent_digest", "evidence_digest")]
    assert [after[c] for c in untouched] == [before[0][c] for c in untouched]
    assert backfill_digests(conn, config, execute=True).already == 1, "idempotent"


def test_the_backfill_refuses_a_row_asked_about_another_intent(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    read(conn, config, relation="neighbour")
    _forget_halves(conn)
    monkeypatch.setattr(deep_run, "preferred_levels", lambda _config: ["JUNIOR"])
    result = backfill_digests(conn, config, execute=True)
    assert (result.proven, result.unproven) == (0, 1)
    stored = conn.execute("SELECT intent_digest FROM deep_review").fetchone()[0]
    assert stored is None, "an unprovable row stays unknown"
