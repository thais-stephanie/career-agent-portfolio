"""Workday CxS adapter: one company's board, reached the way its own page does.

WHY THIS EXISTS NOW AND NOT BEFORE
----------------------------------
Two things had to happen first, and neither was writing code.

The permission question was settled by ADR-0018 on 2026-09-09. No first-party
page documents `/wday/cxs/...`; the documented Workday API is authenticated
SOAP; this endpoint answers without authentication and no `robots.txt` forbids
it. The old catalogue row read that silence as a refusal, which was invariant 2
applied to the wrong question. Nothing here bypasses authentication, a CAPTCHA,
an access control, an explicit block or a rate limit.

The IDENTITY question was settled by `providers/site_detect.py` on 2026-09-10. A
Workday board is not a slug: it is a tenant, a numbered data centre AND a site,
and `purple` / `purple.wd1` / `purple.wd1/purplecareers` are three different
amounts of knowledge. A board identifier here is therefore never guessed from a
company name -- it comes from a careers page the employer published, which is
the same rule ADR-0013 applies to a posting's identity. That is why
`identifier_is_guessable` is False on this adapter and True on every other
board family: `discover` may probe `acme` against Greenhouse, and asking
Workday the same question can only ever produce an invented tenant.

THE CONTRACT, AS MEASURED
-------------------------
Measured 2026-09-05 against two real tenants and written up in
`docs/product/source-evidence-2026-09-05.md`. Every number below is from that
measurement rather than from documentation, because there is no documentation.

    POST {host}/wday/cxs/{tenant}/{site}/jobs     the listing, 20 at a time
    GET  {host}/wday/cxs/{tenant}/{site}/job{path}   one posting, with its body

The POST is a READ. Its response holds `total`, `jobPostings`, `facets` and
`userAuthenticated: false`; there is no created resource, no `Location` header
and no session cookie, and three identical requests returned byte-identical
bodies.

THREE PAGINATION TRAPS, AND THEY ARE WHY THIS IS NOT A LOOP
-----------------------------------------------------------
1. **`total` is only true on the first page.** `offset=20` and `offset=1980`
   both returned twenty real, distinct postings beside `"total": 0`. So the
   bound is computed once, from the first page, and later pages' totals are
   never read.

2. **`offset >= total` silently returns page zero again.** `offset=2000` and
   `offset=9980` each returned the byte-identical body of `offset=0`. A walk
   that stops when a page comes back EMPTY therefore never terminates and
   re-ingests the first twenty postings forever. This walk stops on the
   computed bound, and stops again if a page repeats the first page's ids --
   belt and braces, because the consequence of getting it wrong is an infinite
   collection rather than a missing posting.

3. **`limit` above 20 is REFUSED, not clamped.** `limit=21` returns HTTP 400.
   `PAGE_SIZE` is 20 and is not a tuning knob.

WHAT THIS PROVIDER DOES NOT PUBLISH
-----------------------------------
No CxS field states a HIRING SCOPE. `jobPostingInfo.country` is where the
requisition is, which invariant 3 keeps separate from where the employer may
hire, so `publishes_hiring_scope` stays False and the geography gate reads the
description like it does for every ATS.

`postedOn` in the listing is relative TEXT -- "Posted Today" -- and is archived
unmapped rather than parsed into a date this program would have invented. The
only real date is `jobPostingInfo.startDate` on the detail response.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
)

#: The host a tenant's board lives on. Both spellings are real and the vendor
#: uses them interchangeably; `site_detect` recognises each, and an identifier
#: carries the data centre so the host is built rather than guessed.
HOST_TEMPLATE = "https://{tenant}.{shard}.myworkdayjobs.com"

#: 20, because 21 is a 400. See trap three.
PAGE_SIZE = 20

#: `{tenant}.{shard}/{site}`, which is exactly the string
#: `site_detect.DetectedBoard.identity` produces for a resolved Workday board.
#: Anything else is rejected WITHOUT a request: a board identifier that does not
#: carry all three parts is knowledge this adapter does not have, and the one
#: thing it must never do is fill the gap in.
IDENTIFIER = re.compile(
    r"\A(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.(?P<shard>wd\d+)/(?P<site>[A-Za-z0-9_-]+)\Z",
    re.IGNORECASE,
)

#: A public posting URL, for ADR-0013 identity. `{host}/{locale}/{site}/job/...`
#: and `{host}/{site}/job/...` are both served; the locale segment is optional
#: and is not part of the identity.
POSTING_URL = re.compile(
    r"\Ahttps?://(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.(?P<shard>wd\d+)\."
    r"(?:myworkdayjobs|myworkdaysite)\.com/"
    r"(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?"
    # `/apply` is the same posting one click further (RemoteSource links the
    # apply page on 80 of 80 Workday origins measured 2026-09-12), so it is
    # accepted and stripped: `externalPath` never carries it.
    r"(?P<site>[A-Za-z0-9_-]+)(?P<path>/job/[^?#]+?)(?:/apply)?/?(?:[?#].*)?\Z",
    re.IGNORECASE,
)

WORKDAY_CAPABILITIES = ProviderCapabilities(
    # The listing carries `title`, `externalPath`, `locationsText`, `postedOn`
    # and `bulletFields`, and NO description. Every posting costs its own
    # request, which is a fact about the vendor and is what this flag is for.
    full_description_in_list=False,
    # And the detail response does carry the whole advert: `jobPostingInfo.
    # jobDescription` was 4,471 characters of HTML in the sampled posting.
    obtains_full_description=True,
    # `jobPostingInfo.startDate`, a real ISO date. NOT the listing's `postedOn`,
    # which is the words "Posted Today".
    exposes_posted_date=True,
    # No field in either response names a department, a team or an org unit.
    exposes_department=False,
    # And none names pay. Workday customers put compensation in the description
    # when they publish it at all, so a missing value here is the vendor having
    # nowhere to put one -- which is the distinction this flag exists to keep.
    exposes_compensation=False,
    # `locationsText` is free text ("Santa Clara, CA and 2 more"), and the
    # detail's `country` is one field rather than a structured address. The flag
    # asserts SHAPE, and the shape is not structured.
    exposes_location_structured=False,
    exposes_remote_flag=False,
    # `jobPostingInfo.timeType`: "Full time" / "Part time".
    exposes_employment_type=True,
)

WORKDAY_FIELD_MAP = ProviderFieldMap(
    (
        # Free text, and the only location assertion the LISTING makes. It
        # travels as stated: "Santa Clara, CA and 2 more" is what the employer
        # published and normalising it is M3's job, not this module's.
        FieldMapping("locationsText", MetadataDimension.HIRING_LOCATION_HINT),
        # The requisition's country, from the detail response. NOT a hiring
        # scope -- see the note at the top of this module.
        FieldMapping("jobPostingInfo.country", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping("jobPostingInfo.timeType", MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)

# Archived but unmapped, each for a stated reason:
#   postedOn        - relative text, not a date. Parsing "Posted 30+ Days Ago"
#                     into a timestamp would be this program inventing one
#   bulletFields    - per-tenant customer-defined fields; on the sampled
#                     tenants they held the requisition id, which already
#                     travels as `jobRequisitionId`
#   facets          - per TENANT, with no stable cross-tenant vocabulary. The
#                     first Salesforce facet is a 70-character customer field
#   externalUrl     - the public URL, which `PostingStub.url` already carries
#   jobReqId        - a synonym of `jobRequisitionId` in the same response


class WorkdayProvider(JobProvider):
    """One Workday board, identified by tenant, data centre and site."""

    name = "workday"
    kind = ProviderKind.ATS
    capabilities = WORKDAY_CAPABILITIES
    field_map = WORKDAY_FIELD_MAP

    #: A board per employer, so `collect` walks it like the other four.
    addresses_boards_by_company = True

    #: AND NOT GUESSABLE, which is the new half. `discover` probes a company
    #: slug against every board family; here that would mean inventing a tenant
    #: and a site and reading whatever answered as evidence. A Workday board
    #: reaches the registry from `scan-career-sites`, which reads what the
    #: employer published.
    identifier_is_guessable = False

    def __init__(self, fetcher: HttpFetcher) -> None:
        self._fetcher = fetcher

    # -- identity ----------------------------------------------------------

    def _parts(self, board: BoardRef) -> tuple[str, str, str]:
        match = IDENTIFIER.match(board.board_identifier.strip())
        if match is None:
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                self.name,
                f"{board.board_identifier!r} is not a Workday board identity. "
                "Expected tenant.shard/site, such as acme.wd1/acmecareers.",
            )
        return (
            match.group("tenant").lower(),
            match.group("shard").lower(),
            match.group("site"),
        )

    def validate_board(self, board: BoardRef) -> str | None:
        """Why this row cannot be asked about, answered with no request.

        A Workday identity has three parts and a row holding fewer is a row
        nothing can resolve. Refusing here means `collect` rejects the plan
        entry before a socket opens and carries on with every other board,
        rather than raising in the middle of a run.
        """
        if IDENTIFIER.match(board.board_identifier.strip()) is None:
            return (
                f"{board.board_identifier!r} does not name a Workday tenant, data centre "
                "and site. A Workday board identity is published by the employer's own "
                "careers page and is never derived from a company name."
            )
        return None

    def _host(self, board: BoardRef) -> str:
        tenant, shard, _ = self._parts(board)
        return HOST_TEMPLATE.format(tenant=tenant, shard=shard)

    def _cxs_base(self, board: BoardRef) -> str:
        tenant, _, site = self._parts(board)
        return f"{self._host(board)}/wday/cxs/{tenant}/{site}"

    def board_url(self, board: BoardRef) -> str:
        if board.board_url:
            return board.board_url
        _, _, site = self._parts(board)
        return f"{self._host(board)}/{site}"

    def jobs_url(self, board: BoardRef) -> str:
        return f"{self._cxs_base(board)}/jobs"

    def posting_url(self, board: BoardRef, external_path: str) -> str:
        _, _, site = self._parts(board)
        return f"{self._host(board)}/{site}{external_path}"

    def detail_url(self, board: BoardRef, external_path: str) -> str:
        """The machine endpoint for one posting.

        The measurement records this as `<base>/job<externalPath>`, and the
        guard below is why it is not written as that string. `externalPath` is
        the vendor's own value and on the sampled tenants it already BEGINS
        `/job/`; concatenating blindly would ask for `/job/job/...`. Inserting
        the segment only when it is absent keeps both observed shapes working,
        and neither one is a guess about a value we were given.
        """
        path = external_path if external_path.startswith("/") else f"/{external_path}"
        if not path.startswith("/job/"):
            path = f"/job{path}"
        return f"{self._cxs_base(board)}{path}"

    # -- listing -----------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every posting this board will serve, read twenty at a time.

        Raises `FetchError` on failure. A board with no openings answers 200
        with an empty `jobPostings` and a `total` of zero, and yields nothing
        without raising -- the difference that keeps the closing logic safe.

        The walk is bounded by the FIRST page's `total` for the reason at the
        top of this module, and it stops early if a later page hands back the
        first page's postings, which is what trap two looks like from here.
        """
        url = self.jobs_url(board)
        bound: int | None = None
        first_ids: set[str] = set()
        offset = 0

        while True:
            payload = self._page(board, offset)
            postings = payload.get("jobPostings")
            if not isinstance(postings, list):
                raise FetchError(
                    FetchErrorCategory.MALFORMED,
                    url,
                    "response did not contain a 'jobPostings' list",
                )
            if bound is None:
                # READ ONCE, from page one, and never again. On every later page
                # this number is zero and reading it would end the walk while
                # holding twenty postings.
                bound = _total(payload)
            if not postings:
                return

            paths = {
                str(entry.get("externalPath") or "")
                for entry in postings
                if isinstance(entry, dict)
            }
            if offset and paths and paths == first_ids:
                # TRAP TWO, caught rather than trusted. `offset >= total` serves
                # page zero again, so without this the bound being wrong by one
                # page would mean collecting the same twenty postings forever.
                return
            if not offset:
                first_ids = paths

            for entry in postings:
                stub = self._to_stub(entry, board)
                if stub is None:
                    # COUNTED, not silent. See `JobProvider.postings_skipped`.
                    self.postings_skipped += 1
                    continue
                yield stub

            offset += PAGE_SIZE
            if offset >= bound:
                return

    def _page(self, board: BoardRef, offset: int) -> dict[str, Any]:
        url = self.jobs_url(board)
        body = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": offset, "searchText": ""}
        payload = self._fetcher.post_json(
            url,
            body,
            # No credential is anywhere near this request, so the safe URL is
            # the real one. It is passed explicitly because `post_json` requires
            # it: the method exists partly for a vendor that puts a key in the
            # path, and an implicit default there would be the kind of
            # convenience that leaks one.
            safe_url=url,
            cache_key=f"{url}#offset={offset}&limit={PAGE_SIZE}",
        )
        if not isinstance(payload, dict):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "response was not a JSON object")
        return payload

    def _to_stub(self, entry: Any, board: BoardRef) -> PostingStub | None:
        """Translate one listing entry. A single unusable one is skipped.

        Losing one malformed posting is better than losing a company, which is
        the rule every adapter here follows.
        """
        if not isinstance(entry, dict):
            return None
        title = entry.get("title")
        external_path = str(entry.get("externalPath") or "").strip()
        if not title or not external_path:
            return None

        locations = entry.get("locationsText")
        return PostingStub(
            # THE VENDOR'S OWN PATH, which is stable per board and is what a
            # public posting URL is built from -- so ADR-0013 can recover this
            # id from a URL somebody pasted. The requisition id would read
            # better and lives only on the detail response, which the listing
            # stage has not fetched.
            external_id=external_path,
            title=str(title),
            url=self.posting_url(board, external_path),
            location_raw=str(locations).strip() or None if locations else None,
            # No org unit in either response. See the capability note.
            department=None,
            # NOT `postedOn`: it is the words "Posted Today". The real date
            # arrives with the description.
            posted_at=None,
            description_html=None,
            payload=entry,
        )

    # -- completing a posting ---------------------------------------------

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """One request per posting, because the listing carries no description."""
        url = self.detail_url(board, stub.external_id)
        payload = self._fetcher.get_json(url)
        if not isinstance(payload, dict):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "response was not a JSON object")
        info = payload.get("jobPostingInfo")
        info = info if isinstance(info, dict) else {}

        html = str(info.get("jobDescription") or "")
        merged = {**stub.payload, "jobPostingInfo": info}
        return RawPosting(
            # The stub is replaced rather than edited, because the date is only
            # knowable here and a posting persisted without it would be dated
            # from `first_seen_at` for the rest of its life.
            stub=PostingStub(
                external_id=stub.external_id,
                title=stub.title,
                url=str(info.get("externalUrl") or stub.url),
                location_raw=stub.location_raw,
                department=stub.department,
                posted_at=to_rfc3339_utc(info.get("startDate")),
                description_html=html,
                payload=merged,
            ),
            description_html=html,
            description_text=html_to_text(html),
            payload=merged,
        )


