"""Jobicy, read from the public jobs API it documents and invites reuse of.

WHY THIS SOURCE
---------------
The recommendation audit on 2026-09-08 measured the shape of the owner's
problem: of 19,469 scored postings, only **101** are her specialty AND at a
level she accepts AND from an employer who may hire her. The bottleneck is not
the scorer. It is that almost nothing in the corpus says it can hire in Brazil.

This feed does. Measured over one bounded LATAM request on 2026-09-08:
`Brazil` alone on 20 postings, `LATAM` on 26 more, and multi-country lists
naming Brazil beside Canada, Mexico and the USA. `jobGeo` is the employer's
answer to WHERE APPLICANTS MAY LIVE, not where an office is -- which makes
this the third source in this product ever to publish a hiring scope, after We
Work Remotely and Himalayas.

It also carries the whole advert: 210 to 2,326 normalised words across the 200
rows, so a posting from here is scored on the employer's own text rather than
on an excerpt.

AUTHORISATION, AND THE THING THAT LOOKS LIKE A BLOCKER AND IS NOT
------------------------------------------------------------------
`jobicy.com/robots.txt` answers **HTTP 403** behind an interactive Cloudflare
challenge. That challenge was never solved, no alternate identity was used and
no job page HTML was ever requested. The 403 is recorded as what it is: the
site's crawler policy could not be read.

The permission this adapter rests on is a different instrument and a stronger
one. `jobicy.com/jobs-rss-feed` is first-party, answered HTTP 200, and says:

    "You may use Jobicy listings in your own products and user experiences
    without requesting individual permission."

and the API's own response envelope repeats the conditions in a
`friendlyNotice` field: credit Jobicy with a direct link to the source, and
make every application button redirect to the original job URL in the feed.

**An affirmative grant is not the absence of a ban.** This is the distinction
`docs/product/source-capability-matrix.md` turns on: Get on Board was built
because its terms expressly permit public display, and Torre stays switched
off because `search.torre.co` merely fails to forbid. Jobicy is in the first
group, and both conditions attached to the grant are implemented here rather
than promised: `canonical_url` is the only Apply target this adapter will
produce, and `ATTRIBUTION` is what the interface must show.

WHAT WAS NOT DONE
-----------------
**No live call has ever been made from this machine.** The measurements quoted
above come from the owner's own bounded research pass, and the fixtures under
`tests/fixtures/providers/jobicy/` are invented text built to the measured
contract -- the same precedent Get on Board and Jooble set. The first live
retrieval is the owner's, with `career-agent collect-jobicy`.

`Anywhere` IS NOT WORLDWIDE, AND THAT IS THE LOAD-BEARING LINE
---------------------------------------------------------------
52 of the 200 measured rows carry `jobGeo: "Anywhere"`, and the vendor's own
field documentation says that value can mean **no region was supplied**. It is
therefore a default, not an invitation, and reading it as one would put 52
postings in front of somebody who may not be able to take any of them.

`hiring_scope` returns None for it. The posting reaches the gate as nothing
stated and resolves UNRESOLVED, which is invariant 2 exactly: absence is never
permission. A role-specific worldwide sentence in the BODY can still establish
scope on its own, through the same prose path every other source uses.

THE WINDOW IS NOT THE MARKET
-----------------------------
One request returns at most 200 rows and there is no paging: `count` caps at
200 and the measured 400 on `offset` proves only that unexpected parameters
are rejected. Exactly 200 rows means A WINDOW LIMIT WAS REACHED, never a total
inventory, and a posting's absence from this window is not evidence it closed.

`retrieval_mode` is `QUERY_DRIVEN` and that is a deliberate, slightly
uncomfortable choice. The research contract names a fourth shape,
`FIXED_WINDOW_BOUNDED`: a newest-N window that cannot be paged and where even
repeating the same query cannot reach older rows. That is a genuinely
different thing from Jooble's "the answer to a question somebody chose", and
adding the member is a data-model change that is the owner's to approve. Until
then `QUERY_DRIVEN` is the honest fit -- `geo=latam` IS a question asked, its
coverage is a property of that question, and only `BOARD_EXHAUSTIVE` may ever
close a posting on absence, which is the property that actually matters here.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
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
    parse_amount,
    stated_amounts,
)

API_HOST = "https://jobicy.com"

#: The one host this adapter will talk to, and the one host a canonical URL may
#: point at. A URL out of a payload is untrusted input that ends up as a link
#: somebody clicks.
TRUSTED_HOST = "jobicy.com"

#: What the interface must show beside anything from this source.
#:
#: Not decoration. It is half of the licence: the first-party grant is
#: conditioned on crediting Jobicy with a direct link, and the API repeats it
#: in every response envelope. A constant rather than a translated string,
#: because the vendor's name is the vendor's name in both languages.
ATTRIBUTION = "Jobicy"

#: The documented maximum window, and the only value this adapter asks for.
#:
#: `count` above 200 was measured returning 400 in combination with an
#: unexpected `offset`; that proves parameter rejection rather than overflow
#: behaviour, so this stays at the documented ceiling and is never probed.
MAX_COUNT = 200

#: The size cap on one response, in bytes. The measured maximum-window response
#: was 1,515,339 bytes; 5 MB leaves room for a fuller feed and still refuses a
#: response that has stopped being the thing this adapter agreed to read.
MAX_BYTES = 5 * 1024 * 1024

#: Seconds between list requests, provider-wide.
#:
#: The grant carries "no more than one poll per hour". Provider-WIDE rather
#: than per filter: two `geo` values polled half an hour apart would be two
#: requests in an hour, which is the thing the number forbids. The gate is in
#: `next_allowed_at` and the collector asks it before a socket is opened.
MIN_SECONDS_BETWEEN_POLLS = 3600

#: `jobGeo` values that are the ABSENCE of an answer wearing the clothes of one.
#:
#: The vendor documents `Anywhere` as possibly meaning no region was supplied.
#: 52 of 200 measured rows carry it. Treated as nothing stated, so the posting
#: resolves UNRESOLVED rather than being offered to somebody who cannot take it.
DEFAULT_GEO_LABELS = frozenset({"anywhere", "worldwide", "", "-", "n/a", "none"})

#: `salaryPeriod` as the feed spells it, onto the intervals this product can
#: compare. A weekly rate against an annual target is a wrong number, so those
#: resolve to nothing and the posting scores `salary_unknown` -- which is the
#: correct score for a figure that cannot be compared. Same table Himalayas
#: and Speedrun use.
COMPENSATION_PERIODS: dict[str, str] = {
    "year": "YEAR",
    "yearly": "YEAR",
    "annual": "YEAR",
    "annually": "YEAR",
    "month": "MONTH",
    "monthly": "MONTH",
    "hour": "HOUR",
    "hourly": "HOUR",
    "week": "",
    "weekly": "",
    "day": "",
    "daily": "",
}


JOBICY_CAPABILITIES = ProviderCapabilities(
    # `jobDescription` is the whole advert, in the list response, in one
    # request. Measured 210-2,326 words over 200 rows, none of them empty.
    full_description_in_list=True,
    obtains_full_description=True,
    # `pubDate`, an ISO 8601 string with an offset. A real publication date.
    exposes_posted_date=True,
    # No team or function element. `jobIndustry` is a category, not a
    # department, and reading one as the other would put a marketing taxonomy
    # where an org chart belongs.
    exposes_department=False,
    # `salaryMin` / `salaryMax` / `salaryCurrency` / `salaryPeriod`, present on
    # 51 of the 200 measured rows and structured rather than rendered.
    exposes_compensation=True,
    exposes_location_structured=True,
    # **False, deliberately.** Every posting on this board is remote: that is
    # the board's admission criterion, not a statement this employer made
    # about this role. Declaring a remote flag would let editorial policy read
    # as an employer's answer. WWR and Himalayas earn the same False.
    exposes_remote_flag=False,
    # `jobType`: `Full-Time`, `Contract`, and so on. Full-time is not CLT.
    exposes_employment_type=True,
    # **True, and it is why this source was built.**
    #
    # `jobGeo` is the employer's answer to where applicants may live -- the
    # measured values are country and region lists, and the bodies behind the
    # narrow ones say things like "applicants must reside in Mexico". It is
    # not an office: this feed states no office at all, which is precisely
    # what makes the field readable as a scope.
    #
    # The default label is handled in `hiring_scope`, not here. A capability
    # says what the FIELD means; a value that means "unset" is a row-level
    # fact and answering it with a capability flag would silence the field for
    # the 148 rows that do state something.
    publishes_hiring_scope=True,
)


JOBICY_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="jobGeo", dimension=MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping(path="jobType", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class JobicyError(ValueError):
    """A response, or a configured value, this adapter will not use."""


class JobicyCoolingDown(JobicyError):
    """A list request came round again inside the hour the grant allows.

    Its own class because it is not a fault. The collector reports it as a
    source that was not read this run, exactly as it reports a board it chose
    not to visit, and never as an error that ended the run.
    """


def assert_trusted(url: str) -> str:
    """Refuses anything that is not HTTPS on the one host this adapter reads.

    Applied to the URL this adapter BUILDS and to every canonical URL that
    comes back in a payload. `javascript:`, `file:`, a private address and a
    look-alike host all fail the same way, before anything is fetched and
    before anything is rendered as a link.
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise JobicyError(f"Jobicy URL must be https: {url!r}")
    if parts.hostname != TRUSTED_HOST:
        raise JobicyError(f"untrusted Jobicy host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def next_allowed_at(last_attempt_at: float | None) -> float:
    """The earliest epoch second at which another list request is permitted.

    Takes the time of the last ATTEMPT rather than of the last success, and
    that is the whole point: a response that failed, timed out or arrived
    malformed still consumed a request against somebody else's server. Retrying
    it inside the hour is exactly the behaviour the cadence forbids.
    """
    if last_attempt_at is None:
        return 0.0
    return float(last_attempt_at) + MIN_SECONDS_BETWEEN_POLLS


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def hiring_scope(job: Any) -> str | None:
    """`jobGeo`, or None when the value is the vendor's default.

    **None, never "worldwide".** `Anywhere` is the single most temptingly
    over-readable field in this feed: it looks exactly like an invitation, and
    the vendor's own documentation says it can mean no region was supplied. A
    source that returned it as a scope would make somebody eligible for 52
    postings on the strength of a field nobody filled in.

    A multi-country value is passed through with its own separators intact --
    the feed writes `Brazil,  Canada,  Mexico,  USA` with doubled spaces, and
    normalising that is `match/places.py`'s job, not this adapter's invention.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("jobGeo"))
    if raw is None or raw.strip().casefold() in DEFAULT_GEO_LABELS:
        return None
    return raw


def company_of(job: Any) -> str | None:
    """The employer this posting names, for the collector to resolve."""
    if not isinstance(job, dict):
        return None
    return _text(job.get("companyName"))


def canonical_url(job: Any) -> str | None:
    """`url`, the posting's page on Jobicy, validated before it is used.

    This is the ONLY Apply target this adapter produces, and that is a licence
    condition rather than a preference: the grant requires every application
    button to redirect to the original job URL in the feed. There is no
    employer apply URL in this response to prefer instead, and inventing one
    would breach the terms this source is read under.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("url"))
    if raw is None:
        return None
    try:
        return assert_trusted(raw)
    except JobicyError:
        return None


