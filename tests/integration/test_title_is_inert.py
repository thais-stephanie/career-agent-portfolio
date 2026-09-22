"""The title buys no compatibility points. Measured, on the real configuration.

ADR-0014 decided this and `match/score.py` implements it. Until now the
invariant was protected by prose in three docstrings and by nothing that would
fail if somebody reintroduced a title channel -- and two comments in the
shipped configuration still asserted the OPPOSITE, which is how a future reader
talks themselves into putting it back.

The distinction this file keeps is the one that makes the rule survivable:

    A title may NAME a role, ADMIT it into the search, and carry SENIORITY.
    A title may not make the same work score higher because it sounds better.

So the tests come in pairs. One half proves the score does not move. The other
half proves the classification, the rule id and the seniority reading still DO
move -- because an invariant enforced by deleting the feature would be a
different product, and a test suite that only checked the first half would let
somebody satisfy it by removing the taxonomy.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.match.engine import JobFacts, match_job

WHEN = "2026-09-06T00:00:00Z"

#: One posting, written once. Every test below scores THIS body and varies only
#: the line above it.
BODY = """
We are hiring for our internal platforms team.

Responsibilities
- Own our HubSpot CRM architecture: the object model, custom objects and the
  lifecycle stages everything else depends on.
- Design and build workflow automation across HubSpot, Stripe and our billing
  system, using n8n for orchestration.
- Build and maintain REST API integrations and webhooks between our business
  systems, and own the data synchronisation that keeps them consistent.
- Gather business requirements from revenue operations and finance.

Requirements
- Deep HubSpot experience, including the HubSpot API.
- Comfortable with JSON, SQL and light scripting in Python or JavaScript.
- Experience with an iPaaS such as n8n, Workato, Zapier or Make.

We hire globally and work from anywhere.
"""

#: Titles chosen to span the taxonomy, and every one of them free of a
#: seniority word. `Senior` is deliberately absent: a title stating a LEVEL is
#: evidence and is allowed to move the score, which the last test proves.
TITLES_WITHOUT_A_LEVEL = [
    "Business Systems Analyst",  # PRIMARY
    "Automation Engineer",  # PRIMARY
    "Account Executive",  # EXCLUDED
    "Salesforce Administrator",  # EXCLUDED
    "Operations Wizard",  # UNCLASSIFIED
    "Underwater Basket Weaver",  # UNCLASSIFIED, and absurd on purpose
    "",  # none at all
]


@pytest.fixture(scope="module")
def config():
    """The committed worked example, never the owner's private file."""
    loaded, _ = load_search_config(committed_config_dir())
    return loaded


def score(config, title: str):
    return match_job(config, JobFacts(title=title, description=BODY), computed_at=WHEN)


# =========================================================================
# 1. THE SCORE DOES NOT MOVE
# =========================================================================


def test_the_same_body_scores_the_same_under_every_title(config) -> None:
    """The whole of ADR-0014 in one assertion.

    A warehouse posting titled `Business Systems Analyst` once scored 39 and
    the identical body under `Operations Wizard` scored 14. Three channels
    carried it -- a `role_family` component, a title-only software penalty, and
    title text reaching the weighted signals through `observe()`'s union.
    """
    scores = {title: score(config, title).match_score for title in TITLES_WITHOUT_A_LEVEL}
    assert len(set(scores.values())) == 1, scores


def test_an_excluded_title_costs_nothing(config) -> None:
    """`EXCLUDED` names off-target work. It does not PRICE it.

    The configuration comment said an excluded title "zeroes the role-family
    component and applies the configured penalty". There is no role-family
    component, and no penalty fires.
    """
    excluded = score(config, "Account Executive")
    unclassified = score(config, "Operations Wizard")
    assert excluded.title.resolved_class == "EXCLUDED"
    assert excluded.match_score == unclassified.match_score
    assert excluded.penalties == ()


def test_a_primary_title_earns_nothing(config) -> None:
    """The other direction, and the one a reader is likelier to want back."""
    primary = score(config, "Business Systems Analyst")
    nonsense = score(config, "Underwater Basket Weaver")
    assert primary.title.resolved_class == "PRIMARY"
    assert primary.match_score == nonsense.match_score


def test_no_title_at_all_scores_the_same(config) -> None:
    """A posting with an empty title is not penalised for being untitled."""
    assert score(config, "").match_score == score(config, "Business Systems Analyst").match_score


def test_the_confidence_reading_is_not_a_back_door(config) -> None:
    """`data_confidence` measures how much the POSTING said. A title moving it
    would put the same defect in the second measurement."""
    readings = {title: score(config, title).data_confidence for title in TITLES_WITHOUT_A_LEVEL}
    assert len(set(readings.values())) == 1, readings


def test_no_component_or_penalty_is_named_after_the_title(config) -> None:
    """Structural, and it is what would catch a NEW channel rather than the
    three that were closed. A component called `role_family` is the shape the
    defect took last time."""
    result = score(config, "Business Systems Analyst")
    named = {component.component_id for component in result.components}
    named |= {penalty.penalty_id for penalty in result.penalties}
    for identifier in named:
        assert "title" not in identifier.lower(), identifier
        assert "role_family" not in identifier.lower(), identifier


# =========================================================================
# 2. AND THE TITLE STILL DOES ITS REAL WORK
# =========================================================================


def test_the_title_still_classifies_and_names_its_rule(config) -> None:
    """An invariant satisfied by deleting the taxonomy would be a different
    product. The title still names the role and says which rule matched."""
    primary = score(config, "Business Systems Analyst")
    assert primary.title.resolved_class == "PRIMARY"
    assert primary.title.rule_id == "business_systems"
    assert primary.title.rule_label

    excluded = score(config, "Account Executive")
    assert excluded.title.resolved_class == "EXCLUDED"
    assert excluded.title.rule_id == "account_executive"


def test_two_titles_can_classify_differently_and_still_score_the_same(config) -> None:
    """The pair, in one test. This is the property the product needs: the
    taxonomy is informative and inert at the same time."""
    primary = score(config, "Business Systems Analyst")
    excluded = score(config, "Account Executive")
    assert primary.title.resolved_class != excluded.title.resolved_class
    assert primary.match_score == excluded.match_score


def test_a_level_in_the_title_is_evidence_and_does_move_the_score(config) -> None:
    """The ONE thing a title may still pay for, and it is not the role name.

    A stated level is evidence about the job. `SeniorityReading` records that
    it came from the title, and the scorer asks the SOURCE rather than the
    value -- a level nobody stated earns nothing.
    """
    senior = score(config, "Senior Business Systems Analyst")
    unstated = score(config, "Business Systems Analyst")

    assert senior.seniority.value == "SENIOR"
    assert senior.seniority.source.value == "TITLE_GRADE"
    assert unstated.seniority.source.value == "DEFAULT"
    assert senior.match_score > unstated.match_score


def test_the_level_pays_regardless_of_which_role_name_carries_it(config) -> None:
    """And the level is worth the same whatever the role is called, which is
    what stops the seniority channel becoming a title channel by proxy."""
    a = score(config, "Senior Business Systems Analyst")
    b = score(config, "Senior Underwater Basket Weaver")
    assert a.seniority.value == b.seniority.value == "SENIOR"
    assert a.match_score == b.match_score
