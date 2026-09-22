"""Changing one candidate's search from HubSpot to fashion must change nothing a
provider ingests (ADR-0019). Two shapes of proof: the collection plan is the
same set for three personas, and the collectors cannot even see a search.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from career_agent.providers.jobgether import LOCATION_SLUGS, default_slices
from career_agent.sources.scheduling import slice_order

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"

#: Every module that decides what a provider is asked for.
INGESTION_MODULES = (
    "pipeline/jobgether_collect.py",
    "pipeline/remotive_collect.py",
    "pipeline/fourdayweek_collect.py",
    "pipeline/collect.py",
    "providers/jobgether.py",
    "providers/remotive.py",
    "providers/fourdayweek.py",
    "providers/teamtailor.py",
    "providers/rippling.py",
    "providers/comeet.py",
    "providers/remotesource.py",
    "pipeline/board_discovery.py",
)

#: A candidate's vocabulary. None of these words may appear as an identifier
#: or a string literal in an ingestion module.
CANDIDATE_WORDS = (
    "search_config",
    "lexicon",
    "preferences",
    "verified_claim",
    "career_evidence",
    "seniority_preferred",
    "compensation_target",
    "target_titles",
    "keyword",
    "jobreferences",
    "hubspot",
)


def _names_and_strings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name | ast.Attribute):
            found.add((node.id if isinstance(node, ast.Name) else node.attr).lower())
        elif isinstance(node, ast.Import | ast.ImportFrom):
            found.update((alias.name or "").lower() for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module.lower())
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            # A string literal is code: a query parameter name is where a
            # candidate's vocabulary would actually be smuggled in.
            found.add(node.value.lower())
    return found


@pytest.mark.parametrize("module", INGESTION_MODULES)
def test_an_ingestion_module_never_names_the_candidate(module: str) -> None:
    identifiers = _names_and_strings(SRC / module)
    for word in CANDIDATE_WORDS:
        offenders = sorted(i for i in identifiers if word in i)
        assert not offenders, (
            f"{module} names `{word}`: {offenders}. Ingestion describes the market; "
            "the candidate is a layer later (ADR-0019)."
        )


@pytest.mark.parametrize(
    ("country", "scopes"),
    [
        ("BR", ("WORLDWIDE", "LATAM", "AMERICAS")),
        ("US", ("WORLDWIDE", "NORTH_AMERICA")),
        ("DE", ("WORLDWIDE", "EMEA")),
    ],
)
def test_three_personas_get_the_same_collection_plan(country: str, scopes: tuple[str, ...]) -> None:
    """The ORDER may follow the candidate; the SET may not. `scopes` is what a
    persona's search would say and is deliberately unused: nothing about a
    search reaches the plan at all."""
    del scopes
    ordered = slice_order(LOCATION_SLUGS, countries=(country,))
    assert set(ordered) == set(LOCATION_SLUGS)
    assert len(ordered) == len(LOCATION_SLUGS)
    plan = default_slices(ordered)
    assert {s.key for s in plan} == {s.key for s in default_slices(LOCATION_SLUGS)}
