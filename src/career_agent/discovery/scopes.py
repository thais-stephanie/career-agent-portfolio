"""Market scopes for SEARCHING, derived from the person's eligibility answers.

Boards name markets differently, and a search for one name does not return
postings filed under another: a remote role "open in Latin America", one "open
in South America", one "open in the Americas" and one "open worldwide" are four
different listings for a person in Brazil, and each board exposes a different
subset under each word. So these are RELATED SEARCH SCOPES, never synonyms, and
they are kept apart until results are deduplicated.

The eligibility gate reads regions its own way (`config/places.yaml` folds
"south america" into LATAM, which is right for a gate that asks "does this
posting admit me"). Retrieval asks a different question -- "which words find
postings that might" -- and must not inherit that folding.

A scope is a search hint and nothing more. Searching "Worldwide" never makes a
posting worldwide; its own text decides that.
"""

from __future__ import annotations

from dataclasses import dataclass

from career_agent.config.search_config import SearchConfig

#: Countries of South America (ISO 3166-1 alpha-2). A geographic fact, used only
#: to decide whether "South America" is a word worth searching for somebody.
SOUTH_AMERICA = frozenset({"AR", "BO", "BR", "CL", "CO", "EC", "GY", "PE", "PY", "SR", "UY", "VE"})
#: Latin America as boards use it: South and Central America, Mexico, the
#: Spanish- and Portuguese-speaking Caribbean.
LATIN_AMERICA = SOUTH_AMERICA | frozenset(
    {"MX", "GT", "HN", "SV", "NI", "CR", "PA", "CU", "DO", "PR", "BZ"}
)
NORTH_AMERICA = frozenset({"US", "CA", "MX"})
AMERICAS = LATIN_AMERICA | NORTH_AMERICA

#: English country names boards and search boxes accept, for the countries this
#: product's places data knows. Unknown codes fall back to the code itself.
COUNTRY_NAMES: dict[str, str] = {
    "AR": "Argentina",
    "BO": "Bolivia",
    "BR": "Brazil",
    "CL": "Chile",
    "CO": "Colombia",
    "CR": "Costa Rica",
    "EC": "Ecuador",
    "MX": "Mexico",
    "PE": "Peru",
    "UY": "Uruguay",
    "PY": "Paraguay",
    "VE": "Venezuela",
    "US": "United States",
    "CA": "Canada",
    "GB": "United Kingdom",
    "IE": "Ireland",
    "DE": "Germany",
    "FR": "France",
    "ES": "Spain",
    "PT": "Portugal",
    "NL": "Netherlands",
    "BE": "Belgium",
    "IT": "Italy",
    "PL": "Poland",
    "SE": "Sweden",
    "NO": "Norway",
    "DK": "Denmark",
    "FI": "Finland",
    "CH": "Switzerland",
    "AT": "Austria",
    "IN": "India",
    "AU": "Australia",
    "NZ": "New Zealand",
    "ZA": "South Africa",
    "PH": "Philippines",
}


@dataclass(frozen=True)
class MarketScope:
    #: `country:BR`, `region:LATAM`, `region:SOUTH_AMERICA`, `region:AMERICAS`,
    #: `remote`, `worldwide`.
    key: str
    #: The words a search box is given for this scope.
    label: str
    #: The ISO code when the scope is one country.
    country: str | None = None


def market_scopes(config: SearchConfig) -> tuple[MarketScope, ...]:
    """The scopes worth searching for this person, most specific first."""
    eligibility = config.eligibility
    home = (eligibility.candidate_country or "").upper()
    countries = [c.upper() for c in eligibility.eligible_countries if c]
    if home and home not in countries:
        countries.insert(0, home)
    regions = {r.upper() for r in eligibility.eligible_scopes}

    scopes: list[MarketScope] = []
    for code in countries:
        scopes.append(MarketScope(f"country:{code}", COUNTRY_NAMES.get(code, code), code))
    touches_latam = home in LATIN_AMERICA or any(c in LATIN_AMERICA for c in countries)
    if "LATAM" in regions or ("AMERICAS" in regions and touches_latam):
        scopes.append(MarketScope("region:LATAM", "Latin America"))
    in_south = home in SOUTH_AMERICA or any(c in SOUTH_AMERICA for c in countries)
    if in_south and regions & {"LATAM", "AMERICAS", "SOUTH_AMERICA"}:
        scopes.append(MarketScope("region:SOUTH_AMERICA", "South America"))
    if "AMERICAS" in regions:
        scopes.append(MarketScope("region:AMERICAS", "Americas"))
    if "NORTH_AMERICA" in regions:
        scopes.append(MarketScope("region:NORTH_AMERICA", "North America"))
    if "EMEA" in regions:
        scopes.append(MarketScope("region:EMEA", "Europe"))
    if "APAC" in regions:
        scopes.append(MarketScope("region:APAC", "Asia Pacific"))
    remote_ok = "REMOTE" in {m.upper() for m in config.preferences.remote.accepted_work_models}
    if "WORLDWIDE" in regions:
        scopes.append(MarketScope("worldwide", "Worldwide"))
        if remote_ok:
            scopes.append(MarketScope("remote", "Remote"))
    return tuple(dict.fromkeys(scopes))
