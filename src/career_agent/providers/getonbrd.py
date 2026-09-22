"""Get on Board, read from the category feeds it publishes.

Latin American technology hiring, and the closest thing to a documented public
job API in the region. It is the first source here whose market is the one this
product exists to serve.

**Provenance of this adapter.** The endpoint, the `expand[]=company` parameter,
the JSON:API field names and the category-splitting behaviour were learned from
Career-Ops's MIT-licensed `providers/getonbrd.mjs`, read 2026-09-07. No code was
copied: that file is a `.mjs` module returning a four-field object, and this one
implements a different protocol against a different persistence model. What was
adopted is knowledge of the vendor's shape, which is the expensive part.
`THIRD_PARTY_NOTICES.md` carries the notice.

**Authorisation, read first-party 2026-09-07.** `www.getonbrd.com/robots.txt`
answers `User-agent: *` with `Content-Signal: search=yes,ai-train=no,
use=reference` and `Allow: /`. A generic reader is permitted and `use=reference`
is a fair description of a personal job search.

**It also names `ClaudeBot` with `Disallow: /`.** Career Agent's collector is
not ClaudeBot -- it runs on the owner's machine under its own user agent and
trains nothing -- but the agent that WROTE this file is Claude, so it did not
call the endpoint. Every fixture beside this module is constructed from the
JSON:API contract above rather than captured from a live response, and each one
says so. **The first live retrieval belongs to the owner.** `sources.resolve`
already refuses to call a source operational without postings in the corpus, so
nothing can overstate this even if a document tried.

**What it supplies.** Title, company, country list, a remote flag, a real
publication date, and **the employer's whole posting**. This file used to say
the last of those did not exist, and promised the correction would be a
two-line change if the owner's first live retrieval found otherwise.

**It did.** 409 postings were collected 2026-09-07 and every one carries a
body. The reason the earlier reading was wrong is worth keeping: the feed does
not hold a posting in one element. It holds `functions`, `description`,
`desirable`, `benefits` and `perks` -- the fields Get on Board's own posting
form asks an employer to fill in separately -- so an adapter that read the key
literally named `description` got the requirements list on its own and
concluded the vendor published an excerpt. `compose_description` puts them
back in the order the board renders them.

Measured over those 409: `description` present on 409, `functions` on 408, and
the composed body running 167 words at the shortest, 433 at the median and 944
at the longest. `obtains_full_description` is True and every posting from here
is `FULL_CONTENT`, which is what makes this the first source in the corpus that
is both in this candidate's market and fully scoreable.

**One section is deliberately left out**, and `POSTING_SECTIONS` says which
and why.

**Why AGGREGATOR rather than ATS.** The canonical URL is `getonbrd.com`, the
board composes its own posting pages, and the employer's own record lives
elsewhere. Same reasoning as We Work Remotely, and when the answer is arguable
the safe side claims less.

**Categories, and why the default is not `programming`.** The board splits
leadership, data and machine-learning roles OUT of `programming`, so a search
that reads only that category misses most of what this candidate does. The
configured set is skills- and routines-oriented rather than title-oriented,
which is invariant 7 applied to retrieval rather than to scoring.

**A category is a place to look, never a reason to score.** The vocabulary used
to FIND a posting may not earn it points --
`tests/unit/test_retrieval_vocabulary.py` asserts that for Jooble and the same
rule binds here.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
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
from career_agent.providers.feeds import (
    BoundedWalk,
    FeedPage,
    PageBudget,
    deduplicate_by,
    walk_feed,
)

API_HOST = "https://www.getonbrd.com"

#: The one host this adapter will talk to.
#:
#: Checked on every URL it builds, because a category slug arrives from
#: configuration and a slug containing `..` or `//evil.example` would otherwise
#: become a request somewhere else. `CATEGORY_SLUG` is the first gate and this
#: is the second; neither alone is sufficient, and SSRF through a config file
#: is a real shape rather than a hypothetical one.
TRUSTED_HOST = "www.getonbrd.com"

#: Rows per page. The vendor's, not a preference: a short page is how a walk
#: knows it has finished, and a wrong page size makes a complete walk permanent.
PER_PAGE = 100

#: Lowercase alphanumeric words joined by single hyphens, anchored.
#:
#: Anchored so a configuration typo can never inject a path segment or a query
#: string into the feed URL. `programming` and `machine-learning-ai` pass;
#: `../admin`, `a?b` and `//host` do not.
CATEGORY_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

#: The categories this product asks for when configuration says nothing.
#:
#: **Chosen for the work, not for the titles.** The board splits leadership out
#: of `programming` and data out of both, so a candidate whose work is
#: automation, integrations and revenue operations is largely invisible to the
#: programming feed alone. These are the categories where that work is listed.
#:
#: Named rather than discovered: a category list is a fact about the site that
#: changes rarely, and crawling to learn it would be a request nobody needed.
#: The slugs are recorded from Career-Ops's catalogue and are validated against
#: the live board on the owner's first retrieval, not before.
DEFAULT_CATEGORIES: tuple[str, ...] = (
    "programming",
    "operations-management",
    "machine-learning-ai",
    "data-science-analytics",
    "sysadmin-devops-qa",
)

#: The most categories one retrieval may span.
#:
#: Each costs up to `max_pages` requests, so this is a bound on what a single
#: configuration line can spend on somebody else's server.
MAX_CATEGORIES = 12


GETONBRD_CAPABILITIES = ProviderCapabilities(
    # **True, corrected 2026-09-07 against the owner's own live retrieval.**
    #
    # This file used to say the category feed carried no description element,
    # and promised the correction would be a two-line change if the first live
    # retrieval found one. It did. 409 postings were persisted on 2026-09-07
    # and every one of them carries the employer's body in the list response,
    # split across labelled sections rather than held in a single element --
    # which is why reading the payload for a key called `description` and
    # finding a requirements list looked like an excerpt and was not.
    #
    # `compose_description` is what puts them back together, and
    # `POSTING_SECTIONS` names the five it will read. Measured over those 409:
    # `functions` present on 408, `description` on 409, and the composed body
    # runs 167 words at the shortest and 433 at the median. That is a posting,
    # not a teaser.
    full_description_in_list=True,
    # No second request is made and none is needed: the list response is the
    # whole posting. FULL_CONTENT here means "the employer's description as
    # published", and it is obtained in one page rather than 409 of them.
    obtains_full_description=True,
    # `attributes.published_at`, epoch SECONDS. A real publication date.
    exposes_posted_date=True,
    # No team or function element.
    exposes_department=False,
    # The board renders salary ranges on some postings; whether the public API
    # carries them has NOT been read from a first-party page. Declaring True
    # would make a missing figure read as "the employer did not say", which is
    # a different fact from "we never asked for it".
    exposes_compensation=False,
    # `attributes.countries` is an ARRAY of country names. A list rather than
    # prose, so the shape is structured, and `places.yaml` resolves names.
    exposes_location_structured=True,
    # `attributes.remote`, a boolean the employer sets.
    exposes_remote_flag=True,
    # Not read from a first-party page.
    exposes_employment_type=False,
    # **False, and this is the most consequential line in the file.**
    #
    # `countries` is where the posting says it is, and on a LATAM board a
    # remote role commonly carries the country the company sits in rather than
    # the countries it may hire from. Reading that as a hiring scope is exactly
    # the conflation invariant 3 forbids, and the asymmetry decides it: a false
    # PASS shows a job she cannot take, a false FAIL hides one she can, and a
    # False here yields UNRESOLVED rather than either.
    #
    # We Work Remotely earns True because its `region` element is the
    # employer's answer to that question and takes values like `USA Only`.
    # Nothing verified about this feed says the same of `countries`.
    publishes_hiring_scope=False,
)


GETONBRD_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="attributes.remote", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="attributes.countries", dimension=MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping(
            path="attributes.modality.data.attributes.name",
            dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT,
        ),
    )
)


class CategoryError(ValueError):
    """A configured category that cannot be requested as written."""


def resolve_categories(configured: Any) -> tuple[str, ...]:
    """The categories to read, validated, in configuration order, deduplicated.

    `None` takes `DEFAULT_CATEGORIES`. A string is one category. A list is
    several. Anything whose slug fails `CATEGORY_SLUG` is refused loudly rather
    than skipped: a typo that silently reads a different feed is worse than one
    that stops the run, because the first produces a plausible smaller corpus
    nobody questions.
    """
    if configured is None:
        return DEFAULT_CATEGORIES

    raw = [configured] if isinstance(configured, str) else list(configured)
    resolved: list[str] = []
    for item in raw:
        if not isinstance(item, str) or not CATEGORY_SLUG.match(item.strip()):
            raise CategoryError(
                f"invalid Get on Board category {item!r}. Expected a slug such as "
                '"programming" or "machine-learning-ai"'
            )
        slug = item.strip()
        if slug not in resolved:
            resolved.append(slug)

    if not resolved:
        raise CategoryError(
            "no Get on Board categories configured. Omit the setting to use the default set"
        )
    if len(resolved) > MAX_CATEGORIES:
        raise CategoryError(
            f"{len(resolved)} Get on Board categories configured; the cap is {MAX_CATEGORIES}. "
            "Each one costs up to max_pages requests."
        )
    return tuple(resolved)


def assert_trusted(url: str) -> str:
    """The second gate on a URL built from configuration.

    Refuses anything that is not HTTPS on the one host this adapter reads. The
    slug pattern is the first gate; this catches whatever a future edit lets
    through.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https":
        raise CategoryError(f"Get on Board URL must be https: {url}")
    if parts.hostname != TRUSTED_HOST:
        raise CategoryError(
            f"untrusted Get on Board host {parts.hostname!r}: must be {TRUSTED_HOST}"
        )
    return url


