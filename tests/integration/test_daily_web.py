"""The digest over HTTP, and the one thing it must never become.

`career-agent daily` and `GET /api/daily` answer the same question. Two copies
of a list of sections is how a terminal and an interface start disagreeing
about what "new" means, and the disagreement would be invisible because each
would be internally consistent. These tests hold both to the one module that
defines them.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The committed configuration, copied without the owner's private overrides.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="daily-web")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def digest(api: JobsApi) -> dict:
    return api.handle_api("GET", "/api/daily", {}, {})


def keys(payload: dict) -> list[str]:
    return [section["key"] for section in payload["sections"]]


# =========================================================================
# 1. ONE DEFINITION, TWO SURFACES
# =========================================================================


def test_the_sections_are_the_ones_the_shared_module_defines(api: JobsApi) -> None:
    from career_agent.digest import sections

    config = api.search_config()
    expected = [section.key for section in sections(config)]
    assert keys(digest(api)) == expected


def test_nothing_here_re_ranks_anything(api: JobsApi) -> None:
    """Every section is a filter over the score the cards already show. A
    digest that invented its own ordering would be a second opinion nobody
    could trace, which is what ADR-0004 refuses."""
    best = next(s for s in digest(api)["sections"] if s["key"] == "best")
    scores = [item["match_score"] for item in best["items"] if item["match_score"] is not None]
    assert scores == sorted(scores, reverse=True)


def test_an_empty_section_keeps_its_heading(api: JobsApi) -> None:
    """One that vanished would be indistinguishable from one that failed to
    look."""
    payload = digest(api)
    for section in payload["sections"]:
        assert section["title"], section["key"]
        assert section["lead"], section["key"]
        assert section["count"] == len(section["items"]) or section["count"] >= len(
            section["items"]
        )


# =========================================================================
# 2. THE READER'S CHECKPOINT
# =========================================================================


def test_without_a_checkpoint_the_first_section_goes_by_the_board_date(
    api: JobsApi,
) -> None:
    payload = digest(api)
    assert payload["last_reviewed_at"] is None
    assert keys(payload)[0] == "recent"


def test_marking_it_read_changes_the_question_the_first_section_asks(
    api: JobsApi,
) -> None:
    """ "New in the last seven days" is a guess about a calendar. "Since you
    last looked" is a fact about this reader."""
    after = api.handle_api("POST", "/api/daily/reviewed", {}, {})
    assert after["last_reviewed_at"]
    assert keys(after)[0] == "since_last_review"


def test_nothing_arrived_between_reading_the_list_and_marking_it_read(
    api: JobsApi,
) -> None:
    """The checkpoint is STRICTLY after. If she looked at T she saw everything
    this machine held at T, so a posting first seen at exactly T was in the
    list she was reading. At-or-after re-showed the whole corpus the moment
    she pressed the button."""
    after = api.handle_api("POST", "/api/daily/reviewed", {}, {})
    since = next(s for s in after["sections"] if s["key"] == "since_last_review")
    assert since["count"] == 0


def test_reading_the_digest_does_not_mark_it_read(api: JobsApi) -> None:
    """A digest that marked itself read on every load would make "since you
    last looked" mean "since this page last rendered"."""
    digest(api)
    digest(api)
    assert digest(api)["last_reviewed_at"] is None


# =========================================================================
# 3. IT SAYS WHAT IT COULD SEE
# =========================================================================


def test_the_digest_reports_how_much_of_the_corpus_it_could_see(api: JobsApi) -> None:
    """Without these an empty digest after a settings change reads as "nothing
    is worth looking at", when the truth is that nothing has been rescored."""
    payload = digest(api)
    assert payload["job_count"] > 0
    assert payload["scored_count"] > 0


def test_a_date_is_labelled_by_which_kind_of_date_it_is(api: JobsApi) -> None:
    """`posted_at` and `first_seen_at` are different facts and the card carries
    both, so the interface never has to choose one and call it the other."""
    best = next(s for s in digest(api)["sections"] if s["key"] == "best")
    assert best["items"], "the demo corpus produced no best matches"
    for item in best["items"]:
        assert "posted_at" in item
        assert "first_seen_at" in item
        assert item["first_seen_at"], item["job_id"]
