"""What the board printed, resolved to codes -- and what it must never become.

`resolve_place` reads `location_raw`, which is the third of the three questions
CLAUDE.md invariant 3 keeps apart. The tests that matter most here are the ones
asserting what it does NOT answer.
"""

from __future__ import annotations

import pytest

from career_agent.match.places import (
    REGIONS,
    Gazetteer,
    PlaceGazetteerError,
    load_gazetteer,
    resolve_place,
)


@pytest.fixture(scope="module")
def gaz() -> Gazetteer:
    return load_gazetteer()


# =========================================================================
# The three questions stay apart
# =========================================================================


@pytest.mark.parametrize("location", ["Remote", "Hybrid", "Onsite", "Remote - Hybrid"])
def test_a_work_model_is_not_a_place(location: str, gaz: Gazetteer) -> None:
    """`Remote` says where the work is DONE, not where it may be TAKEN.

    Letting "remote" imply "worldwide" is the exact conflation invariant 3
    exists to prevent, and it is the single most tempting shortcut in this
    module: it would raise the resolved percentage and make the product lie.
    """
    assert resolve_place(location, gaz) == resolve_place(None, gaz)


def test_a_city_says_nothing_about_hiring_scope(gaz: Gazetteer) -> None:
    """The assertion is about what is ABSENT from the return value.

    `ResolvedPlace` carries countries and regions and has no field for scope,
    eligibility or permission -- so there is nothing here for a later layer to
    mistake for one. Stated as a test because "we could not add it by accident"
    is a property worth failing on if the shape ever grows.
    """
    resolved = resolve_place("San Francisco", gaz)
    assert resolved.countries == ("US",)
    assert set(vars(type(resolved))) >= {"countries", "regions"}
    assert not hasattr(resolved, "eligible")
    assert not hasattr(resolved, "hiring_scope")


# =========================================================================
# Resolution
# =========================================================================


@pytest.mark.parametrize(
    ("location", "countries", "regions"),
    [
        ("San Francisco", ("US",), ("NORTH_AMERICA",)),
        ("San Francisco, CA", ("US",), ("NORTH_AMERICA",)),
        ("US-CA-Menlo Park", ("US",), ("NORTH_AMERICA",)),
        ("São Paulo, Brasil", ("BR",), ("LATAM",)),
        ("Sao Paulo", ("BR",), ("LATAM",)),
        ("Toronto, Ontario, Canada", ("CA",), ("NORTH_AMERICA",)),
        ("London, UK", ("GB",), ("EMEA",)),
        ("Bengaluru, India", ("IN",), ("APAC",)),
        ("Home based - Worldwide", (), ("WORLDWIDE",)),
        ("Washington, DC", ("US",), ("NORTH_AMERICA",)),
    ],
)
def test_the_strings_the_corpus_actually_contains(
    location: str, countries: tuple[str, ...], regions: tuple[str, ...], gaz: Gazetteer
) -> None:
    resolved = resolve_place(location, gaz)
    assert resolved.countries == countries
    assert resolved.regions == regions


def test_accents_do_not_change_the_answer(gaz: Gazetteer) -> None:
    assert resolve_place("São Paulo", gaz) == resolve_place("Sao Paulo", gaz)


def test_a_posting_in_two_places_carries_both(gaz: Gazetteer) -> None:
    resolved = resolve_place("San Francisco, CA | London, UK", gaz)
    assert resolved.countries == ("GB", "US")
    assert set(resolved.regions) == {"EMEA", "NORTH_AMERICA"}


# =========================================================================
# Ambiguity is refused, not guessed
# =========================================================================


@pytest.mark.parametrize("code", ["CA", "IL", "CO", "DE", "GA", "IN", "PA", "TX"])
def test_a_lone_subdivision_code_resolves_to_nothing(code: str, gaz: Gazetteer) -> None:
    """Every one of these is both a US state and an ISO country code.

    `CA` is California and Canada; `IL` is Illinois and Israel; `DE` is Delaware
    and Germany. A resolver that guessed would be wrong silently, on 1,384
    occurrences of `CA` in the corpus. Unresolved is the honest answer, and an
    unresolved posting is EXCLUDED by a country filter rather than asserted
    into it.
    """
    assert not resolve_place(code, gaz).resolved