def external_id(job: Any) -> str | None:
    """The vendor's own numeric id, as a decimal string.

    **Qualified by provider everywhere it is used.** A naked `152709` is not an
    identity: another feed can serve the same integer for a different job, and
    two providers' numeric ids collapsing into one row is how a corpus starts
    describing a posting that does not exist. `job.provider` carries the other
    half and ADR-0013 is where the rule lives.
    """
    if not isinstance(job, dict):
        return None
    value = job.get("id")
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    text = _text(value)
    return text if text and text.isdigit() else None


def published_at(job: Any) -> str | None:
    """`pubDate`, an ISO 8601 string that already carries its offset.

    Returned as the vendor wrote it rather than reformatted. Three clocks are
    kept apart and none is derived from another: this publication time, the
    envelope's `lastUpdate`, and the HTTP `Last-Modified`. There is no per-job
    update timestamp in this feed and none is invented.
    """
    if not isinstance(job, dict):
        return None
    return _text(job.get("pubDate"))


def read_compensation(payload: Any) -> CompensationHint | None:
    """What a stored Jobicy payload says about pay. No network, no guessing.

    A band with no currency yields NOTHING, not a band in some assumed
    currency: nothing in this product converts between currencies, and 350,000
    MXN and 350,000 USD are not the same offer. The fixture case
    `jobicy_salary_missing_units` exists for exactly this and expects null.
    """
    if not isinstance(payload, dict):
        return None

    minimum, maximum = stated_amounts(
        parse_amount(payload.get("salaryMin")), parse_amount(payload.get("salaryMax"))
    )
    if minimum is None and maximum is None:
        return None

    currency = _text(payload.get("salaryCurrency"))
    if currency is None:
        return None

    raw_period = payload.get("salaryPeriod")
    period = (
        COMPENSATION_PERIODS.get(str(raw_period).strip().lower()) or None if raw_period else None
    )
    return CompensationHint(
        source_field="salaryMin/salaryMax",
        min_value=minimum,
        max_value=maximum,
        currency=currency.upper(),
        period=period,
        raw_text=None,
    )


