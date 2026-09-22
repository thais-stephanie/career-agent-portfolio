"""4 Day Week, read from the public API v2 it documents and asks to be credited for.

AUTHORISATION
-------------
`4dayweek.io/robots.txt`, read 2026-09-11: `User-Agent: *` with `Allow: /`,
`Allow: /api/v1`, `Allow: /api/v2` and `Allow: /api/mcp` written out before
`Disallow: /api/`, and no AI crawler named. The v1 root answers with its own
terms in the envelope -- "Thanks for using this API! All we ask is that you
link back to https://4dayweek.io" -- and `/api/v2` documents itself at
`4dayweek.io/openapi.yaml`: "60 requests per minute per IP", `Cache-Control:
public, max-age=60`, `limit` at most 100. An affirmative grant with one
condition, and the condition is `ATTRIBUTION` beside every posting.

WHAT THE FEED HOLDS
-------------------
Measured 2026-09-11: `total` 23,690 live listings, paginated, with a plain-text
`description` ("HTML is never exposed"), `work_arrangement` (onsite / hybrid /
remote), `contract_type` (permanent / contract / freelance), `schedule_type`,
`salary_min` / `salary_max` IN THE SMALLEST CURRENCY UNIT with
`salary_currency` and `salary_period` (year / month / hour), `posted_at`, and
`locations`: a list where EACH ENTRY carries its own `work_arrangement`, so a
job lists the offices it is done from and the countries it may be done
remotely from in one array. The OpenAPI names the two halves
`office_locations` and `remote_allowed`; the response the API served carried
them merged in `locations`, and this adapter reads the merged shape and the
split one alike.

THE HIRING SCOPE IS THE REMOTE-ALLOWED LIST
--------------------------------------------
A `locations` entry whose `work_arrangement` is `remote` is where the employer
allows the role to be done from -- the OpenAPI describes the `country` filter
as matching "office or remote-allowed locations". That is a restriction named
as one, the standard Himalayas met, so `publishes_hiring_scope` is True and
`location_raw` on a remote job is the remote-allowed countries. A job with no
remote-allowed entry gets its offices as `location_raw`, and
`structured_geography` reads those against `work_arrangement`.

No apply URL: "apply_url removed -- use the job URL on 4dayweek.io instead".
So there is no origin pointer, ADR-0013 rule 2 is unreachable, and Apply
opens the 4dayweek.io page, which is also what the attribution asks.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

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

API_HOST = "https://4dayweek.io"
TRUSTED_HOST = "4dayweek.io"
FEED_PATH = "/api/v2/jobs"
ATTRIBUTION = "4 Day Week"

#: The documented ceiling per page. Above it the API clamps; this never asks.
PER_PAGE = 100
#: Pages per run unless told otherwise: 5,000 rows, a fifth of the feed, at
#: roughly a request a second under the 60-a-minute limit.
DEFAULT_MAX_PAGES = 50

COMPENSATION_PERIODS: dict[str, str] = {"year": "YEAR", "month": "MONTH", "hour": "HOUR"}
EMPLOYMENT_TYPES: dict[str, str] = {
    "permanent": "Full-time",
    "contract": "Contract",
    "freelance": "Contract",
}

FOURDAYWEEK_CAPABILITIES = ProviderCapabilities(
    full_description_in_list=True,
    obtains_full_description=True,
    exposes_posted_date=True,
    # `category` is the board's taxonomy, not the employer's team.
    exposes_department=False,
    exposes_compensation=True,
    exposes_location_structured=True,
    # `work_arrangement` is the employer's own answer per listing, not the
    # board's admission policy: this board lists onsite work too.
    exposes_remote_flag=True,
    exposes_employment_type=True,
    publishes_hiring_scope=True,
)

FOURDAYWEEK_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="work_arrangement", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="employment_type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class FourDayWeekError(ValueError):
    """A response, or a configured value, this adapter will not use."""


def assert_trusted(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise FourDayWeekError(f"4 Day Week URL must be https: {url!r}")
    if parts.hostname != TRUSTED_HOST:
        raise FourDayWeekError(f"untrusted host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def _locations(job: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(offices, remote_allowed), from either shape the API documents."""
    offices: list[dict[str, Any]] = []
    remote: list[dict[str, Any]] = []
    merged = job.get("locations")
    if isinstance(merged, list):
        for entry in merged:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("work_arrangement") or "").lower() == "remote":
                remote.append(entry)
            else:
                offices.append(entry)
    for key, bucket in (("office_locations", offices), ("remote_allowed", remote)):
        split = job.get(key)
        if isinstance(split, list):
            bucket.extend(e for e in split if isinstance(e, dict))
    return offices, remote