def test_a_subdivision_confirms_a_country_another_token_established(gaz: Gazetteer) -> None:
    assert resolve_place("Austin, TX", gaz).countries == ("US",)


def test_two_agreeing_subdivisions_are_an_anchor(gaz: Gazetteer) -> None:
    """`Washington, DC` is two independent US signals.

    The collision that makes one code untrustworthy does not survive being
    agreed with by a second, different one -- and a repeated SINGLE token is
    not two signals, which is why the weak matches are de-duplicated first.
    """
    assert resolve_place("Washington, DC", gaz).countries == ("US",)
    assert not resolve_place("CA, CA", gaz).resolved


def test_an_unknown_place_resolves_to_nothing_rather_than_a_guess(gaz: Gazetteer) -> None:
    assert not resolve_place("Nowhereville, Atlantis", gaz).resolved
    assert not resolve_place("", gaz).resolved
    assert not resolve_place(None, gaz).resolved


# =========================================================================
# The gazetteer file itself
# =========================================================================


def test_every_region_the_gazetteer_can_produce_is_one_the_api_offers(
    gaz: Gazetteer,
) -> None:
    """The vocabulary the API validates against and the values the resolver can
    emit must be the same set, or a filter would accept a value nothing carries
    -- or, worse, a posting would carry a region no chip can select."""
    produced = set(gaz.regions.values()) | set(gaz.country_regions.values())
    assert produced <= set(REGIONS), sorted(produced - set(REGIONS))


def test_a_yaml_boolean_key_is_named_rather_than_crashing_far_away(tmp_path) -> None:
    """YAML 1.1 reads bare `NO` and `ON` as booleans, so Norway and Ontario
    arrive as `False` and `True`. It happened while writing this file, and it
    surfaced as a TypeError inside `unicodedata.normalize` two hundred lines
    from the cause."""
    bad = tmp_path / "places.yaml"
    bad.write_text("countries:\n  NO: EMEA\n", encoding="utf-8")
    with pytest.raises(PlaceGazetteerError) as error:
        load_gazetteer(bad)
    assert "quote it" in str(error.value)


def test_a_yaml_boolean_value_is_named_too(tmp_path) -> None:
    """The same mistake on the OTHER side of the colon, which shipped.

    `country_regions` quoted its `"NO"` key and carried a comment explaining
    why. Forty lines further down the file, `norway: NO` was written unquoted
    as a VALUE, and the guard only looked at keys. Norway therefore resolved
    to the country code `FALSE`: a bucket in the filter rail labelled "False",
    no EMEA region for any Norwegian posting, and not one error raised.
    """
    bad = tmp_path / "places.yaml"
    bad.write_text("countries:\n  norway: NO\n", encoding="utf-8")
    with pytest.raises(PlaceGazetteerError) as error:
        load_gazetteer(bad)
    assert "quote it" in str(error.value)
    assert "norway" in str(error.value)


def test_norway_resolves_to_norway() -> None:
    """The regression itself, in the shipped gazetteer rather than a fixture."""
    resolved = resolve_place("Oslo, Norway")
    assert resolved.countries == ("NO",)
    assert resolved.regions == ("EMEA",)


def test_no_code_the_gazetteer_produces_is_a_python_bool_word() -> None:
    """Nothing anywhere in the real file may resolve to TRUE or FALSE.

    Checked over the WHOLE gazetteer rather than over Norway, because the next
    bare `ON`, `YES`, `Y` or `N` would be a different line with the same
    consequence.
    """
    gaz = load_gazetteer()
    tables = (gaz.regions, gaz.countries, gaz.cities, gaz.subdivisions, gaz.country_regions)
    produced = {value for table in tables for value in table.values()}
    assert not produced & {"TRUE", "FALSE"}, sorted(produced & {"TRUE", "FALSE"})


def test_a_country_written_as_its_code_is_that_country() -> None:
    """Workable lists a multi-country posting by code, and `BR` was nothing.

    `AR / Mexico City, CDMX, MX / BR / Lima, PE` was read as three countries
    and none of them Brazil, so a posting that listed Brazil was reported to
    a candidate in Brazil as not including it.
    """
    resolved = resolve_place("AR / Mexico City, CDMX, MX / BR / Lima, Callao Region, PE / CL")
    assert resolved.countries == ("AR", "BR", "CL", "MX", "PE")
    assert resolve_place("SV / MX").countries == ("MX", "SV")
    # A hyphenated country-state-city string is not a list of countries.
    assert resolve_place("US-CA-Menlo Park").countries == ("US",)


