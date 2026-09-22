"""Himalayas, read from the public jobs feed it documents.

WHY THIS SOURCE, AHEAD OF EVERY OTHER CANDIDATE
------------------------------------------------
It is the first source in this product that answers, structurally, the question
the whole eligibility gate exists for.

`locationRestrictions` is a LIST OF COUNTRIES the employer will hire from. Not
where the company sits, not where the posting was filed, not a timezone: the
employer's own answer to "where may I hire". Only We Work Remotely has ever
supplied that here, as free text on twenty-five postings at a time, and this
supplies it as an array over a feed of six figures.

Everything else follows from the same response, in one request per page:

* `description` -- the whole advert as HTML, not an excerpt.
* `minSalary` / `maxSalary` / `currency` / `salaryPeriod` -- a real band with a
  currency AND a period, which is what `min_salary` filtering has always
  required and almost no source states.
* `timezoneRestrictions` -- UTC offsets, which is a THIRD question again and
  is stored, never read as geography.
* `timezoneRestrictions`, `seniority`, `employmentType`, `categories`, `guid`,
  `pubDate`, `expiryDate`.

AUTHORISATION, READ FIRST-PARTY 2026-09-07
-------------------------------------------
`himalayas.app/robots.txt` carries a single `User-agent: *` block with
`Allow: /`. It disallows `/apply` and the `?page=` pagination of several
browse sections; it names no AI crawler, no `ClaudeBot`, and does not mention
the feed path at all. There is no `Content-Signal` directive.

**That is a different situation from Get on Board's**, and the difference is
recorded rather than glossed: that site names `ClaudeBot` with `Disallow: /`,
so the agent that wrote its adapter did not call it and the first retrieval was
the owner's. Nothing on this host restricts this reader, so the bounded
retrieval that proves the adapter works was made from here, read-only, under
this product's own user agent, identifying itself honestly.

The published API page states no terms of use, no licence, no attribution
requirement and no rate limit. **Absence of stated terms is not permission for
anything unbounded**, so the page budget is small by default and the walk is
the shared one in `providers/feeds.py`.

WHAT IT IS, AND WHY THAT MATTERS FOR ATTRIBUTION
-------------------------------------------------
`AGGREGATOR`. Himalayas composes its own posting pages and republishes work
advertised elsewhere; `applicationLink` frequently points at a Greenhouse or
Lever board this product already collects directly. That is a FEATURE for
identity -- `identify_posting_url` resolves such a link to its real adapter and
the collector files a second sighting rather than a second job -- and a reason
not to call the text the employer's own record.

WHAT IT DOES NOT SUPPLY, MEASURED RATHER THAN ASSUMED
------------------------------------------------------
**An origin pointer.** `applicationLink` looked like one and is not: over the
first page read live on 2026-09-07, all 20 rows carried an `applicationLink`
byte-identical to their `guid`, which is this board's own posting page. There
is no employer apply URL in this feed.

That is the field whose absence kept Remote OK unbuilt, so it is worth saying
why it does not keep this one unbuilt. Remote OK offers a snippet and no body;
this offers the whole advert and a hiring scope. ADR-0013's rule 2
(`ORIGIN_URL`) is simply unreachable here, exactly as it is for Get on Board,
and rules 1, 3 and 4 remain -- the last of them, byte-identical description
from the same employer for the same role, works precisely BECAUSE the feed
carries full bodies. The consequence is stated rather than discovered later: a
job listed here and on an employer's Greenhouse board is two rows unless the
external id, the canonical URL or the body already matched. Two visible rows
for one job is a smaller harm than one invisible job.

CURSOR PAGINATION, AND WHY THE OFFSET IS NOT USED
--------------------------------------------------
The feed documents both and says plainly that `offset` is deprecated and that
the cursor "will never return the same job twice". A walk that paged by offset
over a feed being written to would re-serve and skip rows, and the resulting
corpus would be wrong in a way no counter here could detect.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
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

API_HOST = "https://himalayas.app"

#: The one host this adapter will talk to.
TRUSTED_HOST = "himalayas.app"

#: Rows per request.
#:
#: **20, because that is what the vendor actually serves.** Asking for 100
#: returned 20 rows and a cursor, measured live 2026-09-07. The constant is set
#: to the observed maximum rather than to the requested one so that the page
#: budget below means what it says: a budget expressed in pages is a lie about
#: how much is read if the page size in this file is five times the real one.
PER_PAGE = 20

#: What the vendor calls a cursor: an opaque token. Bounded and character-class
#: checked before it is ever put in a URL, because it comes back from a
#: response and a response is untrusted input.
CURSOR = re.compile(r"^[A-Za-z0-9_.:=-]{1,512}$")

#: `salaryPeriod` as the feed spells it, mapped onto the three intervals this
#: product can compare. `week` and `day` resolve to the empty string and then
#: to None, deliberately: a weekly rate compared against an annual target is a
#: wrong number, and `salary_unknown` is the correct score for a figure that
#: cannot be compared. Same table Speedrun uses, for the same reason.
COMPENSATION_PERIODS: dict[str, str] = {
    "year": "YEAR",
    "yearly": "YEAR",
    "annual": "YEAR",
    "month": "MONTH",
    "monthly": "MONTH",
    "hour": "HOUR",
    "hourly": "HOUR",
    "week": "",
    "weekly": "",
    "day": "",
    "daily": "",
}


HIMALAYAS_CAPABILITIES = ProviderCapabilities(
    # `description` is the whole advert, in the list response, in one request.
    full_description_in_list=True,
    obtains_full_description=True,
    # `pubDate`, epoch seconds. A real publication date.
    exposes_posted_date=True,
    # No team or function element.
    exposes_department=False,
    # `minSalary`, `maxSalary`, `currency` and `salaryPeriod`, as structured
    # numbers rather than as rendered text. `min_salary` filtering has always
    # required a currency and this is one of the few sources that states one.
    exposes_compensation=True,
    # `locationRestrictions` is an ARRAY of country names.
    exposes_location_structured=True,
    # **False, and it is not an oversight.** Every posting on this board is
    # remote -- that is the board's admission criterion, not a statement the
    # employer made about this role. Declaring a remote flag here would let the
    # board's editorial policy read as an employer's answer, which is the
    # conflation invariant 3 forbids. WWR earns the same False for the same
    # reason.
    exposes_remote_flag=False,
    # `employmentType`: `Full Time`, `Contract`, and so on.
    exposes_employment_type=True,
    # **True, and this is the most consequential line in the file.**
    #
    # `locationRestrictions` is the employer's answer to WHERE IT MAY HIRE. It
    # is not where the company sits and not where the posting was filed: those
    # are questions this feed does not answer at all, which is exactly why this
    # one can be read as a scope. The field is named for the restriction, the
    # documented meaning is the restriction, and a posting with none is
    # unrestricted rather than unknown -- though this adapter still reports an
    # empty list as NOTHING STATED, because absence is never permission.
    #
    # Only WWR has held this True before. Every ATS reports an OFFICE.
    publishes_hiring_scope=True,
)


HIMALAYAS_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(
            path="locationRestrictions",
            dimension=MetadataDimension.HIRING_LOCATION_HINT,
        ),
        FieldMapping(path="employmentType", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class HimalayasError(ValueError):
    """A response, or a configured value, this adapter will not use."""


def assert_trusted(url: str) -> str:
    """Refuses anything that is not HTTPS on the one host this adapter reads."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https":
        raise HimalayasError(f"Himalayas URL must be https: {url}")
    if parts.hostname != TRUSTED_HOST:
        raise HimalayasError(f"untrusted Himalayas host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _published_at(job: Any) -> str | None:
    """`pubDate`, epoch SECONDS, as RFC 3339 UTC.

    Seconds rather than milliseconds is the vendor's encoding, and getting it
    wrong by a factor of a thousand puts every posting in 1970 or in the far
    future -- which reads as a freshness result rather than as a parsing bug.
    """
    if not isinstance(job, dict):
        return None
    value = job.get("pubDate")
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return to_rfc3339_utc(int(value) * 1000)


def hiring_scope(job: Any) -> str | None:
    """`locationRestrictions`, joined, or None when the posting states none.

    **None, never "worldwide".** An empty restriction list is the most tempting
    field in this whole feed to over-read: it looks exactly like "anywhere", and
    a source that returned it for a posting whose restrictions simply were not
    filled in would make somebody eligible for a job they cannot take. Absence
    is never permission, so an empty list reaches the gate as nothing stated and
    the posting resolves UNRESOLVED.
    """
    if not isinstance(job, dict):
        return None
    restrictions = job.get("locationRestrictions")
    if isinstance(restrictions, str):
        return restrictions.strip() or None
    if not isinstance(restrictions, list):
        return None
    named = [r.strip() for r in restrictions if isinstance(r, str) and r.strip()]
    return ", ".join(named) or None


def company_of(job: Any) -> str | None:
    """The employer this posting names, for the collector to resolve."""
    if not isinstance(job, dict):
        return None
    return _text(job.get("companyName"))


def application_link(job: Any) -> str | None:
    """`applicationLink`, and it is NOT an origin pointer.

    Measured live 2026-09-07: on all 20 rows of the first page this was
    byte-identical to `guid`, which is this board's own posting page. It is
    kept because it is what the interface should link a person to, and it is
    deliberately NOT offered to ADR-0013 identity resolution -- feeding a
    board's own URL to `identify_posting_url` would resolve every posting to
    this adapter and prove nothing.

    The function is named for what the field IS rather than for what it was
    hoped to be, because a helper called `origin_url` returning a board's page
    is how a later reader ends up trusting it.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("applicationLink"))
    if raw is None:
        return None

    from urllib.parse import urlsplit

    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return raw


def canonical_url(job: Any) -> str | None:
    """`guid`, which the feed states as the posting's page on this board.

    Accepted only as HTTPS on the trusted host. A URL from a payload is
    untrusted input and reaches the interface as a link somebody clicks.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("guid"))
    if raw is None:
        return None

    from urllib.parse import urlsplit

    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.hostname != TRUSTED_HOST:
        return None
    return raw