def _published_at(attributes: Any) -> str | None:
    """`published_at`, epoch SECONDS, as an RFC 3339 UTC string.

    Seconds rather than milliseconds is the vendor's encoding and getting it
    wrong by a factor of a thousand puts every posting in 1970 or in the far
    future -- which reads as a freshness result rather than as a parsing bug.

    Anything that is not a positive finite number returns None. A date this
    adapter cannot read is an absent date, never today's.
    """
    if not isinstance(attributes, dict):
        return None
    value = attributes.get("published_at")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if value <= 0:
        return None
    # `to_rfc3339_utc` takes an epoch in MILLISECONDS -- it is the same
    # normaliser Lever's epoch goes through -- and Get on Board states SECONDS.
    # The conversion happens here, in the adapter, because the factor of a
    # thousand is a fact about this vendor's encoding rather than about time.
    return to_rfc3339_utc(int(value) * 1000)


def _location(attributes: Any) -> str | None:
    """The country list as the payload stated it, joined for display.

    `remote` is NOT folded in here. It is a separate dimension with its own
    field mapping, and collapsing "remote" and "Chile" into one string would
    discard which of the two the employer actually said -- the conflation
    invariant 3 keeps three questions apart to prevent.
    """
    if not isinstance(attributes, dict):
        return None
    countries = attributes.get("countries")
    if isinstance(countries, str):
        return countries.strip() or None
    if not isinstance(countries, list):
        return None
    named = [c.strip() for c in countries if isinstance(c, str) and c.strip()]
    return ", ".join(named) or None


