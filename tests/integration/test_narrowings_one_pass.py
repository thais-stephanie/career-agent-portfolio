"""The narrowings counted in one pass equal the narrowings counted one by one.

`ScoredJobQuery.count_together` answers every `count` the narrowing methods
make from ONE read of the rows they share (phase 3, 2026-11-12). This file
holds it to the separate counts it replaced, on a corpus built to make the
counts differ: every eligibility status, blocked work, duplicates of one role,
saved, tracked and hidden postings, removed adverts, old, undated and pasted
postings, short adverts (leads), excluded levels and ways of working, an
older revision beside the current one. Filters are drawn at random from a
fixed seed, so a failure names a filter that can be replayed.
"""

from __future__ import annotations

import random
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage import search_index
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
NOW = "2026-11-12T12:00:00Z"
CLONES = 700
SEED = 20261112

STATUSES = ("VERIFIED_ELIGIBLE", "LIKELY_ELIGIBLE", "UNRESOLVED", "VERIFIED_NOT_ELIGIBLE")
LEVELS = ("JUNIOR", "MID", "SENIOR", "LEAD", "PRINCIPAL")
MODELS = ("REMOTE", "HYBRID", "ONSITE", None)
TRACKED = ("DISCOVERED", "SHORTLISTED", "APPLIED", "INTERVIEW", "REJECTED")


def _build(db_path: Path) -> None:
    """The demo corpus, cloned with every attribute a narrowing reads varied."""
    rng = random.Random(SEED)
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(committed_config_dir())
    seed_demo(conn, config, source=DEMO_FILE)
    conn.row_factory = sqlite3.Row
    jobs = [dict(r) for r in conn.execute("SELECT * FROM job ORDER BY id")]
    raws = {r["content_hash"]: dict(r) for r in conn.execute("SELECT * FROM job_raw")}
    matches = {r["job_id"]: dict(r) for r in conn.execute("SELECT * FROM job_match")}
    companies = [r["id"] for r in conn.execute("SELECT id FROM company ORDER BY id")]

    def insert(table: str, row: dict) -> None:
        cols = list(row)
        conn.execute(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            [row[c] for c in cols],
        )

    with transaction(conn):
        for i in range(CLONES):
            template = jobs[i % len(jobs)]
            match = matches.get(template["id"])
            if match is None:
                continue
            raw = raws[template["content_hash"]]
            short = rng.random() < 0.15
            body = f"Short advert {i}." if short else f"{raw['description_text']}\n\nRef {i}."
            content_hash = f"syn-hash-{i:05d}"
            job_id = f"syn-{i:05d}"
            insert(
                "job_raw",
                {
                    **raw,
                    "content_hash": content_hash,
                    "description_text": body,
                    "description_html": None,
                    "byte_length": len(body.encode()),
                },
            )
            posted = rng.choice((None, "2026-11-10", "2026-10-01", "2026-03-01", "2025-01-01"))
            seen = rng.choice((NOW, "2026-11-09T00:00:00Z", "2026-06-01T00:00:00Z"))
            insert(
                "job",
                {
                    **template,
                    "id": job_id,
                    "external_id": job_id,
                    # Few employers and titles, so many postings share a role.
                    "company_id": rng.choice(companies[:4]),
                    "title": f"Role {rng.randrange(60)}",
                    "url": f"https://example.invalid/syn/{job_id}",
                    "provider": "manual_import" if rng.random() < 0.05 else template["provider"],
                    "posted_at": posted,
                    "content_hash": content_hash,
                    "first_seen_at": "2026-01-01T00:00:00Z",
                    "last_seen_at": seen,
                    "closed_at": "2026-11-01T00:00:00Z" if rng.random() < 0.1 else None,
                },
            )
            row = {
                **match,
                "id": f"syn-m-{i:05d}",
                "job_id": job_id,
                "content_hash": content_hash,
                "match_score": rng.randrange(100),
                "eligibility_status": rng.choice(STATUSES),
                "screening_state": "BLOCKED" if rng.random() < 0.2 else "NOT_BLOCKED",
                "seniority": rng.choice(LEVELS),
                "work_model": rng.choice(MODELS),
            }
            insert("job_match", row)
            if rng.random() < 0.3:
                insert(
                    "job_match",
                    {
                        **row,
                        "id": f"syn-o-{i:05d}",
                        "config_version": int(row["config_version"]) - 1,
                        "eligibility_status": rng.choice(STATUSES),
                    },
                )
            if rng.random() < 0.25:
                insert(
                    "job_application",
                    {
                        "id": f"syn-a-{i:05d}",
                        "job_id": job_id,
                        "status": rng.choice(TRACKED),
                        "saved": int(rng.random() < 0.4),
                        "hidden_at": NOW if rng.random() < 0.3 else None,
                        "created_at": NOW,
                        "updated_at": NOW,
                    },
                )
            if rng.random() < 0.08:
                insert(
                    "job_availability",
                    {
                        "job_id": job_id,
                        "checked_at": NOW,
                        "state": rng.choice(("REMOVED", "AVAILABLE")),
                    },
                )
        conn.execute("DELETE FROM job_dirty")
    search_index.rebuild(conn)
    conn.close()


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> JobsApi:
    db_path = tmp_path_factory.mktemp("one_pass") / "corpus.db"
    _build(db_path)
    return JobsApi(ServerConfig(db_path=db_path, config_dir=committed_config_dir()), quiet=True)


