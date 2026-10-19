"""A deep reading is of an advert under a title (migration 0058).

The reader is sent the title and the advert, never the employer. So the same
advert under the same title is one question (a repost shares the reading),
and the same advert under another title is another question: it is planned,
it is read, and reading it replaces nothing. What the planner counts as read
is what Find jobs shows as read. And the migration copies every stored
reading one to one. Nothing here reaches a provider.
"""

from __future__ import annotations

import shutil

from tests.integration.test_deep_run import FakeCli
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_run import corpus  # noqa: F401 - fixture

from career_agent.semantic import deep
from career_agent.semantic.deep_run import DeepRepo, context, run_deep, select
from career_agent.storage.db import MIGRATIONS_DIR, connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


def _jobs(conn) -> list[tuple[str, str, str]]:
    return [
        (str(r[0]), str(r[1]), str(r[2]))
        for r in conn.execute("SELECT id, content_hash, title FROM job ORDER BY id")
    ]


def _planned(conn, config) -> set[str]:
    """Every posting a job could ask for now (limit large enough for all)."""
    chosen = select(
        conn,
        config,
        provider_id="fake_cli",
        limit=1000,
        current_digest=context(conn, config, False).digest,
        include_unresolved=True,
    )
    return {c.job_id for c in chosen.candidates}


def _readable(conn, config) -> list[tuple[str, str, str]]:
    """Postings of the main list a job could read, so a test works on them."""
    planned = _planned(conn, config)
    return [job for job in _jobs(conn) if job[0] in planned]


def _shown(conn, job_ids: list[str]) -> dict[str, str]:
    """job id -> the id of the review Find jobs shows for it."""
    return {job: str(row["id"]) for job, row in DeepRepo(conn).newest_per_job(job_ids)}


def _table(conn, name: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {name} ORDER BY id").fetchall()]


def test_the_same_advert_under_another_title_is_read_and_replaces_nothing(corpus):  # noqa: F811
    conn, config = corpus
    (a, hash_a, _title_a), (b, _hash_b, _title_b), *_ = _readable(conn, config)
    provider = FakeCli()
    first = run_deep(
        conn, config, provider, share=False, limit=10, include_unresolved=True, only={a}
    )
    assert first.published == 1
    kept = _table(conn, "deep_review")
    # The employer publishes A's advert again under B's title.
    with transaction(conn):
        conn.execute("UPDATE job SET content_hash = ? WHERE id = ?", (hash_a, b))
    assert b in _planned(conn, config), "another title is another question"
    assert a not in _planned(conn, config)
    assert set(_shown(conn, [a, b])) == {a}, "Find jobs agrees: B is not read"
    second = run_deep(
        conn, config, provider, share=False, limit=10, include_unresolved=True, only={b}
    )
    assert second.published == 1
    after = _table(conn, "deep_review")
    assert len(after) == 2 and kept[0] in after, "A's reading is untouched"
    shown = _shown(conn, [a, b])
    assert shown[a] == kept[0][0] and shown[b] != shown[a]
    assert {b, a}.isdisjoint(_planned(conn, config))


def test_a_repost_of_the_same_vacancy_shares_the_reading(corpus):  # noqa: F811
    conn, config = corpus
    (a, hash_a, title_a), (c, _hash_c, _title_c), *_ = _readable(conn, config)
    run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True, only={a})
    reading = _shown(conn, [a])[a]
    # C becomes a repost: the same advert under the same title.
    with transaction(conn):
        conn.execute(
            "UPDATE job SET content_hash = ?, title = ? WHERE id = ?", (hash_a, title_a, c)
        )
    assert {a, c}.isdisjoint(_planned(conn, config)), "a repost is not read again"
    assert _shown(conn, [a, c]) == {a: reading, c: reading}, "and Find jobs shows it as read"
    assert _shown(conn, []) == {}
    everything = dict(DeepRepo(conn).newest_per_job())
    assert str(everything[c]["id"]) == reading


