"""The API handlers, driven directly against a real seeded database.

`test_web_boundaries.py` covers routing and refusals. This covers what the
handlers actually return, because that is what the interface renders and it had
no test at all: the route table can be perfect while `job_card` quietly emits a
blended number or loses the salary.

No socket is involved. `handle_api` is called the way the HTTP layer calls it,
which keeps these fast and makes a failure point at the handler rather than at
the transport.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import SET_VALUED_FACETS
from career_agent.storage.search_index import is_current, rebuild
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> JobsApi:
    """One seeded demo database, shared. The handlers do not mutate it unless
    a test asks them to, and the two that do use their own job."""
    tmp = tmp_path_factory.mktemp("webapi")
    db_path = tmp / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    return JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)


def _jobs(api: JobsApi, **params) -> dict:
    query = {k: (v if isinstance(v, list) else [str(v)]) for k, v in params.items()}
    return api.handle_api("GET", "/api/jobs", query, {})


# =========================================================================
# health and configuration
# =========================================================================


def test_health_reports_the_configuration_it_actually_read(api: JobsApi) -> None:
    payload = api.handle_api("GET", "/api/health", {}, {})
    assert payload["ok"] is True
    assert payload["job_count"] == 21
    assert payload["scored_count"] == 21
    # WHICHEVER file the loader resolved, not a hard-coded name. This asserted
    # the committed example and therefore passed only on a machine with no
    # `search.local.yaml` -- a test that depends on local configuration, which
    # is the one thing this suite is not allowed to do. The claim being made is
    # that health reports the file it ACTUALLY read, so that is what is checked.
    from career_agent.config.search_config import load_search_config, local_search_path

    _, resolved = load_search_config(CONFIG_DIR)
    assert payload["config_path"].endswith(resolved.name)
    assert payload["config_is_local"] is (resolved == local_search_path(CONFIG_DIR))


def test_health_does_not_contact_the_local_model(api: JobsApi) -> None:
    """A page load must never open a socket, not even to localhost.

    `reachable: null` is the honest answer before anyone asks -- `false` would
    be a claim we had checked, and the interface would tell the user their
    model is down when nobody has looked.
    """
    ollama = api.handle_api("GET", "/api/health", {}, {})["ollama"]
    assert ollama["reachable"] is None
    assert ollama["checked_at"] is None
    assert ollama["model"]


def test_health_reports_no_configuration_drift_on_a_fresh_score(api: JobsApi) -> None:
    assert api.handle_api("GET", "/api/health", {}, {})["config_drift"] == []


def test_config_exposes_the_vocabulary_the_interface_needs(api: JobsApi) -> None:
    payload = api.handle_api("GET", "/api/config", {}, {})
    assert len(payload["signals"]) > 40
    assert "PRIMARY" in payload["title_classes"]
    assert "APPLIED" in payload["statuses"]


# =========================================================================
# the list -- one endpoint, two views
# =========================================================================


def test_the_whole_corpus_comes_back_unfiltered(api: JobsApi) -> None:
    """Unfiltered means every posting except the two kinds the defaults set aside.

    THREE kinds, and keeping them apart is the point. An employer rejecting
    somebody, a search rejecting a kind of work, and an advert that never said
    where it hires are three different sentences. They have three controls and
    the response counts them separately. Only the first is a rejection.

    **EACH NUMBER IS "HOW MANY MORE YOU WOULD SEE IF YOU TICKED THIS BOX",
    which is not a partition of what is hidden.** That is the right thing for
    a BUTTON to promise, and with three narrowings it means the counts overlap
    and can legitimately read zero: every off-target posting in this corpus is
    also unresolved or ineligible, so its own box reveals nothing by itself.

    11 + 3 + 3 + 1 is 18 of 21, and the missing three are postings caught by
    more than one narrowing. **That is a real reporting gap and it is recorded
    rather than papered over.** A posting held back by two narrowings is
    mentioned by neither, and the honest fix is a fourth sentence -- "and 3
    more that more than one of these set aside" -- rather than widening what
    any of these three numbers claims to mean.

    `hidden_off_target` WAS ZERO AND IS NOW ONE, and that is what demo-021 is
    for. Every off-target posting in this corpus used to be unresolved or
    ineligible as well, so the off-target box revealed nothing on its own and
    the number had nothing to prove. demo-021 is an Administrative Assistant
    role open worldwide: the gate passes, nothing an employer wrote rules this
    candidate out, and the ONLY thing setting it aside is a systems search
    whose required signals do not fire on administration. That is the career
    changer's case, and it is now the one posting this number is about.
    """
    payload = _jobs(api)
    assert payload["total"] == 11
    assert len(payload["items"]) == 11
    assert payload["hidden_by_eligibility"] == 3
    # The third narrowing, added 2026-09-07: postings where nothing in the
    # advert said where the employer hires. Not a rejection, and not a
    # recommendation either.
    assert payload["hidden_unresolved"] == 3
    # ONE since demo-021, and the one is the career changer's posting: open
    # worldwide, nothing stated against this candidate, set aside purely
    # because the search describes different work. Before it, every off-target
    # posting was also unresolved or ineligible and this number was 0 -- true,
    # and with nothing to demonstrate.
    assert payload["hidden_off_target"] == 1

    everything = _jobs(api, include_ineligible=1, include_off_target=1, include_unresolved=1)
    assert everything["total"] == 21
    assert everything["hidden_by_eligibility"] == 0
    assert everything["hidden_unresolved"] == 0


def test_asking_for_the_unresolved_ones_beats_the_default_that_hides_them(
    api: JobsApi,
) -> None:
    """Choosing the "Did not say" filter is asking for exactly what the
    narrowing hides.

    Applying both left a facet whose count says 3 above a list that says
    nothing -- a count and a list disagreeing, which is the defect this
    codebase keeps closing. The narrowing still applies to every other query.
    """
    asked = _jobs(api, eligibility=["UNRESOLVED"])

    assert asked["total"] == 3, "the default hid the postings the filter asked for"
    assert {item["eligibility_status"] for item in asked["items"]} == {"UNRESOLVED"}
    # And nothing is reported as held back, because nothing is.
    assert asked["hidden_unresolved"] == 0


def test_count_and_items_agree_under_every_filter(api: JobsApi) -> None:
    """Cards and Table share this endpoint, so this IS proof 14 at the source."""
    for params in (
        {"min_score": 55},
        {"eligibility": ["VERIFIED_NOT_ELIGIBLE"]},
        {"role_class": ["EXCLUDED"]},
        {"search": "hubspot"},
        {"fit_band": ["STRONG"]},
        {"min_confidence": 70},
    ):
        payload = _jobs(api, limit=500, **params)
        assert payload["total"] == len(payload["items"]), params


def test_facets_total_the_unfiltered_population(api: JobsApi) -> None:
    payload = _jobs(api)
    for name in ("company", "provider", "title_class", "eligibility_status"):
        assert sum(payload["facets"][name].values()) == payload["total"], name


def test_a_search_term_only_in_the_description_finds_the_job(api: JobsApi) -> None:
    """The product's first claim: search the work, not the title."""
    payload = _jobs(api, search="lead routing", limit=500)
    assert payload["total"] >= 1
    assert not any("lead routing" in item["title"].casefold() for item in payload["items"])


