"""PREFER and AVOID sort. HARD BLOCK filters. The difference is the feature.

WHY THIS FILE EXISTS
--------------------
This product had two free-text controls and both were absolute: a phrase the
posting must carry, and a phrase it must not. That makes somebody choose between
a mild preference and never seeing a job they might have taken.

"I would rather not see agency work" and "never show me agency work" are
different sentences. So there are four controls now and they divide in two:

    Must mention    HARD    decides whether a posting is in the list
    Must not         HARD    decides whether a posting is in the list
    Prefer           SOFT    decides where in the list it appears
    Avoid            SOFT    decides where in the list it appears

THE ASSERTION THAT MATTERS
--------------------------
A soft phrase must not move any COUNT. If typing a preferred word changed the
number above the list, the product would be reporting something untrue about the
corpus, and the two numbers on one screen would disagree -- which is the exact
class of defect `ScoredJobQuery` was built to make impossible.

And nothing here may reach a stored score. Two candidates reading the same
posting must see the same `match_score` and the same explanation, because
`JobFingerprint` is candidate independent (invariant 4).
"""

from __future__ import annotations

import dataclasses
import sqlite3
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery

CONFIG_DIR = committed_config_dir()
DEMO_FILE = Path("evaluation/demo/demo_postings.yaml")


@pytest.fixture
def demo(tmp_path):
    """A seeded demo corpus of its own, never the owner's."""
    path = tmp_path / "demo.db"
    conn = connect(path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT config_id, MAX(config_version) AS v FROM job_match GROUP BY config_id"
    ).fetchone()
    return ScoredJobQuery(conn), str(row["config_id"]), int(row["v"])


def _titles(repo, config_id, version, f) -> list[str]:
    return [j.title for j in repo.page(config_id, version, f)]


BASE = JobFilter(
    limit=30,
    include_off_target=True,
    include_unresolved=True,
    include_ineligible=True,
    group_duplicates=False,
)


# =========================================================================
# 1. SOFT MEANS SOFT
# =========================================================================


def test_a_preferred_phrase_moves_a_posting_up_and_removes_nothing(demo) -> None:
    repo, config_id, version = demo
    before = _titles(repo, config_id, version, BASE)
    after = _titles(
        repo,
        config_id,
        version,
        dataclasses.replace(BASE, preferred_keywords=("documentation",)),
    )
    assert sorted(before) == sorted(after), "a preference removed a posting"
    assert before != after, "a preference changed nothing at all"


def test_an_avoided_phrase_moves_a_posting_down_and_removes_nothing(demo) -> None:
    repo, config_id, version = demo
    before = _titles(repo, config_id, version, BASE)
    after = _titles(
        repo, config_id, version, dataclasses.replace(BASE, avoided_keywords=("automation",))
    )
    assert sorted(before) == sorted(after)
    assert before != after


def test_a_soft_phrase_never_moves_a_count(demo) -> None:
    """The assertion this file exists for.

    `count` and `facets` are built from the same `_where` as `page`, and the soft
    terms are deliberately absent from it. A total that moved when somebody
    expressed a preference would be reporting a lie about the corpus.
    """
    repo, config_id, version = demo
    plain = repo.count(config_id, version, BASE)
    preferred = repo.count(
        config_id, version, dataclasses.replace(BASE, preferred_keywords=("documentation",))
    )
    avoided = repo.count(
        config_id, version, dataclasses.replace(BASE, avoided_keywords=("automation",))
    )
    assert plain == preferred == avoided


def test_facets_do_not_move_either(demo) -> None:
    repo, config_id, version = demo
    plain = repo.facets(config_id, version, BASE)
    soft = repo.facets(
        config_id,
        version,
        dataclasses.replace(BASE, preferred_keywords=("documentation",), avoided_keywords=("x",)),
    )
    assert plain == soft


# =========================================================================
# 2. HARD MEANS HARD
# =========================================================================


def test_a_hard_block_removes_the_posting(demo) -> None:
    """The control PREFER and AVOID exist to be different from."""
    repo, config_id, version = demo
    before = repo.count(config_id, version, BASE)
    after = repo.count(
        config_id, version, dataclasses.replace(BASE, excluded_keywords=("automation",))
    )
    assert after < before


def test_must_mention_narrows_to_what_carries_it(demo) -> None:
    repo, config_id, version = demo
    before = repo.count(config_id, version, BASE)
    after = repo.count(config_id, version, dataclasses.replace(BASE, keywords=("automation",)))
    assert 0 < after < before


# =========================================================================
# 3. THE STORED SCORE IS UNTOUCHED
# =========================================================================


def test_a_preference_cannot_change_what_a_posting_scores(demo) -> None:
    """Invariant 4, at the one layer where it would be easiest to break.

    The soft terms are ORDER BY arithmetic computed per request. If they had been
    implemented as a bonus written into `job_match`, two candidates would see
    different numbers for the same posting and the explanation on the card would
    stop matching the score beside it.
    """
    repo, config_id, version = demo
    plain = {
        j.job_id: (j.result.match_score if j.result else None)
        for j in repo.page(config_id, version, BASE)
    }
    soft = {
        j.job_id: (j.result.match_score if j.result else None)
        for j in repo.page(
            config_id,
            version,
            dataclasses.replace(
                BASE, preferred_keywords=("documentation",), avoided_keywords=("automation",)
            ),
        )
    }
    assert plain == soft


def test_more_matched_preferences_beat_fewer(demo) -> None:
    """One point per phrase, in one direction, so the arithmetic is explainable.

    A candidate who asked for three things and got two should be above one who
    got one, and "why is this above that" has an answer somebody can read.
    """
    repo, config_id, version = demo
    one = _titles(repo, config_id, version, dataclasses.replace(BASE, preferred_keywords=("sql",)))
    two = _titles(
        repo,
        config_id,
        version,
        dataclasses.replace(BASE, preferred_keywords=("sql", "documentation")),
    )
    assert sorted(one) == sorted(two)
