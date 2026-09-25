"""Roles in mind: optional search anchors, their aliases, and their provenance.

Synthetic throughout: a temporary configuration copied from the committed
one, a temporary database, invented job titles.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from career_agent.clock import new_id, now_utc
from career_agent.config.search_config import load_search_config
from career_agent.discovery.aliases import aliases_for, plan_aliases
from career_agent.discovery.anchors import MAX_ALIASES_PER_ANCHOR, Anchor, load_anchors
from career_agent.discovery.plan import query_terms
from career_agent.storage.db import connect, migrate
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    config = tmp_path / "config"
    source = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(source, config, ignore=shutil.ignore_patterns("*.local.*"))
    conn = connect(tmp_path / "personal.db")
    migrate(conn)
    candidate = ensure_candidate(conn)
    for order, (title, current) in enumerate([("Staff Nurse", 1), ("Barista", 0)]):
        conn.execute(
            "INSERT INTO career_experience (id, candidate_id, title, current_role,"
            " display_order, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), candidate, title, current, order, now_utc()),
        )
    conn.commit()
    conn.close()
    return JobsApi(ServerConfig(db_path=tmp_path / "personal.db", config_dir=config), quiet=True)


# =========================================================================
# the planner, for any occupation
# =========================================================================


@pytest.mark.parametrize(
    "anchor,expected",
    [
        ("Hair Stylist", {"Hairdresser"}),
        ("ICU Nurse", {"Critical Care Nurse"}),
        ("Senior Teacher", {"Teacher", "Educator"}),
        ("Account Executive", {"Sales Executive"}),
        ("Senior SDR", {"SDR", "Sales Development Representative"}),
        ("CSM", {"Customer Success Manager"}),
        ("Accountant II", {"Accountant"}),
        ("Software Engineer (Remote)", {"Software Engineer", "Software Developer"}),
        ("GTM Engineer / AI Engineer", {"GTM Engineer", "AI Engineer"}),
    ],
)
def test_aliases_are_the_same_work_under_other_titles(anchor: str, expected: set[str]) -> None:
    made = {a.text for a in aliases_for(Anchor(text=anchor))}
    assert expected <= made
    assert anchor not in made
    assert len(made) <= MAX_ALIASES_PER_ANCHOR


def test_no_head_noun_is_swapped_and_team_lead_stays_a_title() -> None:
    assert [a.text for a in aliases_for(Anchor(text="Civil Engineer"))] == []
    assert "SDR Team" not in {a.text for a in aliases_for(Anchor(text="SDR Team Lead"))}


def test_aliases_are_deduplicated_across_anchors_and_named_by_their_generator() -> None:
    made = plan_aliases([Anchor(text="Software Engineer"), Anchor(text="Software Developer")])
    assert [a.text for a in made] == []  # each is the other's alias, and both are anchors
    one = plan_aliases([Anchor(text="Hair Stylist")])[0]
    assert (one.anchor, one.source, one.generator) == ("Hair Stylist", "rule", "rule")


# =========================================================================
# the API
# =========================================================================


def test_roles_are_optional_and_profile_titles_are_only_offered(api: JobsApi) -> None:
    empty = api.handle_api("GET", "/api/role-anchors", {}, {})
    assert empty["anchors"] == [] and empty["aliases"] == []
    assert empty["required"] is False and empty["affects_scores"] is False
    # Background is offered, never stored.
    assert [s["text"] for s in empty["suggestions"]] == ["Staff Nurse", "Barista"]
    assert load_anchors(api.config.config_dir).anchors == ()


def test_saving_keeps_provenance_and_generates_aliases(api: JobsApi) -> None:
    before = load_search_config(api.config.config_dir)[1].read_bytes()
    saved = api.handle_api(
        "PATCH",
        "/api/role-anchors",
        {},
        {
            "anchors": [
                {"text": "ICU  Nurse", "source": "user"},
                {"text": "Staff Nurse", "source": "confirmed_suggestion"},
                # Claimed as a suggestion, but the profile never held it.
                {"text": "Hair Stylist", "source": "confirmed_suggestion"},
            ]
        },
    )
    assert saved["recalculation_required"] is False
    assert [(a["text"], a["source"]) for a in saved["anchors"]] == [
        ("ICU Nurse", "user"),
        ("Staff Nurse", "confirmed_suggestion"),
        ("Hair Stylist", "user"),
    ]
    assert {"Critical Care Nurse", "Hairdresser"} <= {a["text"] for a in saved["aliases"]}
    # The confirmed suggestion is no longer offered.
    assert [s["text"] for s in saved["suggestions"]] == ["Barista"]
    # Anchors are retrieval helpers: the scoring configuration is untouched.
    assert load_search_config(api.config.config_dir)[1].read_bytes() == before
    # And the targeted lane reads them first.
    config = api.search_config()
    terms = query_terms(config, load_anchors(api.config.config_dir))
    assert [t.origin for t in terms[:3]] == ["anchor", "anchor", "anchor"]
    assert any(t.origin == "alias" and t.text == "Critical Care Nurse" for t in terms)


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"anchors": "ICU Nurse"},
        {"anchors": [{"text": ""}]},
        {"anchors": [{"text": "x" * 81}]},
        {"anchors": [{"text": "ICU Nurse", "source": "cv"}]},
        {"anchors": [{"text": "ICU Nurse", "weight": 3}]},
        {"anchors": [{"text": f"Role {n}"} for n in range(9)]},
    ],
)
def test_bad_role_lists_are_refused_and_nothing_is_written(api: JobsApi, body: dict) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("PATCH", "/api/role-anchors", {}, body)
    assert caught.value.status == 400
    assert load_anchors(api.config.config_dir).anchors == ()


def test_clearing_the_roles_clears_their_aliases(api: JobsApi) -> None:
    api.handle_api("PATCH", "/api/role-anchors", {}, {"anchors": [{"text": "Hair Stylist"}]})
    cleared = api.handle_api("PATCH", "/api/role-anchors", {}, {"anchors": []})
    assert cleared["anchors"] == [] and cleared["aliases"] == []
