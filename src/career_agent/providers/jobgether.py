"""Jobgether, read through the job-search API it publishes for AI agents.

TWO FIRST-PARTY STATEMENTS, AND THE NARROWER ONE IS THE GRANT
---------------------------------------------------------------
`jobgether.com/terms-of-use`, read 2026-09-11: users must "refrain from
accessing the Platform or extracting its content by automated means (bots,
scrapers, crawlers)", and "all acts of extraction, data mining, scraping and
similar acts ... are strictly prohibited". Read alone, that closes every page
on the site to this product.

`jobgether.com/robots.txt`, read the same day, carries `Content-Signal:
search=yes, ai-input=yes` and two lines that allow one path and only one path
with a query string: `Allow: /astroapi/ai/jobs.json` and `Allow:
/astroapi/ai/jobs.json?*`, beside `Disallow: /*?*` for everything else. The
document the endpoint links, `/astroapi/ai/jobs/docs`, says the API is
"Intended for AI agents/assistants answering user job-search queries" and
publishes an MCP server for the same purpose.

So the grant is SPECIFIC, and this adapter stays inside it: the documented
API, GET on the explicitly allowed alias, within the documented caps, at the
robots `Crawl-delay: 2`. It never requests an `/offer/` page. That page carries
a JSON-LD `JobPosting` with the whole advert, `applicantLocationRequirements`
and an employer apply URL -- and reading it would be exactly the automated
content extraction the terms prohibit. What the API returns is what this
source has: title, employer, the Jobgether listing URL, a hiring scope, a work
model, a contract type, an experience band, a posted date and sometimes a
salary range. No description. Every posting is METADATA_ONLY and the
partial-source policy routes it accordingly.

`location` IS A HIRING SCOPE, AND `Anywhere` MEANS ANYWHERE HERE
------------------------------------------------------------------
Measured 2026-09-11 with `locations=brazil`: rows come back with `location`
`Anywhere`, `Latin America`, `Brazil`, `North America, Europe`, and `Indiana
(USA), New Jersey (USA)`. A search for jobs open to Brazil returned the ones
whose scope contains Brazil, so the field answers "where may the applicant
be", not "where is the desk". The offer page's JSON-LD confirms it: the same
posting's `applicantLocationRequirements` is `[US]` when the API says
`Indiana (USA), New Jersey (USA)`. `publishes_hiring_scope` is True.

`Anywhere` is not the Jobicy case. Jobicy documents `Anywhere` as possibly
meaning no region was supplied; Jobgether's search filter `locations=brazil`
RETURNS the `Anywhere` rows, which is the vendor stating that they are open to
Brazil. It is passed through, `match/places.py` reads it as WORLDWIDE, and the
gate admits it.

COVERAGE IS QUERY-SCOPED, AND THAT IS WRITTEN DOWN
----------------------------------------------------
`page` is capped at 10 and `limit` at 25: 250 rows per query, and page 11
returns page 10 again rather than an error (measured). `locations=anywhere`
alone holds more than 250 postings from the last three days. The whole
inventory is not reachable through this API, and no partition by anybody's
occupation is used to get closer (ADR-0019). What the collector walks are the
vendor's own neutral dimensions -- `locations` (a geography), `contractType`
(five values), `experience` (five values) -- each slice sorted by date, each
recorded with its own boundary. `jobReferences` is an occupational taxonomy
and `keyword` is free text; neither is ever sent.

IDENTITY
--------
The list `id` and the id in `url` DIFFER (`6aa33da5...` against `6aa3137c...`
on the same row), and only the URL id is the offer page's own. `external_id`
is the id in the URL. The URL points at Jobgether; there is no origin pointer
in the response, so ADR-0013 rule 2 is unreachable and duplicates against ATS
boards fold only through rule 3 (never, here) or not at all. A Jobgether row is
a LEAD with a hiring scope, and it is stored as that.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    CompensationHint,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)

API_HOST = "https://jobgether.com"
TRUSTED_HOST = "jobgether.com"

#: The explicitly allowed alias. `/api/v1/jobs` is the documented stable path
#: and is ALSO the one `Disallow: /*?*` closes to a query string; the alias is
#: the one line robots opens with `?*`. `/astroapi/ai/jobs` (no suffix) is
#: deprecated with a sunset of 2026-09-28 and is not used.
FEED_PATH = "/astroapi/ai/jobs.json"

#: What the interface shows beside anything from this source.
ATTRIBUTION = "Jobgether"

#: robots.txt `Crawl-delay: 2`. Seconds between requests, provider-wide.
MIN_SECONDS_BETWEEN_REQUESTS = 2.0

#: The documented caps. Asking for more is a 400 and is never probed.
MAX_LIMIT = 25
MAX_PAGE = 10

#: The vendor's own closed vocabularies, quoted from `/astroapi/ai/jobs/docs`.
#: Provider-neutral partitions in ADR-0019's sense: a contract type and an
#: experience band are facts about the posting the vendor classified, not a
#: candidate's vocabulary.
CONTRACT_TYPES: tuple[str, ...] = (
    "full-time",
    "part-time",
    "fixed-term",
    "freelance",
    "internships",
)
EXPERIENCE_BANDS: tuple[str, ...] = (
    "entry-level-graduate",
    "junior-1-2-years",
    "mid-level-2-5-years",
    "senior-5-10-years",
    "expert-10-years",
)

#: Location slugs measured VALID on 2026-09-11 (`worldwide`, `global`,
#: `latin-america`, `caribbean` and `remote` were measured 400). Geography,
#: not occupation. Continents and the hemisphere first; the rest are the
#: countries of the Americas and a few large markets so a slice exists for
#: somebody who is not in Brazil. Adding one is a data change, not a rule.
LOCATION_SLUGS: tuple[str, ...] = (
    "anywhere",
    "south-america",
    "latam",
    "central-america",
    "north-america",
    "europe",
    "asia",
    "africa",
    "oceania",
    "brazil",
    "argentina",
    "chile",
    "colombia",
    "peru",
    "uruguay",
    "costa-rica",
    "mexico",
    "canada",
    "united-states",
    "united-kingdom",
    "portugal",
    "spain",
    "germany",
    "india",
    "australia",
)

_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_OFFER_ID = re.compile(r"^/offer/(?P<id>[0-9a-f]{24})(?:-|$)")
_SALARY_RANGE = re.compile(
    r"^\s*(?P<min>\d[\d,\.]*)\s*(?:-|to)\s*(?P<max>\d[\d,\.]*)\s*(?P<cur>[A-Z]{3})\s*$"
)


JOBGETHER_CAPABILITIES = ProviderCapabilities(
    # No description anywhere in the API, and the page that has one is
    # closed by the terms. METADATA_ONLY by construction.
    full_description_in_list=False,
    obtains_full_description=False,
    # `postedAt`, RFC 3339.
    exposes_posted_date=True,
    # `jobFunctions` is the vendor's taxonomy, not the employer's team.
    exposes_department=False,
    # `salaryRange`, a string like `60000-80000 EUR`, on some rows.
    exposes_compensation=True,
    exposes_location_structured=False,
    # `remote`: Full Remote / Remote-first / Hybrid. The vendor's own field.
    exposes_remote_flag=True,
    # `contractType`, one of five documented values.
    exposes_employment_type=True,
    # **True.** See the module docstring: the field answers where the
    # applicant may be, and the search filter proves it.
    publishes_hiring_scope=True,
)

JOBGETHER_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="location", dimension=MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping(path="remote", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="contractType", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class JobgetherError(ValueError):
    """A response, or a configured value, this adapter will not use."""


def assert_trusted(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise JobgetherError(f"Jobgether URL must be https: {url!r}")
    if parts.hostname != TRUSTED_HOST:
        raise JobgetherError(f"untrusted Jobgether host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


@dataclass(frozen=True)
class Slice:
    """One provider-neutral question: a geography, optionally narrowed by the
    vendor's contract-type and experience vocabularies. Never by a keyword."""

    location: str
    contract_type: str | None = None
    experience: str | None = None

    def __post_init__(self) -> None:
        for value in (self.location, self.contract_type, self.experience):
            if value is not None and not _SLUG.match(value):
                raise JobgetherError(f"not a slug: {value!r}")
        if self.contract_type is not None and self.contract_type not in CONTRACT_TYPES:
            raise JobgetherError(f"not a documented contract type: {self.contract_type!r}")
        if self.experience is not None and self.experience not in EXPERIENCE_BANDS:
            raise JobgetherError(f"not a documented experience band: {self.experience!r}")

    @property
    def key(self) -> str:
        return "/".join(
            part for part in (self.location, self.contract_type, self.experience) if part
        )