def to_stub(job: Any) -> PostingStub | None:
    """One feed row into a stub, or None when it cannot be addressed.

    Four things are required and none is invented: an id, a title, a usable
    canonical URL and a description. Counting a row that lacks one is more
    honest than filling the gap in -- an unknown title is a visibly incomplete
    lead, never a value this adapter supplied.
    """
    if not isinstance(job, dict):
        return None

    identity = external_id(job)
    title = _text(job.get("jobTitle"))
    url = canonical_url(job)
    description = _text(job.get("jobDescription"))
    if identity is None or title is None or url is None or description is None:
        return None

    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        # The employer's hiring scope, and NOT a place the posting is.
        # `publishes_hiring_scope` is what tells the pipeline to read it as a
        # scope; storing it here is what puts it on the card.
        location_raw=hiring_scope(job),
        department=None,
        posted_at=published_at(job),
        description_html=description,
        payload=dict(job),
    )


@dataclass(frozen=True)
class WindowRead:
    """One bounded read of the feed, and what it does and does not establish."""

    jobs: tuple[Any, ...]
    #: What the envelope said it was returning, which is checked against the
    #: rows actually present rather than trusted.
    claimed_count: int | None
    #: True when the response filled the window exactly. **A reached limit,
    #: never a total.** The rows beyond it are unreachable: there is no paging.
    window_full: bool
    unaddressable: int = 0
    applied_filters: dict[str, Any] | None = None

    @property
    def complete(self) -> bool:
        """Whether this read saw everything the QUERY could return.

        Never whether it saw everything the SOURCE has. No caller may use this
        to close a posting, and `retrieval_mode` is what enforces that one
        level up.
        """
        return not self.window_full


