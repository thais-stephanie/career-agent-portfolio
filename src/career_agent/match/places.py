"""Turning what the board printed into countries and regions.

`job.location_raw` is free text a board chose: ``San Francisco``,
``US-CA-Menlo Park``, ``Home based - Worldwide``, ``São Paulo, Brasil``. The
interface has to be able to offer "postings in Brazil" without every query
LIKE-ing over that text, so this module resolves it once, deterministically,
and the result is stored on `job_match` beside the score it was computed with.

**This is not an eligibility answer, and nothing here may become one.**
CLAUDE.md invariant 3 keeps three questions apart, and this module answers only
the third:

    hiring_scope                where the employer will HIRE
    worksite_requirement        where the work is DONE
    location_raw (here)         what the board PRINTED

A posting that says ``San Francisco`` tells you where an office is. It does not
say the company will hire in Brazil. The geography gate keeps that question,
and a country resolved here never touches `eligibility_status`.

**Absence resolves to absence.** A string nobody recognises produces no country
and no region, and a country filter therefore EXCLUDES it. That is the
conservative direction -- a posting is left out of an answer rather than
asserted into it -- and the interface reports the unresolved count so the
exclusion is visible rather than silent.

The gazetteer is data, in `config/places.yaml`. Which cities exist is a fact
about the world; only the resolution RULE lives here.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from career_agent.yaml_io import safe_load

#: Anchored on the package rather than on the process working directory, for
#: the reason `sources.catalogue` records at length: a CWD-relative default
#: turns "run from the wrong folder" into a silent wrong answer -- here, every
#: posting resolving to no country at all.
DEFAULT_GAZETTEER = Path(__file__).resolve().parents[3] / "config" / "places.yaml"

#: What `location_raw` uses to separate one place from another. Boards are not
#: consistent: commas, pipes, slashes, bullets, semicolons and a spaced hyphen
#: all appear in the corpus, and `US-CA-Menlo Park` uses bare hyphens as well.
# punctuation-check: allow: a character class, not prose
_SEPARATORS = re.compile(r"[,;|/()·•–—]|\s-\s|\s+or\s+|-")

#: Region ids the interface offers. Frozen here so the API vocabulary, the
#: facet buckets and the gazetteer cannot drift from one another.
REGIONS: tuple[str, ...] = (
    "WORLDWIDE",
    "NORTH_AMERICA",
    "LATAM",
    "EMEA",
    "APAC",
    "AMERICAS",
)


def _fold(text: str) -> str:
    """Lowercase, accent-stripped, whitespace-collapsed.

    `Sao Paulo` and `São Paulo` are one place, and a board that prints one of
    them today may print the other tomorrow.
    """
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.casefold().split())


class PlaceGazetteerError(ValueError):
    """The gazetteer file is not shaped the way this module reads it."""


@dataclass(frozen=True, slots=True)
class Gazetteer:
    """The lookup tables, folded once at load."""

    regions: dict[str, str]
    countries: dict[str, str]
    cities: dict[str, str]
    subdivisions: dict[str, str]
    country_regions: dict[str, str]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> Gazetteer:
        def folded(key: str) -> dict[str, str]:
            table: dict[str, str] = {}
            for raw_key, value in (data.get(key) or {}).items():
                # YAML 1.1 reads bare `NO`, `ON`, `YES`, `Y` and `N` as
                # booleans, which silently turns Norway and Ontario into
                # `False` and `True`. Caught here with the offending line
                # named, rather than surfacing 200 lines away as a TypeError
                # inside `unicodedata.normalize`.
                if not isinstance(raw_key, str):
                    raise PlaceGazetteerError(
                        f"places.yaml: {key!r} has a non-string key {raw_key!r}. "
                        "YAML reads bare NO/ON/YES/Y/N as booleans -- quote it."
                    )
                # And the same check on the VALUE, which this guard used to
                # skip. `country_regions` quoted its `"NO"` key and carried a
                # comment saying why; forty lines later `norway: NO` was
                # written unquoted on the RIGHT of the colon, so Norway
                # resolved to the country code `FALSE` -- a bucket in the
                # filter rail labelled "False", no EMEA region, and no error
                # anywhere. A guard that checks one side of a mapping is a
                # guard for half the mistake.
                if not isinstance(value, str):
                    raise PlaceGazetteerError(
                        f"places.yaml: {key!r}[{raw_key!r}] has a non-string value "
                        f"{value!r}. YAML reads bare NO/ON/YES/Y/N as booleans -- quote it."
                    )
                table[_fold(raw_key)] = value.strip().upper()
            return table

        return cls(
            regions=folded("regions"),
            countries=folded("countries"),
            cities=folded("cities"),
            subdivisions=folded("subdivisions"),
            country_regions=folded("country_regions"),
        )


@lru_cache(maxsize=4)
def display_names(path: Path | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """`(country code -> name, region code -> name)`, for the interface only.

    Kept out of :class:`Gazetteer` deliberately. Everything in that class is
    FOLDED and UPPERCASED because it exists to be matched against; a display
    name must survive with its capitals and its spaces intact, and putting it
    through the same door would render "United Kingdom" as "UNITED KINGDOM".

    Nothing here participates in resolution. A code with no name falls back to
    the code, which is the behaviour this replaced rather than a new failure.
    """
    source = path or DEFAULT_GAZETTEER
    data = safe_load(source.read_text(encoding="utf-8")) or {}

    def named(key: str) -> dict[str, str]:
        return {
            str(code).strip().upper(): str(name).strip()
            for code, name in (data.get(key) or {}).items()
            if str(name).strip()
        }

    return named("country_names"), named("region_names")


@lru_cache(maxsize=4)
def load_gazetteer(path: Path | None = None) -> Gazetteer:
    """Read and fold the gazetteer. Cached: `rescore` calls this 18,000 times."""
    source = path or DEFAULT_GAZETTEER
    data = safe_load(source.read_text(encoding="utf-8")) or {}
    return Gazetteer.from_mapping(data)


@dataclass(frozen=True, slots=True)
class ResolvedPlace:
    """What one `location_raw` resolved to. Empty is a normal answer.

    `regions` is the UNION of what the text named and what its countries
    imply, which is what a filter wants: ``Buenos Aires`` belongs under the
    Latin America facet whether or not the board wrote the word.

    `stated_regions` is only what the text NAMED. The eligibility gate reads
    this one and never `regions`, and the difference is the whole defect the
    field was added for: ``Argentina, Colombia, Mexico`` implies LATAM as a
    fact about geography, and a posting that lists those three countries has
    NOT said it hires in Brazil. Letting the implied region open the gate is
    how an explicit allowlist that omits the candidate's country read as
    eligible.
    """

    countries: tuple[str, ...] = ()
    regions: tuple[str, ...] = ()
    stated_regions: tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return bool(self.countries or self.regions)


def region_contains(region: str, country: str, gazetteer: Gazetteer | None = None) -> bool | None:
    """Whether a REGION geographically contains a COUNTRY, or None if unknown.

    WORLDWIDE contains everything. AMERICAS contains both halves of the
    hemisphere. Any other region contains a country when `country_regions`
    files that country under it. A country the gazetteer does not place
    answers None, and None is not False: the caller decides what an unknown
    membership means, and for eligibility it means "do not open the gate".

    This is what makes a configured scope SAFE rather than merely listed. A
    candidate in Brazil whose settings say NORTH_AMERICA is accepted has
    recorded something that cannot be true of where they live, and the gate
    must not turn `Remote (United States | Canada)` into a job they can take
    because of it.
    """
    gaz = gazetteer or load_gazetteer()
    region = region.strip().upper()
    if region == "WORLDWIDE":
        return True
    home = gaz.country_regions.get(_fold(country))
    if home is None:
        return None
    if region == "AMERICAS":
        return home in ("LATAM", "NORTH_AMERICA")
    return home == region


#: Words that answer the WORKSITE question rather than the geography one.
#:
#: Stripped from either end of a token before it is looked up, so that
#: ``Remote U.S.`` resolves through ``u.s.`` and ``Americas Remote`` through
#: ``americas``. Never stripped from the middle, and never on their own: a
#: token that is ONLY one of these is not a place and resolves to nothing,
#: which is what keeps ``Remote`` from implying ``Worldwide``.
WORKSITE_WORDS: frozenset[str] = frozenset(
    {
        "remote",
        "remoto",
        "hybrid",
        "hibrido",
        "onsite",
        "on site",
        "in office",
        "presencial",
        # Not worksite MODES but the same shape of problem: a word a board
        # appends to a place name that is not part of the place. Measured over
        # the 600 highest-scoring postings, `Berlin Office`, `SF Office` and
        # `San Francisco HQ` accounted for eleven unresolved locations.
        "office",
        "hq",
        "headquarters",
        "escritorio",
    }
)

#: Fillers a board puts between a worksite word and the place.
#:
#: `Remote in the United States` is one token here, and stripping `remote`
#: leaves `in the united states`, which is not a gazetteer key. These are
#: removed from the FRONT only, after a worksite word has been taken off, so
#: they can never eat into a name.
_LEADING_FILLER: tuple[str, ...] = ("in the ", "in ", "across ", "within ", "na ", "no ")


_ALPHA2 = re.compile(r"[A-Z]{2}")

#: ISO alpha-2 country codes that are ALSO a state or province code in one of
#: the three countries whose subdivisions this corpus writes as two letters:
#: the United States (plus DC), Canada and Brazil. A lone one of these is
#: never read as a country: `DE` is Delaware or Germany, `IN` Indiana or
#: India, `AR` Arkansas or Argentina, `PE` Prince Edward Island, Pernambuco
#: or Peru, `RS` Rio Grande do Sul or Serbia. The list is CLOSED and typed
#: out rather than derived from `places.yaml`, whose `subdivisions` table
#: holds only the 49 spellings the corpus has needed so far -- `DE` is not
#: among them, and "not in the table" is not "not a state".
#:
#: Codes that are a subdivision somewhere but NOT a country (`TX`, `NY`,
#: `SP`, `RJ`, `ON`, `QC`) need no entry: the gazetteer does not know them as
#: countries, so they never reach the check.
_COLLIDING_CODES: frozenset[str] = frozenset(
    {
        # United States
        "AL",
        "AR",
        "AZ",
        "CA",
        "CO",
        "DE",
        "GA",
        "ID",
        "IL",
        "IN",
        "KY",
        "LA",
        "MA",
        "MD",
        "ME",
        "MN",
        "MO",
        "MS",
        "MT",
        "NE",
        "PA",
        "SC",
        "SD",
        "TN",
        "VA",
        # Canada
        "NL",
        "PE",
        "SK",
        # Brazil
        "AM",
        "BA",
        "CE",
        "ES",
        "MG",
        "PR",
        "RO",
        "RS",
        "SE",
        "TO",
    }
)


def _lone_country_code(location_raw: str, gaz: Gazetteer) -> dict[str, str]:
    """A location that is exactly one ISO alpha-2 country code, or nothing.

    The second half of the reading `_country_codes` gives a slash list, and
    the more careful half. Workable's XML feed writes a single-country posting
    as the bare code -- `PH`, `ZA`, `GB`, `BR` -- and 5,923 open postings
    carried that shape on 2026-09-11, every one of them resolving to nothing:
    the employer named a country in a structured field and the product
    reported silence. 759 said `PH`; 90 said `BR`.

    A lone code has no neighbour to disambiguate it, so the rule is stricter
    than the list's: the code must be one the gazetteer knows as a country
    AND absent from `_COLLIDING_CODES`. `BR`, `PH`, `GB`, `ZA`, `MX` and
    `PL` are countries; `DE`, `IN`, `AR`, `CA` and `CO` stay nothing, as they
    always have. Upper case is still the signal: `in` is a preposition.
    """
    code = location_raw.strip()
    if not _ALPHA2.fullmatch(code) or code in _COLLIDING_CODES:
        return {}
    known = {value for value in gaz.countries.values() if len(value) == 2}
    return {code: code} if code in known else {}


def _country_codes(location_raw: str, gaz: Gazetteer) -> dict[str, str]:
    """Which items of a slash-separated list are a country written as its code.

    Only the LIST shape is read: `AR / Mexico City, CDMX, MX / BR / Lima, PE`
    is how one board writes a posting open in several countries, and an item
    of that list that is exactly two upper-case letters the gazetteer knows as
    a country is that country -- or that ENDS in one after a comma, which is
    how a board that writes `City, CC` per place (`Madrid, ES / Belgrade, RS`)
    lists the same fact. The case is the signal -- `_fold` would turn
    `IN` into the preposition -- and the slash is the other: `US-CA-Menlo
    Park` is a country, a state and a city, and a lone `DE` is still Delaware
    or Germany and still resolves to nothing.

    Two kinds of code, and the second is the careful one:

    * An UNAMBIGUOUS code, one that is not also a subdivision code (`BR`,
      `MX`, `NI`, `PE`, `ZA`), is that country.
    * An AMBIGUOUS code (`CA` is California and Canada; `CO`, `IL`, `MA`,
      `PA` collide the same way) is read as a country only when the same
      list carries an unambiguous code beside it -- the list is then written
      in the country-code vocabulary, and `CO / MX / AR` is Colombia. Without
      one it stays what it was: a subdivision, which confirms and never
      establishes.
    """
    if "/" not in location_raw:
        return _lone_country_code(location_raw, gaz)
    known = {code for code in gaz.countries.values() if len(code) == 2}
    found: dict[str, str] = {}
    for item in (item.strip() for item in location_raw.split("/")):
        # The item is the code (`BR`), or ends in one after a comma
        # (`Buenos Aires, AR`, `Lima, Callao Region, PE`): a board that writes
        # `City, CC` per place writes the whole list in that vocabulary.
        tail = item.rsplit(",", 1)[-1].strip()
        code = item if _ALPHA2.fullmatch(item) else tail if _ALPHA2.fullmatch(tail) else None
        if code is not None and code in known:
            found[code] = code
    if not any(_fold(code) not in gaz.subdivisions for code in found):
        return {}
    return found


def _place_part(token: str, gaz: Gazetteer) -> str | None:
    """One token, with a worksite word removed from either end.

    Returns None when nothing is left, which is the ``Remote`` case and is
    deliberately not a place.

    The token is returned UNCHANGED when it already matches the gazetteer, so a
    real place whose name happens to start with one of these words -- there are
    none today, and that is not a guarantee about the future -- is never cut
    down before it has been tried.
    """
    if token in gaz.regions or token in gaz.countries or token in gaz.cities:
        return token
    for word in WORKSITE_WORDS:
        if token == word:
            return None
        if token.startswith(f"{word} "):
            return _strip_filler(token[len(word) + 1 :].strip()) or None
        if token.endswith(f" {word}"):
            return token[: -len(word) - 1].strip() or None
    return token


def _strip_filler(token: str) -> str:
    """One leading preposition, so `in the united states` finds its country."""
    for filler in _LEADING_FILLER:
        if token.startswith(filler):
            return token[len(filler) :].strip()
    return token


def resolve_place(location_raw: str | None, gazetteer: Gazetteer | None = None) -> ResolvedPlace:
    """Countries and regions for one posting's stated location.

    Four passes over the tokens, and the order is the whole rule:

    1. **Region words** the board printed itself -- ``Worldwide``, ``EMEA``,
       ``LATAM``. Taken as stated.
    2. **Country names**, including the spellings boards actually use.
    3. **Cities**, which is what most of the corpus contains: 1,238 postings say
       ``San Francisco`` and nothing else, so a resolver that only read country
       names would resolve almost nothing.
    4. **Subdivisions**, which CONFIRM and never establish. ``CA`` is California
       and also the ISO code for Canada; ``IL``, ``CO``, ``DE``, ``GA``, ``IN``,
       ``LA``, ``PA`` and ``TX`` collide the same way. A subdivision is counted
       only when another token already put its country in the set, so
       ``San Francisco, CA`` resolves to US through the city and a bare ``CA``
       resolves to nothing at all.

    Regions are then implied from the countries found, so ``São Paulo`` carries
    ``LATAM`` without the board having said so -- an implication about geography,
    which is safe, and not about hiring, which would not be.

    ``Remote`` and ``Hybrid`` are deliberately NOT places. They answer the
    worksite question, `_work_model` reads them, and letting "remote" imply
    "worldwide" is the exact conflation invariant 3 exists to prevent.

    **A worksite word GLUED to a place is stripped, and that was a real hole.**
    `_SEPARATORS` does not split on plain whitespace, because most of the
    corpus says ``San Francisco`` and a resolver that split on spaces would
    look up ``san`` and ``francisco``. The cost was that ``Remote U.S.`` --
    Ashby's commonest remote-location shape -- arrived as the single token
    ``remote u.s.`` and matched nothing at all, so the most consequential
    structured location in the corpus resolved to no country.

    So each token is tried whole first, and then again with a leading or
    trailing worksite word removed. ``Remote U.S.`` finds ``u.s.``;
    ``San Francisco`` is untouched because neither of its words is one.
    Stripping is not inference: it removes a word that is already known to
    answer a different question.
    """
    gaz = gazetteer or load_gazetteer()
    if not location_raw:
        return ResolvedPlace()

    parts = [part.strip() for part in _SEPARATORS.split(location_raw)]
    parts = [part for part in parts if part]
    tokens = [_fold(part) for part in parts]
    if not tokens:
        return ResolvedPlace()

    countries: list[str] = []
    regions: list[str] = []
    stated: list[str] = []
    weak: list[tuple[str, str]] = []

    # Pass zero: ISO alpha-2 codes standing alone in the list. Workable writes
    # a multi-country posting as `AR / Mexico City, CDMX, MX / BR / Lima, PE`,
    # and before this pass `BR` resolved to nothing while `CO / MX / AR / NI /
    # PA` resolved to the UNITED STATES -- three of its tokens are also state
    # codes, and three distinct subdivisions agreeing is the one case a
    # subdivision may establish a country. A posting listing Brazil by code
    # was reported as not including Brazil. See `_country_codes`.
    codes = _country_codes(location_raw, gaz)

    for raw_token, part in zip(tokens, parts, strict=True):
        if part in codes:
            countries.append(codes[part])
            continue
        token = _place_part(raw_token, gaz)
        if token is None:
            continue
        if token in gaz.regions:
            regions.append(gaz.regions[token])
            stated.append(gaz.regions[token])
            continue
        if token in gaz.countries:
            countries.append(gaz.countries[token])
            continue
        if token in gaz.cities:
            countries.append(gaz.cities[token])
            continue
        if token in gaz.subdivisions:
            # The TOKEN as well as the code: two independent signals means two
            # different words agreeing, and `CA, CA` is one word twice.
            weak.append((token, gaz.subdivisions[token]))

    # A subdivision confirms; on its own it never establishes. The one
    # exception is TWO DISTINCT subdivision tokens agreeing -- `Washington, DC`
    # is two independent US signals, and the collision that makes a single code
    # untrustworthy (`CA` is California and Canada) does not survive being
    # agreed with. A repeated single token is not two signals, so the weak
    # matches are de-duplicated before they are counted.
    if countries:
        countries.extend(code for _, code in weak if code in countries)
    else:
        distinct = {token: code for token, code in weak}
        agreed = set(distinct.values())
        if len(distinct) >= 2 and len(agreed) == 1:
            countries.append(agreed.pop())

    # Geography implies its region. Hiring scope implies nothing.
    #
    # The lookup is FOLDED, because every table in the gazetteer is keyed by
    # `_fold`ed strings while the codes here are upper case. Without it this
    # loop matched nothing at all and `San Francisco, CA` resolved to US with
    # no region -- silently, since an empty region tuple is also what an
    # unrecognised place produces.
    for code in countries:
        implied = gaz.country_regions.get(_fold(code))
        if implied:
            regions.append(implied)

    return ResolvedPlace(
        countries=tuple(sorted(set(countries))),
        regions=tuple(sorted(set(regions))),
        stated_regions=tuple(sorted(set(stated))),
    )