def test_the_search_box_also_finds_the_place(api: JobsApi) -> None:
    """Discover's toolbar search covers title, company, place and posting text.

    The place is not in the full-text index, so it is matched separately; every
    posting whose location names the term must come back.
    """
    everything = _jobs(api, limit=500)["items"]
    for term in ("emea", "latam", "americas", "brazil", "uk"):
        expected = {
            item["job_id"] for item in everything if term in (item["location_raw"] or "").casefold()
        }
        if expected:
            break
    assert expected, "the demo corpus names no place this test can search for"
    found = {item["job_id"] for item in _jobs(api, search=term, limit=500)["items"]}
    assert expected <= found, (term, expected - found)


def test_the_search_box_escapes_like_wildcards(api: JobsApi) -> None:
    """`_` is a LIKE wildcard. Unescaped, `remote_` would match `Remote,` in
    most locations; escaped it means itself and matches nothing here."""
    assert _jobs(api, search="remote_", limit=500)["total"] == 0


@pytest.fixture(scope="module", params=["like-fallback", "fts-index"])
def placed(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[JobsApi, list[dict]]:
    """A seeded database whose first three visible postings get invented places.

    Written straight into `job.location_raw`. Run twice: once without the
    full-text index (the LIKE fallback) and once with it built, which is the
    path the product normally takes. The place is not in the index either way.
    """
    tmp = tmp_path_factory.mktemp("webapi-places")
    db_path = tmp / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    api = JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)
    chosen = _jobs(api, limit=3)["items"]
    assert len(chosen) == 3
    places = ["São Paulo, Brazil", "Zqxton, Zqxland", "Brazqxil"]
    conn = connect(db_path)
    with conn:
        for item, place in zip(chosen, places, strict=True):
            conn.execute("UPDATE job SET location_raw = ? WHERE id = ?", (place, item["job_id"]))
    if request.param == "fts-index":
        rebuild(conn)
        assert is_current(conn)
    conn.close()
    return api, chosen


def _ids(api: JobsApi, search: str) -> set[str]:
    return {item["job_id"] for item in _jobs(api, search=search, limit=500)["items"]}


def test_every_search_word_may_be_found_in_a_different_field(placed) -> None:
    """A word from the title and a word from the place, together."""
    api, chosen = placed
    title_word = next(w for w in chosen[0]["title"].casefold().split() if w.isalpha())
    assert chosen[0]["job_id"] in _ids(api, f"{title_word} brazil")
    assert chosen[0]["job_id"] in _ids(api, f"brazil {title_word}")


def test_the_place_is_matched_without_accents_or_case(placed) -> None:
    api, chosen = placed
    assert chosen[0]["job_id"] in _ids(api, "sao paulo")
    assert chosen[0]["job_id"] in _ids(api, "SÃO PAULO")


def test_the_place_is_matched_on_whole_words(placed) -> None:
    """`zqx` is not a word of "Brazqxil", so a short term cannot match inside
    a longer place name (the "us" in Australia, the "uk" in Milwaukee)."""
    api, chosen = placed
    assert chosen[1]["job_id"] in _ids(api, "zqxland")
    assert chosen[2]["job_id"] not in _ids(api, "zqx")
    assert chosen[2]["job_id"] in _ids(api, "brazqxil")


def test_an_overlong_search_is_refused(api: JobsApi) -> None:
    assert _jobs(api, search="x" * 200, limit=5)["total"] == 0
    with pytest.raises(ApiError):
        _jobs(api, search="x" * 201, limit=5)


def test_sorting_is_whitelisted(api: JobsApi) -> None:
    with pytest.raises((ApiError, ValueError)):
        _jobs(api, sort="match_score; DROP TABLE job")


def test_a_bogus_limit_is_refused_rather_than_clamped(api: JobsApi) -> None:
    with pytest.raises(ApiError):
        _jobs(api, limit=10_000)


# =========================================================================
# the card payload
# =========================================================================


def test_the_card_carries_the_facts_the_score_was_computed_from(api: JobsApi) -> None:
    """demo-001 states a salary, a contract and a remote location.

    `FULL_TIME`, not `Full-time`. The card carries the CANONICAL value, which
    is what `resolve_deterministically` folds a provider's own spelling into --
    twelve of them across nine boards -- and what all 7,184 full-time rows in
    the real corpus hold.

    It read `Full-time` for as long as the demo seeder passed the corpus
    file's string straight to the matcher without normalising it. That made
    the demo the only database in the product using a vocabulary of one, and
    is the same defect `test_demo_is_reconstructible.py` now guards: a fixture
    asserting something no collection could produce.
    """
    item = next(
        i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "northwind-systems"
    )
    assert item["salary"] == {"min": 95000, "max": 125000, "currency": "USD", "period": "YEAR"}
    assert item["employment_type"] == "FULL_TIME"
    assert item["work_model"] == "REMOTE"
    assert item["access_method"] == "ats_structured"


def test_a_posting_that_states_no_salary_says_so_rather_than_guessing(api: JobsApi) -> None:
    item = next(i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "lumen-ridge")
    assert item["salary"] is None


def test_the_three_measurements_arrive_as_three_fields(api: JobsApi) -> None:
    for item in _jobs(api, limit=500)["items"]:
        assert isinstance(item["match_score"], int)
        assert isinstance(item["data_confidence"], int)
        assert item["eligibility_status"] in {
            "VERIFIED_ELIGIBLE",
            "LIKELY_ELIGIBLE",
            "UNRESOLVED",
            "VERIFIED_NOT_ELIGIBLE",
        }
        assert not {"overall", "combined", "final_score", "rating"} & set(item)


def test_a_blocked_job_carries_its_blocker_and_its_quote(api: JobsApi) -> None:
    # `include_ineligible` because this posting is one of the three the default
    # now hides. The reason still has to travel with it when it is asked for.
    items = _jobs(api, limit=500, include_ineligible=1, include_off_target=1)["items"]
    item = next(i for i in items if i["company_slug"] == "bright-harbor")
    assert item["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
    assert item["blockers"], "a blocked job must say what blocked it"
    assert item["blockers"][0]["quote"]
    assert item["match_score"] > 40, "and it must still show the score it earned"


def test_an_eligible_job_is_not_captioned_with_an_irrelevant_unknown(api: JobsApi) -> None:
    """The primary gap must not parade an exclusionary gate at an eligible job."""
    for item in _jobs(api, limit=500)["items"]:
        if item["eligibility_status"] == "VERIFIED_ELIGIBLE" and item["primary_gap"]:
            assert item["primary_gap"]["kind"] != "UNRESOLVED"


# =========================================================================
# the detail payload
# =========================================================================


def test_the_detail_justifies_every_number(api: JobsApi) -> None:
    job_id = _jobs(api, limit=1)["items"][0]["job_id"]
    detail = api.handle_api("GET", f"/api/jobs/{job_id}", {}, {})

    # Five, since `role_family` stopped paying for the title -- and a sixth,
    # "Way of working", because the committed example states a preference.
    ids = [c["component_id"] for c in detail["components"]]
    assert ids[:5] == [
        "responsibilities",
        "technologies",
        "automation_integration",
        "seniority",
        "compensation_contract",
    ]
    assert ids[5:] == ["work_model"]
    assert "role_family" not in {c["component_id"] for c in detail["components"]}
    # The budget, whatever it is. The score is a percentage of this sum, so
    # what matters is that the detail exposes it rather than that it is 100.
    assert sum(c["max_points"] for c in detail["components"]) > 0
    assert any(c["contributions"] for c in detail["components"])
    assert all(
        contribution["quote"]
        for component in detail["components"]
        for contribution in component["contributions"]
        if contribution["signal_id"] not in ("role_family", "seniority", "compensation")
        and not contribution["signal_id"].startswith("work_model")
        and contribution["points"] > 0
        and contribution.get("quote") is not None
    )
    assert detail["gates"], "every gate is reported, including the unresolved ones"
    assert (
        any(not item["awarded"] for item in detail["confidence_items"])
        or detail["data_confidence"] == 100
    )
    assert detail["description"]


def test_an_unknown_job_id_is_a_404(api: JobsApi) -> None:
    with pytest.raises(ApiError) as exc:
        api.handle_api("GET", "/api/jobs/01NOTAREALJOBID", {}, {})
    assert exc.value.status == 404


# =========================================================================
# mutations
# =========================================================================


def test_setting_applied_records_a_date_and_a_history_entry(api: JobsApi) -> None:
    job_id = next(i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "tessera-labs")[
        "job_id"
    ]

    updated = api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "APPLIED"})
    assert updated["application_status"] == "APPLIED"
    assert updated["applied_at"], "moving to APPLIED must not lose the date"
    assert updated["has_applied"] is True

    detail = api.handle_api("GET", f"/api/jobs/{job_id}", {}, {})
    assert detail["history"][-1]["to_status"] == "APPLIED"


