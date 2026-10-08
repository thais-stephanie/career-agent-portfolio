"""Which sources are worth refreshing for THIS candidate, and which can wait.

WHY THIS FILE EXISTS
--------------------
Gupy holds 79,589 of the corpus's 103,272 postings and takes hours to refresh.
For somebody based in Utah looking for work in the United States and Canada,
every one of those hours buys nothing: Gupy is a Brazilian board and a Brazilian
board is where Brazilian jobs are.

The old shape of this product had one answer for everybody -- refresh everything,
then show jobs -- so the slowest source in the wrong market decided when a
candidate saw the right one.

THE INVARIANT, AND IT IS THE WHOLE DESIGN
------------------------------------------
**Scheduling decides WHEN a source is refreshed. It never decides WHAT that
source ingests once it runs.**

ADR-0019 is the reason that sentence has to be written down. V1.6 filtered Gupy
at collection time by one person's preferences -- remote only, no on-site -- and
left 72,682 postings uncollected for everybody. A posting that was never
collected produces no fingerprint for anybody, and the damage is invisible
because nothing reports what was not fetched.

So this module produces a PRIORITY, and nothing else: an ORDER, never a pause.
It is deliberately incapable of expressing anything about the CONTENT of a
feed: there is no parameter here for a role, a seniority, a contract type or a
work model, and adding one would be the V1.6 breach again.

WHAT DECIDES (ADR-0032)
-----------------------
The markets the candidate SAID she wants (`regions.target`). Never where she
lives: residence answers a posting's requirements, it does not choose which
boards are worth reading. It used to: a remote candidate in Brazil had the one
European board paused as "a market you have not asked for", although remote
work on it may hire from anywhere.

* No markets named: every board is refreshed, worldwide ones first.
* Markets named: their boards first, then worldwide ones, then the rest.
  Nothing is left out.
* Only an explicit choice leaves a board out: pausing it in Settings & Sources,
  which the caller applies and this module never overrides.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

__all__ = ["Priority", "SourcePlan", "plan_refresh", "regions_for"]


#: Which catalogue regions a named MARKET's jobs live in: a region id from
#: `regions.target`, or a country code. WORLDWIDE prefers no regional board.
_MARKET_REGIONS: Mapping[str, tuple[str, ...]] = {
    "LATAM": ("br", "latam"),
    "SOUTH_AMERICA": ("br", "latam"),
    "AMERICAS": ("br", "latam"),
    "EMEA": ("eu",),
    "EU": ("eu",),
    "EUROPE": ("eu",),
    "NORTH_AMERICA": (),
    "APAC": (),
    "WORLDWIDE": (),
}

#: Which catalogue regions a country's jobs live in.
#:
#: Small, explicit and INCOMPLETE ON PURPOSE. A country nobody has mapped falls
#: through to `global` alone: no regional board goes first, and none is left
#: out.
#:
#: `global` is added to every answer rather than listed in each row: a worldwide
#: board serves every market by definition, and a candidate who wanted none of
#: them would be a candidate who wanted no remote work at all.
_COUNTRY_REGIONS: Mapping[str, tuple[str, ...]] = {
    # South America. `latam` is a real regional market here -- the staffing
    # agencies that place people at United States companies recruit across it.
    "BR": ("br", "latam"),
    "AR": ("latam",),
    "CL": ("latam",),
    "CO": ("latam",),
    "MX": ("latam",),
    "PE": ("latam",),
    "UY": ("latam",),
    # Europe. One region in the catalogue today; a board for a single country
    # would be a new region rather than a new row here.
    "DE": ("eu",),
    "FR": ("eu",),
    "ES": ("eu",),
    "PT": ("eu",),
    "IT": ("eu",),
    "NL": ("eu",),
    "IE": ("eu",),
    "PL": ("eu",),
    "SE": ("eu",),
    "DK": ("eu",),
    "NO": ("eu",),
    "FI": ("eu",),
    "AT": ("eu",),
    "BE": ("eu",),
    "CH": ("eu",),
    "CZ": ("eu",),
    "RO": ("eu",),
    "GB": ("eu",),
    # North America has no regional board in the catalogue, so it resolves to
    # `global` alone. That is a statement about our coverage rather than about
    # the market, and it is why `docs/product/source-matrix.md` is where a
    # reader goes to see the gap.
    "US": (),
    "CA": (),
}

#: Always in scope. A worldwide board serves every market.
GLOBAL = "global"


class Priority(int):
    """How soon this source should run. Lower is sooner.

    An int rather than an enum because the only operation anybody performs on it
    is sorting, and three named constants that are secretly 0, 1 and 2 make a
    fourth priority a schema change instead of a number.
    """


#: In the candidate's own target market. Run these first.
TARGETED = Priority(0)
#: Worldwide, so useful to everybody and never paused.
WORLDWIDE = Priority(1)
#: Another market's board. Still available, not refreshed by default.
OTHER_MARKET = Priority(2)


@dataclass(frozen=True)
class SourcePlan:
    """When to refresh one source for one candidate, and why.

    `reason` is not decoration: a screen that orders sources without saying why
    is a screen somebody has to guess at.
    """

    source_id: str
    priority: Priority
    reason: str


def regions_for(markets: Iterable[str]) -> frozenset[str]:
    """The catalogue regions of the markets a candidate named. `global` is
    always in it; a market nobody mapped adds nothing regional."""
    out = {GLOBAL}
    for market in markets:
        key = str(market).strip().upper()
        out.update(_MARKET_REGIONS.get(key, _COUNTRY_REGIONS.get(key, ())))
    return frozenset(out)


def plan_refresh(
    sources: Mapping[str, str],
    *,
    markets: Iterable[str],
    enabled: Iterable[str] = (),
) -> list[SourcePlan]:
    """One plan per source, most urgent first. Every source is in it.

    `sources` maps a source id to its catalogue region, so this module never
    reads the catalogue itself and never names a vendor -- the rule
    `tests/unit/test_provider_neutrality.py` enforces over the syntax tree.

    `markets` are the ones the candidate named (`regions.target`), never where
    she lives. `enabled` is the candidate turning a source on by hand; it
    always goes first.
    """
    wanted = regions_for(markets) - {GLOBAL}
    forced = {str(s) for s in enabled}
    plans: list[SourcePlan] = []
    for source_id, region in sources.items():
        region = (region or GLOBAL).strip().lower()
        if source_id in forced:
            plan = SourcePlan(source_id, TARGETED, "you turned this one on yourself")
        elif region in wanted:
            plan = SourcePlan(source_id, TARGETED, "this board covers a market you chose")
        elif region == GLOBAL:
            plan = SourcePlan(source_id, WORLDWIDE, "this board carries work from anywhere")
        else:
            plan = SourcePlan(
                source_id,
                OTHER_MARKET,
                "this board covers another market; it is refreshed after the ones you "
                "chose, and remote work on it may hire from anywhere",
            )
        plans.append(plan)
    plans.sort(key=lambda p: (p.priority, p.source_id))
    return plans


#: Which geographic SLICES of a query-driven source a country's candidate
#: should see walked first. Slice names are the vendor's own location slugs
#: and are passed through untouched; the set of slices is never changed here,
#: only its order (ADR-0019: scheduling decides WHEN, never WHAT).
#:
#: A candidate in Brazil: worldwide, then the hemisphere, then the country,
#: then the rest in the order the source lists them. A candidate nobody has
#: mapped gets the source's own order, which begins with worldwide.
_COUNTRY_SLICE_ORDER: Mapping[str, tuple[str, ...]] = {
    "BR": ("anywhere", "south-america", "latam", "brazil", "north-america"),
    "AR": ("anywhere", "south-america", "latam", "argentina", "north-america"),
    "CL": ("anywhere", "south-america", "latam", "chile", "north-america"),
    "CO": ("anywhere", "south-america", "latam", "colombia", "north-america"),
    "PE": ("anywhere", "south-america", "latam", "peru", "north-america"),
    "UY": ("anywhere", "south-america", "latam", "uruguay", "north-america"),
    "MX": ("anywhere", "latam", "central-america", "mexico", "north-america"),
    "US": ("anywhere", "north-america", "united-states"),
    "CA": ("anywhere", "north-america", "canada"),
    "GB": ("anywhere", "europe", "united-kingdom"),
    "PT": ("anywhere", "europe", "portugal"),
    "ES": ("anywhere", "europe", "spain"),
    "DE": ("anywhere", "europe", "germany"),
    "IN": ("anywhere", "asia", "india"),
    "AU": ("anywhere", "oceania", "australia"),
}


#: The slices of a named REGION market, in the vendor's slug vocabulary.
_MARKET_SLICE_ORDER: Mapping[str, tuple[str, ...]] = {
    "WORLDWIDE": ("anywhere",),
    "LATAM": ("latam", "south-america", "central-america"),
    "SOUTH_AMERICA": ("south-america", "latam"),
    "AMERICAS": ("north-america", "latam", "south-america", "central-america"),
    "NORTH_AMERICA": ("north-america",),
    "EMEA": ("europe",),
    "EU": ("europe",),
    "EUROPE": ("europe",),
    "APAC": ("asia", "oceania"),
}


def slice_order(available: Iterable[str], *, markets: Iterable[str]) -> tuple[str, ...]:
    """`available`, reordered so the markets the candidate named come first
    (a region id or a country code). No markets: the source's own order.

    Every name in `available` is returned exactly once; nothing is added and
    nothing is dropped. A preferred name the source does not offer is
    ignored rather than invented.
    """
    offered = list(dict.fromkeys(str(a) for a in available))
    preferred: list[str] = []
    for market in markets:
        key = str(market).strip().upper()
        for name in _MARKET_SLICE_ORDER.get(key, _COUNTRY_SLICE_ORDER.get(key, ())):
            if name in offered and name not in preferred:
                preferred.append(name)
    return tuple(preferred + [name for name in offered if name not in preferred])
