"""We Work Remotely, read from the feed it publishes.

The first source here that is neither an applicant tracking system nor an API.
It is a job board with an RSS feed, and RSS is a format whose entire purpose is
to be read by a machine -- which is why this is a documented public interface
rather than merely a reachable one. Their `robots.txt` allows `/` and closes
only account and administration paths.

**What it supplies, and it is the reason it was built.** The whole posting.
Roughly 1,200 words of HTML in `description`, not an excerpt, plus a stable
`guid`, a real `pubDate` and a `region`. This product's matcher reads bodies,
so a source that supplies excerpts makes every posting from it look thin on
evidence rather than genuinely thin -- which is exactly the trade Jooble forces
and exactly what WWR does not.

**What it does not supply: completeness.** The feed is a WINDOW of the most
recent postings, twenty-five of them, with no pagination parameter and no
archive. Collecting it repeatedly accumulates postings over time and never
yields a whole board. `RetrievalMode.AGGREGATOR_FEED` carries that, and nothing
built on this may present its counts as coverage of remote hiring.

**Why `AGGREGATOR` and not `ATS`.** The employer writes the posting and WWR
publishes it, so the text has a good claim to being the employer's own. Three
things argue the other way and the conservative reading wins: the canonical URL
is WWR's page rather than the employer's, WWR composes the title itself as
`Company: Role`, and the HTML is WWR's rendering. `ProviderKind` exists so that
nothing downstream presents a third party's rendering as the employer's own
record, and when the answer is arguable the safe side of that question is the
side that claims less.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)

FEED_BASE = "https://weworkremotely.com"

#: The feeds this adapter knows how to ask for. Named rather than discovered,
#: because a category list is a fact about the site that changes rarely and a
#: crawl to learn it would be a request nobody needed to make.
#:
#: `remote-jobs` is everything; the rest narrow it. They OVERLAP -- a posting in
#: the programming feed is in the global one too -- which is fine and is the
#: collector's problem to deduplicate, not a reason to pick only one.
FEEDS: dict[str, str] = {
    "all": "/remote-jobs.rss",
    "programming": "/categories/remote-programming-jobs.rss",
    "devops": "/categories/remote-devops-sysadmin-jobs.rss",
    "design": "/categories/remote-design-jobs.rss",
    "business": "/categories/remote-business-exec-management-jobs.rss",
    "product": "/categories/remote-product-jobs.rss",
    "customer-support": "/categories/remote-customer-support-jobs.rss",
    "sales-marketing": "/categories/remote-marketing-jobs.rss",
    "all-other": "/categories/all-other-remote-jobs.rss",
}

#: How WWR writes a title: `Company: Role`. Splitting on the first colon is the
#: whole company resolution this source needs, and it is deliberately not clever
#: -- a title with no colon keeps its whole text as the role and resolves no
#: company, which is honest rather than a guess.
TITLE_SPLIT = re.compile(r"^(?P<company>[^:]{1,80}):\s*(?P<role>.+)$", re.DOTALL)

#: `, remote` and its variants, which WWR appends to almost every role. Removed
#: from the ROLE only. It is not a fact about the work that the matcher should
#: read twice, and `region` already carries the answer.
TRAILING_REMOTE = re.compile(r"\s*[,-]\s*remote\s*$", re.IGNORECASE)


WWR_CAPABILITIES = ProviderCapabilities(
    # The whole posting, in the feed, in one request. The reason this source is
    # worth having and the thing that distinguishes it from every aggregator
    # that returns a snippet.
    full_description_in_list=True,
    # The category feed carries the employer's full advert text.
    obtains_full_description=True,
    # `pubDate`, RFC 822, on every item measured. A real publication date, not
    # an update timestamp.
    exposes_posted_date=True,
    # No team, function or department field.
    exposes_department=False,
    # Nothing structured. Salary appears inside the description prose on some
    # postings, which is text and is read as text by the matcher like any other
    # sentence -- it is not a field, and declaring one would make a missing
    # figure read as "the employer did not say".
    exposes_compensation=False,
    # `region` is free text: `Anywhere in the World`, `USA Only`, `Europe`. One
    # string, no country code, so the SHAPE is unstructured.
    exposes_location_structured=False,
    # Every posting on this board is remote, which sounds like a remote flag and
    # is not one. "Remote" here is the board's admission criterion, not a
    # statement about WHERE the employer may hire -- and `region` is the field
    # that answers that. Conflating them is precisely invariant 3.
    exposes_remote_flag=False,
    # No employment-type element on the category feeds.
    exposes_employment_type=False,
    # THE reason this board is worth anything to somebody in Brazil. `region`
    # is not where the company sits -- every company here is remote -- it is
    # the employer's answer to where it may hire, and the board publishes both
    # `Anywhere in the World` and `USA Only`.
    publishes_hiring_scope=True,
)


WWR_FIELD_MAP = ProviderFieldMap(
    (
        # `region` and NOT the fact that the board is remote-only. This is the
        # employer's stated hiring scope in their own words, which is the one
        # geography question that matters, and it is why this source is worth
        # anything to somebody in Brazil: `Anywhere in the World` and `USA Only`
        # are different answers and the board publishes both.
        FieldMapping("region", MetadataDimension.HIRING_LOCATION_HINT),
    )
)

# Archived but unmapped, each for a stated reason:
#   category      - `Full-Stack Programming`, the board's own taxonomy. Real,
#                   and not one of this system's dimensions
#   guid, link    - identity, resolved by the collector
#   media:content - a company logo URL
#   title         - carries the company, split by `split_title` rather than by
#                   the field map, which maps FIELDS and not compositions


@dataclass(frozen=True, slots=True)
class FeedRead:
    """One feed, fetched once, with what it did not tell us kept visible."""

    feed: str
    url: str
    items: tuple[dict[str, Any], ...]
    #: Items the feed carried that could not be addressed. Counted rather than
    #: dropped in silence: a feed that suddenly stops carrying `guid` should
    #: read as a broken contract, not as a quiet day.
    unaddressable: int = 0

    @property
    def empty(self) -> bool:
        """A successful read that found nothing.

        Distinct from a failure, and the distinction is load-bearing: an empty
        feed must never be a reason to close jobs. `HttpFetcher` raises on a
        failure, so reaching this at all means the request succeeded.
        """
        return not self.items


def split_title(raw: str) -> tuple[str | None, str]:
    """`Company: Role, remote` into its parts.

    Returns `(None, raw)` when there is no colon, because a title this adapter
    cannot parse is a title whose company it does not know -- and inventing one
    from the first two words is the sort of fuzzy identity ADR-0008 closes by
    forbidding.
    """
    match = TITLE_SPLIT.match(raw.strip())
    if match is None:
        return None, TRAILING_REMOTE.sub("", raw.strip())
    company = match.group("company").strip()
    role = TRAILING_REMOTE.sub("", match.group("role").strip())
    return (company or None), (role or raw.strip())


class WwrProvider(JobProvider):
    """The We Work Remotely RSS feeds."""

    name = "wwr"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    # Its feeds are CATEGORIES. `board_identifier` names one, so asking this
    # adapter about an employer is a question in the wrong vocabulary.
    addresses_boards_by_company = False
    capabilities = WWR_CAPABILITIES
    field_map = WWR_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, feed_base: str = FEED_BASE) -> None:
        self._fetcher = fetcher
        self._feed_base = feed_base.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def feed_url(self, feed: str = "all") -> str:
        if feed not in FEEDS:
            known = ", ".join(sorted(FEEDS))
            raise ValueError(f"unknown WWR feed {feed!r}. Known feeds: {known}")
        return f"{self._feed_base}{FEEDS[feed]}"

    def validate_board(self, board: BoardRef) -> str | None:
        """Is `board_identifier` a thing this source can be asked for?

        **The namespace check, before any socket opens.** `board_identifier`
        means a FEED here and a company slug elsewhere, and the collector walks
        one table holding both. Measured on the real corpus 2026-09-08: 72
        `source_board` rows carry `provider='wwr'` with a company slug in that
        column -- `6sense`, `airbnb`, `databricks` -- because the aggregator
        collector records which employer a posting came from and the generic
        collector reads the same row as somewhere to fetch.

        Asking for the feed `6sense` was correctly refused by `feed_url`. What
        was wrong is WHERE that refusal happened: inside `list_postings`,
        mid-run, as an exception that ended the whole collection. A structural
        mismatch is knowable before the first request and is reported as one.

        `board_providers()` has said this in prose since WWR was added: a
        company slug is a question in the wrong vocabulary. This is that
        sentence made answerable.
        """
        feed = board.board_identifier or "all"
        if feed in FEEDS:
            return None
        return (
            f"{feed!r} is not a We Work Remotely feed. This source is addressed by "
            f"CATEGORY, never by employer. Known feeds: {', '.join(sorted(FEEDS))}"
        )

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this employer up by hand.

        WWR has no per-company board -- it is a feed, and `RetrievalMode` says
        so -- but the protocol asks, and a search link is the honest answer to
        the question rather than a URL that does not exist.
        """
        return board.board_url or f"{self._feed_base}/remote-jobs/search?term={board.company_slug}"

    # -- reading -----------------------------------------------------------

    def read_feed(self, feed: str = "all", *, use_cache: bool = True) -> FeedRead:
        """One feed, parsed. One request, and never more than one.

        There is no pagination to walk: the feed is a fixed window of the most
        recent postings. That is a limitation of the source and is carried as
        one; a paginating loop here would be inventing an interface.
        """
        url = self.feed_url(feed)
        text = self._fetcher.get_text(url, use_cache=use_cache)
        return self.parse(feed, url, text)

    def parse(self, feed: str, url: str, text: str) -> FeedRead:
        """RSS to rows, tolerating everything except a broken document.

        A feed that does not parse is a failure and raises. A feed that parses
        and carries nothing is an empty success, which is the distinction the
        whole lifecycle depends on.
        """
        root = ElementTree.fromstring(text)
        rows: list[dict[str, Any]] = []
        unaddressable = 0

        for item in root.iter("item"):
            row = _row(item)
            if row.get("guid") and row.get("title"):
                rows.append(row)
            else:
                unaddressable += 1

        return FeedRead(feed=feed, url=url, items=tuple(rows), unaddressable=unaddressable)

    # -- the protocol ------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings from one feed, which `board_identifier` names.

        The protocol asks for a board and this source has feeds, so the board
        identifier is a FEED name. That is a smaller lie than registering the
        whole site as one employer's board would be, and it is why
        `retrieval_mode` exists to say the shape out loud.
        """
        read = self.read_feed(board.board_identifier or "all")
        for row in read.items:
            stub = self.to_stub(row)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it. No second request exists or is needed."""
        del board
        html = str(stub.payload.get("description") or "")
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=_text_of(html),
            payload=dict(stub.payload),
        )

    # -- one row -----------------------------------------------------------

    def to_stub(self, row: dict[str, Any]) -> PostingStub | None:
        guid = str(row.get("guid") or "").strip()
        raw_title = str(row.get("title") or "").strip()
        if not guid or not raw_title:
            return None

        _, role = split_title(raw_title)
        html = str(row.get("description") or "")
        return PostingStub(
            external_id=external_id_from(guid),
            title=role,
            url=str(row.get("link") or guid).strip(),
            location_raw=(str(row.get("region") or "").strip() or None),
            department=None,
            posted_at=_published(row.get("pubDate")),
            description_html=html or None,
            payload=dict(row),
        )


