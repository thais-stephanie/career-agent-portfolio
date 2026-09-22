"""Two files describing one person, and the warning when they disagree.

`profile.local.yaml` is deliberately absent on the owner's machine, so this is
the only place the check is exercised at all. Every profile below is built in
memory from the committed example, which is what keeps these tests honest about
the real schema rather than about a convenient fake.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.consistency import OWNERSHIP_RULE, divergences
from career_agent.config.search_config import example_search_path, load_search_config
from career_agent.domain.profile import SearchProfile

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()

CONFIG, _ = load_search_config(CONFIG_DIR)


def _profile_raw() -> dict[str, Any]:
    text = (CONFIG_DIR / "profile.example.yaml").read_text(encoding="utf-8")
    parsed: dict[str, Any] = yaml.safe_load(text)
    return parsed


def _profile(**overrides: Any) -> SearchProfile:
    """The committed example profile, with a section replaced."""
    raw = copy.deepcopy(_profile_raw())
    for key, value in overrides.items():
        raw[key] = value
    return SearchProfile.model_validate(raw)


def _search(**eligibility: Any):
    """The committed search configuration, with `eligibility` keys replaced."""
    raw: dict[str, Any] = yaml.safe_load(
        example_search_path(CONFIG_DIR).read_text(encoding="utf-8")
    )
    raw["eligibility"].update(eligibility)
    from career_agent.config.search_config import SearchConfig, _expand_taxonomy

    _expand_taxonomy(raw)
    return SearchConfig.model_validate(raw)


# =========================================================================
# AGREEMENT IS SILENT
# =========================================================================


def test_the_committed_example_pair_is_reported_as_consistent() -> None:
    """Both shipped files describe the same search, so the check must be quiet.

    A warning that fires on the repository's own defaults is a warning every
    reader learns to skip.
    """
    assert divergences(_profile(), CONFIG) == ()


def test_a_value_only_one_file_states_is_not_a_disagreement() -> None:
    """Absence is never permission, and it is not a contradiction either.

    The shipped configuration leaves the compensation target at zero, which is
    the deliberate placeholder. Reading that as "disagrees with the profile"
    would bury the findings that matter under one that never resolves.
    """
    assert CONFIG.preferences.compensation.target_monthly_amount == 0
    assert not [d for d in divergences(_profile(), CONFIG) if "earn" in d.subject]


# =========================================================================
# DISAGREEMENT IS NAMED, ON BOTH SIDES
# =========================================================================


def test_two_different_countries_of_residence_are_reported() -> None:
    """The finding this mechanism exists for.

    Nothing in the product reconciles these today: the matcher gates on the
    configuration and `doctor` prints the profile, so a person could be told
    they live in two countries by two commands and never see them side by side.
    """
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["candidate_geography"]["residence_country"] = "PT"
    profile_raw["engagement"]["work_authorizations"] = [{"country": "PT", "basis": "CITIZEN"}]
    profile = SearchProfile.model_validate(profile_raw)

    found = divergences(profile, CONFIG)

    assert len(found) >= 1
    where = next(d for d in found if d.subject == "Where you live")
    assert where.profile_value == "PT"
    assert where.config_value == "BR"
    # Both sides named, with paths somebody can open.
    assert "profile.local.yaml" in where.profile_path
    assert "search.yaml" in where.config_path
    assert "PT" in where.sentence and "BR" in where.sentence


def test_a_disagreement_about_hiring_scope_is_reported() -> None:
    search = _search(eligible_scopes=["WORLDWIDE"], eligible_countries=[])
    found = divergences(_profile(), search)
    assert any(d.subject == "Which places may hire you" for d in found)


def test_a_disagreement_about_engagement_is_reported() -> None:
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["engagement"]["contract_types_accepted"] = ["FULL_TIME_EMPLOYEE"]
    found = divergences(SearchProfile.model_validate(profile_raw), CONFIG)
    assert any(d.subject == "How you may be engaged" for d in found)


def test_a_work_model_the_profile_refuses_outright_is_reported() -> None:
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["intent"]["work_environment"]["NEVER"] = ["hybrid", "onsite_required"]
    found = divergences(SearchProfile.model_validate(profile_raw), CONFIG)
    assert any(d.subject == "Where the work happens" for d in found)


# =========================================================================
# WHAT THE CHECK REFUSES TO DO
# =========================================================================


def test_nothing_here_chooses_a_winner() -> None:
    """The interim mechanism warns. Choosing would be the migration, and the
    migration waits for there to be profile history worth migrating."""
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["candidate_geography"]["residence_country"] = "PT"
    profile_raw["engagement"]["work_authorizations"] = [{"country": "PT", "basis": "CITIZEN"}]
    profile = SearchProfile.model_validate(profile_raw)

    found = divergences(profile, CONFIG)

    # The runtime values are untouched by having been compared.
    assert profile.candidate_geography.residence_country == "PT"
    assert CONFIG.eligibility.candidate_country == "BR"
    # And the report says which one the product actually reads, rather than
    # implying the reader should guess.
    assert "The matcher reads" in found[0].sentence


def test_a_cross_currency_target_is_not_compared_rather_than_converted() -> None:
    """No dated rate, no comparison. Manufacturing one to produce a WARNING
    would be the same invented fact the scorer refuses to produce."""
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["compensation"]["annual_target"] = {"amount": 200_000, "currency": "BRL"}
    search = _search()
    object.__setattr__(search.preferences.compensation, "target_monthly_amount", 10_000)
    object.__setattr__(search.preferences.compensation, "currency", "USD")

    found = divergences(SearchProfile.model_validate(profile_raw), search)

    assert not [d for d in found if "earn" in d.subject]


def test_the_future_ownership_rule_is_stated_where_the_warnings_are() -> None:
    """The interim must not quietly become the design."""
    assert "Candidate Profile owns facts about the person" in OWNERSHIP_RULE
    assert "matching machinery" in OWNERSHIP_RULE


@pytest.mark.parametrize(
    "subject",
    [
        "Where you live",
        "Which places may hire you",
        "How you may be engaged",
        "Where the work happens",
    ],
)
def test_every_audited_subject_names_both_files(subject: str) -> None:
    """A finding that does not say what each side said is not actionable."""
    profile_raw = copy.deepcopy(_profile_raw())
    profile_raw["candidate_geography"]["residence_country"] = "PT"
    profile_raw["engagement"]["work_authorizations"] = [{"country": "PT", "basis": "CITIZEN"}]
    profile_raw["engagement"]["contract_types_accepted"] = ["FULL_TIME_EMPLOYEE"]
    profile_raw["intent"]["work_environment"]["NEVER"] = ["hybrid"]
    search = _search(eligible_scopes=["WORLDWIDE"], eligible_countries=[])

    found = {d.subject: d for d in divergences(SearchProfile.model_validate(profile_raw), search)}

    assert subject in found, sorted(found)
    divergence = found[subject]
    assert divergence.profile_value and divergence.config_value
    assert divergence.profile_path and divergence.config_path
