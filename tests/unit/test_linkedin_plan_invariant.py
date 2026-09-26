"""A large skill or tool inventory never multiplies LinkedIn searches.

LinkedIn (and every targeted source) is searched by ROLE: the person's named
role anchors, a bounded set of role-family aliases, and a few of their work
phrases (responsibility signals). Tool signals are score evidence AFTER
retrieval and are not search terms: a profile listing 200 tools makes exactly
as many searches, with the same terms, as one listing 10. Synthetic roles and
tools only.

The configuration keeps ONE responsibility weight, so the work-phrase cap has
room left: a tool leaking into the work phrases would take a free slot and
fail these tests (with the full shipped weights, the cap hid such a leak).
"""

from __future__ import annotations

import inspect

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.discovery import plan
from career_agent.discovery.anchors import RoleAnchors


def _with_tools(n: int) -> SearchConfig:
    config, _ = load_search_config(committed_config_dir())
    data = config.model_dump()
    template = next(iter(data["lexicon"].values()))
    # One responsibility, so the work-phrase cap has room for a leak to show.
    duties = data["scoring"]["components"]["responsibilities"]["weights"]
    keep = next(iter(duties))
    data["scoring"]["components"]["responsibilities"]["weights"] = {keep: duties[keep]}
    # No shipped tools either: the baseline then holds no tool signal at all,
    # so a leak changes the terms whether or not roles are named.
    weights: dict[str, float] = {}
    for i in range(n):
        sid = f"synthetic_tool_{i:03d}"
        data["lexicon"][sid] = {
            **template,
            "label": f"Synthetic Tool {i:03d}",
            "patterns": [rf"\bsynthetic tool {i:03d}\b"],
        }
        # Heavier than any responsibility, so a leak would rank first.
        weights[sid] = 100.0
    data["scoring"]["components"]["technologies"]["weights"] = weights
    return SearchConfig.model_validate(data)


ANCHORS = RoleAnchors.model_validate(
    {
        "anchors": [{"text": "Synthetic Role Engineer"}, {"text": "Invented Ops Lead"}],
        "aliases": [{"text": "Synthetic Role Specialist", "anchor": "Synthetic Role Engineer"}],
    }
)


NO_ANCHORS = RoleAnchors()


@pytest.mark.parametrize("anchors", [ANCHORS, NO_ANCHORS], ids=["anchors", "no-anchors"])
@pytest.mark.parametrize("tools", [10, 60, 200])
def test_tools_never_change_the_linkedin_terms(tools: int, anchors: RoleAnchors) -> None:
    baseline = plan.query_terms(_with_tools(0), anchors)
    work_cap = plan.MAX_WORK_TERMS_WITH_ANCHORS if anchors.anchors else plan.MAX_WORK_TERMS_ALONE
    assert sum(t.origin == "work" for t in baseline) < work_cap, (
        "the work-phrase cap must have room left, or a leak could hide behind it"
    )
    grown = plan.query_terms(_with_tools(tools), anchors)
    assert grown == baseline, "a tool became a search term"
    assert all(term.origin in {"anchor", "alias", "work"} for term in grown)
    assert not any("Synthetic Tool" in term.text for term in grown)


def test_the_plan_is_bounded_whatever_the_inventory() -> None:
    config = _with_tools(200)
    terms = plan.query_terms(config, ANCHORS)
    limit = len(ANCHORS.anchors) + plan.MAX_ALIAS_TERMS + plan.MAX_WORK_TERMS_WITH_ANCHORS
    assert len(terms) <= limit


def test_the_planner_cannot_read_career_evidence() -> None:
    """No database handle reaches the planner, so confirmed skills and tools
    in the Career Profile cannot become searches."""
    for function in (plan.query_terms, plan.plan_queries, plan.targeted_plan):
        names = set(inspect.signature(function).parameters)
        assert not names & {"conn", "connection", "db", "evidence", "claims"}, function
    source = inspect.getsource(plan)
    for evidence in ("verified_claim", "career_repo", "career_experience", "ClaimRepo"):
        assert evidence not in source, evidence