def read_compensation(payload: Any) -> CompensationHint | None:
    """What a stored Himalayas payload says about pay. No network, no guessing.

    Only ever one band: this feed states a single currency per posting, so
    `alternate_bands` is always empty and the multi-currency grouping Ashby
    needs has nothing to do here.

    **A period outside the vocabulary yields None rather than a guess**, and a
    band with no currency yields no band at all -- nothing in this product
    converts between currencies, and an amount with no unit cannot be compared
    against a target.
    """
    if not isinstance(payload, dict):
        return None

    minimum, maximum = stated_amounts(
        parse_amount(payload.get("minSalary")), parse_amount(payload.get("maxSalary"))
    )
    if minimum is None and maximum is None:
        return None

    currency = _text(payload.get("currency"))
    if currency is None:
        # A number with no unit. Kept out of the band rather than carried with
        # a guessed currency: `min_salary` filtering refuses a target without
        # one, and a figure that cannot be compared must not look like one that
        # can.
        return None

    raw_period = payload.get("salaryPeriod")
    period = (
        COMPENSATION_PERIODS.get(str(raw_period).strip().lower()) or None if raw_period else None
    )

    return CompensationHint(
        source_field="minSalary/maxSalary",
        min_value=minimum,
        max_value=maximum,
        currency=currency.upper(),
        period=period,
        raw_text=None,
    )


