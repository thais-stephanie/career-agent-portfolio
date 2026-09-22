"""Four facts the fingerprint could not hold, and now can.

An independent review of the golden set found the same defect four times: a
posting stated something, and the only way to record it was to round it off
into a neighbouring field that meant something else. Each rounding was safe in
isolation and wrong in aggregate, because every one of them ended in the same
place -- a geography or a requirement that the employer never actually stated.

    "should preferably be based in Sao Paulo"
        -> a soft preference recorded as a hard scope, or as INFERRED evidence

    "cover EST working hours"
        -> a clock recorded as a border

    "travel for customer meetings" (no frequency given)
        -> a withheld frequency recorded as the smallest non-zero one

    "must reside in D.C. OR be willing to relocate"
        -> an accepted path recorded as a requirement, or as silence

THE AXIS THAT KEEPS BEING CONFLATED
------------------------------------
Two of the four are the same confusion:

    status    EXPLICIT / INFERRED     how directly the source supports it
    strength  REQUIRED / PREFERRED    how binding the employer made it

They are orthogonal, and every combination occurs. Using INFERRED to signal a
soft requirement claims the evidence is weak when the evidence is a sentence --
and it does not even work, because a downstream gate reads the scope, not the
status.

NONE OF THIS COST ANY SCHEMA
-----------------------------
Two new dimensions and two new nested fields, and the description schema is
byte-identical: 8,768 characters, 24/24 optional, 13/16 union parameters.
Dimensions ride the transport as rows and compound values ride string grammars,
so the vocabulary grew and the wire format did not notice.
"""

from typing import Any

from tests.integration.test_worksite_contract import run

from career_agent.domain.enums import (
    ExtractionStatus,
    HiringScopeKind,
    RequirementStrength,
    TimezoneConstraintKind,
)

# --- A. a preference is EXPLICIT and soft, both at once ---------------------


def test_a_preferred_location_is_explicit_evidence_of_a_soft_requirement() -> None:
    """gc-06. The posting says it outright, and says it is a preference.

    "The candidate should preferably be based in Sao Paulo, Brazil" supports
    `status: EXPLICIT` -- those are the posting's own words -- and
    `requirement_level: PREFERRED`. Before this change the only way to signal
    softness was to write INFERRED, which said the wrong thing on the wrong
    axis and left Sao Paulo looking like a wall regardless.
    """
    fp = run(
        "The candidate should preferably be based in Sao Paulo, Brazil.",
        {"hiring_scope": {"status": "EXPLICIT", "value": "PREFERRED:COUNTRY_LIST:BR"}},
    )
    scope = fp.eligibility.hiring_scope

    assert scope.status is ExtractionStatus.EXPLICIT, "the posting states it in words"
    assert scope.value is not None
    assert scope.value.kind is HiringScopeKind.COUNTRY_LIST
    assert scope.value.countries == ["BR"]
    assert scope.value.requirement_level is RequirementStrength.PREFERRED

    # And a preference must never read as a wall to whatever comes next.
    assert scope.value.requirement_level is not RequirementStrength.REQUIRED


def test_an_unhedged_scope_is_required_without_the_model_saying_so() -> None:
    """`COUNTRY_LIST:US` with no prefix means REQUIRED.

    The default is not laziness, it is the reading. "Candidates must reside in
    the US" is a requirement, and demanding a prefix on every row would add a
    way to get the common case wrong in exchange for nothing.
    """
    fp = run(
        "Candidates must reside in the United States.",
        {"hiring_scope": {"status": "EXPLICIT", "value": "COUNTRY_LIST:US"}},
    )

    assert fp.eligibility.hiring_scope.value.requirement_level is RequirementStrength.REQUIRED


# --- B. a timezone is hours, not an address --------------------------------


def test_a_working_hours_overlap_is_not_a_place() -> None:
    """gc-05, gc-13. EST is a clock. Sao Paulo covers it.

    The anchor alone was ambiguous: `EST` could have meant "cover these hours"
    or "live in this timezone", and the difference decides whether a Brazilian
    candidate is feasible. OVERLAP is the default precisely because it is the
    reading that cannot invent a country.
    """
    fp = run(
        "Although this role is remote you need to be able to cover EST working hours.",
        {
            "timezone_requirement": {"status": "EXPLICIT", "value": "OVERLAP:EST"},
            "work_environment.work_model": {"status": "EXPLICIT", "value": "REMOTE"},
        },
    )
    timezone = fp.eligibility.timezone_requirement

    assert timezone.status is ExtractionStatus.EXPLICIT
    assert timezone.value.anchor == "EST"
    assert timezone.value.kind is TimezoneConstraintKind.OVERLAP

    # The constraint is recorded AND it bought no geography. Both halves.
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


def test_a_residence_timezone_has_to_be_earned_from_the_posting() -> None:
    """`RESIDENCE:` exists, and only the posting can ask for it.

    "You must be located in a US timezone" is a real sentence that real
    postings write, and it constrains where the person lives. It is still not a
    country list -- US timezones cover parts of several countries -- so it
    stays in the timezone dimension rather than becoming geography.
    """
    fp = run(
        "You must be located in a US timezone.",
        {"timezone_requirement": {"status": "EXPLICIT", "value": "RESIDENCE:US-EASTERN"}},
    )

    assert fp.eligibility.timezone_requirement.value.kind is TimezoneConstraintKind.RESIDENCE
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- C. travel required, frequency withheld --------------------------------


