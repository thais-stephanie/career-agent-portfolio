"""The Career Evidence review, over HTTP.

Every package here is SYNTHETIC. The contract was designed against the owner's
real CV and LinkedIn export; neither appears in a tracked test.

THE PROPERTY THIS FILE EXISTS FOR
---------------------------------
**There is no route that returns every claim.** The owner's real package holds
309 proposals, and a list of 309 is a wall -- the thing people close the tab
on. `overview` returns counts and headings; `claims` returns one group or one
state. A reader meets "Acme, 9 things to look at" before she meets a sentence.

If a future change adds a "give me everything" route, the test below fails.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

CONFIG_DIR = committed_config_dir()


def _package(claims: list[dict] | None = None) -> dict:
    """Two documents, one disagreement, and some skills. All invented."""

    def role(ref: str, end: str, text: str = "Integration Engineer at Acme") -> dict:
        return {
            "type": "EMPLOYMENT",
            "text": text,
            "source_ref": ref,
            "employer": "Acme",
            "period": {
                "start": {"original": "Jan 2020", "normalized": "2020-01"},
                "end": {"original": end, "normalized": end},
            },
            "evidence": {"quote": f"Integration Engineer, Acme, Jan 2020 to {end}"},
            "tools": ["Workato"],
        }

    return {
        "schema_version": "1.0",
        "generator": {"kind": "SELF", "name": "test"},
        "sources": [
            {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
            {"ref": "li", "kind": "LINKEDIN", "title": "profile.pdf"},
        ],
        "claims": claims
        if claims is not None
        else [
            role("cv", "2021-03"),
            role("cv", "2021-03", "Owned the billing reconciliation layer"),
            role("li", "2021-04"),
            {
                "type": "EMPLOYMENT",
                "text": "Analyst at Northwind",
                "source_ref": "cv",
                "employer": "Northwind",
                "period": {"start": {"original": "Jan 2018", "normalized": "2018-01"}},
                "evidence": {"locator": "Experience"},
            },
            {
                "type": "SKILL",
                "text": "Workato",
                "source_ref": "cv",
                "evidence": {"locator": "Skills"},
            },
            {
                "type": "CERTIFICATION",
                "text": "Six Sigma Green Belt",
                "source_ref": "li",
                "evidence": {"locator": "Certifications"},
            },
        ],
    }


@pytest.fixture
def staged(tmp_path: Path) -> Iterator[tuple[JobsApi, str]]:
    from career_agent.intake import parse_package
    from career_agent.intake.store import import_package

    db_path = tmp_path / "corpus.db"
    conn = connect(db_path)
    migrate(conn)
    load_search_config(CONFIG_DIR)
    package_id = import_package(conn, parse_package(_package()), filename="import.json")
    conn.commit()
    conn.close()

    api = JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)
    yield api, package_id


def _get(api: JobsApi, path: str, **params: str) -> dict:
    """Through the REAL dispatcher, so a test cannot exercise a handler that
    nothing serves."""
    return api.handle_api("GET", path, {k: [v] for k, v in params.items()}, {})


def _post(api: JobsApi, path: str, body: dict) -> dict:
    return api.handle_api("POST", path, {}, body)


# =========================================================================
# the shape of the surface
# =========================================================================


def test_there_is_no_route_that_returns_every_claim(staged) -> None:
    """The wall this whole design exists to prevent."""
    from career_agent.web.workspace_api import ApiError

    api, package_id = staged

    with pytest.raises(ApiError) as raised:
        _get(api, f"/api/intake/{package_id}/claims")

    assert raised.value.status == 400
    # THREE bounded selectors now, and the refusal is unchanged in substance:
    # each names a slice, and none of them is "all of it".
    assert "one group, one state or one step" in str(raised.value)
    assert "every claim at once" in str(raised.value)


def test_the_overview_returns_headings_rather_than_sentences(staged) -> None:
    api, package_id = staged

    payload = _get(api, f"/api/intake/{package_id}")

    assert payload["total"] == 6
    assert payload["answered"] == 0
    assert [s["ref"] for s in payload["sources"]] == ["cv", "li"]
    # Headings only. No claim text anywhere in the overview.
    rendered = repr(payload)
    assert "Owned the billing reconciliation layer" not in rendered
    assert {g["kind"] for g in payload["groups"]} == {
        "EMPLOYER",
        "SKILL",
        "CERTIFICATION",
    }


def test_everything_about_one_job_is_grouped_by_employer_and_comes_first(staged) -> None:
    """The unit a person thinks in. "What does this say about my time at Acme"
    is a question somebody can answer; "here are 287 sentences" is not.

    EMPLOYER rather than EMPLOYMENT: the screen holds every claim that names
    the job -- bullets, achievements, projects -- because those are one memory.
    Calling the group EMPLOYMENT would say the projects in it are employment.
    """
    api, package_id = staged

    groups = _get(api, f"/api/intake/{package_id}")["groups"]

    assert groups[0]["kind"] == "EMPLOYER"
    assert groups[0]["employer"] == "Acme"
    assert groups[0]["total"] == 3
    # ALL THREE, not two. Every claim about a role whose dates are disputed
    # carries the disputed dates, so confirming any of them would write them
    # onto a fact. The heading says so before she opens it.
    assert groups[0]["conflicted"] == 3
    # Most recent job first, not alphabetical: Acme ran to 2021 and Northwind
    # started in 2018 and never ended, so the reading order is by START.
    employers = [g["employer"] for g in groups if g["kind"] == "EMPLOYER"]
    assert employers == ["Acme", "Northwind"]


def test_one_group_returns_only_its_own_claims(staged) -> None:
    api, package_id = staged

    payload = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")

    assert [c["claim_type"] for c in payload["claims"]] == ["SKILL"]
    assert payload["claims"][0]["text"] == "Workato"


def test_every_claim_carries_its_provenance(staged) -> None:
    """A review whose citation appears only sometimes teaches people to stop
    looking for it, and looking for it is the whole mechanism."""
    api, package_id = staged

    claim = _get(api, f"/api/intake/{package_id}/claims", group="EMPLOYER:acme")["claims"][0]

    assert claim["sources"]
    assert claim["evidence"].get("quote") or claim["evidence"].get("locator")
    # Both halves of every date: what the document wrote and how it was read.
    assert claim["period"]["start"]["original"] == "Jan 2020"
    assert claim["period"]["start"]["normalized"] == "2020-01"


# =========================================================================
# answering
# =========================================================================


def test_confirming_over_http_creates_a_verified_claim(staged) -> None:
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    api, package_id = staged
    claim = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]

    result = _post(
        api,
        f"/api/intake/{package_id}/answer",
        {"claim_key": claim["claim_key"], "answer": "CONFIRM"},
    )

    assert result["counts"]["CONFIRMED"] == 1
    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        claims = ClaimRepo(conn).current(ensure_candidate(conn))
    assert [c.text for c in claims] == ["Workato"]
    assert claims[0].verified is True


def test_correcting_stores_her_words_and_keeps_the_original(staged) -> None:
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    api, package_id = staged
    claim = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]

    _post(
        api,
        f"/api/intake/{package_id}/answer",
        {
            "claim_key": claim["claim_key"],
            "answer": "CORRECT",
            "text": "Workato: built and maintained iPaaS integrations",
        },
    )

    after = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]
    assert after["review_state"] == "CORRECTED_BY_USER"
    assert after["corrected_text"] == "Workato: built and maintained iPaaS integrations"
    # The package's own wording is untouched.
    assert after["text"] == "Workato"

    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        claims = ClaimRepo(conn).current(ensure_candidate(conn))
    assert claims[0].text == "Workato: built and maintained iPaaS integrations"


def test_rejecting_creates_nothing(staged) -> None:
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    api, package_id = staged
    claim = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]

    result = _post(
        api,
        f"/api/intake/{package_id}/answer",
        {"claim_key": claim["claim_key"], "answer": "REJECT"},
    )

    assert result["counts"]["REJECTED"] == 1
    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        assert ClaimRepo(conn).current(ensure_candidate(conn)) == []


def test_a_rule_she_can_act_on_is_a_400_and_not_an_internal_error(staged) -> None:
    """ "That is already a claim you stand behind" reaching the catch-all would
    surface as "internal error, see the terminal"."""
    from career_agent.web.workspace_api import ApiError

    api, package_id = staged
    claim = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]
    _post(
        api,
        f"/api/intake/{package_id}/answer",
        {"claim_key": claim["claim_key"], "answer": "CONFIRM"},
    )

    with pytest.raises(ApiError) as raised:
        _post(
            api,
            f"/api/intake/{package_id}/answer",
            {"claim_key": claim["claim_key"], "answer": "REJECT"},
        )

    assert raised.value.status == 400
    assert "Career Evidence" in str(raised.value)


def test_an_unknown_answer_is_refused(staged) -> None:
    from career_agent.web.workspace_api import ApiError

    api, package_id = staged
    claim = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")["claims"][0]

    with pytest.raises(ApiError):
        _post(
            api,
            f"/api/intake/{package_id}/answer",
            {"claim_key": claim["claim_key"], "answer": "APPROVE"},
        )


# =========================================================================
# the disagreement
# =========================================================================


def test_the_disagreement_arrives_as_two_sides(staged) -> None:
    api, package_id = staged

    [conflict] = _get(api, f"/api/intake/{package_id}")["conflicts"]

    assert conflict["member_count"] == 3
    assert len(conflict["sides"]) == 2
    assert conflict["resolved_claim_key"] is None
    for side in conflict["sides"]:
        # Which document said it, and in whose words.
        assert side["sources"]
        assert side["end_original"]


def test_resolving_releases_the_group_and_confirms_nothing(staged) -> None:
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    api, package_id = staged
    conflict = _get(api, f"/api/intake/{package_id}")["conflicts"][0]
    chosen = conflict["sides"][0]["claim_key"]

    result = _post(
        api,
        f"/api/intake/{package_id}/resolve",
        {"group": conflict["conflict_group"], "claim_key": chosen},
    )

    assert result["released"] == 3
    assert result["counts"]["CONFLICT"] == 0
    assert result["conflicts"][0]["resolved_claim_key"] == chosen
    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        assert ClaimRepo(conn).current(ensure_candidate(conn)) == []


def test_a_resolution_can_be_unmade_over_http(staged) -> None:
    api, package_id = staged
    conflict = _get(api, f"/api/intake/{package_id}")["conflicts"][0]
    _post(
        api,
        f"/api/intake/{package_id}/resolve",
        {"group": conflict["conflict_group"], "claim_key": conflict["sides"][0]["claim_key"]},
    )

    result = _post(
        api,
        f"/api/intake/{package_id}/resolve",
        {"group": conflict["conflict_group"], "reopen": True},
    )

    assert result["counts"]["CONFLICT"] == 3
    assert result["conflicts"][0]["resolved_claim_key"] is None


def test_a_page_of_claims_says_it_is_a_page(tmp_path: Path) -> None:
    """A cap nothing reports is a screen quietly holding claims back, which is
    the exact defect this whole surface exists to avoid."""
    from career_agent.intake import parse_package
    from career_agent.intake.store import import_package
    from career_agent.web.workspace_api import PAGE_OF_CLAIMS

    many = _package(
        claims=[
            {
                "type": "SKILL",
                "text": f"Invented skill {n}",
                "source_ref": "cv",
                "evidence": {"locator": "Skills"},
            }
            for n in range(PAGE_OF_CLAIMS + 25)
        ]
    )
    db_path = tmp_path / "many.db"
    conn = connect(db_path)
    migrate(conn)
    load_search_config(CONFIG_DIR)
    package_id = import_package(conn, parse_package(many), filename="many.json")
    conn.commit()
    conn.close()
    api = JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)

    payload = _get(api, f"/api/intake/{package_id}/claims", group="SKILL")

    assert len(payload["claims"]) == PAGE_OF_CLAIMS
    assert payload["matched"] == PAGE_OF_CLAIMS + 25
    assert payload["truncated"] is True


def test_putting_a_package_away_deletes_nothing(staged) -> None:
    """Two packages built from the same two documents can both be open, and
    until this route existed nothing in the product could say which one she
    meant to work from.

    A discard flips the status. Every row stays, because a REJECTED row is the
    only thing that stops its line being proposed again by the next import of
    the same documents.
    """
    from career_agent.intake.store import summary

    api, package_id = staged
    before = _get(api, f"/api/intake/{package_id}")["counts"]

    result = _post(api, f"/api/intake/{package_id}/discard", {})

    assert result["status"] == "DISCARDED"
    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        row = conn.execute(
            "SELECT status FROM intake_package WHERE id = ?", (package_id,)
        ).fetchone()
        assert str(row["status"]) == "DISCARDED"
        assert summary(conn, package_id) == before, "a discard moved a review state"
    # And it is still readable: putting it away is not losing it.
    assert _get(api, f"/api/intake/{package_id}")["total"] == 6


# =========================================================================
# which reading is in force, over HTTP
# =========================================================================


def _stage_second(api: JobsApi) -> str:
    """A second package from the same documents, staged the way an import is."""
    from career_agent.intake import parse_package
    from career_agent.intake.store import import_package

    payload = _package()
    payload["claims"] = payload["claims"][:2]
    conn = connect(api.config.db_path)
    try:
        package_id = import_package(conn, parse_package(payload), filename="import-2.json")
        conn.commit()
    finally:
        conn.close()
    return package_id


def test_the_list_says_which_package_is_in_force(staged) -> None:
    """Returned beside the list rather than left to be worked out.

    A client that inferred it from the statuses would be a second
    implementation of the invariant, in the one place it cannot be tested.
    """
    api, first = staged
    payload = _get(api, "/api/intake")
    assert payload["active_package_id"] == first
    assert [p["package_id"] for p in payload["packages"]] == [first]

    second = _stage_second(api)
    payload = _get(api, "/api/intake")
    assert payload["active_package_id"] == second

    by_id = {p["package_id"]: p for p in payload["packages"]}
    assert by_id[second]["status"] == "ACTIVE"
    assert by_id[first]["status"] == "SUPERSEDED"
    # WHY it is not in force, not merely that it is not.
    assert by_id[first]["superseded_by"] == second
    assert by_id[second]["superseded_by"] is None


def test_the_list_carries_what_a_choice_between_packages_needs(staged) -> None:
    """Two readings of the same two documents look identical without this.

    The screen has to say where each came from, when, how much is in it, how
    many disagreements it found and how far the review has got -- otherwise
    choosing between them is choosing between two timestamps.
    """
    api, _first = staged
    _stage_second(api)
    for package in _get(api, "/api/intake")["packages"]:
        assert package["created_at"]
        assert package["claim_count"] > 0
        assert {str(s["kind"]) for s in package["sources"]} == {"RESUME", "LINKEDIN"}
        assert package["conflicted"] >= 0
        assert package["answered"] >= 0
        assert set(package["counts"]) >= {"UNREVIEWED", "CONFIRMED", "REJECTED"}


def test_selecting_a_package_puts_it_in_force_and_confirms_nothing(staged) -> None:
    from career_agent.intake.store import summary

    api, first = staged
    second = _stage_second(api)
    before = _get(api, f"/api/intake/{first}")["counts"]

    result = _post(api, f"/api/intake/{first}/select", {})
    assert result["status"] == "ACTIVE"
    assert result["active_package_id"] == first

    assert _get(api, f"/api/intake/{first}")["is_active"] is True
    assert _get(api, f"/api/intake/{second}")["is_active"] is False
    assert _get(api, f"/api/intake/{second}")["superseded_by"] == first

    with connect(api.config.db_path) as conn:  # type: ignore[attr-defined]
        assert summary(conn, first) == before, "selecting a package moved a review state"
        assert int(conn.execute("SELECT COUNT(*) AS n FROM verified_claim").fetchone()["n"]) == 0, (
            "selecting a package verified a claim"
        )


def test_selecting_is_reversible_over_http(staged) -> None:
    api, first = staged
    second = _stage_second(api)
    _post(api, f"/api/intake/{first}/select", {})
    _post(api, f"/api/intake/{second}/select", {})
    assert _get(api, "/api/intake")["active_package_id"] == second


def test_a_discarded_package_is_refused_with_a_sentence_she_can_act_on(staged) -> None:
    """409 rather than 400: the request is fine and the package is real. It is
    the STATE that refuses, and the message says what to do instead."""
    from career_agent.web.server import ApiError

    api, first = staged
    _stage_second(api)
    _post(api, f"/api/intake/{first}/discard", {})

    with pytest.raises(ApiError) as caught:
        _post(api, f"/api/intake/{first}/select", {})
    assert caught.value.status == 409
    assert "estore" in str(caught.value)


def test_restoring_says_which_state_it_landed_in(staged) -> None:
    """A client that assumed ACTIVE would be wrong exactly when it matters."""
    api, first = staged
    second = _stage_second(api)

    _post(api, f"/api/intake/{first}/discard", {})
    landed = _post(api, f"/api/intake/{first}/restore", {})
    assert landed["status"] == "SUPERSEDED", "restoring replaced the review she was working in"
    assert landed["active_package_id"] == second

    # ...and with nothing in force, the same act puts it in force.
    _post(api, f"/api/intake/{second}/discard", {})
    _post(api, f"/api/intake/{first}/discard", {})
    assert _get(api, "/api/intake")["active_package_id"] is None
    assert _post(api, f"/api/intake/{first}/restore", {})["status"] == "ACTIVE"


def test_an_unknown_package_is_a_404_on_every_lifecycle_route(staged) -> None:
    from career_agent.web.server import ApiError

    api, _first = staged
    for route in ("select", "restore", "discard"):
        with pytest.raises(ApiError) as caught:
            _post(api, f"/api/intake/01ZZZZZZZZZZZZZZZZZZZZZZZZ/{route}", {})
        assert caught.value.status == 404, route


# =========================================================================
# where to start
# =========================================================================


def test_the_queue_returns_counts_and_never_claims(staged) -> None:
    """A route that returned "the important ones" would be the wall again
    with a smaller number on it, and it would be this program deciding which
    of her statements are worth reading."""
    api, package_id = staged
    payload = _get(api, f"/api/intake/{package_id}/priority")

    assert "claims" not in payload
    assert {step["key"] for step in payload["steps"]}
    for step in payload["steps"]:
        assert set(step) == {"key", "total", "waiting", "answered", "blocking", "essential"}


def test_the_step_totals_sum_to_the_package_over_http(staged) -> None:
    api, package_id = staged
    payload = _get(api, f"/api/intake/{package_id}/priority")
    overview = _get(api, f"/api/intake/{package_id}")

    assert sum(step["total"] for step in payload["steps"]) == overview["total"]
    assert payload["progress"]["total"] == overview["total"]
    assert payload["progress"]["answered"] + payload["progress"]["waiting"] == overview["total"]


def test_the_queue_says_whether_this_is_the_reading_in_force(staged) -> None:
    api, package_id = staged
    assert _get(api, f"/api/intake/{package_id}/priority")["is_active"] is True
    second = _stage_second(api)
    assert _get(api, f"/api/intake/{package_id}/priority")["is_active"] is False
    assert _get(api, f"/api/intake/{second}/priority")["is_active"] is True


def test_a_step_can_be_fetched_one_at_a_time(staged) -> None:
    """The third bounded selector, beside `group` and `state`."""
    api, package_id = staged
    plan = _get(api, f"/api/intake/{package_id}/priority")
    populated = [step for step in plan["steps"] if step["total"]]
    assert populated, "the synthetic package populates no step at all"

    seen = 0
    for step in populated:
        payload = _get(api, f"/api/intake/{package_id}/claims", step=step["key"])
        assert payload["matched"] == step["total"], step["key"]
        seen += payload["matched"]
        for claim in payload["claims"]:
            # PROVENANCE travels with every claim, in every selector.
            assert "evidence" in claim
            assert "sources" in claim
            assert "period" in claim
    assert seen == plan["progress"]["total"], "the steps did not cover the package"


def test_there_is_still_no_route_that_returns_every_claim(staged) -> None:
    """A third selector must not become a way round the refusal."""
    from career_agent.web.server import ApiError

    api, package_id = staged
    with pytest.raises(ApiError) as caught:
        _get(api, f"/api/intake/{package_id}/claims")
    assert caught.value.status == 400
    assert "step" in str(caught.value)


def test_an_unknown_step_is_refused_rather_than_returning_everything(staged) -> None:
    from career_agent.web.server import ApiError

    api, package_id = staged
    with pytest.raises(ApiError) as caught:
        _get(api, f"/api/intake/{package_id}/claims", step="MOST_IMPORTANT")
    assert caught.value.status == 400


def test_settling_a_disagreement_moves_its_claims_out_of_the_first_step(staged) -> None:
    """The queue is derived, so it follows the review rather than a stored
    order that would go stale the moment she answered something."""
    api, package_id = staged
    before = _get(api, f"/api/intake/{package_id}/priority")
    blocking = next(s for s in before["steps"] if s["key"] == "SETTLE_DISAGREEMENTS")
    assert blocking["total"], "the synthetic package holds no disagreement"

    overview = _get(api, f"/api/intake/{package_id}")
    conflict = overview["conflicts"][0]
    _post(
        api,
        f"/api/intake/{package_id}/resolve",
        {"group": conflict["conflict_group"], "claim_key": conflict["sides"][0]["claim_key"]},
    )

    after = _get(api, f"/api/intake/{package_id}/priority")
    assert next(s for s in after["steps"] if s["key"] == "SETTLE_DISAGREEMENTS")["total"] == 0
    # ...and nothing was confirmed by settling it.
    assert after["progress"]["answered"] == before["progress"]["answered"]
    assert after["progress"]["total"] == before["progress"]["total"]


def test_the_job_she_came_from_is_reported_beside_the_steps(staged) -> None:
    api, package_id = staged
    plain = _get(api, f"/api/intake/{package_id}/priority")
    assert plain["focus"]["term"] is None
    assert plain["focus"]["total"] == 0

    focused = _get(api, f"/api/intake/{package_id}/priority", term="Workato")
    assert focused["focus"]["term"] == "Workato"
    # The lens changes no step total: it is a view over the same claims.
    assert [s["total"] for s in focused["steps"]] == [s["total"] for s in plain["steps"]]


def test_the_queue_on_an_unknown_package_is_a_404(staged) -> None:
    from career_agent.web.server import ApiError

    api, _package_id = staged
    with pytest.raises(ApiError) as caught:
        _get(api, "/api/intake/01ZZZZZZZZZZZZZZZZZZZZZZZZ/priority")
    assert caught.value.status == 404
