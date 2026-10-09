"""EU, EUROPE and EMEA are three scopes (2026-10-09), and Europe is searched.

Until this date `places.yaml` filed `eu`, `european union` and `europe` under
EMEA, so "Remote - EU" admitted a candidate in London and "Europe" one in
Lagos; discovery asked all three as one "Europe" scope; Himalayas, which can
only be asked about countries, asked nothing for a European market; and
"authorized to work in the EU" was dropped by the authorization reader.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.discovery.scopes import country_scopes, market_scopes, region_countries
from career_agent.domain.enums import GateResult
from career_agent.match.gates import evaluate_gates
from career_agent.match.places import region_contains, resolve_place
from career_agent.providers.jobgether import (
    LOCATION_SLUGS,
    UNCUT_LOCATION_SLUGS,
    default_slices,
)
from career_agent.sources.scheduling import TARGETED, plan_refresh, regions_for, slice_order

ROOT = Path(__file__).resolve().parents[2]

EU27 = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "GR", "HU", "IE",
    "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT", "RO", "SK", "SI", "ES", "SE",
}  # fmt: skip


@pytest.fixture(scope="module")
def base():
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    loaded, _ = load_search_config(directory, use_example=True)
    return loaded


def candidate(base, country: str, *, markets: tuple[str, ...] = ()):
    eligibility = base.eligibility.model_copy(
        update={
            "candidate_country": country,
            "eligible_countries": [country],
            "eligible_scopes": [],
        }
    )
    regions = base.regions.model_copy(update={"target": list(markets)})
    return base.model_copy(update={"eligibility": eligibility, "regions": regions})


def geography(config, location: str, workplace: str = "REMOTE", body: str = "We build things."):
    gates = evaluate_gates(
        config,
        {},
        "Engineer",
        body,
        workplace_type=workplace,
        place=resolve_place(location),
        location_raw=location,
    )
    return {g.gate: g for g in gates}


# =========================================================================
# 1. THREE SCOPES IN THE GAZETTEER
# =========================================================================


@pytest.mark.parametrize(
    ("text", "stated", "regions"),
    [
        ("Remote - EU", ("EU",), ("EMEA", "EU", "EUROPE")),
        ("European Union", ("EU",), ("EMEA", "EU", "EUROPE")),
        ("Europe", ("EUROPE",), ("EMEA", "EUROPE")),
        ("Remote (Europa)", ("EUROPE",), ("EMEA", "EUROPE")),
        ("EMEA", ("EMEA",), ("EMEA",)),
    ],
)
def test_each_label_is_kept_as_written_and_filed_under_its_wider_scopes(
    text: str, stated: tuple[str, ...], regions: tuple[str, ...]
) -> None:
    place = resolve_place(text)
    assert place.stated_regions == stated
    assert place.regions == regions


def test_an_emea_posting_is_not_filed_under_europe() -> None:
    """It may be hiring in Cairo."""
    assert "EUROPE" not in resolve_place("Remote, EMEA").regions


def test_a_country_carries_its_memberships_for_the_filters() -> None:
    assert resolve_place("Berlin, Germany").regions == ("EMEA", "EU", "EUROPE")
    assert resolve_place("London, United Kingdom").regions == ("EMEA", "EUROPE")
    assert resolve_place("Lagos, Nigeria").regions == ("EMEA",)


@pytest.mark.parametrize(
    ("region", "country", "answer"),
    [
        ("EU", "DE", True),
        ("EU", "GB", False),
        ("EU", "CH", False),
        ("EU", "BR", False),
        ("EUROPE", "GB", True),
        ("EUROPE", "CH", True),
        ("EUROPE", "NG", False),
        ("EUROPE", "TR", None),
        ("EUROPE", "RU", None),
        ("EMEA", "NG", True),
        ("EMEA", "GB", True),
        ("EMEA", "BR", False),
        ("EU", "ZZ", None),
    ],
)
def test_containment(region: str, country: str, answer: bool | None) -> None:
    assert region_contains(region, country) is answer


def test_the_eu_is_inside_europe_and_europe_inside_emea() -> None:
    eu, europe = set(region_countries("EU")), set(region_countries("EUROPE"))
    assert eu == EU27
    assert eu < europe
    assert all(region_contains("EMEA", code) is True for code in europe)


# =========================================================================
# 2. THE GATE READS THEM APART
# =========================================================================


@pytest.mark.parametrize(
    ("country", "location", "result"),
    [
        # Inside the scope: PASS. Outside it: the label alone refuses nobody,
        # so the gate stays UNRESOLVED (section 5).
        ("GB", "Remote - EU", GateResult.UNRESOLVED),
        ("GB", "Remote - Europe", GateResult.PASS),
        ("GB", "Remote - EMEA", GateResult.PASS),
        ("DE", "Remote - EU", GateResult.PASS),
        ("NG", "Remote - Europe", GateResult.UNRESOLVED),
        ("NG", "Remote - EMEA", GateResult.PASS),
        ("BR", "Remote - EU", GateResult.UNRESOLVED),
        ("BR", "Remote - Europe", GateResult.UNRESOLVED),
        ("BR", "Remote - EMEA", GateResult.UNRESOLVED),
    ],
)
def test_a_remote_posting_open_in_a_scope(base, country, location, result) -> None:
    gate = geography(candidate(base, country), location)["geography"]
    assert gate.result is result


def test_a_country_partly_in_europe_is_neither_admitted_nor_refused(base) -> None:
    gate = geography(candidate(base, "TR"), "Remote - Europe")["geography"]
    assert gate.result is GateResult.UNRESOLVED


def test_a_regional_office_location_is_not_a_residence_requirement(base) -> None:
    """An on-site posting naming only a region is too vague to refuse anybody."""
    gate = geography(candidate(base, "BR"), "Europe", workplace="ONSITE")["geography"]
    assert gate.result is not GateResult.FAIL


def test_a_stated_eu_work_requirement_is_quoted_and_left_to_her(base) -> None:
    """Kept apart from the published location: a requirement, quoted from the
    description, PENDING; never answered from a country she named."""
    body = "We build things. You must be authorized to work in the EU."
    config = candidate(base, "PT")
    config = config.model_copy(
        update={
            "eligibility": config.eligibility.model_copy(update={"authorized_countries": ["PT"]})
        }
    )
    gates = geography(config, "Lisbon, Portugal", workplace="HYBRID", body=body)
    gate = gates["work_authorization"]
    assert gate.result is GateResult.UNRESOLVED
    assert gate.quote and "authorized to work in the EU" in gate.quote
    assert "European Union" in gate.reason
    # The location half is the board's field, and carries no quote.
    assert gates["geography"].quote is None or "EU" not in gates["geography"].quote


# =========================================================================
# 3. THE NEW COUNTRIES, AND THE CODES THAT ARE ALSO STATES
# =========================================================================


@pytest.mark.parametrize(
    ("text", "countries"),
    [
        ("Nederland", ("NL",)),
        ("Republic of Ireland (Remote)", ("IE",)),
        ("Ljubljana, SI", ("SI",)),
        ("Skopje, Greater Skopje, MK", ("MK",)),
        ("Vatican City", ("VA",)),
        ("Richmond, VA", ()),
        ("VA", ()),
        ("Baltimore, MD", ()),
        ("Mobile, AL", ()),
        ("Portland, ME", ("US",)),
        ("Maceio, AL / Recife, PE", ("BR",)),
        ("Nederland, TX, US", ("US",)),
        ("Dublin, OH, US", ("US",)),
        ("Melbourne, FL, US", ("US",)),
        ("Bahrain, Khyber Pakhtunkhwa, PK", ("PK",)),
        ("Macedonia, IL, US", ("US",)),
        ("MA / DZ / AL / TN / MR / LB", ("AL", "LB", "MA")),
    ],
)
def test_countries_and_collisions(text: str, countries: tuple[str, ...]) -> None:
    assert set(countries) <= set(resolve_place(text).countries)
    if not countries or len(countries) == 1:
        assert resolve_place(text).countries == countries


# =========================================================================
# 4. DISCOVERY
# =========================================================================


def test_three_scopes_are_three_searches(base) -> None:
    keys = [s.key for s in market_scopes(candidate(base, "BR", markets=("EMEA", "EU", "EUROPE")))]
    assert keys == ["region:EMEA", "region:EU", "region:EUROPE"]


def test_no_market_stays_one_global_search_and_no_country_search(base) -> None:
    config = candidate(base, "BR")
    assert [s.key for s in market_scopes(config)] == ["remote_worldwide"]
    assert country_scopes(config) == ()


def test_a_country_only_source_asks_a_region_as_its_countries(base) -> None:
    scopes = country_scopes(candidate(base, "BR", markets=("PT", "EU")))
    assert scopes[0].country == "PT"
    assert {s.country for s in scopes} == EU27


def test_rotation_reaches_every_country_under_a_fixed_budget(base) -> None:
    """A budget of 5 country searches and 27 countries: the same five every
    run would leave 22 unasked forever. Rotated by one per run, every member
    is asked within 27 runs."""
    config = candidate(base, "BR", markets=("EU",))
    asked: set[str] = set()
    for run in range(27):
        asked |= {s.country for s in country_scopes(config, rotation=run)[:5]}
    assert asked == EU27
    assert country_scopes(config, rotation=0)[0] != country_scopes(config, rotation=1)[0]


def test_jobgether_slices_put_the_named_scope_first_and_drop_nothing() -> None:
    universe = LOCATION_SLUGS + UNCUT_LOCATION_SLUGS
    assert slice_order(universe, markets=["EU"])[:2] == ("european-union", "europe")
    assert slice_order(universe, markets=["EMEA"])[:4] == (
        "emea",
        "europe",
        "middle-east",
        "africa",
    )
    assert slice_order(universe, markets=["LU"])[:3] == ("anywhere", "europe", "luxembourg")
    for markets in (["EU"], ["EMEA"], ["EUROPE"], ["BR"], []):
        assert sorted(slice_order(universe, markets=markets)) == sorted(universe)
    assert slice_order(universe, markets=[]) == universe


def test_european_countries_are_asked_whole_and_the_universe_grows_modestly() -> None:
    slices = default_slices()
    uncut = [s for s in slices if s.location in UNCUT_LOCATION_SLUGS]
    assert len(uncut) == len(UNCUT_LOCATION_SLUGS)
    assert all(s.key == s.location for s in uncut)
    assert len(slices) == 25 * len(LOCATION_SLUGS) + len(UNCUT_LOCATION_SLUGS)


@pytest.mark.parametrize("market", ["EU", "EUROPE", "EMEA", "LU", "GR"])
def test_any_european_market_puts_the_european_boards_first(market: str) -> None:
    assert "eu" in regions_for([market])
    plans = {
        p.source_id: p for p in plan_refresh({"board": "eu", "world": "global"}, markets=[market])
    }
    assert plans["board"].priority == TARGETED
    assert set(plans) == {"board", "world"}


# =========================================================================
# 5. PUBLISHED LOCATION VS EXPLICIT REQUIREMENT, REMOTE, A CANDIDATE IN BRAZIL
# =========================================================================


@pytest.mark.parametrize("label", ["EU", "Europe", "EMEA", "Remote - EU", "Remote (Europe)"])
def test_a_regional_label_alone_does_not_refuse_her(base, label: str) -> None:
    """Case 1: the board printed a region and the description restricts
    nothing. The label is not a residence requirement: no FAIL; whether the
    employer can hire her stays open."""
    gates = geography(candidate(base, "BR"), label)
    assert gates["geography"].result is not GateResult.FAIL, gates["geography"].reason


def test_must_reside_in_the_eu_refuses_her(base) -> None:
    """Case 2: an explicit residence requirement in the description."""
    body = "We build things. Candidates must reside in the EU."
    gates = geography(candidate(base, "BR"), "Remote", body=body)
    assert gates["geography"].result is GateResult.FAIL
    assert gates["geography"].quote and "reside in the EU" in gates["geography"].quote


def test_worldwide_at_a_european_company_is_not_restricted_by_its_seat(base) -> None:
    """Case 3: headquarters and market are facts about the employer."""
    body = (
        "We are a Berlin-based company serving customers across Europe. "
        "This role is fully remote and open worldwide."
    )
    gates = geography(candidate(base, "BR"), "Worldwide", body=body)
    assert gates["geography"].result is GateResult.PASS, gates["geography"].reason


def test_eu_work_authorization_stays_quoted_and_pending(base) -> None:
    """Case 4: the requirement is kept, quoted, and nobody answered it."""
    body = "We build things. You must be authorized to work in the EU."
    gates = geography(candidate(base, "BR"), "Remote", body=body)
    gate = gates["work_authorization"]
    assert gate.result is GateResult.UNRESOLVED
    assert gate.quote and "authorized to work in the EU" in gate.quote