def test_stepping_back_from_applied_keeps_the_date(api: JobsApi) -> None:
    """A status move is not a claim about whether you applied. ADR-0012."""
    job_id = next(i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "cobalt-field")[
        "job_id"
    ]
    applied = api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "APPLIED"})
    stamped = applied["applied_at"]
    assert stamped

    back = api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})
    assert back["applied_at"] == stamped, "one drag on the board erased the date"
    assert back["has_applied"] is True


def test_clearing_the_date_takes_its_own_route_and_its_own_request(api: JobsApi) -> None:
    """The separate route is the whole mechanism, so it is asserted directly.

    Three things at once, because they are one design: `/status` cannot clear
    a date however it is asked, `/applied-at` can, and `/applied-at` will not
    read an empty body as permission to.
    """
    job_id = next(
        i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "quill-and-vane"
    )["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "APPLIED"})
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})

    # An explicit null on /status does not clear it either: on that route the
    # date is incidental, so absence and null both mean "leave it alone".
    kept = api.handle_api(
        "PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED", "applied_at": None}
    )
    assert kept["applied_at"]

    # An empty body is a request that forgot to say anything, not a request to
    # delete. Absence is never permission -- including here.
    with pytest.raises(ApiError) as missing:
        api.handle_api("PATCH", f"/api/jobs/{job_id}/applied-at", {}, {})
    assert missing.value.status == 400

    cleared = api.handle_api("PATCH", f"/api/jobs/{job_id}/applied-at", {}, {"applied_at": None})
    assert cleared["applied_at"] is None
    assert cleared["has_applied"] is False


def test_a_date_the_status_would_restore_is_refused_with_a_reason(api: JobsApi) -> None:
    """409, naming the move that would make the request legal."""
    job_id = next(i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "lumen-ridge")[
        "job_id"
    ]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "INTERVIEW"})

    with pytest.raises(ApiError) as refusal:
        api.handle_api("PATCH", f"/api/jobs/{job_id}/applied-at", {}, {"applied_at": None})
    assert refusal.value.status == 409
    assert "Move the status first" in str(refusal.value)

    still = api.handle_api("GET", f"/api/jobs/{job_id}", {}, {})
    assert still["applied_at"], "the refusal wrote something anyway"


def test_setting_a_date_explicitly_does_not_move_the_status(api: JobsApi) -> None:
    job_id = next(
        i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "ativa-sistemas"
    )["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})
    updated = api.handle_api(
        "PATCH", f"/api/jobs/{job_id}/applied-at", {}, {"applied_at": "2026-04-04"}
    )
    assert updated["applied_at"] == "2026-04-04"
    assert updated["application_status"] == "SHORTLISTED"
    assert updated["has_applied"] is True

    with pytest.raises(ApiError) as bad:
        api.handle_api("PATCH", f"/api/jobs/{job_id}/applied-at", {}, {"applied_at": "04/04/2026"})
    assert bad.value.status == 400


def test_an_unknown_status_is_refused(api: JobsApi) -> None:
    job_id = _jobs(api, limit=1)["items"][0]["job_id"]
    with pytest.raises(ApiError) as exc:
        api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "MAYBE_LATER"})
    assert exc.value.status == 400


def test_a_malformed_applied_date_is_refused(api: JobsApi) -> None:
    job_id = _jobs(api, limit=1)["items"][0]["job_id"]
    with pytest.raises(ApiError):
        api.handle_api(
            "PATCH",
            f"/api/jobs/{job_id}/status",
            {},
            {"status": "APPLIED", "applied_at": "yesterday"},
        )


def test_saving_a_job_does_not_disturb_its_status(api: JobsApi) -> None:
    job_id = next(
        i for i in _jobs(api, limit=500)["items"] if i["company_slug"] == "ferris-automation"
    )["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "SHORTLISTED"})
    saved = api.handle_api("PATCH", f"/api/jobs/{job_id}/saved", {}, {"saved": True})
    assert saved["saved"] is True
    assert saved["application_status"] == "SHORTLISTED"


def test_manual_import_scores_immediately_and_keeps_its_provenance(api: JobsApi) -> None:
    payload = api.handle_api(
        "POST",
        "/api/import",
        {},
        {
            "title": "Workflow Automation Engineer",
            "company": "Pasted Board Co",
            "description": (
                "You will own workflow automation and business process automation, "
                "building REST API integrations and webhooks between our CRM and our "
                "internal systems. We use n8n. We hire globally and work from anywhere."
            ),
            "url": "https://example.invalid/vaga/1",
        },
    )
    assert payload["access_method"] == "manual_import"
    assert payload["match_score"] > 0
    assert payload["eligibility_status"] == "VERIFIED_ELIGIBLE"


def test_an_import_without_a_description_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError) as exc:
        api.handle_api("POST", "/api/import", {}, {"title": "X", "company": "Y"})
    assert exc.value.status == 400


# =========================================================================
# duplicate grouping -- one role, several postings
# =========================================================================


#: One role, published three times with a different location line, exactly as
#: an employer does it. The texts differ, so `content_hash` would separate them
#: and `(company, title)` will not -- which is why the pair is the key.
_ONE_ROLE = (
    "Own workflow automation and business process automation end to end, "
    "building REST API integrations and webhooks between our CRM and our "
    "internal systems. We use n8n. We hire globally and work from anywhere. "
    "This position is based in {place}."
)


@pytest.fixture
def grouped_api(tmp_path: Path) -> JobsApi:
    """Its own database. The shared `api` fixture is module-scoped and several
    tests assert exact totals against it; seeding duplicates into it would make
    this test's setup break theirs from a distance."""
    db_path = tmp_path / "grouped.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    api = JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)
    for place in ("Austin, TX", "New York, NY", "Remote - US"):
        api.handle_api(
            "POST",
            "/api/import",
            {},
            {
                "title": "Forward Deployed Engineer",
                "company": "Workato",
                "description": _ONE_ROLE.format(place=place),
                "location_raw": place,
            },
        )
    return api


def test_the_grouping_parameter_round_trips_and_collapses_the_repeats(
    grouped_api: JobsApi,
) -> None:
    """The three imports are three distinct postings, and the API must be able
    to say so BOTH ways: ungrouped is the truth about the board, grouped is the
    truth about the role."""
    # ALL THREE widenings, because both numbers here are claims about the
    # CORPUS rather than about what a person is shown by default. The third
    # was added 2026-09-07 and a grouping test that quietly stopped seeing a
    # third of the corpus would still pass while proving less.
    widen = {"include_ineligible": 1, "include_off_target": 1, "include_unresolved": 1}
    ungrouped = _jobs(grouped_api, limit=500, **widen)
    grouped = _jobs(grouped_api, limit=500, **widen, group_duplicates="1")

    assert ungrouped["total"] == 24, "twenty-one demo postings plus the three imported here"
    assert grouped["total"] == 20, (
        "nineteen demo roles -- its own trio already collapsed -- plus this one"
    )
    assert grouped["total"] == len(grouped["items"])