def external_id_from(guid: str) -> str:
    """The posting slug, which is what WWR's `guid` actually is.

    The whole URL would work as an identifier and would break the day the site
    changes scheme or host. The slug is the stable part, it is unique across the
    board, and it is what the URL is built from either way.
    """
    return guid.rstrip("/").rsplit("/", 1)[-1] or guid


def company_of(row: dict[str, Any]) -> str | None:
    """The employer, read from the title WWR composed."""
    company, _ = split_title(str(row.get("title") or ""))
    return company


def _row(item: ElementTree.Element) -> dict[str, Any]:
    """One `<item>` as a flat mapping, namespaces stripped from tag names."""
    row: dict[str, Any] = {}
    for child in item:
        tag = child.tag.rsplit("}", 1)[-1]
        value = (child.text or "").strip()
        if not value and child.attrib:
            # `media:content` carries its URL in an attribute rather than a body.
            value = str(child.attrib.get("url") or "")
        if value:
            row[tag] = value
    return row


def _published(value: Any) -> str | None:
    """`pubDate`, RFC 822, normalised to the project's RFC 3339 UTC."""
    if not value:
        return None
    try:
        # RFC 822 in, ISO 8601 out, then through the project's one normaliser so
        # every provider writes the same 25 characters into the same column.
        return to_rfc3339_utc(parsedate_to_datetime(str(value)).isoformat())
    except (TypeError, ValueError):
        return None


_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"[ \t]*\n[ \t]*")


def _text_of(html: str) -> str:
    """Readable text from the feed's HTML, for the matcher to read.

    Deliberately a small transform rather than a parser: block tags become line
    breaks, every other tag is dropped, entities are unescaped. The ORIGINAL
    HTML is archived beside it, so ADR-0002's contiguous-substring check has
    exact bytes to verify against whatever this produced.
    """
    import html as html_module

    broken = re.sub(r"</(p|div|li|h[1-6]|tr)>", "\n", html, flags=re.IGNORECASE)
    broken = re.sub(r"<br\s*/?>", "\n", broken, flags=re.IGNORECASE)
    broken = re.sub(r"<li[^>]*>", "- ", broken, flags=re.IGNORECASE)
    text = html_module.unescape(_TAGS.sub("", broken))
    return _WHITESPACE.sub("\n", text).strip()
