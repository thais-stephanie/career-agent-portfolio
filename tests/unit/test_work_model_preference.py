"""Ways of working: prefer, rather avoid, never show. What each one does.

A preference prices a posting's STATED way of working in Search Fit, and "never
show" also narrows Discover (tested in `tests/integration/test_work_model_narrowing.py`).
None of it is eligibility: remote never means "hires anywhere", and not wanting
on-site work never makes a posting one she may not take.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.candidate_writer import set_candidate_fields
from career_agent.config.preferences import PreferenceError
from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.match.engine import JobFacts, match_job

COMPUTED_AT = "2026-09-04T00:00:00Z"
BASE, _ = load_search_config(committed_config_dir())
BODY = "You will own the CRM data model and the integrations around it."


def config_with(**remote: list[str]) -> SearchConfig:
    values = {"accepted_work_models": [], "avoided_work_models": [], "excluded_work_models": []}
    values.update(remote)
    return BASE.model_copy(
        update={
            "preferences": BASE.preferences.model_copy(
                update={"remote": BASE.preferences.remote.model_copy(update=values)}
            )
        }
    )


def result(config: SearchConfig, **facts: object):
    return match_job(
        config,
        JobFacts(title="Systems Analyst", description=BODY, **facts),  # type: ignore[arg-type]
        computed_at=COMPUTED_AT,
    )


def component(outcome) -> dict | None:
    for item in outcome.components:
        if item.component_id == "work_model":
            return item
    return None


def test_no_answer_means_no_component_and_an_unchanged_score() -> None:
    silent = result(config_with(), workplace_type="REMOTE")
    assert component(silent) is None
    assert [c.component_id for c in silent.components] == [
        "responsibilities",
        "technologies",
        "automation_integration",
        "seniority",
        "compensation_contract",
    ]


@pytest.mark.parametrize(
    ("preference", "stated", "signal", "points"),
    [
        ({"accepted_work_models": ["REMOTE"]}, "REMOTE", "work_model_preferred", 4.0),
        ({"accepted_work_models": ["REMOTE"]}, "HYBRID", "work_model_neutral", 2.0),
        ({"avoided_work_models": ["ONSITE"]}, "ONSITE", "work_model_avoided", 0.0),
        ({"excluded_work_models": ["ONSITE"]}, "ONSITE", "work_model_avoided", 0.0),
        ({"accepted_work_models": ["REMOTE"]}, None, "work_model_unknown", 2.0),
    ],
)
def test_each_answer_prices_the_stated_way_of_working(
    preference: dict, stated: str | None, signal: str, points: float
) -> None:
    outcome = result(config_with(**preference), workplace_type=stated)
    priced = component(outcome)
    assert priced is not None and priced.max_points == 4.0
    assert priced.points == points
    assert [c.signal_id for c in priced.contributions] == [signal]


def test_the_way_of_working_is_what_the_board_said_including_its_location_words() -> None:
    outcome = result(config_with(accepted_work_models=["REMOTE"]), location_raw="Remote - Brazil")
    assert component(outcome).points == 4.0


def test_a_preferred_way_of_working_scores_above_an_avoided_one() -> None:
    config = config_with(accepted_work_models=["REMOTE"], avoided_work_models=["ONSITE"])
    remote = result(config, workplace_type="REMOTE")
    onsite = result(config, workplace_type="ONSITE")
    assert remote.match_score > onsite.match_score


@pytest.mark.parametrize(
    "answer", ["accepted_work_models", "avoided_work_models", "excluded_work_models"]
)
def test_no_work_model_answer_ever_changes_eligibility(answer: str) -> None:
    """Remote is not worldwide, and avoiding a way of working is not a verdict."""
    facts = {"workplace_type": "REMOTE", "location_raw": "Remote - United States"}
    without = result(config_with(), **facts)
    with_answer = result(config_with(**{answer: ["REMOTE"]}), **facts)
    assert with_answer.eligibility_status == without.eligibility_status
    assert [g.result for g in with_answer.gates] == [g.result for g in without.gates]


# =========================================================================
# the writer
# =========================================================================


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config)
    return config


def saved(workspace: Path) -> dict:
    return yaml.safe_load((workspace / "search.local.yaml").read_text(encoding="utf-8"))


def test_the_three_answers_are_saved_apart(workspace: Path) -> None:
    set_candidate_fields(
        workspace,
        {
            "work_models": ["REMOTE"],
            "avoided_work_models": ["HYBRID"],
            "excluded_work_models": ["ONSITE"],
        },
    )
    remote = saved(workspace)["preferences"]["remote"]
    assert remote["accepted_work_models"] == ["REMOTE"]
    assert remote["avoided_work_models"] == ["HYBRID"]
    assert remote["excluded_work_models"] == ["ONSITE"]


def test_one_way_of_working_cannot_have_two_answers_in_one_save(workspace: Path) -> None:
    with pytest.raises(PreferenceError, match="cannot be both"):
        set_candidate_fields(
            workspace, {"work_models": ["REMOTE"], "excluded_work_models": ["REMOTE"]}
        )


def test_a_new_answer_replaces_the_old_one_for_that_way_of_working(workspace: Path) -> None:
    set_candidate_fields(workspace, {"work_models": ["REMOTE", "HYBRID"]})
    set_candidate_fields(workspace, {"excluded_work_models": ["HYBRID"]})
    remote = saved(workspace)["preferences"]["remote"]
    assert remote["accepted_work_models"] == ["REMOTE"]
    assert remote["excluded_work_models"] == ["HYBRID"]


def test_an_old_overlapping_contract_answer_is_resolved_not_refused(workspace: Path) -> None:
    """The old screen only offered "prefer less" from the accepted list."""
    set_candidate_fields(workspace, {"contract_preferred": ["EOR", "CONTRACTOR_B2B"]})
    document = saved(workspace)
    document["preferences"]["contract"]["unwanted"] = ["EOR"]
    (workspace / "search.local.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")

    set_candidate_fields(workspace, {"contract_unwanted": ["EOR"]})
    contract = saved(workspace)["preferences"]["contract"]
    assert contract["unwanted"] == ["EOR"]
    assert contract["preferred"] == ["CONTRACTOR_B2B"]


@pytest.mark.parametrize(
    ("excluded", "only_remote"),
    [(["HYBRID", "ONSITE"], True), (["ONSITE"], False), ([], False)],
)
def test_only_remote_is_derived_from_never_show(
    workspace: Path, excluded: list[str], only_remote: bool
) -> None:
    set_candidate_fields(workspace, {"excluded_work_models": excluded})
    assert saved(workspace)["preferences"]["remote"]["require_remote"] is only_remote
