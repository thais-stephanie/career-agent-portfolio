"""Home derives deep levels only for the postings its sections can show.

A synthetic corpus with real stored readings (the fake CLI of `test_deep_run`;
no provider is reached): duplicate groups, reposts of one advert, eligible,
ineligible, unresolved and blocked scores, closed and old postings, readings
of every relation, out of date by title, evidence changed, hidden, applied and
saved postings, and a review checkpoint. What must hold:

- Home's whole answer is the one the whole map of levels gives, in every
  state (no cache, the whole map remembered, a stale remembered map, a moved
  catalogue, a collection running, an expired memo, a recalculation);
- the levels derived for a set of postings are exactly the whole map's
  entries for them, whatever the set (reposts, unread, unknown ids);
- Home derives fewer readings than the whole map holds, and only those its
  sections admit;
- the whole map other screens read is never replaced by a part of it;
- a section filtering by the reading still gets every level.
"""

from __future__ import annotations

import json
import random
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_deep_metered import WORK
from tests.integration.test_deep_run import FakeCli, add_claims
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_api import build

from career_agent.pipeline.rescore import rescore
from career_agent.runtime.mode import RuntimeMode
from career_agent.semantic import deep_assess
from career_agent.semantic.deep_run import run_deep
from career_agent.storage.db import connect, transaction
from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo
from career_agent.web import deep_api
from career_agent.web.workspace_api import WorkspaceRoutes

TITLES = [
    "Business Systems Engineer",
    "CRM Automation Engineer",
    "Integrations Engineer",
    "RevOps Systems Analyst",
    "Workflow Automation Specialist",
    "Solutions Engineer",
]
RELATIONS = ("central", "secondary", "neighbour", "unrelated", "unclear")
SUPPORTS = ("supported", "not_shown", "partial")