def test_a_title_changed_since_the_reading_is_shown_out_of_date(corpus):  # noqa: F811
    conn, config = corpus
    (a, *_), *_ = _readable(conn, config)
    run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True, only={a})
    with transaction(conn):
        conn.execute("UPDATE job SET title = title || ' (Remote)' WHERE id = ?", (a,))
    assert a in _planned(conn, config)
    assert set(_shown(conn, [a])) == {a}, "its own older reading is still shown"


def _schema_0057(tmp_path):
    older = tmp_path / "migrations"
    older.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) <= 57:
            shutil.copy(path, older / path.name)
    return older


def test_the_migration_copies_every_reading_one_to_one(tmp_path) -> None:
    conn = connect(tmp_path / "before.db")
    migrate(conn, _schema_0057(tmp_path))
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="a")
        )
        a = _put(conn, company, board, "1", "Business Process Consultant", "Same advert.", None)
        b = _put(conn, company, board, "2", "AI Workflow Analyst", "Same advert.", None)
        same = str(conn.execute("SELECT content_hash FROM job WHERE id = ?", (a,)).fetchone()[0])
        conn.execute(
            "INSERT INTO deep_job (id, provider, model, billing, contract, profile_digest,"
            " share_evidence, include_unresolved, requested, status, created_at, updated_at)"
            " VALUES ('djob', 'claude_code', 'sonnet', 'SUBSCRIPTION', 'c', 'd', 0, 1, 2,"
            " 'CANCELLED', 't', 't')"
        )
        for n, (provider, model) in enumerate(
            (("claude_code", "claude-sonnet-5-5"), ("deepseek", "deepseek-flash"))
        ):
            conn.execute(
                "INSERT INTO deep_review (id, job_id, content_hash, profile_digest, contract,"
                " provider, model, stratum, review_json, gate_json, sent_json, raw_response,"
                " input_tokens, output_tokens, latency_ms, run_id, created_at)"
                " VALUES (?, ?, ?, 'd', ?, ?, ?, 'control', '{\"x\": 1}', '{}', '{}', 'raw',"
                " 10, 20, 30, 'run', ?)",
                (f"deep_{n}", a, same, deep.contract_identity(), provider, model, f"t{n}"),
            )
            conn.execute(
                "INSERT INTO deep_attempt (id, deep_job_id, job_id, content_hash, provider,"
                " model, contract, profile_digest, outcome, cost_usd, created_at)"
                " VALUES (?, 'djob', ?, ?, ?, ?, 'c', 'd', 'PUBLISHED', 0, 't')",
                (f"datt_{n}", a, same, provider, model),
            )
    before = _table(conn, "deep_review")
    attempts = _table(conn, "deep_attempt")
    jobs = _table(conn, "deep_job")
    migrate(conn)
    columns = [r[1] for r in conn.execute("PRAGMA table_info(deep_review)")]
    after = [dict(zip(columns, row, strict=True)) for row in _table(conn, "deep_review")]
    assert len(after) == len(before) == 2
    for old, new in zip(before, after, strict=True):
        assert tuple(v for k, v in new.items() if k != "title") == old
        assert new["title"] == "Business Process Consultant"
    titled = [r for r in conn.execute("SELECT * FROM deep_attempt ORDER BY id")]
    assert [tuple(r)[:-1] for r in titled] == attempts
    assert {r["title"] for r in titled} == {"Business Process Consultant"}
    assert _table(conn, "deep_job") == jobs
    # The new key admits B's reading beside A's instead of replacing it.
    with transaction(conn):
        conn.execute(
            "INSERT INTO deep_review (id, job_id, content_hash, title, profile_digest, contract,"
            " provider, model, stratum, created_at)"
            " SELECT 'deep_b', ?, content_hash, 'AI Workflow Analyst', profile_digest, contract,"
            " provider, model, stratum, 't9' FROM deep_review WHERE id = 'deep_0'",
            (b,),
        )
    assert conn.execute("SELECT count(*) FROM deep_review").fetchone()[0] == 3
    indexes = {r[1] for r in conn.execute("PRAGMA index_list(deep_review)")}
    assert {"idx_deep_review_hash", "idx_deep_review_job"} <= indexes
    conn.close()
