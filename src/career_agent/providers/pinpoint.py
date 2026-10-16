"""Public Pinpoint postings, for validated tenants only (2026-10-16).

A Pinpoint board is `https://<tenant>.pinpointhq.com`, and its whole public
listing is one request: `GET /postings.json` answers `{"data": [...]}` with
every open posting, no pagination. Each posting carries the whole advert in
four HTML sections (`description`, `key_responsibilities`,
`skills_knowledge_expertise`, `benefits`), a STRUCTURED `workplace_type`
(`remote | hybrid | onsite`), `employment_type`, an optional visible salary
(`compensation_minimum / maximum / currency / frequency` behind
`compensation_visible`) and `location.{name, city, province}` as free text.
There is NO published date and NO country code.

Measured 2026-10-16 on three tenants: robots.txt allows `/postings.json`
(`User-Agent: *` disallows only `/mydata`, `/admin`, `/companies` and some
posting paths); an unknown tenant answers `Disallow: /` and a 404.

THE TRAP: some abandoned trial tenants answer 200 with canned demonstration
postings that read like real ones. Their marker, documented upstream
(career-ops `providers/pinpoint.mjs`, MIT, issue #4190; see
THIRD_PARTY_NOTICES.md), is a doubled YouTube embed in the description.
`validate_listing` refuses such a tenant, and a tenant is registered only
after it passed (`scripts/pinpoint_tenants.py`).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
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
)
from career_agent.providers.workable import strip_html

PROVIDER = "pinpoint"

#: A tenant is one DNS label under the vendor's domain.
TENANT = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VENDOR_DOMAIN = "pinpointhq.com"

#: Hosts on the vendor's domain that are the vendor's own, never a tenant.
VENDOR_HOSTS = frozenset({"www", "app", "api", "help", "docs", "blog", "status", "developers"})

#: The four sections that together are the whole advert, in reading order.
SECTIONS = ("description", "key_responsibilities", "skills_knowledge_expertise", "benefits")

#: The canned-demo marker (see the module docstring).
DEMO_MARKER = "youtube.com/embed/https://www.youtube.com/embed/"

_WORKPLACE = {"remote": "remote", "hybrid": "hybrid", "onsite": "onsite", "on_site": "onsite"}
_EMPLOYMENT = {
    "permanent_full_time": "Full-time",
    "full_time": "Full-time",
    "permanent_part_time": "Part-time",
    "part_time": "Part-time",
    "fixed_term_full_time": "Full-time",
    "fixed_term_part_time": "Part-time",
    "contract": "Contract",
    "freelance": "Contract",
    "internship": "Internship",
    "temporary": "Temporary",
}
_PERIOD = {"year": "yearly", "annually": "yearly", "month": "monthly", "hour": "hourly",
           "day": "daily", "week": "weekly"}  # fmt: skip


def origin(tenant: str) -> str:
    return f"https://{tenant}.{VENDOR_DOMAIN}"


def _on_tenant(url: Any, tenant: str) -> str | None:
    """`url` when it is an HTTPS link on this tenant's own host, else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.username or parts.password or parts.port not in (None, 443):
        return None
    if (parts.hostname or "").lower() != f"{tenant}.{VENDOR_DOMAIN}":
        return None
    return url


def read_compensation(payload: Any) -> CompensationHint | None:
    """The salary the employer chose to show, or None."""
    if not isinstance(payload, dict) or payload.get("compensation_visible") is not True:
        return None

    def number(value: Any) -> float | None:
        try:
            n = float(value)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    low = number(payload.get("compensation_minimum"))
    high = number(payload.get("compensation_maximum"))
    if low is None and high is None:
        return None
    currency = payload.get("compensation_currency")
    frequency = str(payload.get("compensation_frequency") or "").lower()
    return CompensationHint(
        min_value=low,
        max_value=high,
        currency=currency.upper() if isinstance(currency, str) and currency else None,
        period=_PERIOD.get(frequency),
        source_field="compensation_minimum",
    )


COMPENSATION_PATHS = (
    "compensation_minimum",
    "compensation_maximum",
    "compensation_currency",
    "compensation_frequency",
)