def _company(attributes: Any) -> str | None:
    """The embedded company name, which `expand[]=company` is asked for.

    Absent without that parameter, and absent for a posting whose company was
    deleted. None rather than a placeholder: a company named "Get on Board"
    would be a company this adapter invented, and ADR-0008 makes a board one
    employer.
    """
    if not isinstance(attributes, dict):
        return None
    company = attributes.get("company")
    if not isinstance(company, dict):
        return None
    data = company.get("data")
    if not isinstance(data, dict):
        return None
    inner = data.get("attributes")
    if not isinstance(inner, dict):
        return None
    name = inner.get("name")
    return name.strip() or None if isinstance(name, str) else None


#: The posting's own sections, in the order the board renders them.
#:
#: **Five, and the sixth is deliberately not here.** `projects` is present on
#: all 409 postings the owner collected and reads as company and product
#: marketing -- "En Improving South America, brindamos servicios de TI...",
#: "Entre pymes, el primer financiamiento no es un credito". It is the
#: employer describing itself, not the role.
#:
#: Folding that into the description is precisely the defect V1.2 measured and
#: closed: 598 of 1,030 postings were VERIFIED_ELIGIBLE because a sentence of
#: product marketing mentioned a place. `HIRING_INTENT` now anchors a scope
#: phrase to a sentence about hiring, so the composed body would probably
#: survive it -- but "probably survives the guard" is a worse reason to
#: include text than "is about the job" is to exclude it. The company's own
#: prose stays in the archived payload, where anything that wants it can read
#: it, and out of the text the matcher scores.
#:
#: Each entry pairs the section with the key holding the EMPLOYER'S OWN
#: heading for it. `benefits_headline` is "Beneficios que ofrecemos" because
#: that employer wrote it; this adapter never supplies a heading of its own,
#: because a heading this system invented would be text in the description
#: that no one at the company ever published.
POSTING_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("functions", "functions_headline"),
    ("description", "description_headline"),
    ("desirable", "desirable_headline"),
    ("benefits", "benefits_headline"),
    ("perks", None),
)