def test_grouping_is_off_unless_the_query_asks_for_it(grouped_api: JobsApi) -> None:
    """The default that makes this safe, asserted at the boundary the browser
    actually talks to -- including the shapes a client might send by accident."""
    baseline = _jobs(grouped_api, limit=500)["total"]
    for value in ("0", "false", "", "no"):
        assert _jobs(grouped_api, limit=500, group_duplicates=value)["total"] == baseline, value


def test_the_card_says_what_it_stands_for(grouped_api: JobsApi) -> None:
    """Grouping HIDES NOTHING: the count and the places travel with the row."""
    # This assertion concerns all reposts. Remote - US is now unresolved for
    # Brazil despite generic worldwide prose and needs the explicit widening.
    items = _jobs(grouped_api, limit=500, group_duplicates="1", include_unresolved=1)["items"]
    representative = next(job for job in items if job["company_name"] == "Workato")

    assert representative["duplicate_count"] == 3
    assert sorted(representative["sibling_locations"]) == [
        "Austin, TX",
        "New York, NY",
        "Remote - US",
    ]
    # The row's own location leads, so the card is about somewhere before it is
    # about everywhere else.
    assert representative["sibling_locations"][0] == representative["location_raw"]


#: The two employers in `grouped_api` that publish a role more than once: the
#: imported trio, and the demo corpus's own trio. Everything else is a
#: singleton, and "everything else" is what several assertions below are about.
_REPEATERS = frozenset({"Workato", "Northwind Systems"})


def test_an_ordinary_posting_reports_a_group_of_one(grouped_api: JobsApi) -> None:
    """`1` and `[]` on every card, grouped or not, so the interface never has
    to tell "one posting" apart from "nobody asked"."""
    for params in ({}, {"group_duplicates": "1"}):
        items = _jobs(grouped_api, limit=500, **params)["items"]
        singletons = [job for job in items if job["company_name"] not in _REPEATERS]
        assert singletons
        assert {job["duplicate_count"] for job in singletons} == {1}
        assert all(job["sibling_locations"] == [] for job in singletons)


# =========================================================================
# the same behaviour, in the DEMO corpus -- so a reviewer can SEE it
# =========================================================================
#
# Everything above imports its own duplicates, which proves the mechanism and
# proves nothing about what `seed-demo` puts on screen. These use the plain
# demo database, because the point of `evaluation/demo/` is that the reviewer
# does not have to take a behaviour on trust.


@pytest.fixture(scope="module")
def demo_api(tmp_path_factory: pytest.TempPathFactory) -> JobsApi:
    """The demo corpus and nothing else.

    The shared `api` fixture is module-scoped and one test imports a posting
    into it, so its totals depend on execution order. These tests assert exact
    corpus counts, which is precisely the assertion that must not be able to
    drift because a neighbour wrote a row.
    """
    db_path = tmp_path_factory.mktemp("demo-only") / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    return JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)


def test_the_demo_corpus_actually_contains_a_duplicate_group(demo_api: JobsApi) -> None:
    """Nineteen postings, seventeen roles. Both numbers reach the interface.

    Asked for with ALL THREE widenings, because these numbers are about the
    CORPUS rather than about what a person is shown by default, and the
    defaults set aside three different kinds of posting.
    """
    everything = {
        "limit": 500,
        "include_ineligible": 1,
        "include_off_target": 1,
        "include_unresolved": 1,
    }
    assert _jobs(demo_api, **everything)["total"] == 21
    assert _jobs(demo_api, **everything, group_duplicates="1")["total"] == 19


def test_the_demo_group_elects_the_posting_the_corpus_says_it_will(demo_api: JobsApi) -> None:
    """demo-001 stands for the trio, and it wins on the TIE-BREAK.

    All three score identically by construction (`same_score_as` in the corpus
    asserts that half), so `MAX(match_score)` cannot separate them and the
    election falls through to `MIN(j.id)`. ULIDs are time-sortable, `seed_demo`
    inserts in file order, so the lowest id is the entry written first in
    `demo_postings.yaml`. Reordering those three entries would change which
    card a reviewer sees -- which is why this asserts the external id and not
    merely "some Northwind posting".
    """
    items = _jobs(demo_api, limit=500, group_duplicates="1")["items"]
    northwind = [job for job in items if job["company_slug"] == "northwind-systems"]
    assert len(northwind) == 1, "the trio must collapse to exactly one card"

    representative = northwind[0]
    assert representative["external_id"] == "demo-001", (
        "the first-written of three tied siblings must win the election"
    )
    assert representative["duplicate_count"] == 3
    assert representative["sibling_locations"] == [
        "Remote, worldwide",
        "Remote, worldwide (Americas)",
        "Remote, worldwide (EMEA)",
    ], "its own place leads, and the rest follow in sorted order"


def test_no_demo_sibling_is_lost_by_being_grouped(demo_api: JobsApi) -> None:
    """All three survive scoring, and the card that stands for them says three.

    Grouping is a collapse of rows that were going to be shown, never a second
    filter: `sum(duplicate_count)` over the grouped page must equal the
    ungrouped total.
    """
    ungrouped = _jobs(demo_api, limit=500)["items"]
    trio = [job for job in ungrouped if job["company_slug"] == "northwind-systems"]
    assert sorted(job["external_id"] for job in trio) == ["demo-001", "demo-018", "demo-019"]
    assert {job["scored"] for job in trio} == {True}
    assert len({job["match_score"] for job in trio}) == 1, "the tie is what the election needs"
    assert {job["eligibility_status"] for job in trio} == {"VERIFIED_ELIGIBLE"}

    grouped = _jobs(demo_api, limit=500, group_duplicates="1")
    assert sum(job["duplicate_count"] for job in grouped["items"]) == len(ungrouped)


def test_the_demo_group_leads_the_list_it_is_shown_in(demo_api: JobsApi) -> None:
    """The representative sorts first, which is what makes it PHOTOGRAPHABLE.

    `docs/evidence/browser/cards-desktop.png` is the evidence that duplicate
    grouping exists at all, and a badge below the fold is not evidence. The
    default sort is score descending and demo-001 scores highest in the corpus,
    so the grouped card is the first one drawn. If that ever stops being true
    the screenshot silently stops showing the feature -- hence a test rather
    than a comment.
    """
    first = _jobs(demo_api, limit=500, group_duplicates="1")["items"][0]
    assert first["company_slug"] == "northwind-systems"
    assert first["duplicate_count"] == 3


def test_the_drawer_lists_the_other_locations(grouped_api: JobsApi) -> None:
    items = _jobs(grouped_api, limit=500, group_duplicates="1")["items"]
    job_id = next(job for job in items if job["company_name"] == "Workato")["job_id"]

    detail = grouped_api.handle_api("GET", f"/api/jobs/{job_id}", {}, {})
    assert detail["duplicate_count"] == 3
    assert len(detail["sibling_locations"]) == 3