def _place(entry: dict[str, Any]) -> str | None:
    bits = [_text(entry.get("city")), _text(entry.get("state")), _text(entry.get("country"))]
    named = [b for b in bits if b]
    if not named:
        region = _text(entry.get("region")) or _text(entry.get("continent"))
        return region
    return ", ".join(named)


def hiring_scope(job: Any) -> str | None:
    """The remote-allowed countries, or None when the listing names none.

    A remote-allowed entry with only a continent (`Europe`) is passed through
    as the continent; `match/places.py` reads it as a region. A job with no
    remote-allowed entry states no scope, and its offices go to `offices`.
    """
    if not isinstance(job, dict):
        return None
    _, remote = _locations(job)
    names = []
    for entry in remote:
        name = (
            _text(entry.get("country"))
            or _text(entry.get("region"))
            or _text(entry.get("continent"))
        )
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def offices(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    listed, _ = _locations(job)
    names = []
    for entry in listed:
        name = _place(entry)
        if name and name not in names:
            names.append(name)
    return " | ".join(names) or None


def company_of(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    company = job.get("company")
    if isinstance(company, dict):
        return _text(company.get("name"))
    return None


def company_website(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    company = job.get("company")
    if not isinstance(company, dict):
        return None
    site = _text(company.get("website"))
    if site and urlsplit(site).scheme == "https":
        return site
    return None


def canonical_url(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("url"))
    if raw is None:
        return None
    try:
        return assert_trusted(raw)
    except FourDayWeekError:
        return None


def external_id(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    value = _text(job.get("id")) or _text(job.get("id_str"))
    return value


def posted_at(job: Any) -> str | None:
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("posted_at"))
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
    """`salary_min` / `salary_max` in the smallest currency unit, with a
    currency and a period. `8000000` is $80,000; a figure with no currency
    yields nothing, and a period outside year/month/hour yields no period."""
    if not isinstance(payload, dict):
        return None

    def number(value: Any) -> float | None:
        if isinstance(value, bool) or not isinstance(value, int | float):
            return None
        return float(value) / 100.0 if value > 0 else None

    minimum, maximum = number(payload.get("salary_min")), number(payload.get("salary_max"))
    if minimum is None and maximum is None:
        return None
    currency = _text(payload.get("salary_currency"))
    if currency is None:
        return None
    period = COMPENSATION_PERIODS.get(str(payload.get("salary_period") or "").lower())
    return CompensationHint(
        source_field="salary_min/salary_max",
        min_value=minimum,
        max_value=maximum,
        currency=currency.upper(),
        period=period or None,
        raw_text=None,
    )


def to_stub(job: Any) -> PostingStub | None:
    if not isinstance(job, dict):
        return None
    identity = external_id(job)
    title = _text(job.get("title"))
    url = canonical_url(job)
    description = _text(job.get("description"))
    if identity is None or title is None or url is None or description is None:
        return None
    payload = dict(job)
    payload["employment_type"] = EMPLOYMENT_TYPES.get(str(job.get("contract_type") or "").lower())
    scope = hiring_scope(job)
    arrangement = str(job.get("work_arrangement") or "").lower()
    if scope:
        location = scope
    elif arrangement == "remote":
        # A remote role whose employer named no remote-allowed place has
        # stated nothing about where it hires. Its offices stay in the
        # payload; reading them as the scope of a remote role would refuse
        # a candidate over a desk nobody asked them to sit at.
        location = None
    else:
        location = offices(job)
    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        # The remote-allowed countries when the employer named any; the
        # offices for onsite and hybrid work; nothing for a remote role that
        # named no place. `publishes_hiring_scope` makes the first a declared
        # scope; `structured_geography` reads the second against the work
        # model.
        location_raw=location,
        department=None,
        posted_at=posted_at(job),
        description_html=None,
        payload=payload,
    )


@dataclass(frozen=True)
class FeedRead:
    """One walk of the feed, and what it could not reach."""

    jobs: tuple[Any, ...]
    pages: int
    stopped_early: bool
    unaddressable: int = 0
    claimed_total: int | None = None


class FourDayWeekProvider(JobProvider):
    """The 4 Day Week public API v2."""

    name = "fourdayweek"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    addresses_boards_by_company = False
    capabilities = FOURDAYWEEK_CAPABILITIES
    field_map = FOURDAYWEEK_FIELD_MAP

    def __init__(
        self, fetcher: HttpFetcher, api_host: str = API_HOST, max_pages: int | None = None
    ) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")
        self._max_pages = DEFAULT_MAX_PAGES if max_pages is None else max(1, int(max_pages))

    def feed_url(self, page: int) -> str:
        """One page. No filter: `category`, `q` and `skills` are questions
        somebody chose, and ADR-0019 keeps them out of ingestion."""
        if page < 1:
            raise FourDayWeekError("pages start at 1")
        return assert_trusted(f"{self._api_host}{FEED_PATH}?limit={PER_PAGE}&page={page}")

    def board_url(self, board: BoardRef) -> str:
        del board
        return f"{self._api_host}/remote-jobs"

    @property
    def max_pages(self) -> int:
        return self._max_pages

    def iter_pages(self, *, use_cache: bool = False) -> Iterator[FeedRead]:
        """One page at a time, so a caller can persist each before the next.

        Each yielded `FeedRead` holds ONE page; `stopped_early` on the last
        one says whether the budget ended the walk or the feed did.
        """
        seen: set[str] = set()
        claimed: int | None = None
        for page in range(1, self._max_pages + 1):
            body = self._fetcher.get_json(self.feed_url(page), use_cache=use_cache)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                keys = ", ".join(sorted(body)) if isinstance(body, dict) else "not an object"
                raise FourDayWeekError(
                    f"4 Day Week returned an unexpected shape: expected {{'data': [...]}}, "
                    f"got [{keys}]"
                )
            total = body.get("total")
            if claimed is None and isinstance(total, int) and not isinstance(total, bool):
                claimed = total
            rows: list[Any] = []
            for job in body["data"]:
                identity = external_id(job)
                if identity is not None and identity in seen:
                    continue
                if identity is not None:
                    seen.add(identity)
                rows.append(job)
            at_end = not body["data"] or not bool(body.get("has_more"))
            yield FeedRead(
                jobs=tuple(rows),
                pages=page,
                stopped_early=not at_end and page == self._max_pages,
                unaddressable=sum(1 for j in rows if to_stub(j) is None),
                claimed_total=claimed,
            )
            if at_end:
                return

    def read_feed(self, *, use_cache: bool = False) -> FeedRead:
        collected: list[Any] = []
        seen: set[str] = set()
        claimed: int | None = None
        pages = 0
        for page in range(1, self._max_pages + 1):
            body = self._fetcher.get_json(self.feed_url(page), use_cache=use_cache)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                keys = ", ".join(sorted(body)) if isinstance(body, dict) else "not an object"
                raise FourDayWeekError(
                    f"4 Day Week returned an unexpected shape: expected {{'data': [...]}}, "
                    f"got [{keys}]"
                )
            pages += 1
            total = body.get("total")
            if claimed is None and isinstance(total, int) and not isinstance(total, bool):
                claimed = total
            for job in body["data"]:
                identity = external_id(job)
                if identity is not None and identity in seen:
                    continue
                if identity is not None:
                    seen.add(identity)
                collected.append(job)
            if not body["data"] or not bool(body.get("has_more")):
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

    def validate_board(self, board: BoardRef) -> str | None:
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something 4 Day Week can be asked for: this source is one "
            f"feed, not a board per employer. Collect it with `career-agent collect-fourdayweek`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        del board
        for job in self.read_feed().jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it: plain text, no HTML, no second request."""
        del board
        text = _text(stub.payload.get("description")) or ""
        return RawPosting(
            stub=stub,
            description_html="",
            description_text=text,
            payload=dict(stub.payload),
        )
