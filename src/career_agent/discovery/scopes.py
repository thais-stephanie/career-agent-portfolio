"""Market scopes for SEARCHING, derived from the markets the person NAMED
(`regions.target`), never from where she lives (ADR-0032). Residence answers a
posting's requirements; desired markets guide discovery. No market named is a
GLOBAL search: one worldwide scope, not one per country she might mean.

Boards name markets differently, and a search for one name does not return
postings filed under another: a remote role "open in Latin America", one "open
in South America", one "open in the Americas" and one "open worldwide" are four
different listings, and each board exposes a different subset under each word.
So these are RELATED SEARCH SCOPES, never synonyms, kept apart until results
are deduplicated.

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
    #: `remote`, `remote_worldwide` (or `worldwide` for somebody not remote).
    key: str
    #: The words a search box is given for this scope.
    label: str
    #: The ISO code when the scope is one country.
    country: str | None = None
    #: Remote roles only. "Remote" (remote roles in the home market) and
    #: "Remote Worldwide" are different questions from "Brazil" and from each
    #: other, whatever a board files under them.
    remote: bool = False
    #: Where a remote search is anchored, for sources that need a place.
    home: str | None = None

    @property
    def location_text(self) -> str:
        """The place a text-location source (LinkedIn, Indeed...) is asked."""
        if self.country:
            return COUNTRY_NAMES.get(self.country, self.country)
        if self.key == "remote" and self.home:
            return COUNTRY_NAMES.get(self.home, self.home)
        if self.key in {"remote_worldwide", "worldwide", "remote"}:
            return "Worldwide"
        return self.label


#: Region markets as search scopes: (key, label). EU and EUROPE are EMEA here.
_REGION_SCOPES: dict[str, tuple[str, str]] = {
    "LATAM": ("region:LATAM", "Latin America"),
    "SOUTH_AMERICA": ("region:SOUTH_AMERICA", "South America"),
    "AMERICAS": ("region:AMERICAS", "Americas"),
    "NORTH_AMERICA": ("region:NORTH_AMERICA", "North America"),
    "EMEA": ("region:EMEA", "Europe"),
    "EU": ("region:EMEA", "Europe"),
    "EUROPE": ("region:EMEA", "Europe"),
    "APAC": ("region:APAC", "Asia Pacific"),
}


def market_scopes(config: SearchConfig) -> tuple[MarketScope, ...]:
    """One scope per market she named, in her order; none named is one
    worldwide scope. The query budget (`plan_queries`) bounds the rest."""
    markets = [str(m).strip().upper() for m in config.regions.target if str(m).strip()]
    remote_ok = "REMOTE" in {m.upper() for m in config.preferences.remote.accepted_work_models}
    worldwide = (
        MarketScope("remote_worldwide", "Remote Worldwide", remote=True)
        if remote_ok
        else MarketScope("worldwide", "Worldwide")
    )
    scopes: list[MarketScope] = []
    for market in markets or ["WORLDWIDE"]:
        if market == "WORLDWIDE":
            scopes.append(worldwide)
        elif market in _REGION_SCOPES:
            key, label = _REGION_SCOPES[market]
            scopes.append(MarketScope(key, label))
        elif len(market) == 2 and market.isalpha():
            name = COUNTRY_NAMES.get(market, market)
            scopes.append(MarketScope(f"country:{market}", name, market))
    return tuple(dict.fromkeys(scopes))
