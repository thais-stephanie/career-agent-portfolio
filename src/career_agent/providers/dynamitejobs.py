"""Dynamite Jobs, read through its public job sitemaps and each posting's own
schema.org `JobPosting`.

AUTHORISATION
-------------
`dynamitejobs.com/robots.txt`, read 2026-09-11: `User-agent: *` with
`Disallow: /api/`, `/form/`, `/webhook/`, `/job-edit/`, `/company-dashboard/`,
`/my-jobs/`, `/admin/` and `/app/`, then four sitemaps named for jobs. The
posting pages are not excluded; no AI crawler is named. The terms of service
(`/terms-and-conditions`, read the same day) are a generic template about
accounts, orders and subscriptions with no clause about automated access.
Read under ADR-0018: public, not prohibited, at the fetcher's delay.

THE CONTRACT, MEASURED
----------------------
`/sitemap/jobs-categories-index.xml` lists one sitemap per vendor category
(`admin-va`, `business-development`, ...), each listing that category's
posting URLs beside two category pages. Categories are the VENDOR's taxonomy
and every one is walked, so the union is the whole inventory: this is not a
query. A posting page carries a `JobPosting` with the whole description
(16,041 characters on the first one read), `datePosted`, `validThrough`,
`employmentType`, `jobLocationType: TELECOMMUTE`, `hiringOrganization` with
the employer's own website in `sameAs`, `estimatedSalary` when stated, and
`applicantLocationRequirements`: a LIST OF COUNTRIES the employer will hire
from. That list is the hiring scope, named as one and exhaustive as one; a
posting without it states nothing. `directApply` was false and no employer
apply link is published, so ADR-0013 rule 2 is unreachable.

The JSON is parsed with `strict=False`, for the reason Programathor's is:
vendors write raw newlines inside description strings.
"""

from __future__ import annotations

import json
import re
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
from career_agent.providers.workable import strip_html

