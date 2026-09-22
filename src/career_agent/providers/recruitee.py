"""Public Recruitee offers, for employer-published identities only.

Protocol precedent: career-ops-hq/career-ops (MIT), providers/recruitee.mjs.
Custom domains must be explicitly present in the registry, never inferred from
an empty vendor subdomain. See THIRD_PARTY_NOTICES.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.timestamps import to_rfc3339_utc
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

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HOST = re.compile(r"^(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}$")


def safe_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(value)
        if u.scheme == "https" and u.hostname and not u.username and not u.password:
            return value
    except ValueError:
        pass
    return None


def read_compensation(payload: Any) -> CompensationHint | None:
    pay = payload.get("salary") if isinstance(payload, dict) else None
    if not isinstance(pay, dict):
        return None

    def number(value):
        try:
            n = float(value)
            return n if n > 0 else None
        except (ValueError, TypeError):
            return None

    low, high = number(pay.get("min")), number(pay.get("max"))
    if low is None and high is None:
        return None
    return CompensationHint(
        min_value=low,
        max_value=high,
        currency=pay.get("currency"),
        period={"year": "yearly", "month": "monthly", "hour": "hourly"}.get(str(pay.get("period"))),
        source_field="salary",
    )


class RecruiteeProvider(JobProvider):
    name = "recruitee"
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    identifier_is_guessable = False
    #: The host answers for slugs nobody registered (see `JobProvider`).
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
        if not SLUG.fullmatch(board.board_identifier):
            return "Recruitee needs an employer-published tenant identifier"
        if board.board_url:
            u = urlsplit(board.board_url)
            if (
                not safe_link(board.board_url)
                or not HOST.fullmatch(u.hostname or "")
                or u.port not in (None, 443)
                or u.query
                or u.fragment
                or u.path not in ("", "/")
            ):
                return "Recruitee custom board must be an explicit public HTTPS origin"
        return None

    def board_url(self, board: BoardRef) -> str:
        return board.board_url or f"https://{board.board_identifier}.recruitee.com"

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        url = self.board_url(board).rstrip("/") + "/api/offers/"
        body = self._fetcher.get_json(url)
        if not isinstance(body, dict) or not isinstance(body.get("offers"), list):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "offers must be a list")
        seen = set()
        for row in body["offers"]:
            if not isinstance(row, dict):
                self.postings_skipped += 1
                continue
            link = safe_link(row.get("careers_url")) or safe_link(row.get("url"))
            external = row.get("id")
            if not link or not external or not row.get("title"):
                self.postings_skipped += 1
                continue
            if external in seen:
                raise FetchError(FetchErrorCategory.MALFORMED, url, "duplicate offer id")
            seen.add(external)
            payload = dict(row)
            flags = [
                name
                for key, name in (("remote", "remote"), ("hybrid", "hybrid"), ("on_site", "onsite"))
                if row.get(key) is True
            ]
            payload["workplace"] = flags[0] if len(flags) == 1 else None
            code = row.get("employment_type_code", "")
            payload["employment_type"] = {
                "fulltime_permanent": "Full-time",
                "fulltime_fixed_term": "Full-time",
                "parttime_permanent": "Part-time",
                "parttime_fixed_term": "Part-time",
                "internship": "Internship",
                "freelance": "Contract",
            }.get(code)
            payload["source_posting_url"] = link
            payload["apply_url"] = safe_link(row.get("careers_apply_url")) or link
            html = "\n\n".join(str(row[k]) for k in ("description", "requirements") if row.get(k))
            yield PostingStub(
                external_id=f"recruitee-{board.board_identifier}-{external}",
                title=row["title"],
                url=link,
                location_raw=", ".join(
                    str(row[k]) for k in ("city", "state_name", "country") if row.get(k)
                )
                or None,
                department=row.get("department"),
                posted_at=to_rfc3339_utc(row.get("published_at")),
                description_html=html or None,
                payload=payload,
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        html = stub.description_html or ""
        return RawPosting(stub, html, strip_html(html), dict(stub.payload))


#: `https://<slug>.recruitee.com/o/<offer-slug>`. The host answers for slugs
#: nobody registered, so a caller must confirm the board LISTS the posting
#: before believing the identity; the URL alone proves only its own shape.
_BOARD_URL = re.compile(
    r"^https?://(?P<slug>[a-z0-9][a-z0-9-]{0,62})\.recruitee\.com/o/[^/?#]+/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_VENDOR_HOSTS = frozenset({"careers", "www", "app", "api", "docs", "help", "blog"})


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(tenant, None)` for a Recruitee-hosted posting URL, or None."""
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    slug = match.group("slug").lower()
    if slug in _VENDOR_HOSTS:
        return None
    return slug, None
