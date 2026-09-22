"""Rippling ATS boards, read through the public board API behind every board.

A FAMILY, NOT A BOARD
---------------------
A Rippling board is `ats.rippling.com/{slug}/jobs`, and behind it the vendor
serves `api.rippling.com/platform/api/ats/v1/board/{slug}/jobs` -- the JSON
the board page itself renders from. Measured 2026-09-11 on the one identity
an employer's own careers page published: `www.rippling.com/careers/open-roles`
links every role to `ats.rippling.com/rippling/jobs/{uuid}`, and the API for
that slug answered 649 rows. An unregistered slug answers HTTP 404 with
`RESOURCE_NOT_FOUND`, so a wrong identifier is told apart from an empty board
and `identifier_is_guessable` may be True.

THE CONTRACT, MEASURED
----------------------
The list carries `uuid`, `name`, `department.label`, `url` and
`workLocation.label` -- and no advert. The detail at `.../jobs/{uuid}` carries
`description.role` and `description.company` (HTML), `workLocations` (a list
of labels), `department.base_department`, `employmentType.label`
(`SALARIED_FT`), `createdOn` (ISO 8601 with an offset), `payRangeDetails`
(empty on the sampled rows) and `companyName`. So the strategy is list, then
one request per posting, at the fetcher's delay.

AUTHORISATION
-------------
`ats.rippling.com/robots.txt`, read 2026-09-11: `User-agent: *`,
`Disallow: /internal/`, nothing else. `api.rippling.com/robots.txt` answers
404, which under ADR-0018 is silence and silence is permission for a public
read. Neither names an AI crawler. The vendor documents this surface as its
"Job Board API" on `developer.rippling.com`.

WHAT A LOCATION IS
------------------
`workLocation.label` is written three ways: `San Francisco, CA` (an office),
`Remote (United States)` and `Hybrid (New York, New York, US)`. The prefix is
the employer's own work-model answer and is read into `workplace`, which is
what lets `structured_geography` treat `Remote (United States)` as where the
remote role is open from and `San Francisco, CA` as a desk. The label is
stored as printed. `publishes_hiring_scope` is False: a board's location is
never a hiring scope on its own, and the gate decides with the work model.
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

API_HOST = "https://api.rippling.com"
BOARD_HOST = "ats.rippling.com"
SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

#: `employmentType.label` as the vendor spells it. Unlisted labels resolve to
#: nothing rather than to a guess.
EMPLOYMENT_TYPES: dict[str, str] = {
    "SALARIED_FT": "Full-time",
    "HOURLY_FT": "Full-time",
    "SALARIED_PT": "Part-time",
    "HOURLY_PT": "Part-time",
    "CONTRACTOR": "Contract",
    "CONTRACT": "Contract",
    "TEMP": "Temporary",
    "TEMPORARY": "Temporary",
    "INTERN": "Internship",
    "INTERNSHIP": "Internship",
}


def workplace_of(label: str | None) -> str | None:
    """`Remote (...)`, `Hybrid (...)`, or a bare place, which is the desk.

    The vendor's vocabulary is three-valued and the third value is written by
    its absence: `Bangalore, India` beside `Remote (United States)` and
    `Hybrid (San Francisco, California, US)` is an office the role is worked
    from. Reading it as nothing let 283 office postings pass through their
    company boilerplate on 2026-09-11 ("you can hire a new employee anywhere
    in the world"); reading it as onsite lets `structured_geography` answer
    from the board's own field, which is the V1.5 rule for every other ATS.
    """
    if not label:
        return None
    lowered = label.strip().lower()
    if lowered.startswith("remote"):
        return "remote"
    if lowered.startswith("hybrid"):
        return "hybrid"
    return "onsite"


def safe_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(value.strip())
        if u.scheme == "https" and u.hostname == BOARD_HOST and not u.username:
            return value.strip()
    except ValueError:
        pass
    return None


def read_compensation(payload: Any) -> CompensationHint | None:
    """`payRangeDetails`, when the employer filled it in.

    Empty on every sampled row on 2026-09-11, so the shape below is read
    defensively: a band needs a currency and at least one figure, and an
    unrecognised shape yields nothing rather than a number with an invented
    unit.
    """
    if not isinstance(payload, dict):
        return None
    details = payload.get("payRangeDetails")
    if not isinstance(details, list) or not details:
        return None
    first = details[0]
    if not isinstance(first, dict):
        return None

    def number(value: Any) -> float | None:
        try:
            n = float(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    low = number(first.get("minPay") or first.get("min"))
    high = number(first.get("maxPay") or first.get("max"))
    currency = first.get("currency")
    if (low is None and high is None) or not isinstance(currency, str):
        return None
    period = {"YEAR": "yearly", "ANNUAL": "yearly", "MONTH": "monthly", "HOUR": "hourly"}.get(
        str(first.get("payPeriod") or first.get("period") or "").upper()
    )
    return CompensationHint(
        min_value=low,
        max_value=high,
        currency=currency.upper(),
        period=period,
        source_field="payRangeDetails",
    )


class RipplingProvider(JobProvider):
    name = "rippling"
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    #: A wrong slug is a 404 (measured), so bounded probing can tell it apart
    #: from an empty board.
    identifier_is_guessable = True
    capabilities = ProviderCapabilities(
        full_description_in_list=False,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        exposes_compensation=True,
        exposes_location_structured=True,
        exposes_remote_flag=True,
        exposes_employment_type=True,
        publishes_hiring_scope=False,
    )
    field_map = ProviderFieldMap(
        (
            FieldMapping("workplace", MetadataDimension.WORK_MODEL_HINT),
            FieldMapping("employment_type", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        )
    )

    def __init__(self, fetcher: HttpFetcher, api_host: str = API_HOST):
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")

    def validate_board(self, board: BoardRef) -> str | None:
        if not SLUG.fullmatch(board.board_identifier or ""):
            return "Rippling needs an employer-published board slug"
        return None

    def board_url(self, board: BoardRef) -> str:
        return f"https://{BOARD_HOST}/{board.board_identifier}/jobs"

    def _list_url(self, board: BoardRef) -> str:
        return f"{self._api_host}/platform/api/ats/v1/board/{board.board_identifier}/jobs"

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        url = self._list_url(board)
        body = self._fetcher.get_json(url)
        if not isinstance(body, list):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "board must be a list")
        # ONE ROW PER LOCATION, ONE POSTING PER UUID. Measured on the live
        # board 2026-09-11: 649 rows carried 348 distinct uuids, because a
        # role open in Pittsburgh or Cleveland is listed once per city with
        # the same uuid, name and url. The variants are folded here into one
        # stub whose location is every label, in the order listed, and whose
        # payload keeps all of them.
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in body:
            if not isinstance(row, dict):
                self.postings_skipped += 1
                continue
            external = str(row.get("uuid") or "")
            link = safe_link(row.get("url"))
            name = row.get("name")
            if not UUID.fullmatch(external) or not link or not isinstance(name, str) or not name:
                self.postings_skipped += 1
                continue
            grouped.setdefault(external, []).append(row)
        for external, rows in grouped.items():
            first = rows[0]
            labels: list[str] = []
            for row in rows:
                location = row.get("workLocation")
                label = location.get("label") if isinstance(location, dict) else None
                if isinstance(label, str) and label.strip() and label not in labels:
                    labels.append(label.strip())
            department = first.get("department")
            yield PostingStub(
                external_id=f"rippling-{board.board_identifier}-{external}",
                title=str(first["name"]),
                url=str(safe_link(first.get("url"))),
                location_raw=" | ".join(labels) or None,
                department=(department.get("label") if isinstance(department, dict) else None),
                posted_at=None,
                description_html=None,
                payload={
                    **first,
                    "workLocationLabels": labels,
                    # The work model of the FIRST listed location. A role
                    # listed as both an office and `Remote (...)` keeps every
                    # label in the payload; the pipeline reads one answer.
                    "workplace": workplace_of(labels[0] if labels else None),
                },
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """One request per posting, for the advert the list does not carry."""
        uuid = stub.external_id.rsplit("-", 5)[-5:]
        uuid_text = "-".join(uuid)
        url = f"{self._list_url(board)}/{uuid_text}"
        body = self._fetcher.get_json(url)
        if not isinstance(body, dict):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "detail must be an object")
        description = body.get("description")
        parts: list[str] = []
        if isinstance(description, dict):
            for key in ("role", "company"):
                text = description.get(key)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
        elif isinstance(description, str) and description.strip():
            parts.append(description.strip())
        html = "\n\n".join(parts)
        employment = body.get("employmentType")
        label = employment.get("label") if isinstance(employment, dict) else None
        payload = {
            **stub.payload,
            **{k: v for k, v in body.items() if k not in ("activeJobApplication", "board")},
            "employment_type": EMPLOYMENT_TYPES.get(str(label or "").upper()),
            "source_posting_url": stub.url,
            "apply_url": stub.url,
        }
        locations = body.get("workLocations")
        posted = to_rfc3339_utc(body.get("createdOn"))
        refreshed = PostingStub(
            external_id=stub.external_id,
            title=stub.title,
            url=stub.url,
            location_raw=(
                " | ".join(str(x) for x in locations if isinstance(x, str) and x.strip())
                if isinstance(locations, list) and locations
                else stub.location_raw
            ),
            department=stub.department,
            posted_at=posted,
            description_html=html or None,
            payload=payload,
        )
        return RawPosting(refreshed, html, strip_html(html), dict(payload))


#: `https://ats.rippling.com/<board>/jobs/<uuid>`, the public posting page.
_BOARD_URL = re.compile(
    r"^https?://ats\.rippling\.com/(?P<board>[a-z0-9][a-z0-9-]{0,62})/jobs/"
    r"(?P<id>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(board_identifier, external_id)` for a Rippling posting URL, or None.

    The external id is returned in the stored form, `rippling-<board>-<uuid>`,
    so a discovered posting can be looked up exactly.
    """
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    board = match.group("board").lower()
    return board, f"rippling-{board}-{match.group('id').lower()}"