def test_travel_can_be_required_with_the_frequency_left_unstated() -> None:
    """gc-05. Two facts, and the posting supplied only one.

    "Travel for customer and internal meetings" says travel happens. It does
    not say how often. `TravelFrequency` has no member meaning "required,
    frequency unknown", and the tempting move -- RARE, because it is the
    smallest non-zero option -- invents the fact the posting withheld.
    """
    fp = run(
        "Although this role is remote you need to travel for customer and internal meetings.",
        {"travel_required": {"status": "EXPLICIT", "value": "true"}},
    )

    assert fp.eligibility.travel_required.status is ExtractionStatus.EXPLICIT
    assert fp.eligibility.travel_required.value is True
    assert fp.eligibility.travel_frequency.status is ExtractionStatus.NOT_STATED
    assert fp.eligibility.travel_frequency.value is None


def test_arranging_someone_elses_travel_is_not_travelling() -> None:
    """gc-20, gc-22. An executive assistant books the flights.

    "Organize domestic and international travel" describes the work. Reading it
    as the assistant's own travel would attach a cost to a job that never
    mentioned one, from a sentence that is about somebody else entirely.
    """
    fp = run(
        "Travel Arrangements: Organize domestic and international travel for the CRO.",
        {},
    )

    assert fp.eligibility.travel_required.status is ExtractionStatus.NOT_STATED
    assert fp.eligibility.travel_frequency.status is ExtractionStatus.NOT_STATED


# --- D. reside OR relocate -------------------------------------------------


def test_relocation_can_be_an_accepted_path_without_being_required() -> None:
    """gc-23. "Must reside in D.C. or be willing to relocate there."

    Relocation is not required -- someone already in D.C. moves nowhere. But
    the sentence is not silence either: it says relocating is a way in, which
    is exactly the fact a candidate weighing the job needs. Before this
    dimension existed the choice was between a false requirement and a lost
    fact.
    """
    fp = run(
        "Must reside in or be willing to relocate to the Washington, D.C. metropolitan area.",
        {
            "relocation_allowed": {"status": "EXPLICIT", "value": "true"},
            "work_environment.worksite_requirement": {
                "status": "EXPLICIT",
                "value": "REQUIRED:Washington, D.C. metropolitan area",
            },
        },
    )

    assert fp.eligibility.relocation_allowed.status is ExtractionStatus.EXPLICIT
    assert fp.eligibility.relocation_allowed.value is True
    assert fp.eligibility.relocation_required.status is ExtractionStatus.NOT_STATED

    # The worksite is where that condition actually lives.
    worksite = fp.work_environment.worksite_requirement
    assert worksite.status is ExtractionStatus.EXPLICIT
    assert "Washington" in worksite.value.locations[0]

    # And none of it says where the employer may hire from.
    assert fp.eligibility.hiring_scope.status is ExtractionStatus.NOT_STATED


# --- E. sub-national scope -------------------------------------------------


def test_named_states_survive_instead_of_becoming_the_whole_country() -> None:
    """gc-04. 26 states is not the United States.

    Flattening this to `COUNTRY_LIST:US` would hand a later geography gate a
    PASS for a candidate in any of the other 24, with nothing anywhere
    recording that 24 states had been quietly added to the posting.

    Rare -- 12 postings in 18,549 -- and severe, which is exactly the profile
    that justifies a representation costing nothing rather than a parser.
    """
    fp = run(
        "This is a remote role open to candidates located in Arizona, California and Colorado.",
        {
            "hiring_scope": {
                "status": "EXPLICIT",
                "value": "COUNTRY_LIST:US-AZ,US-CA,US-CO",
            }
        },
    )
    scope = fp.eligibility.hiring_scope.value

    assert scope.countries == ["US-AZ", "US-CA", "US-CO"]
    assert "US" not in scope.countries, "the whole country was never offered"


def test_a_country_and_its_own_states_cannot_both_be_claimed() -> None:
    """`US, US-AZ` is two different postings' worth of claim.

    Either the country is open or a list of states is. Keeping both would let
    whichever downstream reader is more permissive pick the one it prefers, and
    the permissive reading is the one that costs an application.
    """
    from pydantic import ValidationError

    from career_agent.domain.fingerprint import HiringScope

    try:
        HiringScope(kind=HiringScopeKind.COUNTRY_LIST, countries=["US", "US-AZ"])
    except ValidationError as error:
        assert "subdivisions" in str(error)
    else:  # pragma: no cover - the assertion is that this branch is unreachable
        raise AssertionError("a country and its own subdivisions were accepted together")


# --- F. the schema did not move -------------------------------------------


def test_the_new_dimensions_cost_no_schema(*_: Any) -> None:
    """Two dimensions added, zero parameters spent.

    This is the row-based transport paying for itself. Dimensions are values in
    a row, not properties of an object, so the vocabulary grows without the
    wire format noticing -- and the earlier claim that the schema had "no room
    for another dimension" was measuring the wrong thing. It has no room for
    another optional field on a transport ROW; it has unlimited room for
    dimensions.
    """
    from career_agent.llm.transport import ALL_DIMENSIONS, TDescriptionFamily
    from career_agent.llm.vendors.schema_dialect import count_optional_and_union

    optional, union = count_optional_and_union(TDescriptionFamily.model_json_schema())

    assert len(ALL_DIMENSIONS) == 33
    assert optional == 24, "unchanged across two added dimensions"
    assert union == 13