def default_slices(locations: tuple[str, ...] = LOCATION_SLUGS) -> tuple[Slice, ...]:
    """Every geography, each cut by contract type and experience band.

    The order of `locations` is the caller's: scheduling may put a
    candidate's markets first, and that is a decision about WHEN, not about
    WHAT (ADR-0019). The cuts inside a geography are the vendor's closed
    vocabularies in the vendor's order.
    """
    return tuple(
        Slice(location, contract, band)
        for location in locations
        for contract in CONTRACT_TYPES
        for band in EXPERIENCE_BANDS
    )


def canonical_url(job: Any) -> str | None:
    """`url`, the offer's page on Jobgether, validated before it is used."""
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("url"))
    if raw is None:
        return None
    try:
        return assert_trusted(raw)
    except JobgetherError:
        return None


def external_id(job: Any) -> str | None:
    """The offer id in the URL, never the list row's `id`. See the docstring."""
    url = canonical_url(job)
    if url is None:
        return None
    match = _OFFER_ID.match(urlsplit(url).path)
    return match.group("id") if match else None


def company_of(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    return _text(job.get("company"))


def hiring_scope(job: Any) -> str | None:
    """`location`, as the vendor wrote it. `Anywhere` is a scope here."""
    if not isinstance(job, dict):
        return None
    return _text(job.get("location"))


def posted_at(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("postedAt"))
    if raw is None:
        return None
    from datetime import UTC, datetime

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_compensation(payload: Any) -> CompensationHint | None:
    """`salaryRange`, `60000-80000 EUR`. A band only with a currency.

    The period is left UNKNOWN. The docs say the salary FILTERS are annual;
    they do not say the displayed range is, and an unknown period is stored
    as unknown rather than promoted to yearly.
    """
    if not isinstance(payload, dict):
        return None
    raw = _text(payload.get("salaryRange"))
    if raw is None:
        return None
    match = _SALARY_RANGE.match(raw)
    if match is None:
        return None

    def number(text: str) -> float | None:
        try:
            return float(text.replace(",", ""))
        except ValueError:
            return None

    minimum, maximum = number(match.group("min")), number(match.group("max"))
    if minimum is None or maximum is None:
        return None
    return CompensationHint(
        source_field="salaryRange",
        min_value=minimum,
        max_value=maximum,
        currency=match.group("cur"),
        period=None,
        raw_text=raw,
    )


def to_stub(job: Any) -> PostingStub | None:
    """One API row into a stub, or None when it cannot be addressed."""
    if not isinstance(job, dict):
        return None
    identity = external_id(job)
    title = _text(job.get("title"))
    url = canonical_url(job)
    if identity is None or title is None or url is None:
        return None
    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        location_raw=hiring_scope(job),
        department=None,
        posted_at=posted_at(job),
        # No description. Not an empty string standing in for one: None.
        description_html=None,
        payload=dict(job),
    )