def _narrowings(repo: ScoredJobQuery, cid: str, ver: int, f: JobFilter) -> dict:
    """What `JobsApi._narrowings` counts, through the methods it calls."""
    total = repo.count(cid, ver, f)
    out = {
        name: getattr(repo, name)(cid, ver, f, narrow_total=total)
        for name in (
            "hidden_by_eligibility",
            "hidden_by_screening",
            "hidden_unresolved",
            "hidden_by_seniority",
            "hidden_by_work_model",
            "hidden_by_age",
            "hidden_undated",
            "hidden_by_the_candidate",
        )
    }
    out["total"] = total
    out["leads"] = repo.count(cid, ver, replace(f, lane="leads")) if f.lane == "shortlist" else None
    return out


def _random_filter(rng: random.Random) -> JobFilter:
    pick = rng.random
    statuses = tuple(rng.sample(TRACKED, rng.randrange(1, 3))) if pick() < 0.15 else ()
    return JobFilter(
        lane=rng.choice(("shortlist", "shortlist", "leads", "all")),
        group_duplicates=pick() < 0.5,
        include_ineligible=pick() < 0.3,
        include_unresolved=pick() < 0.3,
        include_off_target=pick() < 0.3,
        include_user_hidden=pick() < 0.3,
        include_removed=pick() < 0.3,
        excluded_seniorities=("SENIOR", "LEAD") if pick() < 0.5 else (),
        include_excluded_seniority=pick() < 0.3,
        excluded_work_models=("ONSITE",) if pick() < 0.5 else (),
        include_excluded_work_model=pick() < 0.3,
        max_age_days=rng.choice((None, 30, 120, 400)),
        include_undated=pick() < 0.4,
        exempt_tracked=bool(statuses) or pick() < 0.2,
        statuses=statuses,
        saved_only=pick() < 0.08,
        with_saved=pick() < 0.2,
        eligibility=tuple(rng.sample(STATUSES, rng.randrange(1, 3))) if pick() < 0.15 else (),
        worksites=("REMOTE",) if pick() < 0.15 else (),
        search=rng.choice((None, None, None, "engineer", "data", "zzzz-nothing")),
        keywords=("integration",) if pick() < 0.1 else (),
        excluded_keywords=("sales",) if pick() < 0.1 else (),
        min_score=rng.choice((None, None, 40)),
        remote_only=pick() < 0.1,
    )


