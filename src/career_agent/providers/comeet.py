"""Comeet (Spark Hire Recruit) boards, read through the Careers API the
vendor's own hosted page calls.

A FAMILY, AND THE ONE WHOSE IDENTITY MAY NOT BE GUESSED
-------------------------------------------------------
A Comeet board is `www.comeet.com/jobs/{slug}/{company_uid}` -- a slug AND a
company uid such as `98.008`, and the uid is not derivable from anything.
So `identifier_is_guessable` is False and a board reaches the registry only
by being published: the employer's own site links it (`tripleten.com` names
`comeet.com/jobs/tripleten/98.008` in its navigation), or an aggregator's
apply URL points at it (`pipeline/board_discovery.py`). The board identifier
is `slug/company_uid`, both parts, refused without either.

THE CONTRACT, MEASURED 2026-09-11 ON ONE BOARD
----------------------------------------------
The hosted page renders client-side and embeds `company_uid` and a company
token, then calls the documented Careers API 2.0:
`www.comeet.co/careers-api/2.0/company/{uid}/positions?token=...`. One read
of TripleTen's board: 335 rows for 47 vacancies. Each row carries `uid`,
`name`, `location{name, country (ISO alpha-2), city, state, timezone,
is_remote, location_uid}`, `workplace_type` (`Remote` / `On-site` /
`Hybrid`), `employment_type` (`Full-time` / `Part-time` / `Hourly`),
`experience_level`, `department`, `categories`, `time_updated`,
`url_active_page` and `position_url`. `?details=true` adds `details`, an
ordered list of `{name, value}` sections holding the advert as HTML.
`salary_range{min, max, currency_code, period}` is in the vendor's schema and
was absent on every row of that board. There is no `time_created`, so the
posted date is not known and is not invented from `time_updated`.

ONE VACANCY, MANY LOCATION ROWS
-------------------------------
A vacancy open in four countries is served as four rows: the base uid
(`62.F64`) with one location, and one variant per further location
(`62.F64-AE.302`, `62.F64-DA.406`, `62.F64-3C.40F`), same name, same advert.
They are one posting here, keyed on the base uid, whose `location_raw` is
every location in the order served and whose payload keeps every variant --
the Rippling and Workable rule, applied to a vendor that multiplies harder
(46 vacancies became 330 rows on the board measured). The variant uids are
kept so an aggregator that linked the Serbia variant resolves to the same
posting (`recognise_posting_url`).

WHAT A LOCATION IS
------------------
`workplace_type` is the employer's own work-model answer and is read into
`workplace`. `location` is where each variant is open from, and for a
`Remote` row that is where the role may be worked from -- which is what
`structured_geography` reads, with the country codes joined the way
Workable joins them so the gazetteer reads each as a country. A four-country
list that omits the reader's country refuses the reader, which is the
employer's own list outranking any aggregator's friendlier label.
`publishes_hiring_scope` is False: a location is never a declared scope on
its own.

THE TOKEN
---------
The token is read from the hosted page at collection time, held for the
life of the adapter instance and never persisted: it is stripped from every
archived payload (`position_url` carries it), kept out of every error and
out of the cache envelope through `safe_url` and `cache_key`, and never
written to a configuration file. It is the vendor's own mechanism for a
public page; no credential of the owner's is anywhere near this adapter.

AUTHORISATION
-------------
`www.comeet.com/robots.txt`, read 2026-09-11: `User-agent: *` excludes
search-result and category pages, `/wp-admin/` and a handful of theme
assets; `/jobs/` is allowed and no AI crawler is named. The Careers API
documentation addresses the customer who owns the token and says the API
"applies IP throttling when a large number of requests is made in a short
period"; one list request per board per run is well inside that. Recorded in
the catalogue as a judgement under ADR-0018 (silence is permission, bounded
by its five limits) rather than as a grant: no first-party sentence
addresses a third party reading the hosted page's token, and the owner may
reverse it.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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

HOSTED_HOST = "www.comeet.com"
API_BASE = "https://www.comeet.co/careers-api/2.0"

#: `slug/company_uid`: both parts, or it is not a board.
IDENTIFIER = re.compile(r"\A(?P<slug>[a-z0-9][a-z0-9-]{0,62})/(?P<uid>[A-Z0-9]{2}\.[A-Z0-9]{3})\Z")

#: A position uid: a base (`62.F64`) with an optional location variant
#: (`62.F64-AE.302`). The base is the vacancy; the variant is where.
POSITION_UID = re.compile(
    r"\A(?P<base>[A-Z0-9]{2}\.[A-Z0-9]{3})(?:-(?P<variant>[A-Z0-9]{2}\.[A-Z0-9]{3}))?\Z"
)

#: The hosted posting page: `/jobs/{slug}/{company_uid}/{position-slug}/{uid}`.
POSTING_URL = re.compile(
    r"\Ahttps?://(?:www\.)?comeet\.com/jobs/(?P<slug>[a-z0-9][a-z0-9-]{0,62})/"
    r"(?P<company>[A-Z0-9]{2}\.[A-Z0-9]{3})/(?P<position_slug>[^/?#]+)/"
    r"(?P<uid>[A-Z0-9]{2}\.[A-Z0-9]{3}(?:-[A-Z0-9]{2}\.[A-Z0-9]{3})?)/?(?:[?#].*)?\Z",
    re.IGNORECASE,
)

#: The token as the hosted page embeds it, in `COMPANY_DATA`.
_PAGE_TOKEN = re.compile(r'"token"\s*:\s*"(?P<token>[A-Za-z0-9]+)"')

#: `workplace_type` as the vendor spells it.
WORKPLACE: dict[str, str] = {
    "remote": "remote",
    "on-site": "onsite",
    "onsite": "onsite",
    "hybrid": "hybrid",
}

#: `employment_type` as the vendor spells it. `Hourly` is a pay basis rather
#: than a relationship and resolves to nothing rather than to a guess.
EMPLOYMENT_TYPES: dict[str, str] = {
    "full-time": "Full-time",
    "part-time": "Part-time",
    "contract": "Contract",
    "contractor": "Contract",
    "temporary": "Temporary",
    "internship": "Internship",
    "intern": "Internship",
}


def scrub_token(value: Any) -> Any:
    """The same value with any `token=` query parameter removed, recursively.

    Applied to every payload before it is archived, so the token the vendor
    embedded in `position_url` never reaches the database.
    """
    if isinstance(value, str) and "token=" in value:
        parts = urlsplit(value)
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "token"]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
        )
    if isinstance(value, dict):
        return {k: scrub_token(v) for k, v in value.items()}
    if isinstance(value, list):
        return [scrub_token(v) for v in value]
    return value


def base_uid(uid: str) -> str | None:
    match = POSITION_UID.match(uid.strip())
    return match.group("base") if match else None


def location_label(location: Any) -> str | None:
    """`City, CC` when the row names a city, else the vendor's own name.

    The country code stands alone after the comma so that a joined list of
    variants (`Madrid, ES / Belgrade, RS / Lisbon, PT / Warszawa, PL`) is
    the slash-list shape `match/places.py` reads country codes from, beside
    the city that confirms them.
    """
    if not isinstance(location, dict):
        return None
    city = str(location.get("city") or "").strip()
    country = str(location.get("country") or "").strip().upper()
    name = str(location.get("name") or "").strip()
    if city and country:
        return f"{city}, {country}"
    if country and not name:
        return country
    return name or None


def compose_description(details: Any) -> str:
    """The advert, every section in the order the employer put them.

    A section's name is a heading and its value is the HTML under it; the
    Get on Board correction applies -- nothing about the company that is not
    part of THIS advert is folded in, because Comeet keeps none there.
    """
    if not isinstance(details, list):
        return ""
    sections = [d for d in details if isinstance(d, dict) and str(d.get("value") or "").strip()]
    sections.sort(key=lambda d: int(d.get("order") or 0))
    parts: list[str] = []
    for section in sections:
        name = str(section.get("name") or "").strip()
        value = str(section["value"]).strip()
        parts.append(f"<h3>{name}</h3>\n{value}" if name else value)
    return "\n\n".join(parts)


def read_compensation(payload: Any) -> CompensationHint | None:
    """`salary_range{min, max, currency_code, period}`, when the employer set it.

    Absent on every row of the board measured, so read to the vendor's
    schema and defensively: a band needs a currency and at least one figure.
    """
    if not isinstance(payload, dict):
        return None
    band = payload.get("salary_range")
    if not isinstance(band, dict):
        return None

    def number(value: Any) -> float | None:
        try:
            n = float(value)
            return n if n > 0 else None
        except (TypeError, ValueError):
            return None

    low = number(band.get("min"))
    high = number(band.get("max"))
    currency = band.get("currency_code") or band.get("currency")
    if (low is None and high is None) or not isinstance(currency, str) or not currency.strip():
        return None
    period = {
        "year": "yearly",
        "yearly": "yearly",
        "annual": "yearly",
        "month": "monthly",
        "monthly": "monthly",
        "hour": "hourly",
        "hourly": "hourly",
        "day": "daily",
        "daily": "daily",
    }.get(str(band.get("period") or "").strip().lower())
    return CompensationHint(
        min_value=low,
        max_value=high,
        currency=currency.strip().upper(),
        period=period,
        source_field="salary_range",
    )


class ComeetProvider(JobProvider):
    name = "comeet"
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    #: A slug and a company uid; the uid is derivable from nothing.
    identifier_is_guessable = False
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=False,
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

    def __init__(self, fetcher: HttpFetcher, api_base: str = API_BASE):
        self._fetcher = fetcher
        self._api_base = api_base.rstrip("/")
        self._tokens: dict[str, str] = {}

    # -- identity ------------------------------------------------------------

    @staticmethod
    def _parts(board: BoardRef) -> tuple[str, str]:
        match = IDENTIFIER.match(board.board_identifier or "")
        if match is None:
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                "comeet",
                "Comeet needs `slug/company_uid`, both parts published by the employer",
            )
        return match.group("slug"), match.group("uid")

    def validate_board(self, board: BoardRef) -> str | None:
        if not IDENTIFIER.match(board.board_identifier or ""):
            return "Comeet needs `slug/company_uid`, both parts published by the employer"
        return None

    def board_url(self, board: BoardRef) -> str:
        slug, uid = self._parts(board)
        return f"https://{HOSTED_HOST}/jobs/{slug}/{uid}"

    def positions_url(self, board: BoardRef) -> str:
        """The API URL WITHOUT the token: what errors and the cache see."""
        _, uid = self._parts(board)
        return f"{self._api_base}/company/{uid}/positions?details=true"

    # -- the token -------------------------------------------------------------

    def _token(self, board: BoardRef) -> str:
        key = board.board_identifier or ""
        if key in self._tokens:
            return self._tokens[key]
        page_url = self.board_url(board)
        html = self._fetcher.get_text(page_url, use_cache=False)
        match = _PAGE_TOKEN.search(html)
        if match is None:
            # A consent interstitial or a rebranded page: the identity is not
            # on it, and nothing here guesses one.
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                page_url,
                "the hosted page did not publish the board's token; nothing was read further",
            )
        self._tokens[key] = match.group("token")
        return self._tokens[key]

    # -- listing --------------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        safe_url = self.positions_url(board)
        token = self._token(board)
        rows = self._fetcher.get_json(
            f"{safe_url}&token={token}",
            safe_url=safe_url,
            cache_key=safe_url,
        )
        if not isinstance(rows, list):
            raise FetchError(FetchErrorCategory.MALFORMED, safe_url, "positions must be a list")

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            if not isinstance(row, dict):
                self.postings_skipped += 1
                continue
            uid = str(row.get("uid") or "")
            base = base_uid(uid)
            name = row.get("name")
            if base is None or not isinstance(name, str) or not name.strip():
                self.postings_skipped += 1
                continue
            if row.get("is_internal") is True:
                self.postings_skipped += 1
                continue
            grouped.setdefault(base, []).append(row)

        for base, variants in grouped.items():
            # The base row first when it is there, the rest in the order
            # served; a vacancy served only as variants is still one vacancy.
            variants.sort(key=lambda r: str(r.get("uid")) != base)
            first = variants[0]
            labels: list[str] = []
            countries: list[str] = []
            for row in variants:
                label = location_label(row.get("location"))
                if label and label not in labels:
                    labels.append(label)
                country = (
                    str((row.get("location") or {}).get("country") or "").strip().upper()
                    if isinstance(row.get("location"), dict)
                    else ""
                )
                if country and country not in countries:
                    countries.append(country)
            yield self._stub(board, base, first, variants, labels, countries)

    def _stub(
        self,
        board: BoardRef,
        base: str,
        first: dict[str, Any],
        variants: list[dict[str, Any]],
        labels: list[str],
        countries: list[str],
    ) -> PostingStub:
        _, company_uid = self._parts(board)
        html = compose_description(first.get("details"))
        url = str(first.get("url_active_page") or first.get("url_comeet_hosted_page") or "")
        if not POSTING_URL.match(url):
            url = f"{self.board_url(board)}/{_slugify(str(first['name']))}/{base}"
        payload: dict[str, Any] = scrub_token(
            {
                **{k: v for k, v in first.items() if k != "details"},
                "uid": base,
                "location_variants": [
                    scrub_token({"uid": r.get("uid"), "location": r.get("location")})
                    for r in variants
                ],
                "location_countries": countries,
                "workplace": WORKPLACE.get(str(first.get("workplace_type") or "").strip().lower()),
                "employment_type": EMPLOYMENT_TYPES.get(
                    str(first.get("employment_type") or "").strip().lower()
                ),
                "source_posting_url": url,
                "apply_url": url,
            }
        )
        return PostingStub(
            external_id=f"comeet-{company_uid}-{base}",
            title=str(first["name"]).strip(),
            url=url,
            location_raw=" / ".join(labels) or None,
            department=(str(first["department"]).strip() if first.get("department") else None),
            # No `time_created` in the contract; `time_updated` is not it.
            posted_at=None,
            description_html=html or None,
            payload=payload,
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The list carried the advert; nothing is requested again."""
        html = stub.description_html or ""
        return RawPosting(stub, html, strip_html(html), dict(stub.payload))


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "position"


# -- identity from a URL --------------------------------------------------------


def recognise_posting_url(url: str) -> str | None:
    """`comeet-<company_uid>-<base_uid>` for a hosted posting URL, or None.

    A variant uid (`62.F64-AE.302`, the Serbia row) resolves to the BASE
    (`62.F64`): the aggregator that linked one location of a vacancy linked
    the vacancy, and ADR-0013 folds it onto the one posting stored here.
    """
    match = POSTING_URL.match(url.strip())
    if match is None:
        return None
    base = base_uid(match.group("uid").upper())
    return f"comeet-{match.group('company').upper()}-{base}" if base else None


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(slug/company_uid, external_id)` for a hosted posting URL, or None."""
    match = POSTING_URL.match(url.strip())
    if match is None:
        return None
    identifier = f"{match.group('slug').lower()}/{match.group('company').upper()}"
    return identifier, recognise_posting_url(url)