def test_grouped_facets_still_total_the_grouped_count(grouped_api: JobsApi) -> None:
    """`total`, `items` and `facets` come from one WHERE clause. This is that
    promise held with the grouping clause in play."""
    payload = _jobs(grouped_api, limit=500, group_duplicates="1")
    for name, buckets in payload["facets"].items():
        # A posting can be in two countries and fire six signals, so those
        # buckets overlap by construction; `test_filter_contract.py` holds them
        # to the property that DOES apply -- each bucket equals its own filter.
        if name in SET_VALUED_FACETS:
            continue
        assert sum(buckets.values()) == payload["total"], name


def test_an_import_with_a_javascript_url_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError):
        api.handle_api(
            "POST",
            "/api/import",
            {},
            {
                "title": "X",
                "company": "Y",
                "description": "a" * 200,
                "url": "javascript:alert(1)",
            },
        )


# =========================================================================
# The filter contract: an unrecognised filter is refused, never forgiven
# =========================================================================


def test_an_unknown_filter_parameter_is_refused_not_silently_ignored(
    api: JobsApi,
) -> None:
    """The defect the person actually felt, and the fourth of its kind here.

    Measured over HTTP against the 18,549-posting corpus before this fix:

        /api/jobs?totally_unknown_param=xyz  ->  total 18549
        /api/jobs?companies=vanta            ->  total 18549
        /api/jobs?company=vanta              ->  total   106

    `companies` is the plural -- what `JobFilter` calls the field -- so the
    obvious guess returned the entire corpus dressed as a filtered result,
    with no error anywhere. That is indistinguishable from "the filters do not
    work", which is exactly how it was reported.

    A wrong VALUE was already a 400. Only a wrong NAME was forgiven, which is
    the worse half: the loud failure was on the typo you can see.
    """
    unfiltered = _jobs(api)["total"]
    assert unfiltered > 0

    for bad in ("companies", "providers", "statuses", "totally_unknown_param"):
        with pytest.raises(ApiError) as caught:
            _jobs(api, **{bad: "anything"})
        assert caught.value.status == 400
        assert bad in str(caught.value)
        # The message must name what IS accepted, or the reader is left
        # guessing which spelling the server wanted.
        assert "accepted:" in str(caught.value)


def test_a_value_outside_a_closed_vocabulary_is_refused(api: JobsApi) -> None:
    """ "No results" and "not a value this system has" are different answers.

    `role_class=primary` used to return zero rows in silence, because the
    column stores `PRIMARY`. An empty list that means "you typed it wrong" is
    indistinguishable from an empty list that means "nothing matched", and the
    person cannot tell which one they are looking at.
    """
    for key, bad in (
        ("role_class", "nonsense"),
        ("status", "NOPE"),
        ("eligibility", "MAYBE"),
        ("fit_band", "EXCELLENT"),
        ("sort", "bogus"),
        ("direction", "sideways"),
    ):
        with pytest.raises(ApiError) as caught:
            _jobs(api, **{key: bad})
        assert caught.value.status == 400
        assert key in str(caught.value)


def test_a_closed_vocabulary_accepts_the_case_the_person_typed(
    api: JobsApi,
) -> None:
    """Case-normalised rather than case-strict: `primary` is not a mistake.

    Rejecting it would be technically defensible and practically hostile --
    the stored spelling is an implementation detail of the column.
    """
    lower = _jobs(api, role_class="primary")
    upper = _jobs(api, role_class="PRIMARY")
    assert lower["total"] == upper["total"]

    lower_status = _jobs(api, status="applied")
    upper_status = _jobs(api, status="APPLIED")
    assert lower_status["total"] == upper_status["total"]


def test_a_boolean_flag_refuses_a_value_it_cannot_read(api: JobsApi) -> None:
    """`saved_only=yes` meant False, because anything outside the true-set was
    read as off. A switch the person flipped and the server ignored is the
    same defect as an unknown parameter name, one layer down."""
    assert _jobs(api, saved_only="yes")["total"] == _jobs(api, saved_only="true")["total"]
    assert _jobs(api, saved_only="off")["total"] == _jobs(api)["total"]

    for bad in ("maybe", "sometimes", "1.5"):
        with pytest.raises(ApiError) as caught:
            _jobs(api, saved_only=bad)
        assert caught.value.status == 400


def test_every_accepted_parameter_is_actually_parsed(api: JobsApi) -> None:
    """The allowlist and the parser must not drift apart.

    Two hand-maintained lists that have to agree, with nothing checking, is
    the shape of the `SALARY_CONFIDENCE_ITEM` bug this repository already
    shipped once: the constant said `compensation_stated`, the scorer emitted
    `salary_known`, and the test hard-coded the same wrong string so it agreed
    with the defect. Every name in the allowlist is exercised here, so a name
    that is accepted but unread fails instead of pretending to filter.
    """
    import dataclasses

    from career_agent.storage.mvp_repo import JobFilter
    from career_agent.web.api import JOB_QUERY_PARAMS

    sample = {
        "search": "engineer",
        "company": "northwind-systems",
        "provider": "greenhouse",
        "role_class": "PRIMARY",
        "status": "DISCOVERED",
        "eligibility": "UNRESOLVED",
        "fit_band": "STRONG",
        "signal": "hubspot_platform",
        "min_score": "10",
        "max_score": "100",
        "min_confidence": "10",
        "saved_only": "true",
        "enriched_only": "true",
        "has_salary": "true",
        "remote_only": "true",
        # "false", not "true": the API layer's default IS False, so sending
        # true would parse correctly and still equal the dataclass default,
        # which would make this assertion pass without proving anything.
        "include_ineligible": "false",
        "include_unresolved": "false",
        "include_excluded_seniority": "false",
        "include_excluded_work_model": "false",
        "include_off_target": "false",
        # The third narrowing, and the one she applies herself. Same "false"
        # for the same reason.
        "include_user_hidden": "false",
        "user_hidden_only": "true",
        "group_duplicates": "true",
        "posted_within_days": "365",
        # -- the twelve section 16 listed as absent ---------------------
        "country": "BR",
        "region": "LATAM",
        "latam_only": "true",
        "worldwide_only": "true",
        "worksite": "REMOTE",
        "seniority": "SENIOR",
        "employment_type": "FULL-TIME",
        "min_salary": "1000",
        "salary_currency": "USD",
        "salary_period": "YEAR",
        "technology": "hubspot_platform",
        # Migration 0020. HOW A POSTING READS, never whether she may take it:
        # the two live in different columns behind different parameters so
        # that a query for one can never answer the other.
        "employment_context": "LIKELY_US_DOMESTIC",
        "contract_regime": "CLT",
        # Migration 0022. HOW MUCH OF THE POSTING we hold, which is provenance
        # and shares a column with nothing: a source's limitation must never
        # be askable as a question about an employer.
        "content_completeness": "FULL_CONTENT",
        "keyword": "integration",
        "exclude_keyword": "clearance",
        # The soft pair. They sort rather than filter, so unlike every
        # name above them they must leave `count` and `facets` alone --
        # asserted in `tests/integration/test_keyword_chips.py`.
        "prefer_keyword": "documentation",
        "avoid_keyword": "clearance",
        # Migration 0027. WHAT THE POSTING ASKED FOR, and who it invited.
        # Three separate parameters because they are three different
        # questions, and a posting that PREFERS three years and one that
        # REQUIRES them must never be askable as one.
        "experience_requirement": "NONE_REQUIRED",
        "experience_max_years": "2",
        "entry_signal": "ENTRY_LEVEL",
        # "true", not "false", and unlike the narrowings above: the dataclass
        # default is an EMPTY TUPLE, so sending true reaches the filter with
        # whatever has been confirmed -- which on this fixture is nothing, and
        # an empty tuple either way. Asserted separately in
        # test_the_transition_control_only_widens.
        "include_transferable": "true",
        "sort": "score",
        "direction": "desc",
        "limit": "10",
        "offset": "5",
    }
    assert set(sample) == set(JOB_QUERY_PARAMS), (
        "the allowlist and this test must name the same parameters"
    )

    # `total >= 0` was the assertion here, and a reviewer was right that it
    # cannot fail: it holds for an implementation that ignores every
    # parameter, which is the exact thing this test claims to catch.
    #
    # So each name is checked to actually REACH the filter: parse it alone and
    # require the resulting JobFilter to differ from the default. A parameter
    # that is accepted and unread leaves the filter untouched and fails here.
    default = JobFilter()
    for name, value in sample.items():
        # `min_salary` is the one parameter that is INVALID on its own: nothing
        # in this system converts between currencies, so a bare figure would
        # compare amounts that are not comparable and the API refuses it. It is
        # parsed with its currency rather than exempted, so it is still held to
        # the same "must reach the filter" standard as everything else.
        query = {name: [value]}
        if name == "min_salary":
            query["salary_currency"] = ["USD"]
        built = api._filter_from(query)
        changed = [
            field.name
            for field in dataclasses.fields(JobFilter)
            if getattr(built, field.name) != getattr(default, field.name)
        ]
        if name in ("sort", "direction"):
            # These two have no "unset" value: the default IS a real choice,
            # so parsing them correctly cannot show up as a difference. Their
            # coverage is `test_every_sort_the_interface_offers_...`, which
            # exercises every value the interface can actually send.
            continue
        if name == "include_transferable":
            # The one parameter whose effect depends on DATA RATHER THAN ON THE
            # REQUEST. It is read -- `_filter_from` calls
            # `transferable_signals()` for it -- and on a fixture where nothing
            # has been confirmed that call correctly returns an empty tuple,
            # which equals the default. That is the control doing the right
            # thing, not the control being ignored.
            #
            # Proved instead by `test_the_transition_control_reaches_the_filter`,
            # which confirms a claim first and then asserts the difference.
            continue
        assert changed, f"{name} is accepted by the allowlist and read by nothing"

    # And all of them together still parse.
    assert _jobs(api, **sample)["total"] >= 0


