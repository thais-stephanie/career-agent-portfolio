"""Somebody who is not the owner, setting this up for the first time.

The failure this file exists to catch is quiet and embarrassing: a stranger
runs `setup`, answers about nursing, and gets a search that quietly still
knows about HubSpot, RevOps and a salary in a currency they never named. The
committed worked example IS the owner's real search, so every one of her
answers is one directory away from a fresh install at all times.

Three professions, chosen to share no vocabulary at all. If any one of them
inherits a word from another, or from the owner, the setup is leaking.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from career_agent.config.search_config import load_search_config
from career_agent.config.setup import Answers, run_setup

ROOT = Path(__file__).resolve().parents[2]

#: Words that belong to the OWNER'S search and to nobody else's. Every one is
#: in `search.worked-example.yaml`; none should survive a fresh setup.
OWNER_WORDS = (
    "hubspot",
    "revops",
    "revenue operations",
    "n8n",
    "salesforce",
    "ipaas",
    "business systems",
    "us_residence_required",
    "gtm",
)

PROFILES = {
    "business_systems": Answers(
        role_examples=("business systems analyst", "revenue operations"),
        residence_country="br",
        label="Business systems",
    ),
    "nursing": Answers(
        role_examples=("nurse educator", "clinical nurse specialist"),
        residence_country="pt",
        label="Nursing",
    ),
    "design": Answers(
        role_examples=("brand designer", "motion designer"),
        residence_country="es",
        label="Design",
    ),
}


@pytest.fixture
def fresh(tmp_path: Path):
    """A config directory holding only what a stranger would have.

    The starter, not the worked example: `setup` without `--example` must
    begin from the neutral file, and copying the owner's search in here would
    make the whole file prove nothing.
    """

    def build(name: str) -> Path:
        directory = tmp_path / name
        directory.mkdir()
        shutil.copy(ROOT / "config" / "search.starter.yaml", directory)
        shutil.copy(ROOT / "config" / "places.yaml", directory)
        return directory

    return build


def written(directory: Path) -> str:
    return (directory / "search.local.yaml").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_a_stranger_inherits_none_of_the_owners_search(fresh, name: str) -> None:
    """No word of hers survives, unless this person asked for it themselves.

    The exemption is not a loophole; it is what makes the test mean anything.
    The `business_systems` profile deliberately ANSWERS "revenue operations",
    which is also one of the owner's words -- and a word that arrived because
    somebody typed it is the product working, while the same word arriving
    unbidden is the leak. Without the distinction the check would have to be
    weakened to professions that share no vocabulary with her, which is the
    easy case and not the one that goes wrong.
    """
    answers = PROFILES[name]
    directory = fresh(name)
    run_setup(directory, answers)

    asked_for = " ".join(answers.role_examples).lower()
    text = written(directory)
    leaked = [word for word in OWNER_WORDS if word in text and word not in asked_for]
    assert not leaked, f"{name} inherited the owner's search: {leaked}"


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_a_stranger_gets_a_search_that_loads(fresh, name: str) -> None:
    """Written through the real loader, because a file that does not load is
    a first run that ends at an error message."""
    directory = fresh(name)
    run_setup(directory, PROFILES[name])

    config, path = load_search_config(directory)
    assert path.name == "search.local.yaml"
    assert config.lexicon, "a search with no signals matches nothing"


def test_three_professions_share_no_vocabulary(fresh) -> None:
    """The strongest form of the check.

    Any word appearing in all three searches came from the template rather than
    from an answer, and a template that carries somebody's profession is the
    leak this file is about.
    """
    lexicons = {}
    for name, answers in PROFILES.items():
        directory = fresh(name)
        run_setup(directory, answers)
        config, _ = load_search_config(directory)
        lexicons[name] = set(config.lexicon)

    shared = set.intersection(*lexicons.values())
    assert not shared, f"every profession got the same signals: {sorted(shared)}"


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_the_country_is_the_one_they_answered(fresh, name: str) -> None:
    directory = fresh(name)
    answers = PROFILES[name]
    run_setup(directory, answers)

    config, _ = load_search_config(directory)
    assert config.eligibility.candidate_country == answers.residence_country.upper()


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_nothing_is_preferred_that_nobody_was_asked_about(fresh, name: str) -> None:
    """The subtler half of inheritance.

    A starter that ships the owner's seniority, work model and target regions
    is not neutral, and it was not: those sections are REQUIRED by the loader,
    so emptying the section looked like the only way to empty the values. The
    generator empties the values instead, and this asserts it from the outside.
    """
    directory = fresh(name)
    run_setup(directory, PROFILES[name])
    document = yaml.safe_load((directory / "search.local.yaml").read_text(encoding="utf-8"))

    preferences = document["preferences"]
    assert preferences["seniority"]["preferred"] == []
    assert preferences["contract"]["preferred"] == []
    assert preferences["contract"]["unwanted"] == []
    assert preferences["remote"]["accepted_work_models"] == []


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_a_stranger_can_change_their_own_settings_immediately(fresh, name: str) -> None:
    """Setup and the editor have to agree about the file they share.

    A first run that produces a document the editing panel then refuses would
    be two writers disagreeing about one file, which is exactly what having a
    single validated writer is supposed to prevent.
    """
    from career_agent.config.candidate_writer import (
        current_candidate_fields,
        set_candidate_fields,
    )

    directory = fresh(name)
    run_setup(directory, PROFILES[name])

    set_candidate_fields(directory, {"work_models": ["REMOTE"], "seniority_preferred": ["MID"]})
    current = current_candidate_fields(directory)
    assert current["work_models"] == ["REMOTE"]
    assert current["seniority_preferred"] == ["MID"]