def clone_readings(conn, readings: int) -> None:
    """More readings than the fake runs make, shaped exactly like theirs: each
    stored review copied onto other postings (their own advert and title, so
    the copy reads as theirs), and every third one given an older review too.
    Deterministic; the derivation runs on them like on any reading."""
    templates = conn.execute("SELECT * FROM deep_review ORDER BY id").fetchall()
    if not templates:
        return
    columns = list(templates[0].keys())
    have = {str(r[0]) for r in conn.execute("SELECT job_id FROM deep_review")}
    # A posting holding an advert already read under its title is a repost:
    # it shares that reading and is left unread, as a repost is.
    keys = {
        (str(r[0]), str(r[1])) for r in conn.execute("SELECT content_hash, title FROM deep_review")
    }
    targets = conn.execute(
        "SELECT id, content_hash, title FROM job WHERE content_hash IS NOT NULL ORDER BY id"
    ).fetchall()
    added = 0
    for n, job in enumerate(targets):
        if added >= readings:
            break
        if str(job["id"]) in have or (str(job["content_hash"]), str(job["title"])) in keys:
            continue
        keys.add((str(job["content_hash"]), str(job["title"])))
        for older in (True, False) if n % 3 == 0 else (False,):
            row = dict(templates[(n + older) % len(templates)])
            # Every relation and support the reader can answer, so the copies
            # carry every recommendation level, not only the template's.
            review = json.loads(row["review_json"])
            if isinstance(review.get("work_relation"), dict):
                review["work_relation"]["value"] = RELATIONS[n % len(RELATIONS)]
            for requirement in review.get("requirements") or []:
                requirement["support"] = SUPPORTS[(n // len(RELATIONS)) % len(SUPPORTS)]
            row["review_json"] = json.dumps(review)
            row.update(
                id=f"clone-{n}-{int(older)}",
                job_id=str(job["id"]),
                content_hash=str(job["content_hash"]),
                title=str(job["title"]),
                model=f"{row['model']}-{n}-{int(older)}",
                created_at=f"2026-0{'1' if older else '9'}-{1 + n % 28:02d}T00:00:00Z",
            )
            conn.execute(
                f"INSERT INTO deep_review ({','.join(columns)})"
                f" VALUES ({','.join('?' for _ in columns)})",
                [row[c] for c in columns],
            )
        have.add(str(job["id"]))
        added += 1


def build_corpus(
    tmp_path: Path,
    postings: int = 60,
    reads: tuple[int, int, int] = (12, 10, 6),
    clones: int = 30,
):
    """The app over an invented corpus with stored readings. Deterministic."""
    app = build(tmp_path, RuntimeMode.PERSONAL)
    config_dir = app.config.config_dir
    (config_dir / "search.local.yaml").write_bytes(
        (config_dir / "search.worked-example.yaml").read_bytes()
    )
    today = datetime.now(UTC).date()
    conn = connect(app.config.db_path)
    with transaction(conn):
        companies = [
            CompanyRepo(conn).upsert(CompanyRecord(slug=s, name=s.title()))
            for s in ("acme", "globex", "initech")
        ]
        boards = [
            SourceBoardRepo(conn).upsert(
                SourceBoardRecord(company_id=c, provider="greenhouse", board_identifier=f"b{i}")
            )
            for i, c in enumerate(companies)
        ]
        distinct = max(1, postings * 3 // 4)  # the rest repeat an advert: reposts
        for n in range(postings):
            text = f"Role number {n % distinct}. {WORK} Team {n % distinct}."
            c = n % 3
            title = TITLES[(n % distinct) % len(TITLES)]  # a repost keeps its title
            _put(conn, companies[c], boards[c], f"p-{n}", title, text, "Remote")
        for n in range(postings):
            age = 1 if n % 5 else (30 if n % 2 else 200)  # recent, older, past the age limit
            conn.execute(
                "UPDATE job SET posted_at = ? WHERE external_id = ?",
                ((today - timedelta(days=age)).isoformat(), f"p-{n}"),
            )
    rescore(conn, app.search_config())
    add_claims(conn)
    config = app.search_config()
    for share, (relation, support), limit in zip(
        (True, False, False),
        (("central", "supported"), ("secondary", "not_shown"), ("unrelated", "supported")),
        reads,
        strict=True,
    ):
        if limit:
            run_deep(
                conn,
                config,
                FakeCli(relation=relation, support=support),
                share=share,
                limit=limit,
                include_unresolved=True,
            )
    with transaction(conn):
        clone_readings(conn, clones)
    conn.close()
    return app


def ids(app) -> dict[str, str]:
    conn = connect(app.config.db_path)
    found = {str(r[0]): str(r[1]) for r in conn.execute("SELECT external_id, id FROM job")}
    conn.close()
    return found


def read_ids(app) -> list[str]:
    conn = connect(app.config.db_path)
    found = sorted({str(r[0]) for r in conn.execute("SELECT job_id FROM deep_review")})
    conn.close()
    return found


def vary(app) -> None:
    """Spread the read postings over every population Home's filters tell apart."""
    config = app.search_config()
    read = read_ids(app)
    conn = connect(app.config.db_path)
    now = datetime.now(UTC)
    with transaction(conn):
        version = (str(config.config_id), int(config.config_version))
        for i, job in enumerate(read):
            if i % 7 == 1:
                conn.execute(
                    "UPDATE job_match SET eligibility_status = 'VERIFIED_NOT_ELIGIBLE'"
                    " WHERE job_id = ? AND config_id = ? AND config_version = ?",
                    (job, *version),
                )
            elif i % 7 == 2:
                conn.execute(
                    "UPDATE job_match SET eligibility_status = 'UNRESOLVED'"
                    " WHERE job_id = ? AND config_id = ? AND config_version = ?",
                    (job, *version),
                )
            elif i % 7 == 3:
                conn.execute("UPDATE job SET closed_at = ? WHERE id = ?", (now.isoformat(), job))
            elif i % 7 == 4:
                conn.execute(
                    "UPDATE job_match SET work_model = 'ONSITE', seniority = 'JUNIOR'"
                    " WHERE job_id = ? AND config_id = ? AND config_version = ?",
                    (job, *version),
                )
        # Out of date by title: another title is another question to the reader.
        for job in read[5:7]:
            conn.execute("UPDATE job SET title = title || ' II' WHERE id = ?", (job,))
        # Evidence changed for the readings that shared the claim.
        conn.execute("UPDATE verified_claim SET revision = revision + 1 WHERE claim_key = 's1'")
        # The person's own marks on read postings.
        for job, (status, saved, hidden) in zip(
            read[8:11],
            (("SHORTLISTED", 1, None), ("APPLIED", 0, None), ("DISCOVERED", 0, now.isoformat())),
            strict=False,
        ):
            conn.execute(
                "INSERT OR REPLACE INTO job_application (id, job_id, status, saved, hidden_at,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"app-{job}", job, status, saved, hidden, now.isoformat(), now.isoformat()),
            )
        # A review checkpoint, so "since you last looked" is a section too.
        conn.execute("UPDATE job SET first_seen_at = ?", ((now - timedelta(days=3)).isoformat(),))
        for job in read[::3]:
            conn.execute("UPDATE job SET first_seen_at = ? WHERE id = ?", (now.isoformat(), job))
    conn.close()
    from career_agent.storage.workspace_repo import (
        LAST_REVIEWED_AT,
        CandidateStateRepo,
        candidate_id_of,
    )

    conn = connect(app.config.db_path)
    with transaction(conn):
        CandidateStateRepo(conn).set(
            candidate_id_of(conn), LAST_REVIEWED_AT, (now - timedelta(days=1)).isoformat()
        )
    conn.close()


def fresh(app) -> None:
    """Forget every remembered answer: Home's lists and the whole map."""
    app._home_lists = None
    app._deep_levels_cache = None
    app._deep_relations_cache = None


def home(app) -> dict:
    answer = app.handle_api("GET", "/api/home", {}, {})
    answer = json.loads(json.dumps(answer, default=str))
    answer.pop("lists_at")  # the moment the lists were counted, nothing else
    return answer


def home_whole_map(app, monkeypatch) -> dict:
    """Home as `8a43fb0` built it: every page gets the whole map."""
    with monkeypatch.context() as m:
        m.setattr(
            WorkspaceRoutes,
            "_home_deep_levels",
            lambda self, conn, repo, cid, cv, filters: self.deep_levels()[0],
        )
        return home(app)


def assert_same_home(app, monkeypatch, *, keep_cache: bool = False) -> dict:
    if not keep_cache:
        fresh(app)
    else:
        app._home_lists = None
    new = home(app)
    app._home_lists = None
    if not keep_cache:
        app._deep_levels_cache = None
    old = home_whole_map(app, monkeypatch)
    assert new == old
    return new


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path)
    vary(app)
    return app


def read_in_home(app, answer) -> int:
    read = set(read_ids(app))
    return sum(1 for s in answer["sections"] for item in s["items"] if item["job_id"] in read)


def test_the_corpus_exercises_what_it_claims(corpus) -> None:
    levels = dict((j, (lv, st)) for j, lv, st, _r in corpus.deep_levels()[0])
    states = dict(corpus.deep_levels()[1])
    assert len(read_ids(corpus)) >= 20
    assert {"current", "stale", "evidence"} <= set(states.values()), states
    assert len({lv for lv, _st in levels.values()}) >= 3
    assert len(levels) > len(read_ids(corpus)), "reposts share a reading"
    answer = home(corpus)
    keys = [s["key"] for s in answer["sections"]]
    # With a checkpoint, "since you last looked" stands where "recent" was.
    assert keys == ["since_last_review", "best"]
    assert read_in_home(corpus, answer) >= 3, "read postings reach the cards"


def test_home_is_the_answer_the_whole_map_gives(corpus, monkeypatch) -> None:
    assert_same_home(corpus, monkeypatch)


def test_home_is_the_same_with_the_whole_map_remembered(corpus, monkeypatch) -> None:
    fresh(corpus)
    corpus.deep_levels()  # Find jobs asked first: the whole map is remembered
    remembered = corpus._deep_levels_cache
    calls = []
    real = deep_api._review_rows
    monkeypatch.setattr(deep_api, "_review_rows", lambda *a, **k: calls.append(a) or real(*a, **k))
    new = home(corpus)
    assert calls == [], "the remembered whole map is read, nothing derived"
    assert corpus._deep_levels_cache is remembered
    monkeypatch.setattr(deep_api, "_review_rows", real)
    corpus._home_lists = None
    assert new == home_whole_map(corpus, monkeypatch)


def test_a_remembered_map_of_another_key_is_never_used(corpus, monkeypatch) -> None:
    fresh(corpus)
    key = deep_api._levels_key(corpus, connect(corpus.config.db_path))
    poisoned = {j: ("STRONG", False, "current") for j in ids(corpus).values()}
    corpus._deep_levels_cache = ((*key[:-1], key[-1] + 1), poisoned)  # another revision
    conn = connect(corpus.config.db_path)
    within = deep_api.deep_levels_within(corpus, conn, set(ids(corpus).values()))
    conn.close()
    corpus._deep_levels_cache = None
    conn = connect(corpus.config.db_path)
    whole = deep_api.deep_levels(corpus, conn)
    conn.close()
    assert within == whole


def test_home_never_stores_a_part_as_the_whole_map(corpus) -> None:
    fresh(corpus)
    home(corpus)
    assert corpus._deep_levels_cache is None
    assert getattr(corpus, "_deep_relations_cache", None) is None
    whole = corpus.deep_levels()
    fresh(corpus)
    assert corpus.deep_levels() == whole, "Find jobs still gets every level"


@pytest.mark.parametrize("seed", range(6))
def test_levels_for_a_set_are_the_whole_maps_entries_for_it(corpus, seed) -> None:
    fresh(corpus)
    conn = connect(corpus.config.db_path)
    whole = deep_api.deep_levels(corpus, conn)
    everyone = sorted(ids(corpus).values())
    rng = random.Random(seed)
    wanted = set(rng.sample(everyone, rng.randint(0, len(everyone))))
    wanted |= {"no-such-posting"}
    for remembered in (False, True):
        corpus._deep_levels_cache = None if not remembered else corpus._deep_levels_cache
        if not remembered:
            fresh(corpus)
        else:
            deep_api.deep_levels(corpus, conn)
        assert deep_api.deep_levels_within(corpus, conn, wanted) == {
            j: v for j, v in whole.items() if j in wanted
        }
    conn.close()


def test_home_derives_only_the_readings_its_sections_admit(corpus, monkeypatch) -> None:
    """Fails on `8a43fb0`, which derived every reading on every Home miss."""
    fresh(corpus)
    seen: list[int] = []
    real = deep_assess.assess
    monkeypatch.setattr(deep_assess, "assess", lambda *a, **k: seen.append(1) or real(*a, **k))
    home(corpus)
    on_home = len(seen)
    fresh(corpus)
    seen.clear()
    corpus.deep_levels()
    whole = len(seen)
    conn = connect(corpus.config.db_path)
    repo = ScoredJobQuery(conn)
    cid, cv = corpus._identity()
    from career_agent.digest import sections

    filters = [
        JobFilter(**{**s.job_filter.__dict__, "sort": "deep", "lane": "shortlist"})
        for s in sections(corpus.search_config(), show=5, last_reviewed_at="2000-01-01T00:00:00Z")
        if s.key in {"since_last_review", "recent", "best"}
    ]
    admitted = set().union(
        *(repo.ids_among(cid, cv, f, deep_api.reviewed_postings(conn)) for f in filters)
    )
    conn.close()
    assert 0 < on_home < whole
    assert on_home <= len(admitted)


def test_the_reviewed_set_is_every_posting_the_whole_map_can_hold(corpus) -> None:
    fresh(corpus)
    conn = connect(corpus.config.db_path)
    reviewed = deep_api.reviewed_postings(conn)
    whole = deep_api.deep_levels(corpus, conn)
    conn.close()
    assert set(whole) <= reviewed
    assert set(read_ids(corpus)) <= reviewed


def test_a_section_filtering_by_the_reading_gets_the_whole_map(corpus) -> None:
    fresh(corpus)
    conn = connect(corpus.config.db_path)
    repo = ScoredJobQuery(conn)
    cid, cv = corpus._identity()
    filters = [JobFilter(lane="shortlist", sort="deep", deep=("READ",))]
    got = corpus._home_deep_levels(conn, repo, cid, cv, filters)
    conn.close()
    assert got == corpus.deep_levels()[0]
    with pytest.raises(ValueError):
        ScoredJobQuery(connect(corpus.config.db_path)).ids_among(cid, cv, filters[0], {"x"})


def test_same_home_after_the_catalogue_moves(corpus, monkeypatch) -> None:
    assert_same_home(corpus, monkeypatch)
    read = read_ids(corpus)
    conn = connect(corpus.config.db_path)
    with transaction(conn):
        # A read posting's advert changes (its reading goes out of date), and
        # an unread one closes.
        conn.execute("UPDATE job SET title = title || ' (moved)' WHERE id = ?", (read[0],))
        conn.execute(
            "UPDATE job SET closed_at = '2026-01-01T00:00:00Z' WHERE id NOT IN"
            " (SELECT job_id FROM deep_review) AND rowid % 4 = 0"
        )
        conn.execute("UPDATE compute_revision SET population_revision = population_revision + 1")
    conn.close()
    assert_same_home(corpus, monkeypatch)


def test_same_home_while_a_collection_runs_and_after_the_memo_expires(corpus, monkeypatch) -> None:
    class Busy:
        running = True

    assert_same_home(corpus, monkeypatch)
    corpus.retrieval = Busy()
    assert_same_home(corpus, monkeypatch)
    from career_agent.web import workspace_api

    monkeypatch.setattr(workspace_api, "HOME_MEMO_SECONDS", 0)
    time.sleep(0.01)
    assert_same_home(corpus, monkeypatch)


def test_same_home_with_the_whole_map_remembered_in_every_state(corpus, monkeypatch) -> None:
    fresh(corpus)
    corpus.deep_levels()
    assert_same_home(corpus, monkeypatch, keep_cache=True)


@pytest.mark.parametrize(
    "shape",
    [
        {"postings": 0, "reads": (0, 0, 0)},
        {"postings": 20, "reads": (0, 0, 0)},
        {"postings": 24, "reads": (6, 4, 2)},
        {"postings": 90, "reads": (20, 10, 10)},
    ],
)
def test_same_home_over_corpora_of_every_shape(tmp_path, monkeypatch, shape) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path, **shape)
    if shape["postings"] and any(shape["reads"]):
        vary(app)
    assert_same_home(app, monkeypatch)


def test_readings_only_of_postings_home_never_shows(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path, postings=24, reads=(8, 0, 0))
    config = app.search_config()
    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute(
            # Every posting holding a read advert: a repost shares the reading.
            "UPDATE job_match SET eligibility_status = 'VERIFIED_NOT_ELIGIBLE' WHERE job_id IN"
            " (SELECT id FROM job WHERE content_hash IN (SELECT content_hash FROM deep_review)"
            " OR id IN (SELECT job_id FROM deep_review)) AND config_id = ? AND config_version = ?",
            (str(config.config_id), int(config.config_version)),
        )
    conn.close()
    seen: list[int] = []
    real = deep_assess.assess
    monkeypatch.setattr(deep_assess, "assess", lambda *a, **k: seen.append(1) or real(*a, **k))
    fresh(app)
    home(app)
    assert seen == [], "no reading Home can show, none derived"
    monkeypatch.setattr(deep_assess, "assess", real)
    assert_same_home(app, monkeypatch)


def test_two_profiles_never_share_levels(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    one = build_corpus(tmp_path / "one", postings=24, reads=(8, 0, 0))
    two = build_corpus(tmp_path / "two", postings=24, reads=(0, 0, 0))
    fresh(one)
    one.deep_levels()  # one's whole map remembered on one's server
    fresh(two)
    assert two.deep_levels()[0] == ()
    assert_same_home(two, monkeypatch)
    assert_same_home(one, monkeypatch)


def test_a_score_written_between_the_levels_and_the_pages_waits_for_the_next_answer(
    tmp_path, monkeypatch
) -> None:
    """The levels and the pages read one snapshot. A read posting made
    eligible by another writer in between, while a collection runs (so the
    lists are not counted again), must not reach a page without its level:
    this answer is the one before the write, the next the one after it."""

    class Busy:
        running = True

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build_corpus(tmp_path, postings=24, reads=(8, 0, 0), clones=0)
    config = app.search_config()
    version = (str(config.config_id), int(config.config_version))
    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute(
            "UPDATE job_match SET eligibility_status = 'VERIFIED_NOT_ELIGIBLE' WHERE job_id IN"
            " (SELECT id FROM job WHERE content_hash IN (SELECT content_hash FROM deep_review)"
            " OR id IN (SELECT job_id FROM deep_review)) AND config_id = ? AND config_version = ?",
            version,
        )
    target = str(conn.execute("SELECT job_id FROM deep_review ORDER BY id").fetchone()[0])
    conn.close()
    app.retrieval = Busy()
    fresh(app)
    before = home_whole_map(app, monkeypatch)

    real = ScoredJobQuery.ids_among

    def then_a_writer(self, *a, **k):
        found = real(self, *a, **k)
        other = connect(app.config.db_path)
        with transaction(other):
            other.execute(
                "UPDATE job_match SET eligibility_status = 'VERIFIED_ELIGIBLE', match_score = 100"
                " WHERE job_id = ? AND config_id = ? AND config_version = ?",
                (target, *version),
            )
        other.close()
        monkeypatch.setattr(ScoredJobQuery, "ids_among", real)
        return found

    monkeypatch.setattr(ScoredJobQuery, "ids_among", then_a_writer)
    fresh(app)
    during = home(app)
    # The catalogue moved after these lists were counted, and the answer says so.
    assert during.pop("lists_current") is False and before.pop("lists_current") is True
    assert during == before, "the snapshot the levels were chosen in"
    fresh(app)
    after = home(app)
    assert any(i["job_id"] == target for s in after["sections"] for i in s["items"])
    app._home_lists = None
    assert after == home_whole_map(app, monkeypatch)