def test_every_sort_the_interface_offers_is_one_the_server_accepts(
    api: JobsApi,
) -> None:
    """Two of six sort options were dead, and nothing noticed.

    The dropdown offered `posted_at`; the server's vocabulary says `posted`.
    Picking "Posted date" -- or clicking the Posted column header in the table
    -- produced a 400 and an empty list. It survived because the allowlist was
    built on the server and never reconciled with the client that feeds it.

    This reads the sort values straight out of the frontend source, so the two
    cannot drift again without failing here.
    """
    import re

    static = REPO_ROOT / "src" / "career_agent" / "web" / "static" / "js"
    offered: set[str] = set()

    # `SORTS` is a list of VALUES now -- the words moved to the catalogue, so
    # a label written beside a value could no longer freeze in English -- so
    # this reads the values themselves rather than a `value:` field.
    state = (static / "state.js").read_text(encoding="utf-8")
    block = state[state.index("export const SORTS") :]
    offered.update(re.findall(r"'([a-z_]+)'", block[: block.index("]")]))

    table = (static / "table.js").read_text(encoding="utf-8")
    offered.update(re.findall(r"sort:\s*'([a-z_]+)'", table))

    assert offered, "no sort values were found in the frontend; this test went blind"
    for value in sorted(offered):
        # Must not raise: every one is a sort the server will honour.
        _jobs(api, sort=value)


# =========================================================================
# eligibility visibility: what is hidden by default, and what never is
# =========================================================================


def test_the_default_list_recommends_only_what_it_can_vouch_for(api: JobsApi) -> None:
    """A DELIBERATE REVERSAL, 2026-09-07, and the reasoning is worth keeping.

    This test used to assert the opposite: that a posting which never said
    where it hires stays VISIBLE, because hiding it would turn a missing
    sentence into a rejection.

    That reasoning was right about what UNRESOLVED MEANS and wrong about what a
    DEFAULT LIST IS. Measured on the real corpus: 12,574 of 19,469 open
    postings are unresolved on geography, and mixed into best matches they
    buried the ones she can take. The twenty highest-scoring were a hybrid role
    in Gurugram, offices in Boston, Dublin, Bengaluru and San Francisco, and
    one job she could actually apply to. Compatibility ranked them all and
    eligibility never got to speak.

    Being unable to vouch for a job is not a rejection, and it is also not a
    recommendation. So UNRESOLVED keeps its meaning, keeps its own population,
    keeps its own count and its own control -- and leaves the list whose whole
    claim is "these are your best matches".

    Nothing is deleted, nothing is closed, and one click brings them back.
    """
    shown = {i["eligibility_status"] for i in _jobs(api, limit=500)["items"]}
    assert "VERIFIED_NOT_ELIGIBLE" not in shown
    assert "UNRESOLVED" not in shown
    assert "VERIFIED_ELIGIBLE" in shown


def test_silence_is_one_click_away_and_counted(api: JobsApi) -> None:
    """The half that keeps the reversal honest.

    A narrowing that is on by default and says nothing is a silent filter, and
    this codebase has fixed that defect once already. The count is reported and
    the control puts back exactly what it reported.
    """
    narrowed = _jobs(api, limit=500)
    assert narrowed["hidden_unresolved"] > 0

    widened = _jobs(api, limit=500, include_unresolved=1)
    statuses = {i["eligibility_status"] for i in widened["items"]}

    assert "UNRESOLVED" in statuses
    assert widened["total"] == narrowed["total"] + narrowed["hidden_unresolved"]
    assert widened["hidden_unresolved"] == 0


def test_a_low_score_is_never_treated_as_ineligible(api: JobsApi) -> None:
    """A weak match is not a conflict, and must not be hidden by this."""
    items = _jobs(api, limit=500)["items"]
    low = [i for i in items if (i["match_score"] or 0) < 40]
    assert low, "the demo corpus must list a low-scoring posting to prove this"
    assert all(i["eligibility_status"] != "VERIFIED_NOT_ELIGIBLE" for i in low)