def _section_html(attributes: dict[str, Any], key: str, headline_key: str | None) -> str | None:
    """One section as HTML, headed by the employer's own words or by nothing."""
    body = attributes.get(key)
    if not isinstance(body, str) or not body.strip():
        return None
    body = body.strip()

    headline = attributes.get(headline_key) if headline_key else None
    if not isinstance(headline, str) or not headline.strip():
        return body

    from html import escape

    return f"<h3>{escape(headline.strip())}</h3>\n{body}"


def compose_description(attributes: Any) -> tuple[str, str]:
    """The employer's posting, reassembled from the sections the feed splits it into.

    Returns `(html, text)`, both empty when the payload carries no section.

    **Why composition rather than one field.** Get on Board's posting form asks
    for responsibilities, requirements, nice-to-haves and benefits separately,
    and its API hands them back the same way. Every other adapter here reads
    one element because every other vendor stores one. Reading only the element
    literally named `description` would take the requirements list and drop the
    responsibilities -- which is how this source spent a day looking like it
    published a 109-word excerpt.

    **It is deterministic, and that is load-bearing.** `pipeline/facts.py`
    rebuilds a posting's facts from its archived payload, so a rescore has to
    reach the same bytes this composition produced at collection time. Section
    order is fixed by `POSTING_SECTIONS`, never by iteration over a dict, and
    the separator is constant. Same payload, same description, forever.

    **Nothing here is written by this system.** The sections are the
    employer's, the headings are the employer's, and a section the employer
    left empty produces no heading rather than an empty one. That is what
    keeps ADR-0002 true of the result: every sentence in the composed body is a
    contiguous run of bytes somebody at the company actually published, so a
    quote drawn from it can still be verified against the source.
    """
    if not isinstance(attributes, dict):
        return "", ""
    parts = [
        html
        for key, headline_key in POSTING_SECTIONS
        if (html := _section_html(attributes, key, headline_key)) is not None
    ]
    if not parts:
        return "", ""
    html = "\n".join(parts)
    return html, html_to_text(html)


