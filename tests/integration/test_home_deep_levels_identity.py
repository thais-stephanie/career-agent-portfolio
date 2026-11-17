"""Step 1c: what the deep levels' identity must see, and a later Home's fast path.

`_levels_key` names the state the remembered levels were derived in. Nothing
remembered (the whole map, the memo of parts, the reviewed set) may be used
while what it was derived from has changed. The writes that change a reading:

- `DeepRepo.save`, the reader's only writer: an INSERT, or an upsert that
  replaces a reading in place (id and, within one second, `created_at` kept).
  It always writes a `deep_attempt` in the same transaction;
- `backfill_digests`: it fills `intent_digest` (and `evidence_digest`) only
  where `intent_digest` IS NULL, so it can only raise `COUNT(intent_digest)`.

And a later Home whose identity still holds derives no reading again: with the
whole map remembered it selects nothing; with only the memo of parts it reads
the reviewed set from memory and asks each section once.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from tests.integration.test_home_deep_levels_memo import miss, whole_map, write
from tests.integration.test_home_deep_levels_within import (
    build_corpus,
    fresh,
    home,
    home_whole_map,
    read_ids,
    vary,
)

from career_agent.semantic import deep_assess
from career_agent.semantic.deep_run import DeepRepo, backfill_digests
from career_agent.storage.db import connect, transaction
from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery
from career_agent.web import deep_api
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path)
    vary(app)
    return app


@pytest.fixture
def counted(monkeypatch):
    """Calls of what a later Home must not repeat while its identity holds."""
    seen = {"assess": 0, "reviewed": 0, "ids_among": 0}
    real_assess = deep_assess.assess
    real_reviewed = DeepRepo.reviewed_job_ids
    real_among = ScoredJobQuery.ids_among

    def assess(*a, **k):
        seen["assess"] += 1
        return real_assess(*a, **k)

    def reviewed(self):
        seen["reviewed"] += 1
        return real_reviewed(self)

    def among(self, *a, **k):
        seen["ids_among"] += 1
        return real_among(self, *a, **k)

    monkeypatch.setattr(deep_assess, "assess", assess)
    monkeypatch.setattr(DeepRepo, "reviewed_job_ids", reviewed)
    monkeypatch.setattr(ScoredJobQuery, "ids_among", among)
    return seen


def clear(seen: dict) -> None:
    for k in seen:
        seen[k] = 0


def key_of(app):
    conn = connect(app.config.db_path)
    try:
        return deep_api._levels_key(app, conn)
    finally:
        conn.close()


def twin(app) -> JobsApi:
    """Another server on the same database: nothing remembered (the truth now)."""
    other = JobsApi(
        ServerConfig(db_path=app.config.db_path, config_dir=app.config.config_dir), quiet=True
    )
    other.search_config()
    return other


def truth(app) -> dict:
    return home(twin(app))


def truth_map(app) -> dict:
    """The whole map derived now by a server that remembers nothing."""
    conn = connect(app.config.db_path)
    try:
        return deep_api.deep_levels(twin(app), conn)
    finally:
        conn.close()


# -- F1: a later Home ------------------------------------------------------


def test_a_later_home_with_the_whole_map_remembered_selects_nothing(
    corpus, counted, monkeypatch
) -> None:
    fresh(corpus)
    corpus.deep_levels()  # as after Find jobs
    clear(counted)
    got = miss(corpus)
    assert counted == {"assess": 0, "reviewed": 0, "ids_among": 0}
    corpus._home_lists = None
    assert got == home_whole_map(corpus, monkeypatch)


def test_a_later_home_with_only_the_part_reads_no_reading_again(corpus, counted) -> None:
    fresh(corpus)
    first = home(corpus)
    clear(counted)
    got = miss(corpus)
    seen = dict(counted)
    assert got == first
    assert seen["assess"] == 0
    assert seen["reviewed"] == 0, "the reviewed set is remembered under the same identity"
    assert seen["ids_among"] >= 1, "the population is asked again (saves, hides, time)"
    assert corpus._deep_levels_cache is None, "a part is never the whole map"


def chunked_ids_among(repo, cid, cv, f, job_ids):
    """`ids_among` as Step 1b wrote it (500 ids per statement): the reference."""
    inner = replace(f, group_duplicates=False, deep_levels=())
    clauses, params = repo._where_parts(cid, cv, inner)
    source = "SELECT j.id" + repo._from_for(repo._joins_needed(inner), f=inner)
    wanted, found = sorted(job_ids), set()
    for start in range(0, len(wanted), 500):
        chunk = wanted[start : start + 500]
        where = " WHERE " + " AND ".join([*clauses, f"jm.job_id IN ({','.join('?' * len(chunk))})"])
        found.update(str(r[0]) for r in repo.conn.execute(source + where, [*params, *chunk]))
    return found


def home_filters(app):
    from career_agent.digest import sections

    return [
        JobFilter(**{**s.job_filter.__dict__, "sort": "deep", "lane": "shortlist"})
        for s in sections(app.search_config(), show=5, last_reviewed_at="2000-01-01T00:00:00Z")
        if s.key in {"since_last_review", "recent", "best"}
    ]


def test_ids_among_asks_each_filter_once_whatever_the_set(corpus) -> None:
    """One statement per call: the set is not cut into chunks that each walk
    the population index again. Same answer as the chunked form."""
    conn = connect(corpus.config.db_path)
    config = corpus.search_config()
    cid, cv = str(config.config_id), int(config.config_version)
    every = {str(r[0]) for r in conn.execute("SELECT id FROM job")}
    asked = every | {f"absent-{n}" for n in range(1700)}
    repo = ScoredJobQuery(conn)
    for f in [JobFilter(), *home_filters(corpus)]:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        got = repo.ids_among(cid, cv, f, asked)
        conn.set_trace_callback(None)
        assert len([s for s in statements if s.lstrip().startswith("SELECT j.id")]) == 1
        assert got == chunked_ids_among(repo, cid, cv, f, asked)
        assert repo.ids_among(cid, cv, f, set(list(every)[:3])) == chunked_ids_among(
            repo, cid, cv, f, set(list(every)[:3])
        )
        assert repo.ids_among(cid, cv, f, set()) == set()
    conn.close()


def test_the_reviewed_memo_follows_the_identity(corpus, counted) -> None:
    """An unread posting that becomes a repost of a reviewed advert (its text
    is now that advert's) moves the population token: the reviewed set is
    read again and the posting gets the reading's level."""
    fresh(corpus)
    home(corpus)
    first_key, first_reviewed = key_of(corpus), set(corpus._deep_reviewed[1])
    conn = connect(corpus.config.db_path)
    read = set(read_ids(corpus))
    unread = str(
        conn.execute(
            "SELECT id FROM job WHERE id NOT IN (SELECT job_id FROM deep_review)"
            " AND content_hash NOT IN (SELECT content_hash FROM deep_review) ORDER BY id"
        ).fetchone()[0]
    )
    # The same advert under the same title: a reading is shared only then.
    reviewed_hash, reviewed_title = conn.execute(
        "SELECT content_hash, title FROM deep_review ORDER BY id LIMIT 1"
    ).fetchone()
    conn.close()
    assert unread not in read and unread not in first_reviewed
    write(
        corpus,
        "UPDATE job SET content_hash = ?, title = ? WHERE id = ?",
        (reviewed_hash, reviewed_title, unread),
    )
    assert key_of(corpus) != first_key
    clear(counted)
    got = miss(corpus)
    assert counted["reviewed"] == 1
    assert unread in corpus._deep_reviewed[1], "the repost is reviewed now"
    assert unread in truth_map(corpus)
    assert got == truth(corpus)


# -- F2: a reading replaced in the same second -----------------------------


def resave_in_the_same_second(app) -> str:
    """What `run_deep` writes when it reads an advert again under the same
    unique key within the second of the newest save: `DeepRepo.save` replaces
    the reading in place (id kept, `created_at` the newest second, so neither
    COUNT nor MAX of the review moves) and a PUBLISHED attempt is inserted, in
    one transaction. The reading replaced is that of a posting Home shows; its
    verdict comes from a reading of another level. Returns the posting."""
    fresh_app = twin(app)
    before = truth_map(app)
    read = set(read_ids(app))
    shown = [
        i["job_id"] for s in home(fresh_app)["sections"] for i in s["items"] if i["job_id"] in read
    ]
    conn = connect(app.config.db_path)
    newest_at = conn.execute("SELECT MAX(created_at) FROM deep_review").fetchone()[0]
    rows = [
        dict(r)
        for r in conn.execute("SELECT * FROM deep_review ORDER BY id")
        if not json.loads(r["sent_json"]).get("shared")  # its level is its review's alone
    ]
    level = {
        r["id"]: deep_assess.assess(json.loads(r["review_json"]), None).recommendation for r in rows
    }
    by_id = {r["id"]: r for r in rows}
    for job in shown:
        # The reading the posting is shown with (a repost may show another's).
        found = DeepRepo(conn).newest_per_job([job])
        row = by_id.get(found[0][1]["id"]) if found else None
        donors = [r for r in rows if row and level[r["id"]] != before[job][0]]
        if row and donors:
            break
    else:
        raise AssertionError("no shown reading to replace")
    row = dict(row)
    row["review_json"], row["gate_json"] = donors[0]["review_json"], donors[0]["gate_json"]
    row["created_at"] = newest_at
    attempt = dict(
        conn.execute("SELECT * FROM deep_attempt ORDER BY rowid DESC LIMIT 1").fetchone()
    )
    attempt["id"] = f"datt_resave_{conn.execute('SELECT COUNT(*) FROM deep_attempt').fetchone()[0]}"
    attempt["job_id"], attempt["content_hash"] = job, row["content_hash"]
    with transaction(conn):
        DeepRepo(conn).save(row)
        DeepRepo(conn).save_attempt(attempt)
    conn.close()
    assert truth_map(app)[job] != before[job], "the verdict shown changed"
    return job


def test_a_reading_replaced_in_the_same_second_changes_what_is_shown(corpus) -> None:
    fresh(corpus)
    corpus.deep_levels()
    before_key = key_of(corpus)

    def stamp() -> tuple:
        conn = connect(corpus.config.db_path)
        try:
            return tuple(
                conn.execute("SELECT COUNT(*), MAX(created_at) FROM deep_review").fetchone()
            )
        finally:
            conn.close()

    old_stamp = tuple(stamp())
    job = resave_in_the_same_second(corpus)
    assert tuple(stamp()) == old_stamp, "the review's own stamp cannot see it"
    assert corpus.deep_levels() == twin(corpus).deep_levels(), "never the stale map"
    assert key_of(corpus) != before_key
    assert job in dict(corpus.deep_levels()[1])


def test_successive_resaves_each_move_the_identity(corpus) -> None:
    fresh(corpus)
    keys = [key_of(corpus)]
    for _ in range(3):
        resave_in_the_same_second(corpus)
        keys.append(key_of(corpus))
    assert len(set(keys)) == 4


def test_a_rolled_back_save_leaves_identity_and_levels(corpus) -> None:
    fresh(corpus)
    before_key, before = key_of(corpus), whole_map(corpus)
    conn = connect(corpus.config.db_path)
    newest = dict(
        conn.execute("SELECT * FROM deep_review ORDER BY created_at DESC LIMIT 1").fetchone()
    )
    newest["review_json"] = conn.execute(
        "SELECT review_json FROM deep_review ORDER BY id LIMIT 1"
    ).fetchone()[0]
    attempt = dict(
        conn.execute("SELECT * FROM deep_attempt ORDER BY rowid DESC LIMIT 1").fetchone()
    )
    attempt["id"] = "datt_rolled_back"
    with pytest.raises(RuntimeError), transaction(conn):
        DeepRepo(conn).save(newest)
        DeepRepo(conn).save_attempt(attempt)
        raise RuntimeError("the run stops before commit")
    conn.close()
    assert key_of(corpus) == before_key
    assert whole_map(corpus) == before


def test_a_home_after_a_same_second_resave_is_the_truth(corpus) -> None:
    fresh(corpus)
    home(corpus)
    resave_in_the_same_second(corpus)
    assert miss(corpus) == truth(corpus)


# -- F3: the digest backfill -----------------------------------------------


@pytest.fixture
def stored_before_0059(tmp_path, monkeypatch):
    """A corpus whose readings' evidence is unchanged since they were made
    (no `vary`), so the backfill can prove the shared ones."""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path, postings=40, reads=(10, 0, 0), clones=6)
    pre_0059(app)
    return app


