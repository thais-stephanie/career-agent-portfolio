"""Remotive, read from the public jobs API it documents for developers.

AUTHORISATION, AND WHY THE ROBOTS FILE IS NOT THE LAST WORD
------------------------------------------------------------
`remotive.com/robots.txt`, read 2026-09-11, carries `Disallow: /api/*` under
`User-agent: *`. Read alone, that closes the path this adapter uses, and the
Career-Ops provider matrix recorded it as `DO_NOT_PORT` on 2026-09-07 for
exactly that reason.

The same day, the same host, on its own page `remotive.com/remote-jobs/api`
and in the README it links (`github.com/remotive-com/remote-jobs-api`), says:

    "Please note that API documentation and access is granted so that
     developers can share our jobs further."

and repeats the grant, with its conditions, inside EVERY response envelope as
`0-legal-notice`. That is the Jobicy precedent: an affirmative first-party
grant for a specific programmatic use, stronger and more specific than a
crawler directive aimed at search engines. Torre stays off because it merely
fails to forbid; this vendor says yes in writing, on the endpoint itself.

THE CONDITIONS, AND WHERE EACH IS IMPLEMENTED
----------------------------------------------
1. "Please link back to the URL found on Remotive AND mention Remotive as a
   source" -- `canonical_url` is the only Apply target this adapter produces
   and `ATTRIBUTION` is what the interface shows beside every posting.
2. "Please do not submit Remotive jobs to third Party websites" -- this
   product sends nothing anywhere; `docs/PRIVACY.md`.
3. "you only need to GET Remotive job data ... a couple of times a day (we
   advise max. 4 times a day) ... excessive requests (more than 2x per
   minute) will be blocked" -- `MIN_SECONDS_BETWEEN_POLLS` is six hours,
   provider-wide, measured from the last ATTEMPT out of `pipeline_run`.
4. "Displaying our jobs in order to collect signups/email addresses ...
   constitutes a breach" -- there is no signup anywhere in this product.

WHAT THE WINDOW HOLDS
---------------------
Measured 2026-09-11, one request: `job-count` 18, `total-job-count` 18. The
rest of the vendor's inventory sits behind its paid "Unlock All Jobs"
product, which this adapter does not and must not reach for. Eighteen
postings, whole adverts (median 6,106 characters of HTML), a `salary` string
on ten, and -- the reason the source is worth eighteen rows --
`candidate_required_location` on every one: `Worldwide` on six, `LATAM,
Europe, USA, Canada, APAC` on three, `Europe` on two, `USA` on two.

`candidate_required_location` IS A HIRING SCOPE. The README documents it as
"Geographical restriction for the remote candidate, if any". It is named for
the restriction and documented as one, which is the standard Himalayas met and
Working Nomads' bare `location` did not. `publishes_hiring_scope` is True.

`limit` is documented and was measured IGNORED: `?limit=3` returned all 18.
So no parameter is sent, the whole window is read in one request, and the
envelope's `job-count` is checked against the rows rather than trusted.
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
)

API_HOST = "https://remotive.com"

#: The one host this adapter will talk to, and the one host a canonical URL may
#: point at. A URL out of a payload is untrusted input that ends up as a link
#: somebody clicks.
TRUSTED_HOST = "remotive.com"

FEED_PATH = "/api/remote-jobs"

#: What the interface must show beside anything from this source. Half of the
#: licence, repeated in every response envelope.
ATTRIBUTION = "Remotive"

#: The size cap on one response, in bytes. The measured window was 209,193
#: bytes; 8 MB leaves room for a fuller feed and still refuses a response that
#: has stopped being the thing this adapter agreed to read.
MAX_BYTES = 8 * 1024 * 1024

#: Seconds between list requests, provider-wide. "We advise max. 4 times a
#: day": six hours, checked by the collector before a socket is opened.
MIN_SECONDS_BETWEEN_POLLS = 6 * 3600

#: `job_type` as the feed spells it. `full_time` is not CLT and is not read as
#: a Brazilian regime; it is an employment type hint and nothing more.
EMPLOYMENT_TYPES: dict[str, str] = {
    "full_time": "FULL_TIME",
    "part_time": "PART_TIME",
    "contract": "CONTRACT",
    "freelance": "CONTRACT",
    "internship": "INTERNSHIP",
}


REMOTIVE_CAPABILITIES = ProviderCapabilities(
    # `description` is the whole advert, in the list response, in one request.
    full_description_in_list=True,
    obtains_full_description=True,
    # `publication_date`, ISO 8601 with no offset. See `published_at`.
    exposes_posted_date=True,
    # `category` is the board's taxonomy, not the employer's team.
    exposes_department=False,
    # `salary` is a STRING the vendor composed ("OTE $25k - $35k"). It is read
    # by the same anchored parser the description fallback uses, so a band is
    # only ever produced when a currency and figures are actually there.
    exposes_compensation=True,
    exposes_location_structured=False,
    # Every posting on this board is remote by admission policy, not by an
    # employer's statement about this role. Same False as WWR and Jobicy.
    exposes_remote_flag=False,
    exposes_employment_type=True,
    # **True, and it is why this source was built.** Documented first-party as
    # "Geographical restriction for the remote candidate, if any".
    publishes_hiring_scope=True,
)


REMOTIVE_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(
            path="candidate_required_location", dimension=MetadataDimension.HIRING_LOCATION_HINT
        ),
        FieldMapping(path="job_type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class RemotiveError(ValueError):
    """A response, or a configured value, this adapter will not use."""


class RemotiveCoolingDown(RemotiveError):
    """A list request came round again inside the interval the grant advises."""


def assert_trusted(url: str) -> str:
    """Refuses anything that is not HTTPS on the one host this adapter reads."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise RemotiveError(f"Remotive URL must be https: {url!r}")
    if parts.hostname != TRUSTED_HOST:
        raise RemotiveError(f"untrusted Remotive host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def next_allowed_at(last_attempt_at: float | None) -> float:
    """The earliest epoch second at which another list request is permitted.

    Measured from the last ATTEMPT, not the last success: a request that
    failed still consumed one against somebody else's server.
    """
    if last_attempt_at is None:
        return 0.0
    return float(last_attempt_at) + MIN_SECONDS_BETWEEN_POLLS


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def hiring_scope(job: Any) -> str | None:
    """`candidate_required_location`, as the vendor wrote it, or None.

    `Worldwide` here is NOT a default: the README documents the field as the
    restriction "if any", and the measured window states it beside narrower
    values like `Europe` and `USA` on the same day. It is passed through and
    `match/places.py` resolves it. An empty value is nothing stated.
    """
    if not isinstance(job, dict):
        return None
    return _text(job.get("candidate_required_location"))


def company_of(job: Any) -> str | None:
    """The employer this posting names, for the collector to resolve."""
    if not isinstance(job, dict):
        return None
    return _text(job.get("company_name"))


def canonical_url(job: Any) -> str | None:
    """`url`, the posting's page on Remotive, validated before it is used.

    The ONLY Apply target this adapter produces, which is a licence condition:
    "link back to the URL found on Remotive".
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("url"))
    if raw is None:
        return None
    try:
        return assert_trusted(raw)
    except RemotiveError:
        return None


def external_id(job: Any) -> str | None:
    """`id`, the vendor's own integer, as a string."""
    if not isinstance(job, dict):
        return None
    value = job.get("id")
    if isinstance(value, bool) or not isinstance(value, int | str):
        return None
    text = str(value).strip()
    return text if text.isdigit() else None


def published_at(job: Any) -> str | None:
    """`publication_date`, an ISO 8601 stamp WITHOUT an offset.

    The vendor writes `2026-09-08T21:47:54` and documents no zone. It is
    stored as UTC and said so here: the alternative, refusing the date, would
    make every posting invisible to "since you last looked", and a
    publication time a few hours off is a smaller wrong than no time at all.
    A stamp that carries an offset is honoured as written.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("publication_date"))
    if raw is None:
        return None
    from datetime import UTC, datetime

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def read_compensation(payload: Any) -> CompensationHint | None:
    """What a stored Remotive payload says about pay. No network, no guessing.

    `salary` is prose the vendor composed. It goes through `match.pay.read_pay`,
    the anchored reader the description fallback uses, so `$60,000 - $80,000`
    becomes a band and `Competitive` becomes nothing. A figure with no
    currency yields no band: nothing here converts, and an amount with no unit
    cannot be compared against a target.
    """
    if not isinstance(payload, dict):
        return None
    raw = _text(payload.get("salary"))
    if raw is None:
        return None
    from career_agent.match.pay import read_pay

    # The reader anchors on a pay word; the field name is that anchor.
    reading = read_pay(f"Salary: {raw}")
    if reading is None or reading.currency is None:
        return None
    return CompensationHint(
        source_field="salary",
        min_value=reading.min_value,
        max_value=reading.max_value,
        currency=reading.currency,
        period=reading.period,
        raw_text=raw,
    )


def to_stub(job: Any) -> PostingStub | None:
    """One feed row into a stub, or None when it cannot be addressed."""
    if not isinstance(job, dict):
        return None

    identity = external_id(job)
    title = _text(job.get("title"))
    url = canonical_url(job)
    description = _text(job.get("description"))
    if identity is None or title is None or url is None or description is None:
        return None

    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        # The employer's hiring scope, and NOT a place the posting is.
        location_raw=hiring_scope(job),
        department=None,
        posted_at=published_at(job),
        description_html=description,
        payload=dict(job),
    )


@dataclass(frozen=True)
class WindowRead:
    """One read of the window, and what it does and does not establish."""

    jobs: tuple[Any, ...]
    #: `job-count` from the envelope, checked against the rows rather than
    #: trusted.
    claimed_count: int | None
    #: `total-job-count`, which on 2026-09-11 equalled `job-count`: the public
    #: window IS the public inventory. Never the vendor's whole market.
    claimed_total: int | None
    unaddressable: int = 0


class RemotiveProvider(JobProvider):
    """The Remotive public jobs API."""

    name = "remotive"
    kind = ProviderKind.AGGREGATOR
    #: A window with no paging. Only `BOARD_EXHAUSTIVE` may close a posting on
    #: absence, and this is not that.
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    addresses_boards_by_company = False
    capabilities = REMOTIVE_CAPABILITIES
    field_map = REMOTIVE_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_host: str = API_HOST) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def feed_url(self) -> str:
        """The one request this adapter makes. No filter, no limit.

        `category`, `search` and `company_name` exist and are not sent: each
        is a question somebody chose, and ADR-0019 keeps a candidate's
        vocabulary out of what a provider ingests. `limit` was measured
        ignored. The whole public window, once.
        """
        return assert_trusted(f"{self._api_host}{FEED_PATH}")

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this up by hand."""
        del board
        return f"{self._api_host}/remote-jobs"

    # -- reading -----------------------------------------------------------

    def read_window(self, *, use_cache: bool = True) -> WindowRead:
        """The window, in exactly one request.

        A malformed envelope raises. An empty validated `jobs` array is an
        empty window and a legitimate answer, and neither may ever be read as
        "these postings are gone".
        """
        body = self._fetcher.get_json(self.feed_url(), use_cache=use_cache)
        if not isinstance(body, dict):
            raise RemotiveError("Remotive returned something that is not a JSON object")
        jobs = body.get("jobs")
        if not isinstance(jobs, list):
            keys = ", ".join(sorted(body))
            raise RemotiveError(
                f"Remotive returned an unexpected shape: expected {{'jobs': [...]}}, got [{keys}]"
            )
        return WindowRead(
            jobs=tuple(jobs),
            claimed_count=_count(body.get("job-count")),
            claimed_total=_count(body.get("total-job-count")),
            unaddressable=sum(1 for job in jobs if to_stub(job) is None),
        )

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        """This source is ONE STREAM. It cannot be asked about an employer."""
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Remotive can be asked for: this source is one "
            f"feed, not a board per employer. Collect it with `career-agent collect-remotive`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        del board
        for job in self.read_window().jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it, body included. No second request."""
        del board
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html) if html else "",
            payload=dict(stub.payload),
        )


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