def public_url(resource: Any) -> str | None:
    """`links.public_url`, accepted only as HTTPS on the trusted host.

    A URL from a payload is untrusted input. It reaches the interface as a
    link a person clicks, so a `javascript:` scheme or an off-host redirect
    target is an attack surface rather than a data-quality issue. Anything that
    is not an absolute HTTPS URL on `www.getonbrd.com` returns None and the row
    is dropped.
    """
    if not isinstance(resource, dict):
        return None
    links = resource.get("links")
    if not isinstance(links, dict):
        return None
    raw = links.get("public_url")
    if not isinstance(raw, str) or not raw.strip():
        return None

    from urllib.parse import urlsplit

    parts = urlsplit(raw.strip())
    if parts.scheme != "https" or parts.hostname != TRUSTED_HOST:
        return None
    return raw.strip()


def to_stub(resource: Any) -> PostingStub | None:
    """One JSON:API resource into a stub, or None when it cannot be addressed.

    Four things are required and none is invented: an `id`, a title, a usable
    public URL, and that is it. A resource missing any of them is not a posting
    this system can address, and counting it is more honest than inventing the
    missing half.
    """
    if not isinstance(resource, dict):
        return None
    attributes = resource.get("attributes")
    if not isinstance(attributes, dict):
        return None

    external_id = resource.get("id")
    if not isinstance(external_id, str | int) or isinstance(external_id, bool):
        return None
    external_id = str(external_id).strip()
    if not external_id:
        return None

    title = attributes.get("title")
    if not isinstance(title, str) or not title.strip():
        return None

    url = public_url(resource)
    if url is None:
        return None

    return PostingStub(
        external_id=external_id,
        title=title.strip(),
        url=url,
        location_raw=_location(attributes),
        department=None,
        posted_at=_published_at(attributes),
        description_html=compose_description(attributes)[0] or None,
        payload=dict(resource),
    )


def company_of(resource: Any) -> str | None:
    """The employer this resource names, for the collector to resolve."""
    if not isinstance(resource, dict):
        return None
    return _company(resource.get("attributes"))


@dataclass(frozen=True)
class CategoryRead:
    """One category's walk, and what it could not reach.

    `walk` carries the bound. A source panel that said "collected" for a run
    that stopped after three of forty pages would be describing a page budget
    as a market.
    """

    category: str
    walk: BoundedWalk
    unaddressable: int = 0

    @property
    def resources(self) -> tuple[Any, ...]:
        return self.walk.entries


