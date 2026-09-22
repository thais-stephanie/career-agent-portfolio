"""Recruiterflow, the ATS behind a whole class of Latin American agencies.

WHY THIS ONE, OUT OF SIX BOARDS THAT WERE ASKED ABOUT
------------------------------------------------------
Six job boards were handed over for evaluation on 2026-09-09. **Two of them
turned out to be the same thing**: `hireinsouth.com/jobs` and
`hirelatam.com/jobs/` both embed a Recruiterflow careers page and nothing else.
That is what makes this worth an adapter rather than a scraper -- it is a
FAMILY, addressed by agency slug, the same shape as Greenhouse, Lever and
Ashby, so `discover-boards` gains a fourth provider to probe and every further
agency on it costs a registry row rather than a module.

WHAT IT ADDS THAT THIS CORPUS LACKS
-------------------------------------
Not more engineering roles. Measured over the two boards on 2026-09-09:

    South      68 postings, 32 naming Brazil. Marketing 13, Sales 12,
               Accounting 7, Administration 4, Human Resources 3,
               Customer Support 2 -- and Software Development 3.
    HireLatam  89 postings. Operations 23, Sales 22, Marketing 18,
               Accounting and Finance 6, Customer Service 4.

These are United States companies hiring REMOTELY ACROSS LATIN AMERICA, which
is the exact shape of "a Brazilian looking for international work" and the
thing this corpus had least of. One posting in the captured fixture is an
`Executive Assistant`, remote, open in Brazil.

AUTHORISATION, READ FIRST-PARTY 2026-09-09
--------------------------------------------
`recruiterflow.com/robots.txt` is `User-agent: *` with `Allow: /`, and then
**names this reader and permits it**:

    User-agent: Anthropic
    Allow: /

    User-agent: Claude
    Allow: /

That is the OPPOSITE of Remote OK and Get on Board, which name `ClaudeBot` with
`Disallow: /` and are therefore never called from this repository. Here the
fixtures below were captured live.

`recruiterflow.com/llms.txt` carries a licence -- "AI models may use this
content only for factual reference. Commercial redistribution is prohibited."
-- attached to a list of the vendor's own MARKETING pages. This product is
local, personal and non-commercial, shows a candidate a posting and links back
to the vendor's own page, and redistributes nothing. Recorded because the
sentence exists, not because it is in doubt.

TWO REQUESTS PER POSTING, AND THE FIRST ONE IS FREE
-----------------------------------------------------
The board page carries `window.jobsList`, a JSON blob holding every opening
grouped three ways -- by department, by group and by LOCATION -- with
`job_id`, `job_name`, `details`, `employment_type`, `remote_type` and
`last_opened` on each row. So the whole list costs ONE request.

The advert is not in it. Each detail page publishes a schema.org `JobPosting`
in `application/ld+json`, the same contract Programathor uses, carrying the
full `description`, `datePosted`, `employmentType` and `jobLocationType`. So a
posting costs its own request, exactly as Programathor's does.

Parse that JSON-LD with `strict=False`. The vendor writes real newlines inside
`description`, and a strict read finds nothing on a page that has everything --
which is precisely the trap Programathor set first.

WHERE THE WORK IS, AND WHO DECIDES
------------------------------------
`details` reads `Brasilia, Brazil, San Salvador, El Salvador, Bogota,
Colombia`, and on a REMOTE posting that is an employer naming the countries it
will hire from. It is tempting to declare `publishes_hiring_scope=True` on the
strength of it, and this adapter does NOT.

The reason is that the same field on a non-remote posting is an office, and one
column cannot mean two things. `gates.structured_geography` already decides
exactly this, from `remote_type` and the resolved place, and it was built and
tested in V1.5 for boards in this shape. Declaring a hiring scope here would
route around a decision the product already makes correctly.

`Apply to our Talent Pool` rows carry no `remote_type` at all and are not
openings. They are collected like everything else -- what a person wants to see
belongs to the filter layer, not to a collector -- and nothing here invents a
work model for them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
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
    RetrievalMode,
)

PROVIDER = "recruiterflow"

HOST = "recruiterflow.com"

#: The blob the board page defines, and the only thing this adapter reads from
#: that page. Non-greedy to the first `};`, which is how the vendor writes it.
_JOBS_LIST = re.compile(r"window\.jobsList\s*=\s*(\{.*?\});", re.S)

_LD_JSON = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.S | re.I
)

#: An agency slug is a path segment and arrives from our own registry, never
#: from a response. Guarded anyway: a registry row is still a value somebody
#: typed, and `../` in a path segment is how a typo becomes a different site.
_SLUG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}$")


class RecruiterflowError(ValueError):
    """This adapter refused to do something, and says which."""


RECRUITERFLOW_FIELD_MAP = ProviderFieldMap(
    mappings=(
        #: `Remote` on the vendor's own field. Absent on talent-pool rows,
        #: which resolves to nothing rather than to a value we invented.
        FieldMapping(path="remote_type", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="employment_type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
        #: `details` is DELIBERATELY UNMAPPED. It is where the work is, and the
        #: only location dimension means "where the employer may hire".
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise RecruiterflowError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def board_slug(board: BoardRef) -> str:
    slug = (board.board_identifier or "").strip()
    if not _SLUG.match(slug):
        raise RecruiterflowError(f"not a Recruiterflow agency slug: {slug!r}")
    return slug


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def read_jobs_list(html: str) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Every opening on a board page, with the department it sat under.

    The blob groups the SAME postings three ways -- by department, by group and
    by location -- so reading more than one grouping would return each posting
    several times. Only `department` is read, and it is read for the department
    NAME as much as for the rows: it is the only place this source states one.
    """
    found = _JOBS_LIST.search(html)
    if not found:
        raise RecruiterflowError(
            "the board page defined no `window.jobsList`. That is how this vendor "
            "publishes a board, so an absence is a changed contract rather than an "
            "empty board, and must not be read as one."
        )
    try:
        blob = json.loads(found.group(1), strict=False)
    except ValueError as exc:
        raise RecruiterflowError(f"`window.jobsList` was not readable JSON: {exc}") from exc

    grouped = blob.get("department")
    if not isinstance(grouped, list):
        raise RecruiterflowError("`window.jobsList` carried no `department` grouping")

    rows: list[tuple[str, dict[str, Any]]] = []
    for pair in grouped:
        if not isinstance(pair, list) or len(pair) != 2:
            continue
        department, entries = pair
        if not isinstance(entries, list):
            continue
        name = department.strip() if isinstance(department, str) else ""
        for entry in entries:
            if isinstance(entry, dict):
                rows.append((name, entry))
    return tuple(rows)


