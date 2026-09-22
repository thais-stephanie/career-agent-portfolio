"""RemoteSource: a discovery index over ATS boards, and deliberately nothing more.

WHAT IT IS
----------
`remotesource.com` republishes remote postings and, on every page sampled
(170 of 170 across two passes on 2026-09-11), links the ORIGINAL applicant
tracking system: Workday, Greenhouse, Lever, Ashby, Rippling, iCIMS,
SmartRecruiters. Its sitemap names the employer in every posting URL
(`/jobs/<id>-<title>-at-<employer>`), so one page per employer is enough to
learn which board that employer runs.

WHAT IT IS NOT
--------------
A posting source. Two of its fields fail this product's rules on their own
terms: its hiring scope carries `eligibilityConfidence: "parsed"` -- the
aggregator's own reading of the advert, which invariant 5 does not accept as
evidence -- and part of its salary is `market_estimate`, an estimate presented
beside real figures. Storing a RemoteSource row would also duplicate a
posting this product can collect from the employer's own board with the
employer's own words. So nothing here yields a `PostingStub`, and there is no
`JobProvider` in this module: `pipeline/board_discovery.py` reads the index
to find boards and hands every board to the family that owns it.

PERMISSION
----------
`www.remotesource.com/robots.txt`, read 2026-09-11: `User-Agent: *`,
`Allow: /`, `Disallow: /api/`, `/admin/`, `/saved-jobs/`, `/alerts/`, the
account paths and `/ingest/`; `Sitemap: /sitemap.xml`. `/terms` addresses
intellectual property and "interfering with the Service" and says nothing
about automated reading. So the sitemap and the job pages are read; `/api/`
is never called, which is why the job object is read out of the page's own
server payload rather than asked for.

THE READ IS BOUNDED BY CONSTRUCTION
-----------------------------------
One request per employer, not per posting. 33,163 postings sat behind 2,305
employers on the day this was measured, and the identity of a board does
not change from one of an employer's postings to the next.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from career_agent.net.fetcher import FetchError, HttpFetcher

HOST = "www.remotesource.com"
SITEMAP_INDEX_URL = f"https://{HOST}/sitemap.xml"

#: A posting URL in the sitemap: `/jobs/<id>-<title-words>-at-<employer>`.
#: The employer is the last `-at-` split, which the vendor writes on 33,053 of
#: 33,163 URLs; the 110 without it are recorded as having no employer key
#: rather than guessed from the title.
_JOB_PATH = re.compile(r"^/jobs/(?P<slug>[^/?#]+)$")

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One posting URL from the sitemap, with the employer it names."""

    url: str
    employer_key: str | None


@dataclass(frozen=True, slots=True)
class PageLead:
    """What one job page says about where the posting really lives."""

    origin_url: str | None
    source_type: str | None
    employer_name: str | None
    employer_website: str | None


def sitemap_shards(xml_text: str) -> list[str]:
    """The shard URLs a sitemap index names, in the order it names them."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    return [
        loc.text.strip()
        for loc in root.iter(f"{_SITEMAP_NS}loc")
        if loc.text and loc.text.strip().startswith(f"https://{HOST}/sitemap/")
    ]


def index_entries(xml_text: str) -> list[IndexEntry]:
    """Every posting URL in one shard, with its employer key.

    Non-posting URLs (`/privacy`, `/compare/...`, category pages) are in the
    same shard and are skipped by path shape, never by guessing at content.
    """
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    entries: list[IndexEntry] = []
    for loc in root.iter(f"{_SITEMAP_NS}loc"):
        text = (loc.text or "").strip()
        if not text:
            continue
        parts = urlsplit(text)
        if parts.netloc.lower() != HOST:
            continue
        match = _JOB_PATH.match(parts.path)
        if match is None:
            continue
        slug = match.group("slug")
        employer = slug.rsplit("-at-", 1)[1] if "-at-" in slug else None
        entries.append(IndexEntry(url=text, employer_key=employer or None))
    return entries


def group_by_employer(entries: list[IndexEntry]) -> dict[str, list[str]]:
    """Posting URLs per employer key, in index order. Keyless entries are dropped:
    an employer this index could not name is not one it can hand over."""
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        if entry.employer_key:
            grouped.setdefault(entry.employer_key, []).append(entry.url)
    return grouped


# -- the job page --------------------------------------------------------------
#
# A Next.js page whose server payload carries the job object as JSON text
# inside a script, with the quotes escaped once (`\"applicationUrl\":\"...\"`).
# The keys are read by name from that text; nothing here executes the page or
# asks the site's `/api/`.

_ESCAPED_STRING = r'\\"{key}\\":\\"(?P<value>(?:[^"\\]|\\\\.)*)\\"'


def _escaped_field(html: str, key: str) -> str | None:
    match = re.search(_ESCAPED_STRING.format(key=re.escape(key)), html)
    if match is None:
        return None
    raw = match.group("value")
    # The value is JSON-escaped twice: once as a JSON string, once again for
    # embedding in the page script. Undo the outer layer, then the inner.
    try:
        once = json.loads(f'"{raw}"')
        value = json.loads(f'"{once}"') if "\\" in once else once
    except (json.JSONDecodeError, ValueError):
        return None
    return value.strip() or None


def _plain_field(html: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}":"(?P<value>(?:[^"\\]|\\.)*)"', html)
    if match is None:
        return None
    try:
        value = json.loads(f'"{match.group("value")}"')
    except (json.JSONDecodeError, ValueError):
        return None
    return value.strip() or None


def read_page(html: str) -> PageLead:
    """The origin pointer and the employer, read out of one job page.

    `applicationUrl` is the employer's own apply link when `sourceType` is
    `ats`; anything else is recorded as what it says and never promoted.
    """

    def field(key: str, text: str = html) -> str | None:
        return _escaped_field(text, key) or _plain_field(text, key)

    origin = field("applicationUrl")
    if origin and not origin.lower().startswith(("http://", "https://")):
        origin = None
    # The employer is the FIRST `company` object on the page: the job's own.
    # A bare `name` would find the page's meta tags first, and a later
    # `company` object belongs to a "similar jobs" block.
    company = _company_block(html)
    return PageLead(
        origin_url=origin,
        source_type=field("sourceType"),
        employer_name=field("name", company) if company else None,
        employer_website=field("websiteUrl", company) if company else None,
    )


#: `\"company\":{...}` in the escaped payload, or `"company":{...}` plain.
_COMPANY_BLOCK = re.compile(r'(?:\\)?"company(?:\\)?":\{(?P<body>[^{}]*)\}')


def _company_block(html: str) -> str | None:
    match = _COMPANY_BLOCK.search(html)
    return match.group("body") if match else None


# -- fetching ------------------------------------------------------------------


def fetch_index(fetcher: HttpFetcher) -> list[IndexEntry]:
    """Every posting URL the sitemap index reaches. Seven requests today."""
    entries: list[IndexEntry] = []
    for shard_url in sitemap_shards(fetcher.get_text(SITEMAP_INDEX_URL)):
        entries.extend(index_entries(fetcher.get_text(shard_url)))
    return entries


def fetch_lead(fetcher: HttpFetcher, url: str) -> PageLead | None:
    """One job page, read for its origin pointer. None when it could not be read."""
    try:
        html = fetcher.get_text(url, use_cache=False)
    except FetchError:
        return None
    return read_page(html)


def payload_summary(lead: PageLead) -> dict[str, Any]:
    return {
        "origin_url": lead.origin_url,
        "source_type": lead.source_type,
        "employer_name": lead.employer_name,
        "employer_website": lead.employer_website,
    }
