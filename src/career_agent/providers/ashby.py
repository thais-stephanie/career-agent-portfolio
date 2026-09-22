"""Ashby job board adapter.

Ashby publishes every company's board as public JSON with no key and no account:

    GET https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true

One request returns the whole board. Measured on a 751-posting board: `limit`,
`offset`, `page` and `cursor` are all silently ignored and the complete listing
comes back regardless, and the envelope carries no cursor, no `hasMore` and no
total. There is nothing to page with, and nothing to tear.

The compensation flag is not optional. Without it the `compensation` key is
absent from every posting -- measured, 0/14 with the flag omitted and 14/14 with
it -- so declaring `exposes_compensation` while not sending it would be false
advertising.

Like the other two adapters, this module translates shape and never meaning.
Ashby says `workplaceType: "Hybrid"` and `addressCountry: "USA"`; those are
things an employer typed, recorded as typed. Whether the candidate may work
there is decided much later, against the description text, by M3.

Two decisions here are less obvious than they look, and both come from counting
3105 real postings rather than from the documentation. See
`docs/architecture/milestone-1c-ashby.md`.

1. The description is `descriptionHtml`, NOT `descriptionPlain` (see below).
2. The work-model signal is `workplaceType`, NOT `isRemote` (see the field map).
"""

import re
from collections.abc import Iterator
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    CompensationBand,
    CompensationHint,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    parse_amount,
    stated_amounts,
)

API_BASE = "https://api.ashbyhq.com/posting-api/job-board"
PUBLIC_BOARD_BASE = "https://jobs.ashbyhq.com"

ASHBY_CAPABILITIES = ProviderCapabilities(
    # descriptionHtml on 3105/3105 in the list response. No second request, and
    # no assembly: Ashby has exactly one description, in two encodings.
    full_description_in_list=True,
    # And it IS the whole description. `descriptionHtml` and
    # `descriptionPlain` are the employer's own body, on 3105/3105, with
    # nothing held back for a detail page.
    obtains_full_description=True,
    # publishedAt on 3105/3105. Already UTC -- the first provider to be -- but
    # carrying milliseconds, which the shared contract drops.
    exposes_posted_date=True,
    # department AND team, both on 3105/3105.
    exposes_department=True,
    # compensationTierSummary on 59.8%, but only when includeCompensation=true
    # is sent. The flag answers "does the vendor have a place to put it?", so a
    # missing value means this employer chose not to disclose.
    exposes_compensation=True,
    # address.postalAddress gives locality, region and country as separate
    # nested fields, so the SHAPE is genuinely structured -- which is all this
    # flag asserts. The VALUES are not: "United States" appears 1465 times and
    # "USA" 609 times, across 35 spellings for far fewer countries. That is an
    # employer-entry problem to record as stated, and normalising it is M3's
    # job. Lever sets the same flag and returns clean ISO-3166-1 alpha-2; the
    # flag is not the place to express the difference in quality.
    exposes_location_structured=True,
    # workplaceType on 80.4%. Null on two entire boards in the sample, which is
    # exactly the case the flag exists to keep separate from "not exposed".
    exposes_remote_flag=True,
    # employmentType on 3105/3105, and a clean five-value enum where Lever had
    # twelve spellings of "full time".
    exposes_employment_type=True,
)

# Ashby is the first provider to declare all seven. Greenhouse declares three.
# That is a fact about the vendors, not a ranking of them.