def posting_url(slug: str, job_id: Any) -> str | None:
    """The vendor's own posting page, which is the only Apply target here."""
    if isinstance(job_id, bool) or not isinstance(job_id, int):
        return None
    return f"https://{HOST}/{slug}/jobs/{job_id}"


def to_stub(slug: str, department: str, record: Any) -> PostingStub | None:
    """One row of the board blob as a stub, or None when it cannot be addressed."""
    if not isinstance(record, dict):
        return None
    title = _text(record, "job_name")
    url = posting_url(slug, record.get("job_id"))
    if not title or not url:
        return None
    payload = dict(record)
    #: Carried so the field map and the archived payload both see it, and so a
    #: rescore can reconstruct the department without re-reading the board.
    payload["department"] = department or None
    return PostingStub(
        external_id=f"{PROVIDER}-{slug}-{record['job_id']}",
        title=title,
        url=url,
        #: WHERE THE WORK IS. `gates.structured_geography` decides whether that
        #: is an office or a hiring region, from `remote_type`.
        location_raw=_text(record, "details"),
        department=department or None,
        posted_at=to_rfc3339_utc(record.get("last_opened")),
        #: Not in the listing. The detail page carries it.
        description_html=None,
        payload=payload,
    )


def read_posting_ld(html: str) -> dict[str, Any] | None:
    """The schema.org `JobPosting` on a detail page, or None.

    `strict=False` is REQUIRED and is the same trap Programathor set: the
    vendor writes real newlines inside `description`, and a strict read finds
    nothing on a page that has everything.
    """
    for block in _LD_JSON.findall(html):
        try:
            parsed = json.loads(block.strip(), strict=False)
        except ValueError:
            continue
        for item in parsed if isinstance(parsed, list) else [parsed]:
            if isinstance(item, dict) and item.get("@type") == "JobPosting":
                return item
    return None


@dataclass(frozen=True)
class BoardRead:
    """One board page, read once."""

    rows: tuple[tuple[str, dict[str, Any]], ...]
    departments: tuple[str, ...]