@dataclass(frozen=True)
class PageRead:
    """One page of one slice."""

    jobs: tuple[Any, ...]
    has_more: bool
    page: int
    unaddressable: int = 0


class JobgetherProvider(JobProvider):
    """The Jobgether job-search API for agents."""

    name = "jobgether"
    kind = ProviderKind.AGGREGATOR
    #: Every read is the answer to a bounded question, never a board.
    retrieval_mode = RetrievalMode.QUERY_DRIVEN
    addresses_boards_by_company = False
    capabilities = JOBGETHER_CAPABILITIES
    field_map = JOBGETHER_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_host: str = API_HOST) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def page_url(self, slice_: Slice, page: int) -> str:
        """One question, one page, newest first, at the documented caps."""
        if not 1 <= page <= MAX_PAGE:
            raise JobgetherError(f"page must be 1..{MAX_PAGE}: {page}")
        params: list[tuple[str, str]] = [("locations", slice_.location)]
        if slice_.contract_type:
            params.append(("contractType", slice_.contract_type))
        if slice_.experience:
            params.append(("experience", slice_.experience))
        params.extend([("sort", "date"), ("limit", str(MAX_LIMIT)), ("page", str(page))])
        return assert_trusted(f"{self._api_host}{FEED_PATH}?{urlencode(params)}")

    def board_url(self, board: BoardRef) -> str:
        del board
        return f"{self._api_host}/search-offers"

    # -- reading -----------------------------------------------------------

    def read_page(self, slice_: Slice, page: int, *, use_cache: bool = False) -> PageRead:
        body = self._fetcher.get_json(self.page_url(slice_, page), use_cache=use_cache)
        if not isinstance(body, dict):
            raise JobgetherError("Jobgether returned something that is not a JSON object")
        if "jobs" not in body:
            # An RFC 9457 problem document, or something else. Name it.
            detail = body.get("detail") or body.get("title")
            raise JobgetherError(f"Jobgether refused: {body.get('code')!r} {detail!r}")
        jobs = body.get("jobs")
        if not isinstance(jobs, list):
            raise JobgetherError("Jobgether returned `jobs` that is not a list")
        pagination = body.get("pagination")
        has_more = bool(pagination.get("hasMore")) if isinstance(pagination, dict) else False
        return PageRead(
            jobs=tuple(jobs),
            has_more=has_more,
            page=page,
            unaddressable=sum(1 for job in jobs if to_stub(job) is None),
        )

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Jobgether can be asked for: this source answers "
            "geographic questions, not employers. Collect it with "
            "`career-agent collect-jobgether`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        del board
        for job in self.read_page(Slice("anywhere"), 1).jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The row as the API gave it. NO second request: the page that holds
        the advert is closed by the terms, and this method must never open it."""
        del board
        return RawPosting(
            stub=stub,
            description_html="",
            description_text="",
            payload=dict(stub.payload),
        )
