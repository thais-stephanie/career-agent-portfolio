"""The Career Profile: what the product believes, without opening a YAML file.

Assembled entirely from data that already exists. These tests are mostly about
what it must NOT do: invent a section, invent a value, or resolve a
disagreement between two files that both describe the same person.
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
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture(scope="module")
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="career-profile")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


@pytest.fixture(scope="module")
def payload(api: JobsApi) -> dict:
    return api.handle_api("GET", "/api/profile", {}, {})


# =========================================================================
# 1. IT DESCRIBES WHAT EXISTS
# =========================================================================


def test_it_names_the_file_the_answers_came_from(payload: dict) -> None:
    """Which settings produced this must never be a guess. The same discipline
    `search-config` and the health line already follow."""
    source = payload["source"]
    assert source["file"].startswith("search.")
    assert source["config_id"] and source["config_version"]
    assert isinstance(source["is_local"], bool)


def test_the_sections_are_built_from_real_configuration(payload: dict) -> None:
    ids = {section["id"] for section in payload["sections"]}
    assert {"about", "place", "blockers", "shape"} <= ids
    assert any(section_id.startswith("signals-") for section_id in ids)


def test_every_section_that_appears_has_something_in_it(payload: dict) -> None:
    """A section with no data is ABSENT, not empty.

    An interface rendering "Languages: --" is describing a feature nobody
    built, and this product has enough real unknowns to report without
    inventing display ones.
    """
    for section in payload["sections"]:
        assert section["rows"], f"{section['id']} reached the screen with nothing to say"
        assert section["label"] and section["lead"]
        for row in section["rows"]:
            assert row["label"], f"{section['id']} has a row with no label"


def test_a_section_with_no_data_does_not_appear(payload: dict) -> None:
    """`preferences.compensation.target_monthly_amount` is 0 in the shipped
    configuration, which is the deliberate placeholder. So there is no pay
    section, rather than a pay section saying nothing."""
    ids = {section["id"] for section in payload["sections"]}
    assert "pay" not in ids


# =========================================================================
# 2. EDITABLE WHERE A WRITER EXISTS, READ-ONLY WHERE IT DOES NOT
# =========================================================================


def test_only_the_phrase_groups_claim_to_be_editable(payload: dict) -> None:
    """A second write path is how two files start disagreeing.

    The phrase groups already have `PATCH /api/preferences`, which writes to
    `search.local.yaml` and bumps `config_version`. Nothing else here has a
    safe writer, so nothing else says it does.
    """
    for section in payload["sections"]:
        editable = section.get("editable", False)
        if editable:
            assert section["id"].startswith("signals-")
            assert section.get("category"), "an editable section must say what to PATCH"
        else:
            assert "category" not in section


def test_a_read_only_section_names_the_file_that_holds_it(payload: dict) -> None:
    """Read-only is not a dead end. Somebody who wants to change it should be
    told where it lives."""
    for section in payload["sections"]:
        if not section.get("editable", False) and section["id"] != "about":
            assert section.get("file"), f"{section['id']} is read-only and says nothing about where"


def test_an_editable_section_names_signals_the_patch_route_accepts(
    api: JobsApi, payload: dict
) -> None:
    """The link between the two routes, asserted rather than assumed."""
    editable = next(s for s in payload["sections"] if s.get("editable"))
    preferences = api.handle_api("GET", "/api/preferences", {}, {})
    known = {signal["signal_id"] for signal in preferences["signals"]}
    assert {row["signal_id"] for row in editable["rows"]} <= known


# =========================================================================
# 3. TWO FILES, ONE PERSON, NO WINNER PICKED HERE
# =========================================================================


def test_an_absent_candidate_profile_is_a_state_and_not_an_error(payload: dict) -> None:
    """`profile.local.yaml` is deliberately absent, and M2 is
    candidate-independent by design. The screen says so in a sentence instead
    of failing or pretending the file is empty."""
    candidate = payload["candidate_profile"]
    assert candidate["present"] is False
    assert "expected state" in candidate["note"]
    assert payload["divergences"] == []


def test_the_future_ownership_rule_travels_with_the_answer(payload: dict) -> None:
    """So the interim cannot quietly become the design."""
    assert "Candidate Profile owns facts about the person" in payload["ownership_rule"]


def test_nothing_here_resolves_a_disagreement(payload: dict) -> None:
    """The response shape has no winner field, and that is the point.

    Choosing between two files that both describe the person is the ownership
    migration, which is a decision rather than a screen.
    """
    assert "resolved_value" not in payload
    assert "canonical" not in payload


# =========================================================================
# 4. IT IS A READ
# =========================================================================


def test_the_route_takes_no_parameters(api: JobsApi) -> None:
    with pytest.raises(ApiError):
        api.handle_api("GET", "/api/profile", {"anything": ["1"]}, {})


def test_reading_it_twice_gives_the_same_answer(api: JobsApi) -> None:
    first = api.handle_api("GET", "/api/profile", {}, {})
    second = api.handle_api("GET", "/api/profile", {}, {})
    assert first == second


# =========================================================================
# EDITING, WITHOUT OPENING A YAML FILE
# =========================================================================


@pytest.fixture
def writable_api() -> Iterator[JobsApi]:
    """An API over a config directory of its own.

    Never `config/`. The writer replaces `search.local.yaml` and bumps
    `config_version`; a test doing that to the repository would replace the
    owner's real search and detach her corpus from its scores.
    """
    import shutil

    workspace = Path(tempfile.mkdtemp(prefix="career-profile-write"))
    shutil.copy(CONFIG_DIR / "search.worked-example.yaml", workspace)
    shutil.copy(workspace / "search.worked-example.yaml", workspace / "search.local.yaml")
    shutil.copy(CONFIG_DIR / "places.yaml", workspace)
    db_path = workspace / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        config, _ = load_search_config(workspace)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=workspace, port=0), quiet=True)


def test_the_panel_is_told_what_it_may_change(writable_api: JobsApi) -> None:
    """The descriptor comes from the server, and from the same table that
    validates the write. A control the backend would refuse is worse than no
    control: it saves, fails, and teaches the reader not to trust the panel."""
    editable = writable_api.handle_api("GET", "/api/profile", {}, {})["editable"]
    by_field = {row["field"]: row for row in editable}

    assert "work_models" in by_field
    assert by_field["work_models"]["choices"] == ["REMOTE", "HYBRID", "ONSITE"]
    assert by_field["work_models"]["value"] == ["REMOTE", "HYBRID"]
    assert by_field["candidate_country"]["value"] == "BR"
    for row in editable:
        assert row["label"], f"{row['field']} has no label to draw"


def test_a_saved_change_is_readable_back_through_the_api(writable_api: JobsApi) -> None:
    response = writable_api.handle_api(
        "PATCH", "/api/profile", {}, {"changes": {"work_models": ["REMOTE"]}}
    )
    assert response["ok"] is True
    assert response["written_to"] == "search.local.yaml"
    assert response["rescore_required"] is True
    assert response["changed"] == ["work_models"]

    again = writable_api.handle_api("GET", "/api/profile", {}, {})["editable"]
    assert {row["field"]: row["value"] for row in again}["work_models"] == ["REMOTE"]


def test_saving_says_the_existing_matches_need_recalculating(writable_api: JobsApi) -> None:
    """A score is only true relative to the configuration that produced it, and
    the person who just changed one has to be told that in words."""
    response = writable_api.handle_api(
        "PATCH", "/api/profile", {}, {"changes": {"travel_max_pct": 0}}
    )
    assert "recalculat" in response["note"]


def test_the_panel_cannot_rewrite_the_matcher(writable_api: JobsApi) -> None:
    """The security property. This body arrives from a browser."""
    for forbidden in ("scoring", "lexicon", "screening", "eligibility"):
        with pytest.raises(ApiError) as raised:
            writable_api.handle_api(
                "PATCH", "/api/profile", {}, {"changes": {forbidden: ["anything"]}}
            )
        assert raised.value.status == 400


def test_a_rejected_value_says_which_box_to_look_at(writable_api: JobsApi) -> None:
    with pytest.raises(ApiError) as raised:
        writable_api.handle_api(
            "PATCH", "/api/profile", {}, {"changes": {"candidate_country": "Brazil"}}
        )
    assert "two-letter" in str(raised.value)


def test_an_empty_save_is_refused(writable_api: JobsApi) -> None:
    for body in ({}, {"changes": {}}, {"changes": "everything"}):
        with pytest.raises(ApiError):
            writable_api.handle_api("PATCH", "/api/profile", {}, body)
