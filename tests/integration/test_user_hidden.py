"""A posting she hid, and the four reasons a posting is off the screen.

`USER_HIDDEN` is the fourth thing that keeps a job out of the discovery views
and the only one she chose. The other three are facts about somebody else:

    VERIFIED_NOT_ELIGIBLE  the EMPLOYER stated a requirement she does not meet
    off target             her SEARCH CONFIGURATION calls this other work
    REJECTED / WITHDRAWN   an APPLICATION ended

None of them can express "I have read this one and I do not want to see it
again", and making one of them carry it would be a lie in the data: marking a
posting REJECTED records that somebody applied and was turned down, and a
board showing a rejection she never received is worse than a list one item
too long.

The failure mode this file guards is therefore not a crash. It is a control
that silently does the wrong thing.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.application import ApplicationStatus
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import (
    DEFAULT_APPLICATION_STATUS,
    ApplicationRepo,
    JobFilter,
    ScoredJobQuery,
)
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="user-hidden")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


# =========================================================================
# 1. THE COLUMN, AND WHAT IT DOES NOT TOUCH
# =========================================================================


def test_hiding_writes_a_time_and_restoring_clears_it(api: JobsApi) -> None:
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        job_id = _any_job(conn)

        stamp = repo.set_hidden(job_id, True, now="2026-09-06T10:00:00Z")
        assert stamp == "2026-09-06T10:00:00Z"
        assert _hidden_at(conn, job_id) == "2026-09-06T10:00:00Z"

        assert repo.set_hidden(job_id, False, now="2026-09-06T10:05:00Z") is None
        assert _hidden_at(conn, job_id) is None


def test_hiding_appends_no_status_event_and_moves_no_status(api: JobsApi) -> None:
    """Hiding is not a position in the application workflow.

    The nearest status is ARCHIVED, and writing it here would record that an
    application ended. None was ever sent.
    """
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        job_id = _any_job(conn)
        repo.set_status(
            job_id,
            ApplicationStatus.APPLIED,
            applied_at="2026-09-01",
            now="2026-09-01T09:00:00Z",
        )
        before = len(repo.history(job_id))

        repo.set_hidden(job_id, True, now="2026-09-06T10:00:00Z")

        assert len(repo.history(job_id)) == before, "hiding wrote a move that never happened"
        state = repo.get(job_id)
        assert state is not None
        assert state[0] is ApplicationStatus.APPLIED, "hiding moved the application"
        assert state[1] == "2026-09-01", "hiding touched the date an application was sent"


def test_hiding_does_not_touch_saved_or_notes(api: JobsApi) -> None:
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        job_id = _any_job(conn)
        repo.set_saved(job_id, True, now="2026-09-01T09:00:00Z")
        repo.set_notes(job_id, "worth a second look", now="2026-09-01T09:01:00Z")

        repo.set_hidden(job_id, True, now="2026-09-06T10:00:00Z")

        state = repo.get(job_id)
        assert state is not None
        assert state[2] is True
        assert state[3] == "worth a second look"


# =========================================================================
# 2. THE NARROWING, AND THE EXEMPTION THAT DOES NOT APPLY
# =========================================================================


def test_the_default_filter_shows_a_hidden_job(api: JobsApi) -> None:
    """`include_user_hidden` defaults to True, like the other two flags.

    The default is "everything", so a caller counting a corpus gets the
    corpus. The narrowing is a product decision about a view, applied by the
    web layer, and a repository whose default answer was smaller than the
    database would make every other count wrong in a way nothing announces.
    """
    with _conn(api) as conn:
        ApplicationRepo(conn).set_hidden(_any_job(conn), True, now="2026-09-06T10:00:00Z")
        query = ScoredJobQuery(conn)
        identity = api._identity()

        everything = query.count(*identity, JobFilter())
        narrowed = query.count(*identity, JobFilter(include_user_hidden=False))

        assert everything == narrowed + 1


def test_a_saved_and_tracked_job_is_still_hidden_when_she_hides_it(api: JobsApi) -> None:
    """THE EXEMPTION DOES NOT APPLY HERE, and its absence is the point.

    `include_ineligible=False` and `include_off_target=False` both spare a
    saved or tracked posting, because those narrowings are the MACHINE
    deciding and it must never retract a decision she made. This narrowing IS
    her decision. A hide that quietly failed on the jobs she had saved would
    be a control that does not work where it matters most.
    """
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        job_id = _any_job(conn)
        repo.set_saved(job_id, True, now="2026-09-01T09:00:00Z")
        repo.set_status(job_id, ApplicationStatus.INTERVIEW, now="2026-09-02T09:00:00Z")
        repo.set_hidden(job_id, True, now="2026-09-06T10:00:00Z")

        rows = ScoredJobQuery(conn).page(
            *api._identity(), JobFilter(include_user_hidden=False, limit=500)
        )

    assert job_id not in {row.job_id for row in rows}


def test_only_hidden_is_the_restore_view(api: JobsApi) -> None:
    with _conn(api) as conn:
        kept, gone = _two_jobs(conn)
        ApplicationRepo(conn).set_hidden(gone, True, now="2026-09-06T10:00:00Z")

        rows = ScoredJobQuery(conn).page(
            *api._identity(), JobFilter(user_hidden_only=True, limit=500)
        )

    assert {row.job_id for row in rows} == {gone}
    assert kept not in {row.job_id for row in rows}


def test_the_restore_view_can_order_by_when_she_hid_them(api: JobsApi) -> None:
    """Score order puts the one she hid a minute ago wherever its match
    happens to fall, which is nowhere useful when she is looking for it."""
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        first, second = _two_jobs(conn)
        repo.set_hidden(first, True, now="2026-09-01T10:00:00Z")
        repo.set_hidden(second, True, now="2026-09-06T10:00:00Z")

        rows = ScoredJobQuery(conn).page(
            *api._identity(),
            JobFilter(user_hidden_only=True, sort="hidden", direction="desc", limit=500),
        )

    assert [row.job_id for row in rows] == [second, first]


def test_sorting_by_when_she_hid_them_joins_the_table_it_reads(api: JobsApi) -> None:
    """`ja.hidden_at` sits on an OPTIONAL join, and a sort key is not a filter.

    Without `_SORTS_ON_APPLICATION` this raises a missing-table SQL error
    rather than returning a wrong answer, which is the loud half of the
    asymmetry `_joins_needed` relies on. Asserted with no filter at all, so
    nothing else asks for the join.
    """
    with _conn(api) as conn:
        ScoredJobQuery(conn).page(*api._identity(), JobFilter(sort="hidden", limit=5))


# =========================================================================
# 3. FOUR REASONS, COUNTED SEPARATELY
# =========================================================================


def test_the_count_is_reported_apart_from_the_other_two(api: JobsApi) -> None:
    """One number covering all three would say "47 hidden" about populations
    she can only put back three different ways."""
    with _conn(api) as conn:
        ApplicationRepo(conn).set_hidden(_any_job(conn), True, now="2026-09-06T10:00:00Z")
        query = ScoredJobQuery(conn)
        identity = api._identity()
        narrow = JobFilter(include_user_hidden=False)

        assert query.hidden_by_the_candidate(*identity, narrow) == 1
        # The other two counted nothing, because their own flags are True.
        assert query.hidden_by_eligibility(*identity, narrow) == 0
        assert query.hidden_by_screening(*identity, narrow) == 0


def test_the_count_is_zero_when_nothing_is_narrowed(api: JobsApi) -> None:
    with _conn(api) as conn:
        ApplicationRepo(conn).set_hidden(_any_job(conn), True, now="2026-09-06T10:00:00Z")
        assert ScoredJobQuery(conn).hidden_by_the_candidate(*api._identity(), JobFilter()) == 0


# =========================================================================
# 4. THE ROUTE
# =========================================================================


def test_the_route_hides_and_restores(api: JobsApi) -> None:
    job_id = _first_job_id(api)

    card = api.patch_hidden(job_id=job_id, query={}, body={"hidden": True})
    assert card["hidden"] is True

    card = api.patch_hidden(job_id=job_id, query={}, body={"hidden": False})
    assert card["hidden"] is False


@pytest.mark.parametrize("value", ["true", 1, None, "yes"])
def test_the_route_refuses_anything_but_a_boolean(api: JobsApi, value: object) -> None:
    """`"true"` is a string a form sent, not a decision somebody made."""
    with pytest.raises(ApiError) as raised:
        api.patch_hidden(job_id=_first_job_id(api), query={}, body={"hidden": value})
    assert raised.value.status == 400


def test_the_list_reports_what_she_hid_apart_from_what_was_hidden_for_her(
    api: JobsApi,
) -> None:
    job_id = _first_job_id(api)
    api.patch_hidden(job_id=job_id, query={}, body={"hidden": True})

    payload = api.list_jobs(query={"include_user_hidden": ["0"]}, body={})

    assert payload["hidden_by_you"] == 1
    assert job_id not in {item["job_id"] for item in payload["items"]}

    # THREE NUMBERS, NEVER ONE. The web layer narrows all three by default, so
    # the other two are non-zero on the demo corpus -- which is exactly why
    # they must be reported apart: "4 hidden" would be one sentence about
    # three populations she can only restore three different ways.
    unhidden = api.list_jobs(query={"include_user_hidden": ["1"]}, body={})
    assert unhidden["hidden_by_eligibility"] == payload["hidden_by_eligibility"], (
        "restoring what SHE hid changed the count of what the EMPLOYER ruled out"
    )
    assert unhidden["hidden_off_target"] == payload["hidden_off_target"], (
        "restoring what SHE hid changed the count of what her SEARCH set aside"
    )


def test_hiding_through_the_route_never_writes_a_status(api: JobsApi) -> None:
    job_id = _first_job_id(api)
    before = api.get_job(job_id=job_id, query={}, body={})["application_status"]

    api.patch_hidden(job_id=job_id, query={}, body={"hidden": True})

    after = api.get_job(job_id=job_id, query={}, body={})
    assert after["application_status"] == before
    assert after["application_status"] == DEFAULT_APPLICATION_STATUS.value


# =========================================================================
# helpers
# =========================================================================


class _conn:
    """The API's own database, opened for a direct read or write."""

    def __init__(self, api: JobsApi) -> None:
        self._api = api

    def __enter__(self):
        self._conn = connect(self._api.config.db_path)
        return self._conn

    def __exit__(self, *exc: object) -> None:
        self._conn.close()


