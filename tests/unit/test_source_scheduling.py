"""Scheduling decides WHEN a source runs. It never decides what the source ingests,
and it never decides that a source does not run (ADR-0032).

Gupy once held 79,589 of the corpus's postings, and V1.6 filtered it AT
COLLECTION TIME by one person's preferences; ADR-0019 drew the line these tests
defend: WHAT a source ingests is a fact about the market. ADR-0032 drew the
second line: WHETHER a source runs is the candidate's explicit choice, and the
markets she NAMED only put boards in order. Where she lives decides nothing here.
"""

from __future__ import annotations

from career_agent.sources import scheduling
from career_agent.sources.scheduling import (
    OTHER_MARKET,
    TARGETED,
    WORLDWIDE,
    plan_refresh,
    regions_for,
    slice_order,
)

#: The catalogue's own regions, as `config/source_catalogue.yaml` records them.
SOURCES = {
    "gupy": "br",
    "programathor": "br",
    "getonbrd": "latam",
    "recruiterflow": "latam",
    "arbeitnow": "eu",
    "greenhouse": "global",
    "ashby": "global",
    "remoteok": "global",
}


def _by_id(plans) -> dict:
    return {p.source_id: p for p in plans}


# =========================================================================
# 1. NO MARKETS: A GLOBAL SEARCH
# =========================================================================


def test_no_markets_named_refreshes_every_board_worldwide_ones_first() -> None:
    plans = plan_refresh(SOURCES, markets=[])
    assert {p.source_id for p in plans} == set(SOURCES)
    assert not hasattr(plans[0], "paused"), "the scheduler can no longer pause anything"
    by_id = _by_id(plans)
    for source in ("greenhouse", "ashby", "remoteok"):
        assert by_id[source].priority == WORLDWIDE
    for source in ("gupy", "getonbrd", "arbeitnow"):
        assert by_id[source].priority == OTHER_MARKET
    assert [p.priority for p in plans] == sorted(p.priority for p in plans)


# =========================================================================
# 2. MARKETS SHE CHOSE GO FIRST; NOTHING ELSE IS LEFT OUT
# =========================================================================


def test_chosen_markets_go_first_and_every_other_board_still_runs() -> None:
    plans = plan_refresh(SOURCES, markets=["EMEA"])
    assert {p.source_id for p in plans} == set(SOURCES)
    assert plans[0].source_id == "arbeitnow" and plans[0].priority == TARGETED
    assert "market you chose" in plans[0].reason
    by_id = _by_id(plans)
    assert by_id["gupy"].priority == OTHER_MARKET
    assert "may hire from anywhere" in by_id["gupy"].reason


def test_where_she_lives_does_not_pause_the_european_board() -> None:
    """The case ADR-0032 exists for: a remote candidate in Brazil. Residence is
    not an input at all; with Latin America chosen, Europe still runs."""
    plans = _by_id(plan_refresh(SOURCES, markets=["LATAM"]))
    assert plans["gupy"].priority == plans["getonbrd"].priority == TARGETED
    assert plans["arbeitnow"].priority == OTHER_MARKET


def test_a_country_market_reaches_its_own_boards_and_its_region() -> None:
    plans = _by_id(plan_refresh(SOURCES, markets=["BR"]))
    assert plans["gupy"].priority == plans["getonbrd"].priority == TARGETED


def test_worldwide_as_a_market_prefers_no_regional_board() -> None:
    plans = _by_id(plan_refresh(SOURCES, markets=["WORLDWIDE"]))
    assert plans["greenhouse"].priority == WORLDWIDE
    assert plans["arbeitnow"].priority == OTHER_MARKET


# =========================================================================
# 3. THE CANDIDATE OVERRULES THE SCHEDULER
# =========================================================================


def test_turning_a_source_on_by_hand_always_goes_first() -> None:
    plans = _by_id(plan_refresh(SOURCES, markets=["NORTH_AMERICA"], enabled=["gupy"]))
    assert plans["gupy"].priority == TARGETED
    assert "yourself" in plans["gupy"].reason


