"""Shared retrieval machinery for paginated feeds.

Three adapters had already been written against paginated JSON before this
module existed, and each grew its own answer to the same three questions: how
far may I walk, how do I know I reached the end, and how do I say what I could
not reach. Speedrun's answer is the most developed one and it is the ancestor
of everything here.

**What this module is for.** A provider adapter should describe one vendor's
shape. Deciding when a walk has finished is not a fact about a vendor -- it is
a fact about paginated feeds -- and writing it once means the next adapter
inherits the traps rather than rediscovering them.

**The traps this encodes, each of which cost a real measurement.**

*An empty page is not always the end.* Speedrun returns intermittent 500s and
a 200 carrying `jobs: []` on page 3 of 40 was once reported as a complete pass.
`BoundedWalk.truncated` is how that stops being invisible.

*A page limit is not a truncation.* Stopping because the caller asked for two
pages is a deliberate bound and must read differently from stopping because
the feed broke. `stopped_early` and `truncated` are separate fields for that
reason, and both are fields rather than log lines.

*A short page is the end.* A feed that hands back fewer rows than the page size
was asked for has nothing more at this scope. This is the ordinary termination
and it is the only one that means "complete".

**What is deliberately NOT here.** No retry policy, no rate limiter and no
cache: `net/fetcher.py` owns all three, for every provider, and a second copy
inside the provider layer is how two backoff behaviours start disagreeing about
what a 429 means. No relevance, no eligibility and no scoring -- this module is
inside `providers/` and `tests/unit/test_provider_neutrality.py` is right to
forbid that.

**`speedrun.FeedWalk` is not migrated onto this, and that is deliberate.** It
carries a clamp detector for an API that answers page 200 again above page 200,
and a contract validator against a published OpenAPI document, neither of which
generalises. Rewriting a working, measured walk to sit on a younger abstraction
is a refactor with no behaviour change and real regression risk. It is worth
doing and it is its own commit, not a side effect of adding a provider.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

#: The most pages any single walk may request, whatever a caller configures.
#:
#: A ceiling on the ceiling. A configuration typo asking for 10,000 pages is a
#: request this product should refuse rather than serve politely, because the
#: cost lands on somebody else's server. Adapters may set a lower default; none
#: may exceed this.
#:
#: **Raised from 50 to 100 on 2026-09-09, and the number is now derived rather
#: than chosen.** The right ceiling for "how far may one walk go" is the most a
#: vendor itself is willing to serve, and the strictest one this product has
#: measured is Gupy: `offset` is refused at 10,000, which at its 100-row pages
#: is exactly 100 pages. At 50 this constant was stopping walks at HALF what
#: the vendor would have answered -- a politeness nobody asked for, paid in
#: postings a candidate never saw, and reported as `stopped_early` so it looked
#: like a decision rather than an arbitrary constant.
#:
#: It changes no default. `DEFAULT_MAX_PAGES` is still 3 and every adapter's
#: own default is untouched; this only widens what a caller may ASK for.
MAX_PAGES_CAP = 100

#: What a walk asks for when a caller says nothing.
#:
#: Deliberately small. A first run should be cheap and legible, and a person
#: who wants the whole feed can say so. Defaulting high is how a preflight
#: becomes a crawl by accident.
DEFAULT_MAX_PAGES = 3


class PageBudgetError(ValueError):
    """A configured page budget that cannot be honoured as written."""


@dataclass(frozen=True)
class PageBudget:
    """How far one walk may go, resolved once and carried.

    Separate from the adapter so that "how much may I read" is answerable
    without constructing an HTTP client, and so that the same sentence is not
    written into every provider with a slightly different cap.

    `page_size` is the vendor's, not a preference: an adapter states what the
    endpoint serves per page, because a short page is the termination signal
    and a wrong page size turns a complete walk into a permanent one.
    """

    page_size: int
    max_pages: int = DEFAULT_MAX_PAGES

    def __post_init__(self) -> None:
        if self.page_size < 1:
            raise PageBudgetError(f"page_size must be positive, got {self.page_size}")
        if self.max_pages < 1:
            raise PageBudgetError(f"max_pages must be positive, got {self.max_pages}")
        if self.max_pages > MAX_PAGES_CAP:
            raise PageBudgetError(
                f"max_pages {self.max_pages} exceeds the cap of {MAX_PAGES_CAP}. "
                "The cap is not a preference: the cost of a runaway walk lands "
                "on the vendor's server."
            )

    @classmethod
    def resolve(cls, page_size: int, max_pages: int | None) -> PageBudget:
        """A budget from optional configuration, with the default applied.

        `None` means "the caller did not say" and takes the default. It does
        NOT mean unlimited: there is no way to express unlimited here, on
        purpose.
        """
        return cls(
            page_size=page_size, max_pages=DEFAULT_MAX_PAGES if max_pages is None else max_pages
        )


@dataclass(frozen=True)
class FeedPage:
    """One response, and the entries it carried.

    `claimed_total` is whatever the response said it holds, when it says
    anything. It is recorded and never trusted as a stop condition: Gupy
    returns `total: 100` when `limit=100` and 82,958 when `limit=10`, and a
    walk that stopped on `total` would have ended on the first page.
    """

    url: str
    entries: tuple[Any, ...]
    page: int
    claimed_total: int | None = None

    def short_for(self, page_size: int) -> bool:
        """Fewer entries than a full page: the ordinary end of a walk."""
        return len(self.entries) < page_size


@dataclass(frozen=True)
class BoundedWalk:
    """Everything one walk read, and everything it could not reach.

    The two ways a walk can end without being complete are separate fields
    because they mean opposite things to a person reading a source panel.
    `stopped_early` is this product deciding to stop. `truncated` is the feed
    failing to serve what it said it had. Reporting either as "collected" is
    the silent-truncation failure this class exists to prevent.
    """

    scope: str
    pages: tuple[FeedPage, ...]
    budget: PageBudget
    stopped_early: bool = False

    @property
    def entries(self) -> tuple[Any, ...]:
        return tuple(entry for page in self.pages for entry in page.entries)

    @property
    def claimed_total(self) -> int | None:
        """What the feed said it holds at this scope, as of the first page."""
        return self.pages[0].claimed_total if self.pages else None

    @property
    def reachable_total(self) -> int | None:
        """The most this walk could have retrieved, budget included.

        `None` when the feed states no total. A number here is a CEILING and
        never a promise: it is what the budget allows, not what arrived.
        """
        total = self.claimed_total
        if total is None:
            return None
        return min(total, self.budget.page_size * self.budget.max_pages)

    @property
    def truncated(self) -> bool:
        """The walk ended on an empty page the feed had not finished serving.

        False when the walk was bounded by the page budget: that is a
        deliberate limit, and `stopped_early` is the field that says so.
        """
        if self.stopped_early or not self.pages:
            return False
        last = self.pages[-1]
        if last.entries:
            return False
        total = self.claimed_total
        if total is None:
            return False
        return len(self.entries) < total

    @property
    def hit_page_limit(self) -> bool:
        """The budget ran out before the feed did.

        Distinct from `truncated`: nothing failed, and there is more to read.
        A source panel should say "2 pages of an unknown number" rather than
        implying the feed was exhausted.
        """
        return len(self.pages) >= self.budget.max_pages and bool(
            self.pages and not self.pages[-1].short_for(self.budget.page_size)
        )

    @property
    def complete(self) -> bool:
        """Whether this walk reached the end of the feed at this scope."""
        return not (self.truncated or self.hit_page_limit or self.stopped_early)


#: Given a page number, return that page. Raises `FetchError` on failure.
#:
#: A walk never converts a failure into an empty page: an empty board and a
#: failed request are different outcomes, and conflating them lets a network
#: blip close a company's whole posting history. `providers/base.py` states the
#: same rule for `list_postings` and this is where a paginated adapter honours
#: it -- by not catching.
PageReader = Callable[[int], FeedPage]


def walk_feed(
    scope: str,
    read_page: PageReader,
    budget: PageBudget,
    first_page: int = 1,
) -> BoundedWalk:
    """Read a paginated feed to its end, or to the budget, whichever is first.

    `first_page` exists because feeds disagree about whether pages are counted
    from zero or one, and getting it wrong silently skips or repeats a page.
    An adapter states its vendor's convention; it is never guessed.

    Termination, in the order it is checked:

    1. a short page -- fewer entries than the page size -- is the end;
    2. an empty page is the end, and `truncated` decides whether that was
       legitimate;
    3. the budget is the end, and `hit_page_limit` says so.

    Exceptions are NOT caught. A `FetchError` on page 3 must reach the caller
    as a failure, because a walk that swallowed it would report two pages of
    postings as the whole feed.
    """
    pages: list[FeedPage] = []
    for offset in range(budget.max_pages):
        page = read_page(first_page + offset)
        pages.append(page)
        if not page.entries or page.short_for(budget.page_size):
            break
    return BoundedWalk(scope=scope, pages=tuple(pages), budget=budget)


def deduplicate_by(entries: Sequence[Any], key: Callable[[Any], str | None]) -> tuple[Any, ...]:
    """First sighting wins, over an EXACT key.

    Used where one walk covers several scopes -- Get on Board lists a posting
    under every category it belongs to -- so the same posting arrives more than
    once inside a single retrieval.

    **This is not deduplication in the ADR-0013 sense and must not be confused
    with it.** That question is "are these two observations the same job", it is
    answered across sources against stored rows, and it never compares titles.
    This is narrower: the same feed handed us the same row twice in one pass,
    identified by a key the adapter builds from the vendor's own identifier.
    An entry whose key is None is kept, because dropping a row for lacking an
    id would be discarding a posting to tidy a list.
    """
    seen: set[str] = set()
    kept: list[Any] = []
    for entry in entries:
        identity = key(entry)
        if identity is not None:
            if identity in seen:
                continue
            seen.add(identity)
        kept.append(entry)
    return tuple(kept)