def to_stub(job: Any) -> PostingStub | None:
    """One feed row into a stub, or None when it cannot be addressed.

    Four things are required and none is invented: an id, a title, a usable
    canonical URL and a description. A row missing any of them is not a posting
    this system can address, and counting it is more honest than inventing the
    missing half.
    """
    if not isinstance(job, dict):
        return None

    title = _text(job.get("title"))
    url = canonical_url(job)
    if title is None or url is None:
        return None

    description = _text(job.get("description"))
    return PostingStub(
        external_id=_external_id(job) or url,
        title=title,
        url=url,
        # The employer's hiring restriction, and NOT a place the posting is.
        # `publishes_hiring_scope` is what tells the pipeline to read it as a
        # scope; storing it here is what makes it visible on the card.
        location_raw=hiring_scope(job),
        department=None,
        posted_at=_published_at(job),
        description_html=description,
        payload=dict(job),
    )


def _external_id(job: dict[str, Any]) -> str | None:
    """The vendor's own identifier for this posting.

    `guid` is the documented stable one. The URL path's final segment is used
    when `guid` is absent, because an id derived from the vendor's own
    canonical URL is still the vendor's rather than one this adapter invented.
    """
    raw = _text(job.get("guid"))
    if raw is None:
        return None
    return raw.rstrip("/").rsplit("/", 1)[-1] or raw


@dataclass(frozen=True)
class FeedRead:
    """One walk of the feed, and what it could not reach."""

    jobs: tuple[Any, ...]
    pages: int
    stopped_early: bool
    unaddressable: int = 0
    claimed_total: int | None = None