def test_nothing_in_this_module_can_describe_the_content_of_a_feed() -> None:
    """The structural half of ADR-0019, asserted rather than promised.

    V1.6's breach was a collector that applied one person's preferences -- remote
    only, no on-site, no PJ -- and left 72,682 postings uncollected for everybody.
    A scheduler that could take a role, a seniority, a work model or a contract
    type would be one refactor away from doing it again.

    So the module's SYNTAX TREE is read for those words -- not its text, which is
    the mistake the first version of this test made. Comments naming what is
    forbidden are how the rule is explained; a grep over the file failed on the
    explanation itself, which is the same defect as reading a company's own age
    as a requirement and is worth not repeating in a test.

    `plan_refresh` takes sources, countries and an override list, and there is
    nowhere else to put anything.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(scheduling))

    # A DOCSTRING IS THE FIRST STATEMENT OF A BODY, and identifying it that way
    # is the only reliable method: comparing against `ast.get_docstring` fails
    # because that function cleans indentation, so the cleaned text and the raw
    # literal are different strings.
    docstrings = {
        id(body[0].value)
        for node in ast.walk(tree)
        if (body := getattr(node, "body", None))
        and isinstance(body, list)
        and body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    }

    identifiers: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr.lower())
        elif isinstance(node, ast.arg) or isinstance(node, ast.keyword) and node.arg:
            identifiers.add(node.arg.lower())
        elif isinstance(node, ast.FunctionDef | ast.ClassDef):
            identifiers.add(node.name.lower())
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            # A string LITERAL is code: a dictionary key or a reason sentence is
            # where a content rule would actually be smuggled in.
            identifiers.add(node.value.lower())

    forbidden = (
        "seniority",
        "work_model",
        "workplace_type",
        "contract_regime",
        "employment_type",
        "role_family",
        "lexicon",
    )
    for word in forbidden:
        offenders = [i for i in identifiers if word in i]
        assert not offenders, (
            f"`{word}` in a scheduling module is ADR-0019 happening again: "
            f"scheduling decides WHEN a source runs, never what it ingests. {offenders}"
        )

    parameters = set(inspect.signature(plan_refresh).parameters)
    assert parameters == {"sources", "markets", "enabled"}, parameters


def test_global_is_in_every_answer() -> None:
    assert "global" in regions_for([])
    assert "global" in regions_for(["BR"])
    assert "global" in regions_for(["EMEA"])
    assert "global" in regions_for(["ZZ"])


def test_brazil_reaches_both_its_own_market_and_the_regional_one() -> None:
    assert regions_for(["BR"]) == frozenset({"global", "br", "latam"})


def test_north_america_has_no_regional_board_here_and_that_is_a_coverage_fact() -> None:
    """`US` resolving to `global` alone is a statement about OUR catalogue.

    It is not a claim that the United States has no job boards. The gap is real,
    it is why the source matrix is the document to read, and hiding it behind an
    invented region would make the gap harder to see rather than smaller.
    """
    assert regions_for(["US", "CA"]) == frozenset({"global"})


def test_the_country_code_is_read_case_insensitively_and_trimmed() -> None:
    assert regions_for([" br "]) == regions_for(["BR"])


def test_a_region_market_reaches_its_boards() -> None:
    assert regions_for(["EMEA"]) == regions_for(["EU"]) == frozenset({"global", "eu"})
    assert regions_for(["LATAM"]) == frozenset({"global", "br", "latam"})


# =========================================================================
# 5. SLICES OF A QUERY-DRIVEN SOURCE
# =========================================================================

OFFERED = ("anywhere", "north-america", "latam", "south-america", "europe", "brazil", "asia")


def test_slices_keep_the_vendor_order_without_markets() -> None:
    assert slice_order(OFFERED, markets=[]) == OFFERED


def test_chosen_markets_lead_the_slices_and_none_is_dropped() -> None:
    ordered = slice_order(OFFERED, markets=["EMEA", "LATAM"])
    assert ordered[:3] == ("europe", "latam", "south-america")
    assert sorted(ordered) == sorted(OFFERED)