def pre_0059(app) -> None:
    """Readings as stored before migration 0059 (no digest halves), and a
    claim confirmed AFTER they were made: a shared reading is then read as
    fully out of date until the backfill proves what it was asked
    (`test_deep_work_fit_reuse` builds the same case on one reading)."""
    from career_agent.domain.claims import VerifiedClaim
    from career_agent.domain.enums import ClaimSource, ClaimType
    from career_agent.storage.repositories import ClaimRepo

    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute("UPDATE deep_review SET intent_digest = NULL, evidence_digest = NULL")
        ClaimRepo(conn).add(
            "c1",
            VerifiedClaim(
                claim_key="cert-new",
                claim_type=ClaimType.CERTIFICATION,
                text="A certificate confirmed after the readings",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )
        conn.execute(
            "UPDATE verified_claim SET created_at = '2999-01-01T00:00:00Z'"
            " WHERE claim_key = 'cert-new'"
        )
    conn.close()


def backfill(app) -> object:
    conn = connect(app.config.db_path)
    try:
        return backfill_digests(conn, app.search_config(), execute=True)
    finally:
        conn.close()


def shown_read(app) -> list[str]:
    read = set(read_ids(app))
    return [i["job_id"] for s in truth(app)["sections"] for i in s["items"] if i["job_id"] in read]


def test_backfill_moves_levels_and_the_identity(stored_before_0059) -> None:
    """The scenario is real: filled digests change levels, and the key sees it."""
    app = stored_before_0059
    before, before_key = truth_map(app), key_of(app)
    result = backfill(app)
    assert result.proven >= 1
    after = truth_map(app)
    shown = set(shown_read(app))
    assert any(before[j] != after[j] for j in shown), "a level Home shows moves"
    assert key_of(app) != before_key


def test_a_backfill_of_postings_already_asked_same_ids(stored_before_0059) -> None:
    """A: the memo holds these postings; no new id comes."""
    app = stored_before_0059
    fresh(app)
    before = truth_map(app)
    home(app)
    asked = set(app._deep_levels_part[2])
    backfill(app)
    after = truth_map(app)
    assert any(before.get(j) != after.get(j) for j in asked), "a level asked moved"
    assert miss(app) == truth(app)
    _key, levels, asked_now = app._deep_levels_part
    assert asked <= asked_now
    assert levels == {j: after[j] for j in asked_now if j in after}, "never the old level"


def test_a_backfill_then_more_ids(stored_before_0059, monkeypatch) -> None:
    """B: the first Home asks half of the read postings; after the backfill a
    later Home asks the rest too. One epoch only, and the truth."""
    app = stored_before_0059
    held = set(shown_read(app)[1::2])
    real = ScoredJobQuery.ids_among
    monkeypatch.setattr(ScoredJobQuery, "ids_among", lambda s, *a, **k: real(s, *a, **k) - held)
    fresh(app)
    home(app)
    backfill(app)
    monkeypatch.setattr(ScoredJobQuery, "ids_among", real)
    assert miss(app) == truth(app)
    _key, levels, asked = app._deep_levels_part
    after = whole_map(app)
    assert levels == {j: after[j] for j in asked if j in after}


def test_a_backfill_that_changes_nothing_keeps_everything(corpus, counted) -> None:
    """C and F: nothing to fill (every reading already has its digests), or
    the backfill run again: the identity holds and nothing is derived."""
    fresh(corpus)
    first = home(corpus)
    key = key_of(corpus)
    result = backfill(corpus)
    assert result.proven == 0
    again = backfill(corpus)
    assert again.proven == 0 and again.already == result.already
    assert key_of(corpus) == key
    clear(counted)
    assert miss(corpus) == first
    assert counted["assess"] == 0


def test_a_backfill_repeated_after_it_filled(stored_before_0059, counted) -> None:
    """F: the second run finds every row filled and writes nothing."""
    app = stored_before_0059
    assert backfill(app).proven >= 1
    fresh(app)
    first = home(app)
    key = key_of(app)
    assert backfill(app).proven == 0
    assert key_of(app) == key
    clear(counted)
    assert miss(app) == first
    assert counted["assess"] == 0


def test_a_backfill_between_two_overlapping_parts(stored_before_0059) -> None:
    """D: `deep_levels_within` of S1, the backfill, then of S2 overlapping S1."""
    app = stored_before_0059
    read = read_ids(app)
    s1, s2 = set(read[: len(read) * 2 // 3]), set(read[len(read) // 3 :])
    fresh(app)
    conn = connect(app.config.db_path)
    deep_api.deep_levels_within(app, conn, s1)
    backfill(app)
    got = deep_api.deep_levels_within(app, conn, s2)
    conn.close()
    after = whole_map(app)
    assert got == {j: after[j] for j in s2 if j in after}


def test_a_backfill_committed_during_a_read_transaction(stored_before_0059) -> None:
    """E: a reader's snapshot keeps its own epoch whole; the next one sees the new."""
    app = stored_before_0059
    fresh(app)
    before = whole_map(app)
    read = read_ids(app)
    s1, s2 = set(read[::2]), set(read)
    conn = connect(app.config.db_path)
    conn.execute("BEGIN")
    first = deep_api.deep_levels_within(app, conn, s1)
    backfill(app)  # another connection commits
    during = deep_api.deep_levels_within(app, conn, s2)
    conn.execute("COMMIT")
    assert first == {j: before[j] for j in s1 if j in before}
    assert during == {j: before[j] for j in s2 if j in before}, "one snapshot, one epoch"
    after = whole_map(app)
    assert after != before
    now = deep_api.deep_levels_within(app, conn, s2)
    conn.close()
    assert now == {j: after[j] for j in s2 if j in after}


# -- the writers the identity relies on ------------------------------------


#: A write and the table it names, across lines and string pieces (the
#: statements are written as adjacent literals), in any source file.
WRITE = re.compile(
    r"\b(INSERT\s+(?:OR\s+\w+\s+)?INTO|UPDATE|DELETE\s+FROM|REPLACE\s+INTO|DROP\s+TABLE)"
    r"\s*[\"'\s]*([{\w.\"]+)",
    re.I,
)


def writes() -> tuple[set, set]:
    named, runtime = set(), set()
    for path in SRC.rglob("*.py"):
        where = path.relative_to(SRC).as_posix()
        for match in WRITE.finditer(path.read_text(encoding="utf-8")):
            verb, table = " ".join(match.group(1).upper().split()), match.group(2).strip('"')
            if table in ("deep_review", "deep_attempt"):
                named.add((where, verb, table))
            elif table.startswith("{"):
                runtime.add(where)
    return named, runtime


def test_every_writer_of_a_reading_is_one_the_identity_sees() -> None:
    """The identity holds because these are the only product writers of
    `deep_review` and `deep_attempt` (migrations aside). A new writer, or a
    new module writing to a table named at runtime, fails here and must be
    checked against `_levels_key` before it is added.

    The runtime-named writers, read 2026-11-17: none reaches either table
    while a server runs. `catalogue.py` drops and copies tables when an
    install is split (offline, before any server opens it); the others name
    their own module's tables (resume documents, matching v2, public health,
    the career repo's application tables)."""
    named, runtime = writes()
    assert named == {
        ("semantic/deep_run.py", "UPDATE", "deep_review"),  # backfill_digests: NULL only
        ("semantic/deep_run.py", "INSERT INTO", "deep_review"),  # DeepRepo.save
        ("semantic/deep_run.py", "INSERT INTO", "deep_attempt"),  # DeepRepo.save_attempt
    }
    assert runtime == {
        "matching_v2/store.py",
        "resume_doc/store.py",
        "sources/public_health.py",
        "storage/career_repo.py",
        "storage/catalogue.py",
    }
