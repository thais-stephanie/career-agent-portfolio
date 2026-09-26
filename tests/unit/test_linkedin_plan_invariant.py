"""A large skill or tool inventory never multiplies LinkedIn searches.

LinkedIn (and every targeted source) is searched by ROLE: the person's named
role anchors, a bounded set of role-family aliases, and a few of their work
phrases. Skills and tools are score evidence AFTER retrieval, never search
terms: a profile listing 200 tools makes exactly as many searches as one
listing 10. Synthetic roles and tools only.
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
    weights = dict(data["scoring"]["components"]["technologies"]["weights"])
    for i in range(n):
        sid = f"synthetic_tool_{i:03d}"
        data["lexicon"][sid] = {
            **template,
            "label": f"Synthetic Tool {i:03d}",
            "patterns": [rf"\bsynthetic tool {i:03d}\b"],
        }
        weights[sid] = 1.0
    data["scoring"]["components"]["technologies"]["weights"] = weights
    return SearchConfig.model_validate(data)


ANCHORS = RoleAnchors.model_validate(
    {
        "anchors": [{"text": "Synthetic Role Engineer"}, {"text": "Invented Ops Lead"}],
        "aliases": [{"text": "Synthetic Role Specialist", "anchor": "Synthetic Role Engineer"}],
    }
)


@pytest.mark.parametrize("tools", [10, 60, 200])
def test_tools_never_change_the_linkedin_terms(tools: int) -> None:
    baseline = plan.query_terms(_with_tools(0), ANCHORS)
    grown = plan.query_terms(_with_tools(tools), ANCHORS)
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