class GetonbrdProvider(JobProvider):
    """The Get on Board public category feeds."""

    name = "getonbrd"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    #: `board_identifier` names a CATEGORY. Asking this adapter about one
    #: employer is a question in the wrong vocabulary, exactly as for WWR.
    addresses_boards_by_company = False
    capabilities = GETONBRD_CAPABILITIES
    field_map = GETONBRD_FIELD_MAP

    def __init__(
        self,
        fetcher: HttpFetcher,
        api_host: str = API_HOST,
        max_pages: int | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")
        self._budget = PageBudget.resolve(PER_PAGE, max_pages)

    # -- URLs --------------------------------------------------------------

    def feed_url(self, category: str, page: int) -> str:
        """One page of one category, with the company embedded.

        `expand[]=company` is what makes the employer's name available at list
        level. Without it every posting would need a second request to learn
        who is hiring, which is a request per posting for a field the vendor
        will hand over for free.
        """
        if not CATEGORY_SLUG.match(category):
            raise CategoryError(f"invalid Get on Board category {category!r}")
        base = f"{self._api_host}/api/v0/categories/{category}/jobs"
        return assert_trusted(f"{base}?per_page={PER_PAGE}&expand[]=company&page={page}")

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this category up by hand."""
        category = board.board_identifier or DEFAULT_CATEGORIES[0]
        if not CATEGORY_SLUG.match(category):
            return f"{self._api_host}/jobs"
        return f"{self._api_host}/categories/{category}"

    # -- reading -----------------------------------------------------------

    def read_category(self, category: str, *, use_cache: bool = True) -> CategoryRead:
        """One category, walked to its end or to the page budget.

        A failed request raises. It is never converted into an empty page: an
        empty category and a broken request are different outcomes, and
        `providers/base.py` states why conflating them lets a network blip
        close a company's whole posting history.
        """
        unaddressable = 0

        def read_page(page: int) -> FeedPage:
            nonlocal unaddressable
            url = self.feed_url(category, page)
            body = self._fetcher.get_json(url, use_cache=use_cache)
            if not isinstance(body, dict) or not isinstance(body.get("data"), list):
                keys = ", ".join(sorted(body)) if isinstance(body, dict) else "not an object"
                raise ValueError(
                    f"Get on Board returned an unexpected shape for category {category!r} "
                    f"page {page}: expected {{'data': [...]}}, got [{keys}]"
                )
            resources = tuple(body["data"])
            return FeedPage(
                url=url,
                entries=resources,
                page=page,
                claimed_total=_claimed_total(body),
            )

        walk = walk_feed(category, read_page, self._budget)
        addressable = tuple(r for r in walk.entries if to_stub(r) is not None)
        unaddressable = len(walk.entries) - len(addressable)
        return CategoryRead(category=category, walk=walk, unaddressable=unaddressable)

    def read_categories(
        self, categories: tuple[str, ...] | None = None, *, use_cache: bool = True
    ) -> tuple[CategoryRead, ...]:
        """Every configured category, in order.

        Deduplication across categories happens in `list_postings`, not here,
        so each read keeps its own honest count of what that category returned.
        """
        resolved = resolve_categories(categories)
        return tuple(self.read_category(c, use_cache=use_cache) for c in resolved)

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        """`board_identifier` is a CATEGORY here, and only ever a category.

        The class comment above has said so since this adapter was written and
        nothing consulted it. On the real corpus 2026-09-08, 134 `source_board`
        rows carry this provider with a company slug in that column -- written
        as provenance by the dedicated collector, read as a category by the
        generic one, which would ask the board for a category named after an
        employer.
        """
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", *DEFAULT_CATEGORIES):
            return None
        return (
            f"{identifier!r} is not a Get on Board category. This source is addressed by "
            f"CATEGORY, never by employer. Configured categories: "
            f"{', '.join(DEFAULT_CATEGORIES)}. Collect it with `career-agent collect-getonbrd`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings from one category, which `board_identifier` names.

        A posting listed under several categories is returned once per walk,
        by exact vendor id. That is `feeds.deduplicate_by`, and it is NOT
        ADR-0013 deduplication: this is the same feed handing back the same row
        in one pass, not two sources describing one job.
        """
        category = board.board_identifier or DEFAULT_CATEGORIES[0]
        read = self.read_category(category)
        for resource in deduplicate_by(read.resources, _identity):
            stub = to_stub(resource)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it, body included.

        No second request is made, and now that is a statement about how much
        this vendor hands over in one page rather than about how little.
        `compose_description` reassembles the employer's sections from the same
        resource the stub was built from, so the body a rescore reconstructs
        from the archived payload is the body collection stored.
        """
        del board
        attributes = stub.payload.get("attributes") if isinstance(stub.payload, dict) else None
        html, text = compose_description(attributes)
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=text,
            payload=dict(stub.payload),
        )


def _identity(resource: Any) -> str | None:
    if not isinstance(resource, dict):
        return None
    value = resource.get("id")
    if isinstance(value, bool) or not isinstance(value, str | int):
        return None
    return str(value).strip() or None


def _claimed_total(body: dict[str, Any]) -> int | None:
    """`meta.total`, when the response states one.

    Recorded and never used as a stop condition. Gupy returns `total: 100` when
    `limit=100`, so a walk that trusted a paginated response's own total would
    end on the first page; `walk_feed` stops on a short page instead.
    """
    meta = body.get("meta")
    if not isinstance(meta, dict):
        return None
    total = meta.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total if total >= 0 else None