def test_one_pass_counts_equal_the_separate_counts(api: JobsApi) -> None:
    cid, ver = api._identity()
    rng = random.Random(SEED)
    filters = [JobFilter(), JobFilter(lane="shortlist")] + [_random_filter(rng) for _ in range(160)]
    nonzero = 0
    with api.connect() as conn:
        for f in filters:
            alone = ScoredJobQuery(conn)
            alone.now = NOW
            together = ScoredJobQuery(conn)
            together.now = NOW
            expected = _narrowings(alone, cid, ver, f)
            got = together.count_together(
                cid, ver, lambda r=together, g=f: _narrowings(r, cid, ver, g)
            )
            assert got == expected, f
            nonzero += any(v for k, v in expected.items() if k not in ("total", "leads"))
    # The corpus is only a test of anything if the counts it compares moved.
    assert nonzero > len(filters) // 2


def test_one_pass_reads_the_rows_once(api: JobsApi) -> None:
    """The point of it: one SELECT per shape, not one per narrowing."""
    cid, ver = api._identity()
    grouped = JobFilter(
        lane="shortlist",
        group_duplicates=True,
        max_age_days=120,
        excluded_seniorities=("SENIOR",),
        excluded_work_models=("ONSITE",),
    )
    for f in (grouped, replace(grouped, group_duplicates=False)):
        with api.connect() as conn:
            seen: list[str] = []
            conn.set_trace_callback(seen.append)
            repo = ScoredJobQuery(conn)
            repo.count_together(cid, ver, lambda r=repo, g=f: _narrowings(r, cid, ver, g))
            conn.set_trace_callback(None)
        reads = [s for s in seen if "FROM job_match" in s]
        assert len(reads) == 1, reads


def test_the_page_answers_what_the_methods_count(api: JobsApi) -> None:
    """`/api/jobs/narrowings` and `/api/jobs` agree with the methods, one by one."""
    cid, ver = api._identity()
    for query in (
        {},
        {"group_duplicates": ["1"]},
        {"lane": ["all"]},
        {"lane": ["leads"]},
        {"include_unresolved": ["1"], "search": ["engineer"]},
        {"status": ["SHORTLISTED", "APPLIED"]},
        {"saved_only": ["1"]},
    ):
        api._aggregates.clear()
        got = api.job_narrowings(query=dict(query), body={})
        f = api._filter_from(dict(query))
        with api.connect() as conn:
            want = _narrowings(ScoredJobQuery(conn), cid, ver, f)
        want["hidden_off_target"] = want.pop("hidden_by_screening")
        want["hidden_by_you"] = want.pop("hidden_by_the_candidate")
        want.pop("total")
        assert got == want, query
        listed = api.list_jobs(query={**query, "narrowings": ["later"]}, body={})
        assert listed["narrowings_pending"] is False
        assert listed["hidden_by_eligibility"] == got["hidden_by_eligibility"]


def test_a_clause_the_pairing_does_not_understand_fails_loudly() -> None:
    from career_agent.storage.mvp_repo import _bound_clauses

    assert _bound_clauses(["a = ?", "b IN (?, ?)"], [1, 2, 3]) == [
        ("a = ?", (1,)),
        ("b IN (?, ?)", (2, 3)),
    ]
    with pytest.raises(AssertionError):
        _bound_clauses(["a = ?"], [1, 2])


@pytest.mark.parametrize("excluded", [("ONSITE",), ("ONSITE", "HYBRID"), ("REMOTE",), ("NONE",)])
def test_the_work_model_narrowing_selects_the_rows_the_column_test_did(
    api: JobsApi, excluded: tuple[str, ...]
) -> None:
    """The clause asks the work-model index by rowid instead of reading the
    column (phase 3); the rows are the ones the column test selected."""
    marks = ", ".join("?" for _ in excluded)
    with api.connect() as conn:
        by_column = {
            r[0]
            for r in conn.execute(
                f"SELECT id FROM job_match WHERE work_model IS NULL OR work_model NOT IN ({marks})",
                excluded,
            )
        }
        by_index = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM job_match jm WHERE jm.rowid NOT IN"
                f" (SELECT rowid FROM job_match WHERE work_model IN ({marks}))",
                excluded,
            )
        }
        nulls = conn.execute("SELECT COUNT(*) FROM job_match WHERE work_model IS NULL")
        nulls = nulls.fetchone()[0]
    assert by_column == by_index
    assert nulls and by_column, "the corpus must hold unstated and kept ways of working"