def _any_job(conn) -> str:
    row = conn.execute("SELECT id FROM job ORDER BY id LIMIT 1").fetchone()
    assert row is not None, "the fixture database holds no jobs"
    return str(row["id"])


def _two_jobs(conn) -> tuple[str, str]:
    rows = conn.execute("SELECT id FROM job ORDER BY id LIMIT 2").fetchall()
    assert len(rows) == 2, "the fixture database holds fewer than two jobs"
    return str(rows[0]["id"]), str(rows[1]["id"])


def _hidden_at(conn, job_id: str) -> str | None:
    row = conn.execute(
        "SELECT hidden_at FROM job_application WHERE job_id = ?", (job_id,)
    ).fetchone()
    return None if row is None else row["hidden_at"]


def _first_job_id(api: JobsApi) -> str:
    payload = api.list_jobs(query={}, body={})
    assert payload["items"], "the fixture served no jobs"
    return str(payload["items"][0]["job_id"])


# =========================================================================
# 5. WHAT THE CARD STOOD FOR
# =========================================================================


def test_hiding_a_grouped_card_hides_the_whole_role(api: JobsApi) -> None:
    """Measured in a browser before it was fixed.

    Hiding the representative of a three-city group promoted a sibling into
    the same position, and the card came back looking almost identical. To a
    reader that is a control that does nothing, which is worse than one that
    refuses.
    """
    grouped = api.list_jobs(query={"group_duplicates": ["1"]}, body={})
    card = next(item for item in grouped["items"] if int(item["duplicate_count"]) > 1)

    api.patch_hidden(job_id=card["job_id"], query={}, body={"hidden": True, "scope": "role"})

    after = api.list_jobs(query={"group_duplicates": ["1"]}, body={})
    titles = {(item["company_name"], item["title"]) for item in after["items"]}
    assert (card["company_name"], card["title"]) not in titles, (
        "a sibling was promoted into the hidden card's place"
    )