def test_the_hidden_count_is_what_the_toggle_would_reveal(api: JobsApi) -> None:
    """Counted, not estimated, and it has to hold under another filter too.

    The comparison is against the ITEMS, not against the two totals. Asserting
    `wide["total"] - narrow["total"] == hidden_by_eligibility` reads like a
    check and is not one: `hidden_by_eligibility` is literally implemented as
    `count(widened) - count(f)`, so the assertion was `A - B == A - B` and
    would have held with the narrowing switched off entirely. Found by an
    independent functional review.

    Counting the returned rows asks the question from the other side: how many
    of the postings the wider query returns are ones the narrow view is
    entitled to hide? In a DISCOVERY request that is every posting whose gate
    failed against stated text -- tracking no longer exempts one, because a
    recommendation list is not where a decision she already took is kept.
    See `JobFilter.exempt_tracked`.
    """
    for params in ({}, {"min_score": 40}, {"worksite": ["REMOTE"]}):
        narrow = _jobs(api, limit=500, **params)
        wide = _jobs(api, limit=500, include_ineligible=1, **params)

        hideable = [
            item for item in wide["items"] if item["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
        ]
        assert narrow["hidden_by_eligibility"] == len(hideable), params
        assert len(wide["items"]) - len(narrow["items"]) == len(hideable), params

        # And none of them survived into the narrow view.
        narrow_ids = {item["job_id"] for item in narrow["items"]}
        assert not (narrow_ids & {item["job_id"] for item in hideable}), params


def test_counts_pagination_and_facets_all_reflect_the_narrowing(api: JobsApi) -> None:
    """Three numbers on one screen have to agree, or none of them is trusted."""
    payload = _jobs(api, limit=500)
    assert payload["total"] == len(payload["items"])

    # A facet bucket must count what the list would actually return.
    buckets = payload["facets"]["eligibility_status"]
    assert buckets.get("VERIFIED_NOT_ELIGIBLE") is None
    assert sum(buckets.values()) == payload["total"]

    # And pagination pages the narrowed population, not the wider one.
    first = _jobs(api, limit=10, offset=0)
    second = _jobs(api, limit=10, offset=10)
    assert first["total"] == second["total"] == payload["total"]
    assert len(first["items"]) + len(second["items"]) == payload["total"]


def _a_hidden_job(api: JobsApi) -> str:
    """A posting that the eligibility default is hiding RIGHT NOW.

    Computed as the difference between the two populations rather than by
    taking the first conflict, because the `api` fixture is module-scoped and a
    conflict another test has already tracked is no longer hidden.
    """
    wide = {
        i["job_id"]
        for i in _jobs(api, limit=500, include_ineligible=1, include_off_target=1)["items"]
    }
    narrow = {i["job_id"] for i in _jobs(api, limit=500)["items"]}
    hidden = sorted(wide - narrow)
    assert hidden, "the demo corpus must hide at least one posting for this to mean anything"
    return hidden[0]


def _untrack(api: JobsApi, job_id: str) -> None:
    """Put a posting back where the module found it."""
    api.patch_saved(job_id=job_id, query={}, body={"saved": False})
    api.patch_status(job_id=job_id, query={}, body={"status": "DISCOVERED"})
    api.patch_applied_at(job_id=job_id, query={}, body={"applied_at": None})


def test_tracking_preserves_access_without_restoring_a_recommendation(
    api: JobsApi,
) -> None:
    """The 2026-09-07 correction, in the one place it is easiest to get wrong.

    A job she saved or applied to must not vanish -- she already made a
    decision about it, and the interface's job is to warn her, not to quietly
    retract it. That promise is kept by the TRACKING views. It is not kept by
    putting the posting back on the list of jobs this product is recommending,
    which is what the unconditional exemption used to do: the morning audit
    found one ineligible posting in the default top 50 of the real corpus, and
    it was there because she had shortlisted it.

    Four things are asserted together, because any one of them alone can be
    made true in a way that breaks the others:

      GONE       from default discovery, whether saved or applied;
      REACHABLE  through Saved and through the Applications board;
      REACHABLE  directly by id, which no narrowing touches;
      RESTRICTED plainly, wherever it is shown.
    """
    job_id = _a_hidden_job(api)
    try:
        api.patch_saved(job_id=job_id, query={}, body={"saved": True})
        assert job_id not in {i["job_id"] for i in _jobs(api, limit=500)["items"]}, (
            "saving a posting an employer ruled her out of put it back on the recommendation list"
        )
        saved = _jobs(api, limit=500, saved_only=1)
        assert job_id in {i["job_id"] for i in saved["items"]}, (
            "a saved posting was not reachable from Saved"
        )

        api.patch_status(job_id=job_id, query={}, body={"status": "APPLIED"})
        assert job_id not in {i["job_id"] for i in _jobs(api, limit=500)["items"]}
        board = _jobs(api, limit=500, status=["APPLIED"])
        assert job_id in {i["job_id"] for i in board["items"]}, (
            "a posting she applied to was not on the Applications board"
        )

        # Direct access, and the restriction travels with it.
        detail = api.get_job(job_id=job_id, query={}, body={})
        assert detail["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
        assert detail["application_status"] == "APPLIED"
        # ...and on the board row too, so she is not told only in the drawer.
        row = next(i for i in board["items"] if i["job_id"] == job_id)
        assert row["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE"
    finally:
        _untrack(api, job_id)


def test_history_survives_the_posting_leaving_the_recommendations(
    api: JobsApi,
) -> None:
    """Leaving the recommended population is a VIEW decision and nothing else.

    No status is cleared, no date is rewritten and no event is dropped. This is
    the half of the correction that would be easy to "fix" destructively, so it
    is asserted rather than promised.
    """
    job_id = _a_hidden_job(api)
    try:
        api.patch_status(
            job_id=job_id, query={}, body={"status": "APPLIED", "applied_at": "2026-09-01"}
        )
        api.patch_status(job_id=job_id, query={}, body={"status": "INTERVIEW"})

        detail = api.get_job(job_id=job_id, query={}, body={})
        assert detail["application_status"] == "INTERVIEW"
        assert detail["applied_at"] == "2026-09-01"
        assert len(detail["history"]) >= 2, "the trail of her own moves was truncated"
    finally:
        _untrack(api, job_id)


def test_a_tracked_job_still_counts_as_hidden(api: JobsApi) -> None:
    """The disclosure promises exactly what the toggle would add.

    The reverse of what this test asserted before 2026-09-07, and for the same
    reason the narrowing changed: a tracked ineligible posting IS hidden from
    discovery now, so ticking "show these" really does add it back and the
    count really does include it. The invariant never moved -- the disclosure
    must equal what the control does -- only the behaviour it describes.
    """
    job_id = _a_hidden_job(api)
    first = _jobs(api, limit=500)
    before = first["hidden_by_eligibility"] + first["hidden_off_target"]
    try:
        api.patch_status(job_id=job_id, query={}, body={"status": "SHORTLISTED"})
        after = _jobs(api, limit=500)
        assert after["hidden_by_eligibility"] + after["hidden_off_target"] == before
        assert after["total"] == len(after["items"])

        # And the promise is real: the toggle adds it.
        widened = _jobs(api, limit=500, include_ineligible=1, include_off_target=1)
        assert job_id in {i["job_id"] for i in widened["items"]}
    finally:
        _untrack(api, job_id)


def test_asking_for_the_conflicts_explicitly_still_works(api: JobsApi) -> None:
    """The existing `eligibility` filter is a different control and still is.

    Somebody who deliberately asks to see only the conflicts gets them: the
    default narrowing is a default, not a prohibition.
    """
    payload = _jobs(api, limit=500, include_ineligible=1, eligibility=["VERIFIED_NOT_ELIGIBLE"])
    assert payload["total"] == 3
    assert all(i["eligibility_status"] == "VERIFIED_NOT_ELIGIBLE" for i in payload["items"])


def test_the_filter_rail_is_given_country_names_rather_than_iso_codes(api: JobsApi) -> None:
    """`BR` and `GB` are exactly right to STORE and are not a label.

    An independent UX review, reading the product as a nontechnical job
    seeker, found the place filter printing a code table. The names come from
    `places.yaml` -- the same file that decides which codes can exist -- so a
    country added there cannot arrive unnamed, and a name is never invented
    for a code the gazetteer cannot produce.
    """
    payload = _jobs(api, limit=500)
    labels = payload["facet_labels"]

    assert labels["country"]["BR"] == "Brazil"
    assert labels["country"]["GB"] == "United Kingdom"
    assert labels["region"]["LATAM"] == "Latin America"

    # Every bucket the demo corpus actually produces has a name, which is the
    # property that matters: an unnamed bucket falls back to the raw code and
    # nothing fails, so only this notices.
    for code in payload["facets"]["country"]:
        assert code in labels["country"], f"the country facet offers {code} with no name"
    for code in payload["facets"]["region"]:
        assert code in labels["region"], f"the region facet offers {code} with no name"


def test_every_code_the_gazetteer_can_produce_has_a_display_name() -> None:
    """The corpus only exercises the codes it happens to hold.

    A name added to `places.yaml` at the same time as a country is free; one
    added later is a code sitting in somebody's filter rail until they report
    it. So this is asserted over the gazetteer rather than over the demo data.
    """
    from career_agent.match.places import display_names, load_gazetteer

    gaz = load_gazetteer()
    countries, regions = display_names()

    produced = set(gaz.countries.values()) | set(gaz.cities.values())
    assert not produced - set(countries), sorted(produced - set(countries))

    produced_regions = set(gaz.regions.values()) | set(gaz.country_regions.values())
    assert not produced_regions - set(regions), sorted(produced_regions - set(regions))


# =========================================================================
# The way out of the empty screen
# =========================================================================


def test_a_rescore_can_be_started_and_reported_without_a_terminal(api: JobsApi) -> None:
    """An independent UX review's CRITICAL 2, closed rather than reworded.

    The empty state told somebody who had never opened a terminal to run
    `career-agent rescore`, and they had reached that screen by editing a
    preference in the interface. Rewording it left the dead end in place and
    added a promise the page could not keep.

    `rescore` opens no socket and asks no model of either kind, which is what
    makes it safe to sit behind a button.
    """
    before = api.rescore_status(query={}, body={})
    assert before["running"] is False
    assert before["run"] is None
    assert before["scored"] > 0, "the demo corpus must be scored for this to prove anything"

    started = api.start_rescore(query={}, body={})
    assert started["status"] == "running"
    api.rescore.join(timeout=60)

    after = api.rescore_status(query={}, body={})
    assert after["running"] is False
    run = after["run"]
    assert run["status"] == "done", run
    # Legacy seed/import scores have no checked receipt. The first pass safely
    # replays them and settles FTS; the second must be an indexed no-op.
    assert run["funnel"]["mode"] == "TARGETED", run["funnel"]
    assert run["funnel"]["considered"] == before["scored"], run["funnel"]
    assert run["funnel"]["targeted"] > 0, run["funnel"]
    assert run["funnel"]["scored"] == run["funnel"]["targeted"]
    assert after["scored"] == before["scored"], "a rescore of the same config changed the count"
    api.start_rescore(query={}, body={})
    api.rescore.join(timeout=60)
    resumed = api.rescore_status(query={}, body={})
    assert resumed["run"]["status"] == "done"
    assert resumed["run"]["funnel"]["targeted"] == 0


def test_two_rescores_at_once_are_refused_rather_than_interleaved(api: JobsApi) -> None:
    """Two passes would interleave writes to `job_match`."""
    from career_agent.pipeline.retrieval import RetrievalRunner

    # A fresh runner, so this test does not depend on what an earlier one left
    # behind. Replacing the attribute is the whole setup: the route reads it.
    api.rescore = RetrievalRunner()
    api.start_rescore(query={}, body={})
    try:
        with pytest.raises(ApiError) as error:
            api.start_rescore(query={}, body={})
        assert error.value.status == 409
    finally:
        api.rescore.join(timeout=60)


def test_a_rescore_is_not_a_retrieval_and_does_not_block_one(api: JobsApi) -> None:
    """Separate runners, because they are separate refusals.

    Two collections would double the request rate at an endpoint this project
    is deliberately polite to. Two rescores would interleave writes. A rescore
    refusing because a COLLECTION is running would be a conflict that does not
    exist: they touch different tables.
    """
    assert api.rescore is not api.retrieval


def test_the_facets_are_computed_in_one_pass_over_the_filtered_rows(api: JobsApi) -> None:
    """A performance change that must not be a behaviour change.

    `facets()` ran fourteen queries -- eleven `GROUP BY`, two set columns and
    one membership scan -- each re-executing the whole `WHERE`. Under
    `group_duplicates` that `WHERE` carries a two-level correlated subquery
    choosing one representative per (company, title), which SQLite reuses
    within a statement and cannot reuse across fourteen. Measured on the
    21,202-row corpus: 9,411 ms, of which essentially none was the tallying.

    What this asserts is the part that matters: the ANSWER did not move. The
    single-valued facets still total `count()`, the set-shaped ones still
    overlap, and every bucket still counts what its own filter returns.
    """
    payload = _jobs(api, limit=500)
    total = payload["total"]
    facets = payload["facets"]

    set_shaped = SET_VALUED_FACETS
    for name, buckets in facets.items():
        if name in set_shaped:
            continue
        assert sum(buckets.values()) == total, (
            f"{name} totals {sum(buckets.values())} against a population of {total}"
        )

    # `n DESC, bucket ASC`, which is the order the eleven queries produced and
    # the order the panel draws its chips in. Asserted because the tally moved
    # into Python, where nothing sorts unless it is told to.
    for name, buckets in facets.items():
        ranked = list(buckets.items())
        assert ranked == sorted(ranked, key=lambda kv: (-kv[1], kv[0])), name


def test_one_query_serves_every_facet(api: JobsApi) -> None:
    """The change itself, counted rather than described.

    A test that only checked the numbers would pass with fourteen queries and
    with one, so it would not notice the day somebody put the loop back.
    """
    import sqlite3

    from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery

    conn = api.connect()
    try:
        statements: list[str] = []
        conn.set_trace_callback(statements.append)
        ScoredJobQuery(conn).facets("demo", 1, JobFilter(limit=60, group_duplicates=True))
        conn.set_trace_callback(None)
    finally:
        conn.close()

    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, f"{len(selects)} statements for one set of facets"
    assert isinstance(conn, sqlite3.Connection)


def test_the_three_numbers_on_the_screen_reconcile(api: JobsApi) -> None:
    """21 jobs, 3 hidden, 16 roles. Twenty-one minus three is eighteen.

    An independent UX review pointed out that nothing on the screen accounted
    for the missing two. They are the same role posted in three cities, which
    grouping collapses into one card, and the interface never said so.

    Asserted as arithmetic rather than as copy: whatever the wording, the
    numbers have to add up.
    """
    grouped = _jobs(api, limit=500, group_duplicates=1)
    ungrouped = _jobs(api, limit=500, group_duplicates=0)

    assert grouped["grouped_away"] == ungrouped["total"] - grouped["total"], (
        f"{ungrouped['total']} rows, {grouped['total']} roles, "
        f"{grouped['grouped_away']} reported as folded"
    )
    assert grouped["grouped_away"] > 0, (
        "the demo corpus no longer contains a reposted role, so this proves nothing"
    )

    # And with everything shown, the whole population is accounted for:
    # roles + folded reposts + hidden = every row in the database.
    wide = _jobs(api, limit=500, group_duplicates=1, include_ineligible=1)
    every_row = _jobs(api, limit=500, group_duplicates=0, include_ineligible=1)["total"]
    assert wide["total"] + wide["grouped_away"] == every_row, (
        f"{wide['total']} roles + {wide['grouped_away']} folded != {every_row} rows"
    )

    narrow = _jobs(api, limit=500, group_duplicates=1)
    assert narrow["total"] + narrow["grouped_away"] + narrow["hidden_by_eligibility"] <= every_row


def test_nothing_is_reported_as_folded_when_grouping_is_off(api: JobsApi) -> None:
    """A line reading "0 reposts folded in" is noise, so the number is zero."""
    assert _jobs(api, limit=500, group_duplicates=0)["grouped_away"] == 0
