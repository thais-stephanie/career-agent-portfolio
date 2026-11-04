"""Public BambooHR careers boards, for validated tenants only (2026-11-03).

A BambooHR careers board is `https://<tenant>.bamboohr.com/careers`, and the
page itself is fed by two public JSON endpoints, the same ones any visitor's
browser reads:

    GET /careers/list          {"meta": {"totalCount": n}, "result": [...]}
    GET /careers/<id>/detail   {"result": {"jobOpening": {...}, "formFields": {...}}}

The list carries id, title, department, the tenant's employment-status label,
`location {city, state}`, `atsLocation`, `isRemote` and `locationType`, and NO
description and NO date. The detail carries the whole advert (`description`,
HTML), `datePosted` (a calendar date), `jobOpeningStatus`, `compensation`
(free text or null) and a fuller location with `addressCountry`. So one board
costs 1 + n requests; the boards known are small (3 to 10 postings).

Measured 2026-11-03 on four tenants: each tenant's robots.txt is
`User-Agent: *` with an empty `Disallow:`. An unknown tenant answers `302` to
`www.bamboohr.com` on both endpoints, and a posting id the tenant does not hold
answers `404`. A tenant can exist with its careers page switched off (the
tenant root answers but `/careers` redirects to the vendor), which is the same
302. That redirect is why a tenant is registered only after
`scripts/bamboohr_tenants.py` read its list without following redirects; the
collector's own fetcher follows redirects, so a tenant that later switches its
page off reads as a body that is not JSON: an error on the board, never an
empty board and never a retirement.

`formFields` (the application form) is not archived: it is about applying,
not about the job. `locationType` ("0", "1", "2") is archived but NOT read as
a work model: no first-party page says what its values mean, and a guessed
mapping would be an invented fact. `isRemote` is read only when it is the
boolean `true`; measured, it was null on every posting of the four tenants.
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

PROVIDER = "bamboohr"

#: A tenant is one DNS label under the vendor's domain.
TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VENDOR_DOMAIN = "bamboohr.com"

#: Hosts on the vendor's domain that are the vendor's own, never a tenant.
VENDOR_HOSTS = frozenset(
    {
        "www",
        "app",
        "api",
        "help",
        "partners",
        "marketplace",
        "status",
        "developers",
        "documentation",
    }
)

#: A posting id is the tenant's own integer.
_POSTING_ID = re.compile(r"^[0-9]{1,12}$")


def origin(tenant: str) -> str:
    return f"https://{tenant}.{VENDOR_DOMAIN}"


def posting_url(tenant: str, posting_id: str) -> str:
    return f"{origin(tenant)}/careers/{posting_id}"


def external_id(tenant: str, posting_id: str) -> str:
    return f"bamboohr-{tenant}-{posting_id}"


def validate_listing(body: Any) -> str | None:
    """Why this `/careers/list` body must not be trusted, or None."""
    if not isinstance(body, dict) or not isinstance(body.get("result"), list):
        return "careers/list must be an object with a `result` list"
    return None


def compose_location(place: Any) -> str | None:
    """`City, State, Country` from what the employer filled in, without repeats."""
    if not isinstance(place, dict):
        return None
    parts: list[str] = []
    for key in ("city", "state", "province", "addressCountry", "country"):
        value = place.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return ", ".join(parts) or None


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


class BambooHRProvider(JobProvider):
    name = PROVIDER
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    identifier_is_guessable = False
    capabilities = ProviderCapabilities(
        full_description_in_list=False,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        # `compensation` exists but is free text the tenant types; no reader
        # is registered, so this adapter does not supply pay.
        exposes_compensation=False,
        exposes_location_structured=True,
        exposes_remote_flag=True,
        exposes_employment_type=True,
    )
    field_map = ProviderFieldMap(
        (
            FieldMapping("workplace", MetadataDimension.WORK_MODEL_HINT),
            FieldMapping("employmentStatusLabel", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        )
    )

    def __init__(self, fetcher: HttpFetcher):
        self._fetcher = fetcher

    def validate_board(self, board: BoardRef) -> str | None:
        tenant = board.board_identifier
        if not TENANT.fullmatch(tenant) or tenant in VENDOR_HOSTS:
            return "BambooHR needs a validated tenant identifier"
        if board.board_url and board.board_url.rstrip("/") not in (
            origin(tenant),
            f"{origin(tenant)}/careers",
        ):
            return "BambooHR board must be the tenant's own HTTPS careers page"
        return None

    def board_url(self, board: BoardRef) -> str:
        return f"{origin(board.board_identifier)}/careers"

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        tenant = board.board_identifier
        url = f"{origin(tenant)}/careers/list"
        body = self._fetcher.get_json(url)
        if refusal := validate_listing(body):
            raise FetchError(FetchErrorCategory.MALFORMED, url, refusal)
        seen: set[str] = set()
        for row in body["result"]:
            if not isinstance(row, dict):
                self.postings_skipped += 1
                continue
            posting_id = str(row.get("id") or "")
            title = _text(row.get("jobOpeningName"))
            if not _POSTING_ID.fullmatch(posting_id) or title is None:
                self.postings_skipped += 1
                continue
            if posting_id in seen:
                raise FetchError(FetchErrorCategory.MALFORMED, url, "duplicate posting id")
            seen.add(posting_id)
            payload = dict(row)
            payload["workplace"] = "remote" if row.get("isRemote") is True else None
            yield PostingStub(
                external_id=external_id(tenant, posting_id),
                title=title,
                url=posting_url(tenant, posting_id),
                location_raw=compose_location(row.get("location"))
                or compose_location(row.get("atsLocation")),
                department=_text(row.get("departmentLabel")),
                posted_at=None,
                payload=payload,
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """One request per posting: the advert and its date are only here."""
        tenant = board.board_identifier
        posting_id = stub.external_id.rsplit("-", 1)[-1]
        url = f"{origin(tenant)}/careers/{posting_id}/detail"
        body = self._fetcher.get_json(url)
        result = body.get("result") if isinstance(body, dict) else None
        opening = result.get("jobOpening") if isinstance(result, dict) else None
        if not isinstance(opening, dict):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "detail has no jobOpening object")
        html = str(opening.get("description") or "")
        # The listing row, then the opening (the fuller record wins), never
        # the application form.
        merged = {**stub.payload, **opening, "workplace": stub.payload.get("workplace")}
        return RawPosting(
            # Replaced rather than edited, as Workday does: the date and the
            # fuller location are only knowable here.
            stub=PostingStub(
                external_id=stub.external_id,
                title=_text(opening.get("jobOpeningName")) or stub.title,
                url=stub.url,
                location_raw=compose_location(opening.get("location")) or stub.location_raw,
                department=_text(opening.get("departmentLabel")) or stub.department,
                posted_at=to_rfc3339_utc(opening.get("datePosted")),
                description_html=html,
                payload=merged,
            ),
            description_html=html,
            description_text=html_to_text(html),
            payload=merged,
        )


#: `https://<tenant>.bamboohr.com/careers/<id>` with any query (aggregators
#: add `?source=...`) or a trailing slash.
_POSTING_URL = re.compile(
    r"^https://(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.bamboohr\.com"
    r"/careers/(?P<id>[0-9]{1,12})/?(?:[?#].*)?$",
    re.IGNORECASE,
)
#: The board root a careers page links to.
_BOARD_ROOT = re.compile(
    r"^https://(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.bamboohr\.com/careers/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The stored `external_id` of a BambooHR posting URL, or None."""
    match = _POSTING_URL.match(url.strip())
    if match is None or match.group("tenant").lower() in VENDOR_HOSTS:
        return None
    return external_id(match.group("tenant").lower(), match.group("id"))


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(tenant, external_id or None)` for a BambooHR careers URL, or None.

    The URL proves its shape only; a tenant is registered after its own list
    answered (`scripts/bamboohr_tenants.py`)."""
    text = url.strip()
    if match := _POSTING_URL.match(text):
        tenant = match.group("tenant").lower()
        found: tuple[str, str | None] = (tenant, external_id(tenant, match.group("id")))
    elif match := _BOARD_ROOT.match(text):
        tenant = match.group("tenant").lower()
        found = (tenant, None)
    else:
        return None
    return None if tenant in VENDOR_HOSTS else found