def _total(payload: dict[str, Any]) -> int:
    """The first page's `total`, as a number, with no invented fallback.

    A response that omits it or writes nonsense into it bounds the walk at ONE
    page rather than at a number this module chose. Trap two makes the opposite
    mistake unbounded: guessing high means walking page zero forever.
    """
    raw = payload.get("total")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return PAGE_SIZE
    value = int(raw)
    return value if value > 0 else PAGE_SIZE


def recognise_posting_url(url: str) -> str | None:
    """This board's native id for a posting URL, or None if it is not one.

    ADR-0013: a posting keeps ONE authoritative origin, recovered from the
    pointer the provider published rather than from a fuzzy match. The id
    returned is `externalPath`, which is what `_to_stub` stores, so a URL
    somebody pastes resolves to the row that is already here.
    """
    match = POSTING_URL.match(url.strip())
    if match is None:
        return None
    return match.group("path")


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(tenant.shard/site, externalPath)` for a public posting URL, or None.

    All three parts of the identity are in the URL or the URL is not one of
    ours: the tenant and the shard are the host, the site is the first
    non-locale path segment. A URL that stops at the host names the tenant
    and nothing else, and this returns None for it rather than a two-thirds
    identity -- the rule `IDENTIFIER` and `validate_board` already enforce.
    """
    match = POSTING_URL.match(url.strip())
    if match is None:
        return None
    identifier = (
        f"{match.group('tenant').lower()}.{match.group('shard').lower()}/{match.group('site')}"
    )
    # `externalPath` is the `/job/...` tail, exactly as `_to_stub` stores it.
    return identifier, match.group("path")
