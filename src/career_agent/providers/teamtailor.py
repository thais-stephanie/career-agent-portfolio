"""Teamtailor career sites, read through the RSS feed every tenant publishes.

A FAMILY, NOT A BOARD
---------------------
Teamtailor is an ATS: one adapter reaches every employer that publishes a
career site on it, the shape that gives this corpus its Greenhouse, Lever and
Ashby inventory. A tenant is `{slug}.teamtailor.com` or an employer's own
domain pointed at Teamtailor, and its openings are at `{origin}/jobs.rss`.

Measured 2026-09-11 on three real tenants: `career.teamtailor.com` (the
vendor's own company, 12 openings), `payfit.teamtailor.com` (9) and
`puzzle.teamtailor.com` (3). Each `<item>` carries `title`, `description`
(the whole advert as HTML), `pubDate`, `link` (the posting page on the
tenant), `guid`, `remoteStatus`, `<tt:locations>` with one or more
`<tt:location>` holding `city` and `country`, and `<tt:department>`. An
unregistered slug answers HTTP 404, so a wrong identifier is told apart from
an empty board -- which is what makes `identifier_is_guessable` True here and
False for Recruitee.

AUTHORISATION
-------------
`career.teamtailor.com/robots.txt`, read 2026-09-11: `User-Agent: *` with
`Disallow: /app/`, `/messages/`, `/messenger/`, `/facebook/tab/`,
`/jobs/internal/`, and `Content-Signal: search=yes, ai-train=no,
ai-input=yes`. `/jobs.rss` is not excluded and is the feed the vendor
publishes for exactly this. No AI crawler is named. The marketing host
`www.teamtailor.com` is `Disallow:` with nothing after it.

WHAT A LOCATION IS
------------------
`<tt:location>` is an office: a street address with a zip code. It is never
a hiring scope, so `publishes_hiring_scope` is False and the field reaches the
gate only through `structured_geography`, beside the tenant's own
`remoteStatus` -- which is what decides whether the office is where the work
is done or where the remote role is open from. `fully` is REMOTE and `hybrid`
is HYBRID; `none` and `temporary` resolve to nothing, because a vendor saying
a role is not remote has not said where its desk is.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator
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
)
from career_agent.providers.workable import strip_html

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
HOST = re.compile(r"^(?:[a-z0-9][a-z0-9-]*\.)+[a-z]{2,}$")
TT = "{https://teamtailor.com/locations}"
#: `/jobs/{id}-{slug}`: the one shape a Teamtailor posting URL takes, on the
#: vendor subdomain or on the tenant's own career host alike.
POSTING_PATH = re.compile(r"^/jobs/\d+(?:-|$)")

#: Labels on `teamtailor.com` that belong to the VENDOR, not to a tenant. A
#: careers page linking `www.teamtailor.com` has named the product, not a
#: board. `career` is deliberately absent: that is the vendor's own company
#: board, a real employer with real openings.
NOT_A_TENANT = frozenset(
    {"www", "app", "api", "cdn", "help", "docs", "support", "status", "trust", "discover", "blog"}
)

#: `remoteStatus` as the feed spells it, onto the work-model vocabulary the
#: pipeline resolves. Absent keys resolve to nothing on purpose.
REMOTE_STATUS: dict[str, str] = {"fully": "remote", "hybrid": "hybrid"}


def safe_link(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(value.strip())
        if u.scheme == "https" and u.hostname and not u.username and not u.password:
            return value.strip()
    except ValueError:
        pass
    return None


def _text(node: ET.Element | None) -> str | None:
    if node is None or node.text is None:
        return None
    text = node.text.strip()
    return text or None


def _published_at(raw: str | None) -> str | None:
    """`pubDate` is RFC 822 (`Fri, 24 Jul 2026 13:57:16 +0200`)."""
    if not raw:
        return None
    from email.utils import parsedate_to_datetime

    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return to_rfc3339_utc(raw)
    return to_rfc3339_utc(moment.isoformat())


def parse_feed(xml_text: str, origin_host: str) -> list[dict[str, Any]]:
    """Every item of one tenant's feed as a flat payload the pipeline can read.

    Raises on a document that is not an RSS channel: a malformed feed and an
    empty board are different outcomes.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise FetchError(FetchErrorCategory.MALFORMED, origin_host, f"not XML: {exc}") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise FetchError(FetchErrorCategory.MALFORMED, origin_host, "not an RSS channel")
    rows: list[dict[str, Any]] = []
    for item in channel.findall("item"):
        locations = []
        holder = item.find(f"{TT}locations")
        if holder is not None:
            for loc in holder.findall(f"{TT}location"):
                locations.append(
                    {
                        "name": _text(loc.find(f"{TT}name")),
                        "city": _text(loc.find(f"{TT}city")),
                        "country": _text(loc.find(f"{TT}country")),
                    }
                )
        rows.append(
            {
                "title": _text(item.find("title")),
                "description": (item.findtext("description") or "").strip() or None,
                "link": _text(item.find("link")),
                "guid": _text(item.find("guid")),
                "pubDate": _text(item.find("pubDate")),
                "remoteStatus": (_text(item.find("remoteStatus")) or "").lower() or None,
                "department": _text(item.find(f"{TT}department")),
                "role": _text(item.find(f"{TT}role")),
                "locations": locations,
            }
        )
    return rows