class HimalayasProvider(JobProvider):
    """The Himalayas public jobs feed."""

    name = "himalayas"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    #: `board_identifier` names nothing here: the feed is one stream. Asking
    #: this adapter about one employer is a question in the wrong vocabulary,
    #: exactly as for WWR and Get on Board.
    addresses_boards_by_company = False
    capabilities = HIMALAYAS_CAPABILITIES
    field_map = HIMALAYAS_FIELD_MAP

    def __init__(
        self,
        fetcher: HttpFetcher,
        api_host: str = API_HOST,
        max_pages: int | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")
        #: A bound with no "unlimited" value. `providers/feeds.PageBudget`
        #: makes the same point for the paginated sources: a walk that can be
        #: asked for everything is a walk somebody will ask for everything.
        self._max_pages = 2 if max_pages is None else max(1, int(max_pages))

    # -- URLs --------------------------------------------------------------

    def feed_url(self, cursor: str | None = None) -> str:
        """One page of the feed, by cursor.

        `offset` is documented as deprecated and as capable of returning the
        same job twice, so it is not built here at all -- not defaulted, not
        supported behind a flag. A walk that re-served and skipped rows would
        produce a corpus wrong in a way no counter here could detect.
        """
        url = f"{self._api_host}/jobs/api?limit={PER_PAGE}"
        if cursor is not None:
            if not CURSOR.match(cursor):
                raise HimalayasError(
                    "the feed returned a cursor this adapter will not put in a URL. "
                    "A token from a response is untrusted input."
                )
            url = f"{url}&cursor={cursor}"
        return assert_trusted(url)

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this up by hand."""
        del board
        return f"{self._api_host}/jobs"

    # -- reading -----------------------------------------------------------

    def read_feed(self, *, use_cache: bool = True) -> FeedRead:
        """The feed, walked to its end or to the page budget.

        A failed request raises. It is never converted into an empty page: an
        empty feed and a broken request are different outcomes, and
        `providers/base.py` states why conflating them lets a network blip
        close a company's whole posting history.
        """
        collected: list[Any] = []
        seen: set[str] = set()
        cursor: str | None = None
        pages = 0
        claimed: int | None = None

        while pages < self._max_pages:
            body = self._fetcher.get_json(self.feed_url(cursor), use_cache=use_cache)
            if not isinstance(body, dict) or not isinstance(body.get("jobs"), list):
                keys = ", ".join(sorted(body)) if isinstance(body, dict) else "not an object"
                raise HimalayasError(
                    f"Himalayas returned an unexpected shape: expected {{'jobs': [...]}}, "
                    f"got [{keys}]"
                )
            pages += 1
            if claimed is None:
                total = body.get("totalCount")
                if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                    claimed = total

            page = body["jobs"]
            for job in page:
                identity = _external_id(job) if isinstance(job, dict) else None
                # The cursor is documented never to return the same job twice.
                # This is not trust in that: a feed being written to while it
                # is read is exactly where a duplicate appears, and one row
                # arriving twice must not become two jobs.
                if identity is not None and identity in seen:
                    continue
                if identity is not None:
                    seen.add(identity)
                collected.append(job)

            cursor = _text(body.get("nextCursor"))
            if cursor is None or not page:
                # The end of the feed. Distinguished from the page budget
                # below, because "there is no more" and "we chose to stop" are
                # different facts and a caller that cannot tell reports a
                # bounded read as a complete one.
                return FeedRead(
                    jobs=tuple(collected),
                    pages=pages,
                    stopped_early=False,
                    unaddressable=sum(1 for j in collected if to_stub(j) is None),
                    claimed_total=claimed,
                )

        return FeedRead(
            jobs=tuple(collected),
            pages=pages,
            stopped_early=True,
            unaddressable=sum(1 for j in collected if to_stub(j) is None),
            claimed_total=claimed,
        )

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        """This source is ONE STREAM. It cannot be asked about an employer.

        `list_postings` here does `del board` and reads the whole feed, so a
        `source_board` row per employer would fetch the entire feed once per
        employer. Measured on the real corpus 2026-09-08: 191 rows carry
        this provider with a company slug in `board_identifier`, recorded as
        PROVENANCE by the dedicated collector and read as a FETCH TARGET by the
        generic one. That is 191 identical full-feed reads against somebody
        else's server for one run.

        `addresses_boards_by_company` has said False since this adapter was
        written. This is that declaration made answerable before the run.
        """
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Himalayas can be asked for: this source is one "
            f"feed, not a board per employer. Collect it with `career-agent collect-himalayas`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings from the feed, bounded by this adapter's page budget."""
        del board
        for job in self.read_feed().jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it, body included.

        No second request: the feed carries the whole advert. `html_to_text` is
        the same normaliser every other adapter's body goes through, so a
        Himalayas description and a Greenhouse one reach the matcher rendered
        identically.
        """
        del board
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html) if html else "",
            payload=dict(stub.payload),
        )
