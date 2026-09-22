"""The default list is a RECOMMENDATION, and here is what that costs.

WHY THIS FILE EXISTS
--------------------
The V1.5 acceptance audit read the default top 50 of the owner's real corpus
and found one violation: a posting an employer had verifiably ruled her out of,
sitting among the jobs this product was putting forward. It was there because
she had SHORTLISTED it, and every automatic narrowing carried an unconditional
exemption for anything she was tracking.

The exemption's reasoning was right and its placement was wrong. A job she
decided about must never vanish from under her -- but "must not vanish" is a
promise about ACCESS, and it was implemented as "must appear everywhere",
including in the one list whose whole meaning is "these are jobs you could
take". Shortlisting a posting is not a fact about whether she may take it.

So the two questions are separated, and this file is the guard on both halves
at once. It fails if the recommendations become dishonest AND it fails if the
tracked posting becomes unreachable, which is the trade a careless fix makes.

WHAT COUNTS AS A DEFAULT RECOMMENDATION
----------------------------------------
Exactly what the browser asks for when nothing is ticked: no
`include_ineligible`, no `include_unresolved`, no `include_excluded_seniority`,
no `include_off_target`. `JobsApi._filter_from` reads all four as absent-means-
narrow, so an empty query IS the default view, and that is what these assert
against rather than a filter assembled here.

WHY TOP 20 AND TOP 50
----------------------
Because the violation was found at 50 and would have been missed at 20. The
sort is by score, so a narrowing that leaks only occasionally leaks further
down; asserting both sizes is what makes "zero" mean zero rather than "zero so
far".
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: The two window sizes the acceptance audit reads. See the header.
WINDOWS = (20, 50)


# =========================================================================
# fixtures
# =========================================================================


def _seed(db_path: Path, config_dir: Path) -> None:
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(config_dir)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> JobsApi:
    """The committed configuration, which excludes no level."""
    tmp = tmp_path_factory.mktemp("recommended")
    db_path = tmp / "demo.db"
    config_dir = committed_config_dir()
    _seed(db_path, config_dir)
    return JobsApi(ServerConfig(db_path=db_path, config_dir=config_dir), quiet=True)


@pytest.fixture(scope="module")
def excluding_api(tmp_path_factory: pytest.TempPathFactory) -> tuple[JobsApi, str]:
    """The same corpus under a configuration that sets a level aside.

    The level is CHOSEN FROM THE CORPUS rather than named here: the demo has no
    Principal posting, so hard-coding one would give a test that asserts zero
    against a population that was already empty. It picks the most common level
    among the default recommendations, which guarantees the exclusion bites.

    `config_version` is deliberately NOT bumped. `preferences.seniority.
    excluded` is a display gate -- it moves no score and invalidates no row --
    and bumping the version would detach the seeded corpus from its scores and
    make every assertion here read an empty list.
    """
    tmp = tmp_path_factory.mktemp("recommended-excluded")
    db_path = tmp / "demo.db"
    config_dir = tmp / "config"
    shutil.copytree(committed_config_dir(), config_dir)

    # Seed and score first, under the untouched configuration.
    _seed(db_path, config_dir)
    plain = JobsApi(ServerConfig(db_path=db_path, config_dir=config_dir), quiet=True)

    levels: dict[str, int] = {}
    for item in _recommended(plain, 50):
        level = item.get("seniority")
        if level:
            levels[level] = levels.get(level, 0) + 1
    assert levels, "no posting in the demo corpus resolves a seniority at all"
    victim = max(sorted(levels), key=lambda name: levels[name])

    target = config_dir / "search.local.yaml"
    data = yaml.safe_load(target.read_text(encoding="utf-8"))
    data["preferences"]["seniority"]["excluded"] = [victim]
    target.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")

    return JobsApi(ServerConfig(db_path=db_path, config_dir=config_dir), quiet=True), victim


# =========================================================================
# helpers
# =========================================================================


def _jobs(api: JobsApi, **params) -> dict:
    query = {k: (v if isinstance(v, list) else [str(v)]) for k, v in params.items()}
    return api.handle_api("GET", "/api/jobs", query, {})


def _recommended(api: JobsApi, limit: int) -> list[dict]:
    """The default view, exactly as an untouched browser asks for it."""
    return _jobs(api, limit=limit)["items"]


def _an_ineligible_posting(api: JobsApi) -> str:
    wide = _jobs(api, limit=500, include_ineligible=1, include_off_target=1)["items"]
    conflicted = [i["job_id"] for i in wide if i["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"]
    assert conflicted, "the demo corpus states no eligibility conflict, so this proves nothing"
    return sorted(conflicted)[0]


def _untrack(api: JobsApi, job_id: str) -> None:
    api.patch_saved(job_id=job_id, query={}, body={"saved": False})
    api.patch_status(job_id=job_id, query={}, body={"status": "DISCOVERED"})
    api.patch_applied_at(job_id=job_id, query={}, body={"applied_at": None})


# =========================================================================
# the recommended population
# =========================================================================


@pytest.mark.parametrize("limit", WINDOWS)
def test_no_recommendation_was_ruled_out_by_an_employer(api: JobsApi, limit: int) -> None:
    offending = [
        i["job_id"]
        for i in _recommended(api, limit)
        if i["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
    ]
    assert not offending, f"top {limit} recommends postings that ruled her out: {offending}"


@pytest.mark.parametrize("limit", WINDOWS)
def test_no_recommendation_left_the_question_unanswered(api: JobsApi, limit: int) -> None:
    """Silence is not a refusal, and it is not a recommendation either.

    12,634 of the owner's postings never say where the employer hires. They are
    real jobs and some of them are hers; none of them is this product putting
    one forward. They have their own control and their own banner.
    """
    offending = [
        i["job_id"] for i in _recommended(api, limit) if i["eligibility_status"] == "UNRESOLVED"
    ]
    assert not offending, f"top {limit} recommends postings that said nothing: {offending}"


@pytest.mark.parametrize("limit", WINDOWS)
def test_no_recommendation_is_a_level_she_set_aside(
    excluding_api: tuple[JobsApi, str], limit: int
) -> None:
    api, victim = excluding_api
    offending = [i["job_id"] for i in _recommended(api, limit) if i.get("seniority") == victim]
    assert not offending, f"top {limit} recommends {victim} after she excluded it: {offending}"


# =========================================================================
# tracking preserves access and buys nothing
# =========================================================================


@pytest.mark.parametrize("limit", WINDOWS)
def test_tracking_an_ineligible_posting_does_not_recommend_it_again(
    api: JobsApi, limit: int
) -> None:
    """Every tracked state, not only the one the audit happened to find.

    SHORTLISTED is what the owner's violating posting carried, but a fix that
    special-cased it would leave APPLIED and saved doing the same thing. Each
    state is set, asserted and cleared in turn.
    """
    job_id = _an_ineligible_posting(api)
    try:
        for body in (
            {"saved": True},
            {"status": "SHORTLISTED"},
            {"status": "TO_APPLY"},
            {"status": "APPLIED"},
            {"status": "INTERVIEW"},
            {"status": "REJECTED"},
        ):
            if "saved" in body:
                api.patch_saved(job_id=job_id, query={}, body=body)
            else:
                api.patch_status(job_id=job_id, query={}, body=body)
            shown = {i["job_id"] for i in _recommended(api, limit)}
            assert job_id not in shown, f"{body} put an ineligible posting back in top {limit}"
    finally:
        _untrack(api, job_id)


def test_the_tracked_posting_stays_reachable_everywhere_it_should(api: JobsApi) -> None:
    """The other half. A fix that hid it from tracking too would be worse."""
    job_id = _an_ineligible_posting(api)
    try:
        api.patch_saved(job_id=job_id, query={}, body={"saved": True})
        assert job_id in {i["job_id"] for i in _jobs(api, limit=500, saved_only=1)["items"]}

        api.patch_status(job_id=job_id, query={}, body={"status": "APPLIED"})
        board = _jobs(api, limit=500, status=["APPLIED"])
        assert job_id in {i["job_id"] for i in board["items"]}, "missing from Applications"

        # Direct access never consults a narrowing at all.
        detail = api.get_job(job_id=job_id, query={}, body={})
        assert detail["job_id"] == job_id
    finally:
        _untrack(api, job_id)


def test_the_restriction_is_stated_wherever_the_posting_appears(api: JobsApi) -> None:
    """Preserved, and never relabelled. The verdict travels with the row.

    Read from the list payload as well as the detail, because a warning that
    exists only in the drawer is a warning she has to open something to see.
    """
    job_id = _an_ineligible_posting(api)
    try:
        api.patch_status(job_id=job_id, query={}, body={"status": "APPLIED"})
        row = next(
            i for i in _jobs(api, limit=500, status=["APPLIED"])["items"] if i["job_id"] == job_id
        )
        detail = api.get_job(job_id=job_id, query={}, body={})
        assert row["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
        assert detail["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
        # ...and it is not softened into a score. ADR-0004 keeps three
        # measurements and this is the one that is not negotiable.
        assert row["eligibility_status"] != row.get("fit_band")
    finally:
        _untrack(api, job_id)


def test_tracking_changes_no_stored_measurement_and_no_history(api: JobsApi) -> None:
    """Leaving the recommendations is a view decision. Nothing is written."""
    job_id = _an_ineligible_posting(api)
    before = api.get_job(job_id=job_id, query={}, body={})
    try:
        api.patch_status(
            job_id=job_id, query={}, body={"status": "APPLIED", "applied_at": "2026-09-01"}
        )
        api.patch_status(job_id=job_id, query={}, body={"status": "INTERVIEW"})
        after = api.get_job(job_id=job_id, query={}, body={})

        for field in ("match_score", "data_confidence", "eligibility_status", "fit_band"):
            assert after[field] == before[field], f"{field} moved when she tracked the posting"
        assert after["applied_at"] == "2026-09-01", "the date she applied was rewritten"
        assert len(after["history"]) >= 2, "her own moves were truncated"
    finally:
        _untrack(api, job_id)


# =========================================================================
# the reveal controls
# =========================================================================


def test_each_reveal_control_adds_exactly_what_its_banner_promised(api: JobsApi) -> None:
    """A count above a list that disagrees with it is the defect this repo keeps
    closing. With the exemption gone the arithmetic is simpler, not looser."""
    narrow = _jobs(api, limit=500)
    for control, disclosure in (
        ("include_ineligible", "hidden_by_eligibility"),
        ("include_off_target", "hidden_off_target"),
        ("include_unresolved", "hidden_unresolved"),
    ):
        widened = _jobs(api, limit=500, **{control: 1})
        assert len(widened["items"]) - len(narrow["items"]) == narrow[disclosure], control


def test_asking_for_the_conflicts_by_name_still_overrides_the_narrowing(api: JobsApi) -> None:
    """Asked for beats hidden by default, and that did not change."""
    payload = _jobs(api, limit=500, eligibility=["VERIFIED_NOT_ELIGIBLE"], include_ineligible=1)
    assert payload["items"], "the explicit filter returned nothing"
    assert all(i["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE" for i in payload["items"])


@pytest.mark.parametrize("limit", WINDOWS)
def test_tracking_an_excluded_level_does_not_recommend_it_again(
    excluding_api: tuple[JobsApi, str], limit: int
) -> None:
    """The seniority narrowing has its own clause, so it needs its own guard.

    A fix applied to the eligibility clause alone would leave a shortlisted
    Principal role back at the top of a search that asked for senior execution
    work -- the exact shape of the defect, one column over.
    """
    api, victim = excluding_api
    wide = _jobs(api, limit=500, include_excluded_seniority=1)["items"]
    candidates = [i["job_id"] for i in wide if i.get("seniority") == victim]
    assert candidates, f"no posting carries {victim}, so this proves nothing"
    job_id = sorted(candidates)[0]
    try:
        api.patch_status(job_id=job_id, query={}, body={"status": "SHORTLISTED"})
        shown = {i["job_id"] for i in _recommended(api, limit)}
        assert job_id not in shown, f"a shortlisted {victim} posting returned to top {limit}"
        # ...and it is still reachable where her decisions live.
        board = _jobs(api, limit=500, status=["SHORTLISTED"])
        assert job_id in {i["job_id"] for i in board["items"]}
    finally:
        _untrack(api, job_id)