def compose_location(row: dict[str, Any]) -> str | None:
    """`City, Province, Name` from the free-text location, without repeats."""
    raw = row.get("location")
    place: dict[str, Any] = raw if isinstance(raw, dict) else {}
    parts: list[str] = []
    for key in ("city", "province", "name"):
        value = place.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return ", ".join(parts) or None


def validate_listing(body: Any, tenant: str) -> str | None:
    """Why this `postings.json` body must not be trusted, or None.

    A tenant serving the canned demonstration postings is an abandoned trial,
    and its plausible-looking jobs do not exist."""
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return "postings.json must be an object with a `data` list"
    for row in body["data"]:
        if not isinstance(row, dict):
            continue
        text = " ".join(str(row.get(section) or "") for section in SECTIONS)
        if DEMO_MARKER in text:
            return f"{tenant} serves Pinpoint's canned demonstration postings"
    return None


class PinpointProvider(JobProvider):
    name = PROVIDER
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    identifier_is_guessable = False
    #: An unknown tenant answers 404, but an abandoned one answers 200 with
    #: canned postings: the listing must be validated, never just reached.
    answers_for_unknown_identifiers = True
    capabilities = ProviderCapabilities(True, True, True, True, True, True, True, True)
    field_map = ProviderFieldMap(
        (
            FieldMapping("workplace", MetadataDimension.WORK_MODEL_HINT),
            FieldMapping("employment_type", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        )
    )

    def __init__(self, fetcher: HttpFetcher):
        self._fetcher = fetcher

    def validate_board(self, board: BoardRef) -> str | None:
        tenant = board.board_identifier
        if not TENANT.fullmatch(tenant) or tenant in VENDOR_HOSTS:
            return "Pinpoint needs a validated tenant identifier"
        if board.board_url and board.board_url.rstrip("/") != origin(tenant):
            return "Pinpoint board must be the tenant's own HTTPS origin"
        return None

    def board_url(self, board: BoardRef) -> str:
        return origin(board.board_identifier)

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        tenant = board.board_identifier
        url = f"{origin(tenant)}/postings.json"
        body = self._fetcher.get_json(url)
        if refusal := validate_listing(body, tenant):
            raise FetchError(FetchErrorCategory.MALFORMED, url, refusal)
        seen: set[str] = set()
        for row in body["data"]:
            if not isinstance(row, dict):
                self.postings_skipped += 1
                continue
            external = row.get("id")
            link = _on_tenant(row.get("url"), tenant)
            title = row.get("title")
            usable = isinstance(title, str) and title.strip()
            if external in (None, "") or not link or not usable:
                self.postings_skipped += 1
                continue
            key = str(external)
            if key in seen:
                raise FetchError(FetchErrorCategory.MALFORMED, url, "duplicate posting id")
            seen.add(key)
            payload = dict(row)
            payload["workplace"] = _WORKPLACE.get(str(row.get("workplace_type") or "").lower())
            payload["employment_type"] = _EMPLOYMENT.get(
                str(row.get("employment_type") or "").lower()
            )
            payload["source_posting_url"] = link
            payload["apply_url"] = link
            html = "\n\n".join(str(row[k]) for k in SECTIONS if row.get(k))
            job = row.get("job") if isinstance(row.get("job"), dict) else {}
            department = job.get("department") if isinstance(job, dict) else None
            yield PostingStub(
                external_id=f"pinpoint-{tenant}-{key}",
                title=str(title).strip(),
                url=link,
                location_raw=compose_location(row),
                department=department.get("name") if isinstance(department, dict) else None,
                posted_at=None,
                description_html=html or None,
                payload=payload,
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        html = stub.description_html or ""
        return RawPosting(stub, html, strip_html(html), dict(stub.payload))


#: `https://<tenant>.pinpointhq.com/[<locale>/]postings/<uuid>`.
_POSTING_URL = re.compile(
    r"^https://(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.pinpointhq\.com"
    r"/(?:[a-z]{2}(?:-[A-Za-z]{2})?/)?postings/(?P<id>[0-9a-f-]{8,})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(tenant, None)` for a Pinpoint-hosted posting URL, or None. The URL
    proves its shape only; the tenant is accepted after `validate_listing`."""
    match = _POSTING_URL.match(url.strip())
    if match is None:
        return None
    tenant = match.group("tenant").lower()
    if tenant in VENDOR_HOSTS:
        return None
    return tenant, None