class JobicyProvider(JobProvider):
    """The Jobicy public jobs API."""

    name = "jobicy"
    kind = ProviderKind.AGGREGATOR
    #: See the module docstring: the precise shape is a fixed newest-N window,
    #: and naming that would be a data-model change the owner has not approved.
    #: `QUERY_DRIVEN` is truthful and carries the property that matters -- only
    #: `BOARD_EXHAUSTIVE` may close a posting on absence.
    retrieval_mode = RetrievalMode.QUERY_DRIVEN
    #: One stream, filtered by region. Asking this adapter about an employer is
    #: a question in the wrong vocabulary, as for WWR, Himalayas and GoB.
    addresses_boards_by_company = False
    capabilities = JOBICY_CAPABILITIES
    field_map = JOBICY_FIELD_MAP

    def __init__(
        self,
        fetcher: HttpFetcher,
        api_host: str = API_HOST,
        geo: str | None = "latam",
        count: int = MAX_COUNT,
    ) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")
        self._geo = (geo or "").strip() or None
        self._count = max(1, min(int(count), MAX_COUNT))

    # -- URLs --------------------------------------------------------------

    def feed_url(self) -> str:
        """The one request this adapter makes.

        No `page` and no `offset`, not defaulted and not behind a flag. The
        feed documents neither, the measured 400 came from sending one, and
        enumerating partitions to simulate completeness is precisely what a
        bounded window forbids.
        """
        url = f"{self._api_host}/api/v2/remote-jobs?count={self._count}"
        if self._geo is not None:
            if not self._geo.replace("-", "").replace("_", "").isalnum():
                raise JobicyError(f"geo must be a plain taxonomy value: {self._geo!r}")
            url = f"{url}&geo={self._geo}"
        return assert_trusted(url)

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this up by hand."""
        del board
        return f"{self._api_host}/remote-jobs"

    # -- reading -----------------------------------------------------------

    def read_window(self, *, use_cache: bool = True) -> WindowRead:
        """The window, in exactly one request.

        **A valid empty response, an error and a challenge are three different
        outcomes**, and this is where they are kept apart. An empty validated
        `jobs` array is an empty window and a legitimate answer; a malformed
        envelope raises; and neither may ever be read as "these postings are
        gone". The fixture case `jobicy_empty_error` asserts both halves.
        """
        body = self._fetcher.get_json(self.feed_url(), use_cache=use_cache)
        if not isinstance(body, dict):
            raise JobicyError("Jobicy returned something that is not a JSON object")

        # The envelope is validated before a single row is read. A response
        # that says `success: false` while carrying rows is not a response this
        # adapter will half-accept.
        if body.get("success") is not True:
            raise JobicyError(
                f"Jobicy did not report success: success={body.get('success')!r} "
                f"statusCode={body.get('statusCode')!r} error={body.get('error')!r}"
            )
        jobs = body.get("jobs")
        if not isinstance(jobs, list):
            keys = ", ".join(sorted(body))
            raise JobicyError(
                f"Jobicy returned an unexpected shape: expected {{'jobs': [...]}}, got [{keys}]"
            )

        claimed = body.get("jobCount")
        claimed_count = (
            claimed if isinstance(claimed, int) and not isinstance(claimed, bool) else None
        )
        if claimed_count is not None and claimed_count != len(jobs):
            raise JobicyError(
                f"Jobicy said jobCount={claimed_count} and sent {len(jobs)} rows. "
                "A count that disagrees with its own list is a partial response."
            )

        seen: set[str] = set()
        collected: list[Any] = []
        for job in jobs:
            identity = external_id(job)
            # A duplicate id inside one window is a conflicting response, not a
            # posting seen twice: there is no paging here, so the same row
            # cannot legitimately arrive again.
            if identity is not None and identity in seen:
                raise JobicyError(
                    f"Jobicy sent id {identity} twice in one window. "
                    "A window that contradicts itself is not a window this adapter will store."
                )
            if identity is not None:
                seen.add(identity)
            collected.append(job)

        filters = body.get("appliedFilters")
        return WindowRead(
            jobs=tuple(collected),
            claimed_count=claimed_count,
            window_full=len(collected) >= self._count,
            unaddressable=sum(1 for job in collected if to_stub(job) is None),
            applied_filters=dict(filters) if isinstance(filters, dict) else None,
        )

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        """This source is ONE WINDOW. It cannot be asked about an employer.

        The same answer Himalayas gives, for the same measured reason: a
        `source_board` row per employer would fetch the whole window once per
        employer, which is one run hammering somebody else's server -- and
        under a grant capped at one poll an hour it would breach the terms as
        well as being wasteful.
        """
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Jobicy can be asked for: this source is one "
            f"bounded window, not a board per employer. Collect it with "
            f"`career-agent collect-jobicy`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings from the window. One request, no paging, no retry."""
        del board
        for job in self.read_window().jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the window gave it, body included.

        No second request: the window carries the whole advert, and there is no
        per-job endpoint in this contract. `html_to_text` is the same
        normaliser every other adapter's body goes through, so a Jobicy
        description and a Greenhouse one reach the matcher rendered
        identically -- and it strips markup rather than executing it.
        """
        del board
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html) if html else "",
            payload=dict(stub.payload),
        )