ASHBY_FIELD_MAP = ProviderFieldMap(
    (
        # workplaceType, deliberately NOT isRemote. Ashby supplies both, and
        # they are not equivalent: isRemote is true on 2153 postings, which is
        # every Remote one (761) PLUS every Hybrid one (1392). The vendor has
        # already collapsed hybrid into remote. For a product whose founding
        # complaint is "remote does not necessarily mean remote for me", taking
        # the pre-collapsed boolean would import someone else's judgement as
        # though it were data. isRemote stays archived and unmapped.
        FieldMapping("workplaceType", MetadataDimension.WORK_MODEL_HINT),
        # Three paths, one dimension. `location` is free text on 100%,
        # `addressCountry` is a country name on 94.4%, and `secondaryLocations`
        # is an array on 26.9%. Three separate assertions about where the job
        # is; collapsing them would be deciding which one the employer meant.
        FieldMapping("location", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping(
            "address.postalAddress.addressCountry", MetadataDimension.HIRING_LOCATION_HINT
        ),
        # An array, and the reason no path-language extension was needed: it
        # travels whole as one canonical JSON value. Indexing into it would mean
        # choosing which location matters, which is a decision about the job
        # rather than about its shape.
        FieldMapping("secondaryLocations", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping("employmentType", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        # Two compensation assertions: one written for a human
        # ("$205K - $300K - Offers Equity") and one meant to be parsed
        # ("$205K - $300K"). Both travel; neither is judged.
        FieldMapping("compensation.compensationTierSummary", MetadataDimension.COMPENSATION_HINT),
        FieldMapping(
            "compensation.scrapeableCompensationSalarySummary",
            MetadataDimension.COMPENSATION_HINT,
        ),
    )
)

# Archived but unmapped, each for a stated reason:
#   isRemote          - a lossy derivative of a field already mapped (above)
#   isListed          - constant true on 3105/3105; a constant is not a signal
#   addressLocality   - the location assertion already travels three ways
#   addressRegion       and a fourth and fifth would add noise, not evidence
#   compensationTiers - the summaries carry the same claim in the form a human
#   summaryComponents   reads; adding the structured array later is one line
#                       and no migration, which is the point of the mechanism
#   applyUrl          - jobUrl plus "/application"; derivable, not an assertion
#   department, team  - already carried as PostingStub.department
#
# No new MetadataDimension was needed. Checked honestly: Ashby exposes no
# visa-sponsorship, work-authorisation, security-clearance, seniority, travel or
# timezone field -- those keys exist in none of the 3105 sampled postings.


class AshbyProvider(JobProvider):
    name = "ashby"
    kind = ProviderKind.ATS
    capabilities = ASHBY_CAPABILITIES
    field_map = ASHBY_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_base: str = API_BASE) -> None:
        self._fetcher = fetcher
        self._api_base = api_base.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return board.board_url or f"{PUBLIC_BOARD_BASE}/{board.board_identifier}"

    def jobs_url(self, board: BoardRef) -> str:
        return f"{self._api_base}/{board.board_identifier}?includeCompensation=true"

    # -- listing -----------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every posting on the board.

        Raises FetchError on failure. A board with no openings returns HTTP 200
        with `{"jobs": [], "apiVersion": "1"}` and yields nothing without
        raising -- the difference that keeps the closing logic safe.

        Ashby answers an unknown board with a 404 whose body is plain text
        rather than JSON, which is less dangerous than Lever's JSON error
        object. The envelope check below is kept anyway: relying on a vendor's
        error format staying inconveniently shaped is not a safety property, and
        a body that is not the expected envelope must never read as an empty
        board.
        """
        url = self.jobs_url(board)
        payload = self._fetcher.get_json(url)

        if not isinstance(payload, dict) or "jobs" not in payload:
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                url,
                "response did not contain a 'jobs' key",
            )
        jobs = payload["jobs"]
        if not isinstance(jobs, list):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "'jobs' was not a list")

        for entry in jobs:
            stub = self._to_stub(entry, board)
            if stub is None:
                # COUNTED, not silent. See `JobProvider.postings_skipped`.
                self.postings_skipped += 1
                continue
            yield stub

    def _to_stub(self, entry: Any, board: BoardRef) -> PostingStub | None:
        """Translate one Ashby posting object.

        A single unusable entry is skipped rather than failing the whole board:
        losing one malformed posting is better than losing a company.
        """
        if not isinstance(entry, dict):
            return None
        external_id = entry.get("id")
        title = entry.get("title")
        if not external_id or not title:
            return None

        location = entry.get("location")
        location_raw = str(location).strip() if location else None

        return PostingStub(
            external_id=str(external_id),
            title=str(title),
            url=str(entry.get("jobUrl") or f"{self.board_url(board)}/{external_id}"),
            location_raw=location_raw or None,
            department=_department(entry),
            posted_at=to_rfc3339_utc(entry.get("publishedAt")),
            # descriptionHtml, never descriptionPlain -- see the note at the
            # bottom of this module. descriptionPlain uppercases every heading.
            description_html=str(entry.get("descriptionHtml") or ""),
            payload=entry,
        )

    # -- completing a posting ---------------------------------------------

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """Complete a stub. No second request: the list response carried it all."""
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html),
            payload=stub.payload,
        )


def _department(entry: dict[str, Any]) -> str | None:
    """The posting's org unit, coarse level first.

    Third provider, third variation on the same two-level idea. Ashby supplies
    `department` and `team` on 3105/3105 -- neither is ever missing -- but they
    are the SAME string on 54.2% of postings ("Engineering / Engineering",
    "Sales / Sales"). One board in the sample splits them on 92% of postings
    ("Engineering & Product / Enterprise - Deployment"); another repeats them on
    100%.

    The existing rule handles all of that without a special case: join the
    levels with " / ", coarse first, dropping an identical repeat. Same
    convention as Greenhouse's flattened hierarchy and Lever's department/team.
    """
    names: list[str] = []
    for key in ("department", "team"):
        value = str(entry.get(key) or "").strip()
        if value and value not in names:
            names.append(value)
    return " / ".join(names) if names else None


#: Ashby's `interval` strings, mapped to the shared period vocabulary.
#: Counted over every salary component in the 5,982 archived Ashby payloads:
#: "1 YEAR" 3066, "1 HOUR" 62, "1 MONTH" 5, and nothing else. An interval
#: outside this table resolves to None rather than to the nearest guess -- a
#: fortnightly figure scored against a monthly target is a wrong number, and
#: `salary_unknown` is the right answer for a rate we cannot compare.
COMPENSATION_INTERVALS: dict[str, str] = {
    "1 YEAR": "YEAR",
    "1 MONTH": "MONTH",
    "1 HOUR": "HOUR",
}

#: The one component type that is pay for the work. Ashby's other four --
#: EquityPercentage (1400), EquityCashValue (1129), Bonus (431), Commission
#: (396) -- are real compensation and deliberately not read here: adding a
#: bonus range to a salary range would produce a number no posting states, and
#: equity has no comparable currency at all. They stay in the archived payload.
SALARY_COMPONENT_TYPE = "Salary"

#: Every payload path `read_compensation` reads, declared so the neutrality
#: guard can cover them. `field_map.paths()` only lists the paths that carry a
#: canonical DIMENSION, and compensation carries numbers rather than a
#: dimension, so these were in no declared list at all -- generic code could
#: have written `payload["compensation"]["compensationTiers"]` and sailed past
#: `test_provider_neutrality.py`. See `providers.registry.declared_payload_paths`.
COMPENSATION_PATHS: tuple[str, ...] = (
    "compensation.compensationTiers",
    "compensation.compensationTierSummary",
    "compensation.scrapeableCompensationSalarySummary",
)


def read_compensation(payload: Any) -> CompensationHint | None:
    """What this stored Ashby payload says about pay. No network, no guessing.

    Prefers the structured components over the display summaries, because they
    are the only place the numbers exist as numbers.

    **The multi-tier rule: widen only within one currency and interval.** 167 of
    the 2,865 payloads that state a salary carry more than one salary
    component, and they split two ways when counted: 108 agree on currency and
    interval and are genuinely one band expressed in pieces, while **59 state
    different currencies** -- one payload offers GBP 90-122K, EUR 135-183K and
    PLN 280-378K, which are three regional offers, not a range. Taking
    min-of-mins and max-of-maxes across those would produce "90,000 to 378,000"
    in no currency anyone named. So components are grouped by currency and
    interval, each group widens within itself, and the groups stay separate.
    Interval never disagreed within a payload: 0 of 167.

    **Every group travels, and the scorer chooses.** The groups used to be
    dropped on the floor: the first one in the JSON became the hint and the
    rest were left in the payload. Payload `01M0XZKEGAPG0EY02HK36QBTW8` states
    EUR 110,500-181,500 and USD 170,350-275,550 in that order, so a
    USD-targeting candidate was told the posting stated no comparable salary
    while the posting stated one in their own currency. Payload order is not
    relevance, and the adapter cannot know what is relevant -- that is a
    preference. The first group is still the lead, for every consumer that
    wants one number; the rest ride in `alternate_bands` and `match.score`
    picks the one whose currency the candidate configured. Measured: this
    reaches 5 payloads for a USD target, 17 for GBP, 14 for EUR.

    A payload with only the summary strings returns them as `raw_text` with no
    numbers. "$205K" is a rounding Ashby rendered for a human; reading 205000
    back out of it would turn a display string into a measurement, and would be
    wrong the first time a board writes "$205K+" or "from $205K".
    """
    if not isinstance(payload, dict):
        return None
    compensation = payload.get("compensation")
    if not isinstance(compensation, dict):
        return None

    summary = _first_text(
        compensation.get("compensationTierSummary"),
        compensation.get("scrapeableCompensationSalarySummary"),
    )

    components = [
        component
        for tier in compensation.get("compensationTiers") or []
        if isinstance(tier, dict)
        for component in tier.get("components") or []
        if isinstance(component, dict)
        and component.get("compensationType") == SALARY_COMPONENT_TYPE
    ]
    # A component counts as banded if it states EITHER bound. Requiring both
    # would discard "from $205K", which is a real thing an employer said.
    banded = [
        c
        for c in components
        if parse_amount(c.get("minValue")) is not None
        or parse_amount(c.get("maxValue")) is not None
    ]

    bands = _bands(banded)
    if not bands:
        # Nothing structured, or nothing but zeros. The summary is still an
        # assertion worth carrying, but it carries no amounts, so the matcher
        # will score it unknown.
        if summary is None:
            return None
        return CompensationHint(
            source_field="compensation.compensationTierSummary",
            raw_text=summary,
        )

    lead, *alternates = bands
    return CompensationHint(
        source_field="compensation.compensationTiers",
        min_value=lead.min_value,
        max_value=lead.max_value,
        currency=lead.currency,
        period=lead.period,
        raw_text=summary,
        alternate_bands=tuple(alternates),
    )


def _bands(banded: list[dict[str, Any]]) -> list[CompensationBand]:
    """One band per (currency, interval) the payload stated, in payload order."""
    groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = {}
    for component in banded:
        key = (_text(component.get("currencyCode")), _text(component.get("interval")))
        groups.setdefault(key, []).append(component)

    bands: list[CompensationBand] = []
    for (currency, interval), members in groups.items():
        minima = [v for v in (parse_amount(c.get("minValue")) for c in members) if v is not None]
        maxima = [v for v in (parse_amount(c.get("maxValue")) for c in members) if v is not None]
        # `or None` is wrong here and min()/max() over an empty list raises, so
        # both are guarded explicitly: a stated minimum with no maximum stays a
        # minimum with no maximum.
        minimum = min(minima) if minima else None
        maximum = max(maxima) if maxima else None

        # An inverted band is constructible from components that are each
        # individually sane: `[{min: 200000, max: None}, {min: None, max:
        # 100000}]` widens to 200,000-100,000, and the card would read
        # "200,000 - 100,000" while the scorer compared the 100,000. Not
        # present in the corpus today -- 0 payloads -- and cheap to refuse now
        # rather than to discover on a card. The fallback is the group's own
        # first component, which is a band one employer actually wrote.
        if minimum is not None and maximum is not None and minimum > maximum:
            first = members[0]
            minimum, maximum = (
                parse_amount(first.get("minValue")),
                parse_amount(first.get("maxValue")),
            )

        minimum, maximum = stated_amounts(minimum, maximum)
        if minimum is None and maximum is None:
            continue
        bands.append(
            CompensationBand(
                min_value=minimum,
                max_value=maximum,
                currency=currency,
                period=COMPENSATION_INTERVALS.get(interval or ""),
            )
        )
    return bands


def _first_text(*candidates: Any) -> str | None:
    """The first candidate that is a non-blank string."""
    for candidate in candidates:
        text = _text(candidate)
        if text:
            return text
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


# WHY descriptionHtml AND NOT descriptionPlain
#
# Both are present on 3105/3105, and picking the one whose name sounds friendlier
# would have been a quiet mistake. Compared word-for-word with tags stripped and
# entities decoded, the two are identical on 1 posting out of 3105:
# `descriptionPlain` UPPERCASES every heading -- "ABOUT ABRIDGE" where the HTML
# says "About Abridge" -- and is longer on 666 postings and shorter on none.
#
# Taking it would have upper-cased every heading in the corpus: worse to read,
# and enough to break any M2 evidence quote that spans one. `descriptionHtml`
# preserves the author's casing and markup, and `html_to_text` turns that into
# the section-and-bullet structure the product depends on.
#
# KNOWN LIMITATION, reported and awaiting a decision. Ashby authors list items as
# `<li><p>text</p></li>` on 99.5% of postings, and the shared normaliser emits
# the bullet marker and the block newline back to back, stranding "-" on its own
# line: 72095 such lines, 22.7% of the normalised Ashby corpus. The fix is one
# rule in domain/normalize.py and a no-op for Greenhouse and Lever, but generic
# normalisation is stop-and-report territory. See section 4 of
# docs/architecture/milestone-1c-ashby.md. Deliberately NOT worked around here:
# absorbing it would hide the pressure and leave the next adapter to rediscover
# it.


# -- recognising our own posting URLs -------------------------------------

#: `https://jobs.ashbyhq.com/<board>/<uuid>`, optionally with a trailing
#: `/application` and any query string.
#:
#: Measured against the corpus this recognises all 6,787 archived Ashby job
#: URLs, and the id it returns is byte-identical to the stored `external_id`.
_POSTING_URL = re.compile(
    r"^https?://jobs\.ashbyhq\.com/[^/]+/"
    r"(?P<id>[0-9a-fA-F-]{36})"
    r"(?:/application)?/?"
    # The query string the comment above always claimed was accepted and the
    # pattern rejected. It matters beyond tidiness: while this rejected
    # `?utm=x`, a Lever or Ashby apply link carrying a stray `gh_jid`
    # parameter was claimed by the Greenhouse recogniser ALONE, so the
    # registry saw no tie to refuse and resolved the wrong posting.
    r"(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The Ashby posting id in this URL, or None when it is not one of ours.

    Pure string work. It exists so an aggregator's apply link can be resolved
    to an EXACT `(provider, external_id)` key without any fuzzy matching, and
    without generic code ever spelling this vendor's hostname.
    """
    match = _POSTING_URL.match(url.strip())
    return match.group("id").lower() if match else None


#: `https://jobs.ashbyhq.com/<board>/<uuid>`.
_BOARD_URL = re.compile(
    r"^https?://jobs\.ashbyhq\.com/(?P<board>[A-Za-z0-9_.-]+)/"
    r"(?P<id>[0-9a-fA-F-]{36})(?:/application)?/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(board_identifier, posting_id)` for an Ashby posting URL, or None."""
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    return match.group("board"), match.group("id").lower()