def test_a_lone_unambiguous_code_is_that_country() -> None:
    """Workable writes a single-country posting as the bare code.

    5,923 open postings carried the shape on 2026-09-11 -- 759 `PH`, 315 `ZA`,
    267 `GB`, 90 `BR` -- and every one resolved to nothing: a country named in
    a structured field, reported as silence. A lone code has no neighbour to
    disambiguate it, so only a code that is NOT also a state or province of
    the United States, Canada or Brazil is read. The rest stay exactly as
    they were.
    """
    assert resolve_place("BR").countries == ("BR",)
    assert resolve_place("BR").regions == ("LATAM",)
    assert resolve_place("PH").countries == ("PH",)
    assert resolve_place("GB").countries == ("GB",)
    assert resolve_place(" ZA ").countries == ("ZA",)


@pytest.mark.parametrize(
    "code",
    [
        "CA",  # California / Canada
        "CO",  # Colorado / Colombia
        "DE",  # Delaware / Germany -- and NOT in the gazetteer's subdivisions
        "IN",  # Indiana / India
        "AR",  # Arkansas / Argentina
        "PE",  # Prince Edward Island, Pernambuco / Peru
        "RS",  # Rio Grande do Sul / Serbia
        "ES",  # Espirito Santo / Spain
        "NL",  # Newfoundland / Netherlands
        "MT",  # Montana, Mato Grosso / Malta
    ],
)
def test_a_lone_colliding_code_still_resolves_to_nothing(code: str) -> None:
    """The brief's own line: never turn a two-letter state into a country."""
    assert resolve_place(code).countries == ()


def test_a_lone_code_is_read_only_in_upper_case() -> None:
    assert resolve_place("br").countries == ()
    assert resolve_place("gb").countries == ()


def test_an_ambiguous_code_is_a_country_only_beside_an_unambiguous_one() -> None:
    """`CO / MX / AR / NI / PA` resolved to the UNITED STATES.

    Three of its tokens are also state codes, and three distinct subdivisions
    agreeing is the one case a subdivision may establish a country. Beside
    `MX` and `NI` the list is plainly written in country codes. On its own an
    ambiguous code stays a subdivision: `CA` alone is still nothing, and
    `San Francisco, CA` still finds its country through the city.
    """
    assert resolve_place("CO / MX / AR / NI / PA").countries == ("AR", "CO", "MX", "NI", "PA")
    assert resolve_place("CA").countries == ()
    assert resolve_place("CO").countries == ()
    assert resolve_place("San Francisco, CA").countries == ("US",)
    assert resolve_place("Denver, CO / Austin, TX").countries == ("US",)


def test_the_case_is_the_signal() -> None:
    """`in` is a preposition and `IN` is India; folding would lose that."""
    assert resolve_place("in / MX").countries == ("MX",)
    assert resolve_place("IN / MX").countries == ("IN", "MX")


def test_a_city_and_code_per_place_is_the_same_list_written_longer() -> None:
    """Comeet writes one row per location and the adapter joins them as
    `City, CC`. The code after the comma is read exactly as a bare item is:
    `Remote, BR / Buenos Aires, AR / Panama, PA` names Brazil, and a US city
    list (`Denver, CO / Austin, TX`) still resolves through its cities."""
    resolved = resolve_place("Remote, BR / Buenos Aires, AR / Santa Cruz, BO / Panama, PA")
    assert resolved.countries == ("AR", "BO", "BR", "PA")
    assert resolve_place("Madrid, ES / Belgrade, RS / Lisbon, PT / Warszawa, PL").countries == (
        "ES",
        "PL",
        "PT",
        "RS",
    )
    assert resolve_place("Boston, US / Remote, BR").countries == ("BR", "US")
    assert resolve_place("Denver, CO / Austin, TX").countries == ("US",)
    assert resolve_place("San Francisco, CA / New York, NY").countries == ("US",)