def test_hiding_one_posting_leaves_its_siblings_alone(api: JobsApi) -> None:
    """The other half of the same decision.

    In the Table, ungrouped, a row is one posting and the person is looking at
    postings. Hiding one there must not take the other two with it.
    """
    grouped = api.list_jobs(query={"group_duplicates": ["1"]}, body={})
    card = next(item for item in grouped["items"] if int(item["duplicate_count"]) > 1)
    with _conn(api) as conn:
        family = ApplicationRepo(conn).siblings_of(card["job_id"])
    assert len(family) > 1, "the fixture has no grouped role to test with"

    api.patch_hidden(job_id=card["job_id"], query={}, body={"hidden": True})

    rows = api.list_jobs(query={"limit": ["500"]}, body={})
    visible = {item["job_id"] for item in rows["items"]}
    assert card["job_id"] not in visible
    assert visible & set(family), "hiding one posting took its siblings with it"


def test_the_route_refuses_a_scope_it_does_not_know(api: JobsApi) -> None:
    with pytest.raises(ApiError) as raised:
        api.patch_hidden(
            job_id=_first_job_id(api), query={}, body={"hidden": True, "scope": "everything"}
        )
    assert raised.value.status == 400


def test_siblings_of_a_singleton_is_just_itself(api: JobsApi) -> None:
    with _conn(api) as conn:
        repo = ApplicationRepo(conn)
        singletons = [
            job_id
            for job_id in (str(row["id"]) for row in conn.execute("SELECT id FROM job"))
            if len(repo.siblings_of(job_id)) == 1
        ]
        assert singletons, "every posting in the fixture shares a company and title"
        assert repo.siblings_of(singletons[0]) == [singletons[0]]
