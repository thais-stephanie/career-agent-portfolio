"""Scheduling decides WHEN a source runs. It never decides what the source ingests.

WHY THIS FILE EXISTS
--------------------
Gupy holds 79,589 of the corpus's 103,272 postings and takes hours to refresh.
For somebody in Utah looking for work in the United States and Canada, every one
of those hours buys nothing.

But the last time this product made a decision about Gupy it made the wrong kind:
V1.6 filtered it AT COLLECTION TIME by one person's preferences and left 72,682
on-site postings uncollected for everybody. ADR-0019 is the correction, and the
line it drew is the line these tests defend.

  * WHEN a source is refreshed is a fact about this candidate's market.
  * WHAT a source ingests once it runs is a fact about the market itself.

The second test section is the important one: it asserts that this module is
STRUCTURALLY incapable of the first mistake, because there is nowhere in its
signature to put a role, a seniority or a contract type.
"""

from __future__ import annotations

from career_agent.sources import scheduling
from career_agent.sources.scheduling import (
    OTHER_MARKET,
    TARGETED,
    WORLDWIDE,
    plan_refresh,
    regions_for,
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
# 1. THE TWO SCENARIOS THIS WAS BUILT FOR
# =========================================================================


def test_a_candidate_targeting_the_united_states_does_not_wait_for_a_brazilian_board() -> None:
    """Scenario B. Utah, looking at the United States and Canada.

    Gupy is 77% of the corpus and hours of refreshing, and none of it is work
    this person can take. It is PAUSED, with a reason, and one button turns it
    back on.
    """
    plans = _by_id(plan_refresh(SOURCES, countries=["US", "CA"]))
    assert plans["gupy"].paused is True
    assert plans["gupy"].priority == OTHER_MARKET
    assert "market you have not asked for" in plans["gupy"].reason
    # And the worldwide boards, which is where United States work actually is
    # for this product today, are not paused.
    for source in ("greenhouse", "ashby", "remoteok"):
        assert plans[source].paused is False


def test_a_candidate_targeting_brazil_gets_the_brazilian_boards_first() -> None:
    """Scenario A. The same machinery, the opposite answer."""
    plans = _by_id(plan_refresh(SOURCES, countries=["BR"]))
    assert plans["gupy"].paused is False
    assert plans["gupy"].priority == TARGETED
    assert plans["programathor"].priority == TARGETED
    # Brazil is in Latin America, so the LATAM agencies are targeted too.
    assert plans["getonbrd"].priority == TARGETED
    # ...and a European board is not.
    assert plans["arbeitnow"].paused is True


def test_a_worldwide_board_is_never_paused_for_anybody() -> None:
    """ "Remote, worldwide" is the one market everybody shares."""
    for countries in (["BR"], ["US"], ["DE"], []):
        plans = _by_id(plan_refresh(SOURCES, countries=countries))
        assert plans["greenhouse"].paused is False, countries
        assert plans["greenhouse"].priority == WORLDWIDE, countries


def test_saying_nothing_about_where_you_can_work_pauses_only_the_regional_boards() -> None:
    """The honest default: we do not know the market, so we refresh every market's.

    Not "refresh everything", which is the behaviour this module exists to end,
    and not "refresh nothing", which would leave a new install with no jobs.
    """
    plans = _by_id(plan_refresh(SOURCES, countries=[]))
    assert plans["gupy"].paused is True
    assert plans["arbeitnow"].paused is True
    assert plans["remoteok"].paused is False


def test_a_country_nobody_mapped_still_gets_the_worldwide_boards() -> None:
    plans = _by_id(plan_refresh(SOURCES, countries=["JP"]))
    assert plans["ashby"].paused is False
    assert plans["gupy"].paused is True


# =========================================================================
# 2. IT CANNOT EXPRESS WHAT V1.6 GOT WRONG
# =========================================================================


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
    assert parameters == {"sources", "countries", "enabled"}, parameters


def test_pausing_hides_no_posting_that_was_already_collected() -> None:
    """The sentence a candidate reads has to be true, so it is asserted.

    A paused source is one that will not be REFRESHED. Everything it has ever
    returned is still in the corpus, still scored and still in the list, because
    nothing here touches a query.
    """
    plans = _by_id(plan_refresh(SOURCES, countries=["US"]))
    assert "still in your list" in plans["gupy"].reason


# =========================================================================
# 3. THE CANDIDATE OVERRULES THE SCHEDULER
# =========================================================================


def test_turning_a_source_on_by_hand_always_wins() -> None:
    """A Brazilian in Toronto may want Brazilian boards for reasons no country
    code can express, and a scheduler that could not be overruled would be one
    making the decision instead of informing it.
    """
    plans = _by_id(plan_refresh(SOURCES, countries=["CA"], enabled=["gupy"]))
    assert plans["gupy"].paused is False
    assert plans["gupy"].priority == TARGETED
    assert "yourself" in plans["gupy"].reason


# =========================================================================
# 4. THE REGION MAPPING
# =========================================================================


def test_global_is_in_every_answer() -> None:
    assert "global" in regions_for([])
    assert "global" in regions_for(["BR"])
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
