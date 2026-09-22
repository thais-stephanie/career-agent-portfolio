"""Four people who are not the owner, set up from scratch.

The starter configuration is empty by construction and a unit test asserts it
is exactly what its generator produces. This file asks the question one level
up: after somebody answers the wizard, does the configuration describe HER, or
does it still describe the person who wrote the product?

Four backgrounds, deliberately far apart. Business systems is the owner's own
field and is here as the control; healthcare, design and customer support are
here because a product that only works for the field it was built in is a
product with one user.

Nothing in this file touches `config/`. Every run writes into a temporary copy
of the committed directory, and `search.local.yaml` is created inside it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.config.setup import Answers, run_setup

#: Words that belong to the OWNER's search and to nobody else's. A fresh
#: configuration containing one of these inherited it rather than being asked
#: for it. `BR` is checked separately, as a whole value rather than a
#: substring, because two letters appear inside ordinary words.
OWNER_WORDS = (
    "hubspot",
    "salesforce",
    "revops",
    "revenue operations",
    "business systems",
    "n8n",
    "latam",
    "brasil",
    "brazil",
)

PERSONAS = {
    "business_systems": Answers(
        label="Business systems, remote",
        role_examples=("Business systems analyst", "Internal tooling"),
        skills=("SQL", "Workflow automation"),
        residence_country="PT",
        target_regions=("EMEA",),
        accepted_work_models=("REMOTE",),
        preferred_seniorities=("SENIOR",),
        target_amount=5000,
        currency="EUR",
    ),
    "healthcare": Answers(
        label="Clinical operations",
        role_examples=("Clinical trial coordination", "Patient safety reporting"),
        skills=("Good clinical practice", "Protocol deviation review"),
        residence_country="IE",
        target_regions=("EMEA",),
        accepted_work_models=("HYBRID", "ONSITE"),
        preferred_seniorities=("MID",),
        target_amount=4200,
        currency="EUR",
    ),
    "design": Answers(
        label="Product design",
        role_examples=("Design systems", "Interaction design for complex tools"),
        skills=("Figma", "Prototyping"),
        residence_country="MX",
        target_regions=("AMERICAS",),
        accepted_work_models=("REMOTE",),
        preferred_seniorities=("SENIOR", "LEAD"),
        target_amount=6000,
        currency="USD",
    ),
    "support": Answers(
        label="Customer support leadership",
        role_examples=("Support escalation management", "Knowledge base ownership"),
        skills=("Zendesk", "Macros and triage"),
        negative_keywords=("cold calling",),
        residence_country="ZA",
        accepted_work_models=("REMOTE",),
        preferred_seniorities=("LEAD",),
        target_amount=3000,
        currency="USD",
    ),
}


@pytest.fixture
def fresh_config(tmp_path: Path) -> Path:
    """A copy of the committed directory, with no local file in it."""
    target = tmp_path / "config"
    shutil.copytree(committed_config_dir(), target)
    (target / "search.local.yaml").unlink()  # Remove the explicitly selected test example.
    assert not (target / "search.local.yaml").exists()
    return target


def _text_of(config_dir: Path) -> str:
    return (config_dir / "search.local.yaml").read_text(encoding="utf-8").lower()


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_a_fresh_setup_loads_through_the_real_loader(fresh_config: Path, persona: str) -> None:
    """A configuration that does not load is worse than no configuration: every
    later command refuses, and the person who answered a wizard has no way to
    know that is what happened."""
    run_setup(fresh_config, PERSONAS[persona])
    config, path = load_search_config(fresh_config)
    assert path.name == "search.local.yaml"
    assert config.config_id
    assert config.config_version >= 1


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_nothing_of_the_owner_survives_into_a_fresh_configuration(
    fresh_config: Path, persona: str
) -> None:
    """The starter is empty by construction, so there is nothing to inherit.
    This asserts it rather than trusting the generator, because the wizard
    could reach for the worked example by mistake and the failure would look
    like a very well-informed first run."""
    if persona == "business_systems":
        pytest.skip("this persona's own words legitimately overlap the owner's field")
    run_setup(fresh_config, PERSONAS[persona])
    written = _text_of(fresh_config)
    found = [word for word in OWNER_WORDS if word in written]
    assert not found, f"{persona} inherited: {found}"


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_the_person_s_own_words_are_what_the_matcher_reads(
    fresh_config: Path, persona: str
) -> None:
    """Section 34: onboarding is about the WORK, not about job titles. Every
    role example becomes a lexicon phrase matched against the whole posting,
    and a test that only checked the file was written would not notice if they
    had become title rules instead."""
    answers = PERSONAS[persona]
    run_setup(fresh_config, answers)
    config, _ = load_search_config(fresh_config)

    phrases = {
        pattern.lower()
        for signal in config.lexicon.values()
        for pattern in getattr(signal, "patterns", ())
    }
    for example in answers.role_examples + answers.skills:
        assert example.lower() in phrases, f"{example!r} did not become a phrase"

    # And NOT a title rule. A title may name a role and admit it; it may never
    # establish compatibility -- ADR-0014.
    titles = {
        fragment.lower()
        for section in ("primary", "strong_adjacent", "conditional", "excluded")
        for rule in config.taxonomy.rules(section)
        for group in rule.all_of
        for fragment in group
    }
    for example in answers.role_examples:
        assert example.lower() not in titles, f"{example!r} became a title rule"


@pytest.mark.parametrize("persona", sorted(PERSONAS))
def test_where_she_lives_is_hers_and_is_not_the_owners(fresh_config: Path, persona: str) -> None:
    run_setup(fresh_config, PERSONAS[persona])
    config, _ = load_search_config(fresh_config)
    assert config.eligibility.candidate_country == PERSONAS[persona].residence_country
    assert config.eligibility.candidate_country != "BR"


def test_two_people_answering_differently_get_different_searches(
    tmp_path: Path,
) -> None:
    """The one that would be silently wrong. If the wizard were writing the
    same file whatever it was told, every test above would still pass."""
    written = {}
    for persona, answers in PERSONAS.items():
        target = tmp_path / persona
        shutil.copytree(committed_config_dir(), target)
        run_setup(target, answers)
        written[persona] = _text_of(target)
    assert len(set(written.values())) == len(PERSONAS)


def test_pressing_enter_through_the_whole_wizard_still_works(
    fresh_config: Path,
) -> None:
    """A person who has not seen a posting yet cannot be asked to invent
    preferences. Answering nothing has to produce something that loads."""
    run_setup(fresh_config, Answers())
    config, path = load_search_config(fresh_config)
    assert path.name == "search.local.yaml"
    assert config.config_id