def location_of(row: dict[str, Any]) -> str | None:
    """`City, Country` per office, offices joined with ` | `. An office list."""
    parts = []
    for loc in row.get("locations") or []:
        bits = [loc.get("city"), loc.get("country")]
        text = ", ".join(b for b in bits if b)
        if text:
            parts.append(text)
    return " | ".join(dict.fromkeys(parts)) or None


class TeamtailorProvider(JobProvider):
    name = "teamtailor"
    kind = ProviderKind.ATS
    addresses_boards_by_company = True
    #: An unregistered slug answers 404 (measured), so `discover` may probe a
    #: bounded set of identifiers and tell a wrong one from an empty board.
    identifier_is_guessable = True
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        exposes_compensation=False,
        exposes_location_structured=True,
        exposes_remote_flag=True,
        exposes_employment_type=False,
        publishes_hiring_scope=False,
    )
    field_map = ProviderFieldMap((FieldMapping("workplace", MetadataDimension.WORK_MODEL_HINT),))

    def __init__(self, fetcher: HttpFetcher):
        self._fetcher = fetcher

    def validate_board(self, board: BoardRef) -> str | None:
        if not SLUG.fullmatch(board.board_identifier or ""):
            return "Teamtailor needs an employer-published tenant identifier"
        if board.board_identifier in NOT_A_TENANT:
            return f"{board.board_identifier!r} is the vendor's own host, not a tenant"
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
                return "Teamtailor custom board must be an explicit public HTTPS origin"
        return None

    def board_url(self, board: BoardRef) -> str:
        return (board.board_url or f"https://{board.board_identifier}.teamtailor.com").rstrip("/")

    def feed_url(self, board: BoardRef) -> str:
        return self.board_url(board) + "/jobs.rss"

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        if error := self.validate_board(board):
            raise FetchError(FetchErrorCategory.MALFORMED, self.name, error)
        url = self.feed_url(board)
        host = urlsplit(url).hostname or ""
        body = self._fetcher.get_text(url)
        # `host` names the feed for error messages; posting links may live on
        # the tenant's own career domain and are checked by shape below.
        seen: set[str] = set()
        for row in parse_feed(body, host):
            link = safe_link(row.get("link"))
            external = row.get("guid")
            if not link or not external or not row.get("title"):
                self.postings_skipped += 1
                continue
            if not POSTING_PATH.match(urlsplit(link).path or ""):
                # PayFit's feed, read from `payfit.teamtailor.com`, links each
                # item to `careers.payfit.com/jobs/{id}-...`: the tenant's own
                # custom career host (measured 2026-09-11, nine of nine). So
                # the host is not the check; the vendor's posting-path shape
                # is, and anything else is not a posting.
                self.postings_skipped += 1
                continue
            if external in seen:
                raise FetchError(FetchErrorCategory.MALFORMED, url, "duplicate guid")
            seen.add(external)
            payload = dict(row)
            payload["workplace"] = REMOTE_STATUS.get(row.get("remoteStatus") or "")
            payload["source_posting_url"] = link
            payload["apply_url"] = link
            yield PostingStub(
                external_id=f"teamtailor-{board.board_identifier}-{external}",
                title=str(row["title"]),
                url=link,
                location_raw=location_of(row),
                department=row.get("department"),
                posted_at=_published_at(row.get("pubDate")),
                description_html=row.get("description"),
                payload=payload,
            )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        html = stub.description_html or ""
        return RawPosting(stub, html, strip_html(html), dict(stub.payload))


#: `https://<slug>.teamtailor.com/jobs/<number>-<title>`. Only the vendor
#: subdomain names a tenant; an employer's own career host pointed at
#: Teamtailor is a family detection and not an identity (`site_detect`).
#: `careers`, `www`, `trust`, `discover` and `blog` are the vendor's own
#: hosts and never a customer board.
_BOARD_URL = re.compile(
    r"^https?://(?P<slug>[a-z0-9][a-z0-9-]{0,62})\.teamtailor\.com/jobs/\d+(?:-[^/?#]*)?/?"
    r"(?:[?#].*)?$",
    re.IGNORECASE,
)
_VENDOR_HOSTS = frozenset({"careers", "www", "trust", "discover", "blog", "career", "app"})


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(tenant, None)` for a Teamtailor-hosted posting URL, or None.

    No posting id: the feed's `guid` is not derivable from the page URL, so a
    caller validating the board compares the listing's own links instead.
    """
    match = _BOARD_URL.match(url.strip())
    if match is None:
        return None
    slug = match.group("slug").lower()
    if slug in _VENDOR_HOSTS:
        return None
    return slug, None