API_HOST = "https://dynamitejobs.com"
TRUSTED_HOST = "dynamitejobs.com"
CATEGORY_INDEX = "/sitemap/jobs-categories-index.xml"
ATTRIBUTION = "Dynamite Jobs"

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>")
_LD_JSON = re.compile(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
#: `/company/{slug}/remote-job/{slug}`: the one shape a posting URL takes.
_POSTING_PATH = re.compile(r"^/company/(?P<company>[a-z0-9-]+)/remote-job/(?P<job>[a-z0-9-]+)/?$")

EMPLOYMENT_TYPES: dict[str, str] = {
    "FULL_TIME": "Full-time",
    "PART_TIME": "Part-time",
    "CONTRACTOR": "Contract",
    "CONTRACT": "Contract",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
}

DYNAMITEJOBS_CAPABILITIES = ProviderCapabilities(
    full_description_in_list=False,
    obtains_full_description=True,
    exposes_posted_date=True,
    exposes_department=False,
    exposes_compensation=True,
    exposes_location_structured=False,
    # Every posting is `TELECOMMUTE` by the board's admission policy, which
    # is not the employer's answer about this role. Same False as WWR.
    exposes_remote_flag=False,
    exposes_employment_type=True,
    # `applicantLocationRequirements`: a country list the employer will hire
    # from, named for the restriction. The Himalayas standard.
    publishes_hiring_scope=True,
)

DYNAMITEJOBS_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="applicantLocations", dimension=MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping(path="employment_type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    )
)


class DynamiteJobsError(ValueError):
    """A response, or a URL, this adapter will not use."""


def assert_trusted(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise DynamiteJobsError(f"Dynamite Jobs URL must be https: {url!r}")
    if parts.hostname != TRUSTED_HOST:
        raise DynamiteJobsError(f"untrusted host {parts.hostname!r}: must be {TRUSTED_HOST}")
    return url


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def sitemap_locs(xml_text: str) -> tuple[str, ...]:
    """Every `<loc>` in a sitemap or sitemap index, on the trusted host only."""
    out: list[str] = []
    for loc in _LOC.findall(xml_text):
        try:
            assert_trusted(loc)
        except DynamiteJobsError:
            continue
        if loc not in out:
            out.append(loc)
    return tuple(out)


def is_posting_url(url: str) -> bool:
    try:
        assert_trusted(url)
    except DynamiteJobsError:
        return False
    return _POSTING_PATH.match(urlsplit(url).path) is not None


def external_id(url: str) -> str | None:
    match = _POSTING_PATH.match(urlsplit(url).path)
    return f"{match.group('company')}/{match.group('job')}" if match else None


def job_posting(html: str) -> dict[str, Any] | None:
    """The `JobPosting` block, or None when the page carries none."""
    for block in _LD_JSON.findall(html):
        try:
            parsed = json.loads(block.strip(), strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("@type") == "JobPosting":
            return parsed
    return None


def hiring_scope(posting: Any) -> str | None:
    """`applicantLocationRequirements` as a comma-separated country list, or
    None. Names as the employer wrote them; `match/places.py` resolves them,
    and a country the gazetteer does not know resolves to nothing rather than
    to a refusal."""
    if not isinstance(posting, dict):
        return None
    reqs = posting.get("applicantLocationRequirements")
    if isinstance(reqs, dict):
        reqs = [reqs]
    if not isinstance(reqs, list):
        return None
    names = []
    for entry in reqs:
        name = _text(entry.get("name")) if isinstance(entry, dict) else _text(entry)
        if name and name not in names:
            names.append(name)
    return ", ".join(names) or None


def company_of(posting: Any) -> str | None:
    if not isinstance(posting, dict):
        return None
    org = posting.get("hiringOrganization")
    return _text(org.get("name")) if isinstance(org, dict) else None


def company_website(posting: Any) -> str | None:
    if not isinstance(posting, dict):
        return None
    org = posting.get("hiringOrganization")
    site = _text(org.get("sameAs")) if isinstance(org, dict) else None
    return site if site and urlsplit(site).scheme == "https" else None


def posted_at(posting: Any) -> str | None:
    if not isinstance(posting, dict):
        return None
    raw = _text(posting.get("datePosted"))
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
    """`estimatedSalary` or `baseSalary`, in either shape schema.org allows.

    Measured 2026-09-11: `estimatedSalary` is a LIST of
    `MonetaryAmountDistribution` with `currency`, `duration: P1M` and
    `minValue`/`maxValue` at the top level. The `MonetaryAmount` shape with a
    nested `value` and `unitText` is read too. A figure with no currency is no
    band; an ISO duration outside month, year and hour is no period.
    """
    if not isinstance(payload, dict):
        return None
    raw_money = payload.get("baseSalary") or payload.get("estimatedSalary")
    if isinstance(raw_money, list):
        raw_money = next((m for m in raw_money if isinstance(m, dict)), None)
    if not isinstance(raw_money, dict):
        return None
    money: dict[str, Any] = raw_money
    currency = _text(money.get("currency"))
    if currency is None:
        return None
    nested = money.get("value")
    value: dict[str, Any] = nested if isinstance(nested, dict) else money

    def number(raw: Any) -> float | None:
        try:
            n = float(raw)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    low, high = number(value.get("minValue")), number(value.get("maxValue"))
    if low is None and high is None:
        single = number(value.get("value"))
        if single is None:
            return None
        low = high = single
    unit = str(value.get("unitText") or money.get("unitText") or "").upper()
    duration = str(money.get("duration") or value.get("duration") or "").upper()
    period = {"YEAR": "YEAR", "MONTH": "MONTH", "HOUR": "HOUR"}.get(unit) or {
        "P1Y": "YEAR",
        "P1M": "MONTH",
        "PT1H": "HOUR",
    }.get(duration)
    return CompensationHint(
        source_field="estimatedSalary",
        min_value=low,
        max_value=high,
        currency=currency.upper(),
        period=period,
        raw_text=None,
    )


def to_stub(url: str, posting: Any) -> PostingStub | None:
    if not isinstance(posting, dict) or not is_posting_url(url):
        return None
    identity = external_id(url)
    title = _text(posting.get("title"))
    description = _text(posting.get("description"))
    if identity is None or title is None or description is None:
        return None
    payload = dict(posting)
    # The flattened country list under a name of the VENDOR's shape. It was
    # `hiring_scope` for one commit, and that word is what every neutral
    # module calls the fact -- so the provider-neutrality guard read the
    # gate's own vocabulary as a vendor payload path and failed four modules.
    payload["applicantLocations"] = hiring_scope(posting)
    payload["employment_type"] = EMPLOYMENT_TYPES.get(
        str(posting.get("employmentType") or "").upper()
    )
    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        location_raw=hiring_scope(posting),
        department=None,
        posted_at=posted_at(posting),
        description_html=description,
        payload=payload,
    )


@dataclass(frozen=True)
class SitemapRead:
    """Every posting URL the category sitemaps list, and how many were read."""

    urls: tuple[str, ...]
    sitemaps: int
    stopped_early: bool


class DynamiteJobsProvider(JobProvider):
    name = "dynamitejobs"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    addresses_boards_by_company = False
    capabilities = DYNAMITEJOBS_CAPABILITIES
    field_map = DYNAMITEJOBS_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_host: str = API_HOST) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")

    def index_url(self) -> str:
        return assert_trusted(f"{self._api_host}{CATEGORY_INDEX}")

    def board_url(self, board: BoardRef) -> str:
        del board
        return f"{self._api_host}/remote-jobs"

    def read_sitemaps(self, *, max_sitemaps: int | None = None) -> SitemapRead:
        """Every category sitemap, then every posting URL in them, once."""
        index = self._fetcher.get_text(self.index_url(), use_cache=False)
        sitemaps = [loc for loc in sitemap_locs(index) if "jobs-of-category" in loc]
        if not sitemaps:
            raise DynamiteJobsError("the category index listed no category sitemaps")
        urls: list[str] = []
        read = 0
        for loc in sitemaps:
            if max_sitemaps is not None and read >= max_sitemaps:
                return SitemapRead(tuple(urls), read, stopped_early=True)
            body = self._fetcher.get_text(loc, use_cache=False)
            read += 1
            for url in sitemap_locs(body):
                if is_posting_url(url) and url not in urls:
                    urls.append(url)
        return SitemapRead(tuple(urls), read, stopped_early=False)

    def read_posting(self, url: str) -> PostingStub | None:
        html = self._fetcher.get_text(assert_trusted(url))
        return to_stub(url, job_posting(html))

    def validate_board(self, board: BoardRef) -> str | None:
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Dynamite Jobs can be asked for: this source is one "
            f"set of sitemaps, not a board per employer. Collect it with "
            f"`career-agent collect-dynamitejobs`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        del board
        for url in self.read_sitemaps().urls:
            stub = self.read_posting(url)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        del board
        html = stub.description_html or ""
        return RawPosting(stub, html, strip_html(html), dict(stub.payload))