class RecruiterflowProvider(JobProvider):
    """One careers page per agency, addressed by the agency's own slug."""

    name = PROVIDER
    #: An ATS, not an aggregator. The posting originates in this agency's own
    #: Recruiterflow account and the agency wrote it, so `access_method`
    #: reporting `ats_structured` is accurate rather than flattering.
    kind = ProviderKind.ATS
    retrieval_mode = RetrievalMode.BOARD_EXHAUSTIVE
    #: TRUE, and this is the point of building it as a family: `discover-boards`
    #: gains a fourth provider to probe, so every further agency costs a
    #: registry row rather than a module.
    addresses_boards_by_company = True
    field_map = RECRUITERFLOW_FIELD_MAP
    capabilities = ProviderCapabilities(
        #: The listing carries titles, places and dates and NO advert.
        full_description_in_list=False,
        #: One request per posting obtains the whole thing.
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        #: No salary field anywhere in the blob or the JSON-LD.
        exposes_compensation=False,
        exposes_location_structured=False,
        exposes_remote_flag=True,
        exposes_employment_type=True,
        #: FALSE, on purpose. See the module docstring: `details` is where the
        #: work is, and `structured_geography` is what reads it as a scope.
        publishes_hiring_scope=False,
    )

    def __init__(self, fetcher: HttpFetcher) -> None:
        self._fetcher = fetcher

    # -- URLs --------------------------------------------------------------

    def board_url(self, board: BoardRef) -> str:
        """The human-facing board page. **VALIDATES NOTHING**, on purpose.

        This built the URL through `board_slug`, which REFUSES an identifier
        that could not be a slug -- and that broke fourteen tests in one line,
        because `discover._public_prefixes` calls this method with the sentinel
        `\x00probe\x00` to learn what this provider's board URLs LOOK LIKE. It
        is asking about the shape, not about a company, and every sibling
        adapter answers by formatting a string.

        Refusing there took `identifier_from_url` out entirely, so a board URL
        pasted by a person stopped being recognised for ANY provider -- which
        is the strongest evidence discovery can be handed.

        The refusal itself is right and it lives in the two places that reach a
        socket: `validate_board`, which the collection plan asks before the run
        begins, and `list_postings`, which validates before it fetches.
        """
        return f"https://{HOST}/{board.board_identifier}/jobs"

    def validate_board(self, board: BoardRef) -> str | None:
        """Refuse a slug that could not be one, without opening a socket."""
        try:
            board_slug(board)
        except RecruiterflowError as exc:
            return str(exc)
        return None

    # -- reading -----------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every opening on the board.

        Raises `FetchError` on failure. A board that legitimately has no
        openings yields nothing and does NOT raise -- the same difference every
        ATS adapter here turns on, because it is what keeps the closing logic
        safe.
        """
        slug = board_slug(board)
        url = assert_trusted(f"https://{HOST}/{slug}/jobs")
        html = self._fetcher.get_text(url)
        try:
            rows = read_jobs_list(html)
        except RecruiterflowError as exc:
            #: A CHANGED CONTRACT, not an empty board. Raised as a fetch error
            #: so the collector records it and closes NOTHING.
            raise FetchError(FetchErrorCategory.MALFORMED, url, str(exc)) from exc

        for department, record in rows:
            stub = to_stub(slug, department, record)
            if stub is None:
                # COUNTED, not silent. See `JobProvider.postings_skipped`.
                self.postings_skipped += 1
                continue
            yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The advert, from the posting's own page.

        The JSON-LD is merged UNDER the listing row rather than over it. The
        row is what the field map reads, and `employmentType: FULL_TIME` from
        schema.org and `employment_type: Full time` from the blob are the same
        fact in two vocabularies -- keeping both, and mapping only the row's,
        means the archived payload stays complete without two paths resolving
        to one dimension twice.
        """
        del board
        html = self._fetcher.get_text(assert_trusted(stub.url))
        posting = read_posting_ld(html)
        description_html = (posting or {}).get("description") or ""
        if not isinstance(description_html, str):
            description_html = ""

        payload = dict(stub.payload)
        if posting is not None:
            payload["posting"] = posting

        return RawPosting(
            stub=stub,
            description_html=description_html,
            description_text=strip_html(description_html),
            payload=payload,
        )


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """The advert as text, with paragraph breaks kept.

    The same reduction every sibling adapter performs. A requirements list
    collapsed onto one line makes the employer's sentences harder to read than
    the employer wrote them, and ADR-0002 verifies every evidence quote against
    exactly this text.
    """
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n", text)
    text = _TAG.sub("", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()
