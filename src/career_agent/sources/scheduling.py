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

So this module produces a PRIORITY and a PAUSE, both reversible from the screen,
both about scheduling. It is deliberately incapable of expressing anything about
the CONTENT of a feed: there is no parameter here for a role, a seniority, a
contract type or a work model, and adding one would be the V1.6 breach again.

WHAT DECIDES
------------
Where the candidate may work, which they answered on the first-run screen and
which already lives in `preferences.eligible_countries`. Not their nationality,
not their language, and not where they are sitting: somebody in Sao Paulo
targeting Berlin needs German sources, and somebody with a Brazilian passport in
Toronto targeting Canada does not need Gupy.

A source with no market of its own is `global` and is never paused, because
"remote, worldwide" is the one market everybody shares.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

__all__ = ["Priority", "SourcePlan", "plan_refresh", "regions_for"]


#: Which catalogue regions a country's jobs live in.
#:
#: Small, explicit and INCOMPLETE ON PURPOSE. A country nobody has mapped falls
#: through to `global` alone, which pauses the regional boards and keeps every
#: worldwide one -- the conservative direction, because a paused source is a
#: button away and a source nobody thought to run is invisible.
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
    """What to do about one source for one candidate, and why.

    `reason` is not decoration. A screen that says "paused" without saying why is
    a screen somebody has to guess at, and the guess available -- "it is broken"
    -- is the wrong one.
    """

    source_id: str
    priority: Priority
    paused: bool
    reason: str


def regions_for(countries: Iterable[str]) -> frozenset[str]:
    """The catalogue regions worth refreshing for somebody who may work in `countries`.

    An empty answer is impossible: `global` is always in it. A candidate who has
    not said where they may work therefore gets the worldwide boards and nothing
    regional, which is the honest default -- we do not know their market, so we
    refresh the ones that serve every market.
    """
    out = {GLOBAL}
    for code in countries:
        out.update(_COUNTRY_REGIONS.get(str(code).strip().upper(), ()))
    return frozenset(out)


def plan_refresh(
    sources: Mapping[str, str],
    *,
    countries: Iterable[str],
    enabled: Iterable[str] = (),
) -> list[SourcePlan]:
    """One plan per source, most urgent first.

    `sources` maps a source id to its catalogue region, so this module never
    reads the catalogue itself and never names a vendor -- the rule
    `tests/unit/test_provider_neutrality.py` enforces over the syntax tree.

    `enabled` is the candidate overriding this by hand. It always wins, and it
    has to: a Brazilian in Toronto may want Brazilian boards for reasons no
    country code can express, and a scheduler that could not be overruled would
    be a scheduler making the decision instead of informing it.
    """
    wanted = regions_for(countries)
    forced = {str(s) for s in enabled}
    plans: list[SourcePlan] = []
    for source_id, region in sources.items():
        region = (region or GLOBAL).strip().lower()
        if source_id in forced:
            plans.append(
                SourcePlan(
                    source_id=source_id,
                    priority=TARGETED,
                    paused=False,
                    reason="you turned this one on yourself",
                )
            )
        elif region == GLOBAL:
            plans.append(
                SourcePlan(
                    source_id=source_id,
                    priority=WORLDWIDE,
                    paused=False,
                    reason="this board carries work from anywhere",
                )
            )
        elif region in wanted:
            plans.append(
                SourcePlan(
                    source_id=source_id,
                    priority=TARGETED,
                    paused=False,
                    reason="this board covers a place you said you can work",
                )
            )
        else:
            plans.append(
                SourcePlan(
                    source_id=source_id,
                    priority=OTHER_MARKET,
                    paused=True,
                    reason=(
                        "this board covers a market you have not asked for. "
                        "Nothing is hidden: everything already collected from it "
                        "is still in your list, and you can refresh it whenever "
                        "you like."
                    ),
                )
            )
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


def slice_order(available: Iterable[str], *, countries: Iterable[str]) -> tuple[str, ...]:
    """`available`, reordered so the candidate's markets come first.

    Every name in `available` is returned exactly once; nothing is added and
    nothing is dropped. A preferred name the source does not offer is
    ignored rather than invented.
    """
    offered = list(dict.fromkeys(str(a) for a in available))
    preferred: list[str] = []
    for code in countries:
        for name in _COUNTRY_SLICE_ORDER.get(str(code).strip().upper(), ()):
            if name in offered and name not in preferred:
                preferred.append(name)
    return tuple(preferred + [name for name in offered if name not in preferred])
