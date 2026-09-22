"""Reading a company's careers page, and what each answer is allowed to mean.

WHY THIS FILE EXISTS
--------------------
Six outcomes, and the value of the design is entirely in keeping four of them
apart:

    REFUSED_BY_HOST     the host said not to, and we did not
    UNREACHABLE         no page could be read
    NO_SIGNAL           read, and nothing named a family we recognise
    PROVIDER_DETECTED   a family is on the page and no board identity is in it

Merging any two of those would report a network failure, a policy refusal, or a
widget, as evidence about a company. Every assertion below is about a boundary
between them.

**Nothing here opens a socket.** The fetcher is a stub whose whole behaviour is
a mapping from URL to page, so what is under test is the reading rather than
the network.
"""

from __future__ import annotations

from career_agent.net.fetcher import FetchError, FetchErrorCategory
from career_agent.net.robots import RobotsDecision
from career_agent.pipeline.site_discovery import (
    CAREERS_PATHS,
    SiteOutcome,
    careers_urls,
    read_site,
)

BOARD_LINK = '<a href="https://acmehealth.recruitee.com/o/nurse">Open roles</a>'
WIDGET_ONLY = "<div data-recruitee-widget></div>"
NOTHING = "<h1>We are hiring</h1><p>Email us.</p>"


class StubFetcher:
    """A fetcher that cannot fetch. It answers from a dict and counts calls.

    Structural rather than polite: a test that could reach the network would
    pass or fail on somebody else's website, and this module's whole subject is
    what to conclude from a page.
    """

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url: str, use_cache: bool = True) -> str:  # noqa: FBT001, FBT002
        del use_cache
        self.calls.append(url)
        if url in self.pages:
            return self.pages[url]
        raise FetchError(FetchErrorCategory.NOT_FOUND, url, "404")


# =========================================================================
# 1. THE PAGES IT TRIES
# =========================================================================


def test_the_paths_are_few_ordered_and_end_at_the_root() -> None:
    """Five plausible paths is reading a website; forty is crawling one.

    The root is LAST because a home page mentions careers far less often than a
    careers page does, and the order is what makes the common case one request.
    """
    assert len(CAREERS_PATHS) <= 6
    assert CAREERS_PATHS[0] == "/careers"
    assert CAREERS_PATHS[-1] == "/"


def test_the_declared_website_wins_over_a_bare_domain() -> None:
    """It is what the registry actually recorded, and it may carry a subdomain
    or a path this product would never have guessed."""
    urls = careers_urls("https://jobs.acme.invalid", "acme.invalid")
    assert urls[0] == "https://jobs.acme.invalid/careers"


def test_a_company_with_neither_is_unreachable_rather_than_silent() -> None:
    finding = read_site(StubFetcher({}), company="acme")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.UNREACHABLE
    assert finding.detail == "no website or domain"


def test_it_stops_at_the_first_page_that_answers() -> None:
    """Every further request is one somebody else pays to serve."""
    fetcher = StubFetcher({"https://acme.invalid/careers": BOARD_LINK})
    read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert fetcher.calls == ["https://acme.invalid/careers"]


# =========================================================================
# 2. THE THREE OUTCOMES THAT MUST NOT MERGE
# =========================================================================


def test_a_page_that_names_a_board_resolves_it() -> None:
    fetcher = StubFetcher({"https://acme.invalid/careers": BOARD_LINK})
    finding = read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.IDENTITY_RESOLVED
    assert [s.identity for s in finding.resolved] == ["acmehealth"]
    assert finding.read_url == "https://acme.invalid/careers"


def test_a_widget_is_a_detection_and_not_a_board() -> None:
    """The finding a simpler design would have thrown away or over-claimed."""
    fetcher = StubFetcher({"https://acme.invalid/careers": WIDGET_ONLY})
    finding = read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.PROVIDER_DETECTED
    assert finding.resolved == ()
    assert finding.signals, "the detection itself must survive"


def test_a_page_that_reads_and_names_nobody_is_not_unreachable() -> None:
    """The boundary that matters most.

    A company whose careers page reads cleanly and names no vendor has been
    READ, and reporting that as unreachable would file a fact about the company
    as a fact about the network -- and would put it back in the queue for
    another scan forever.
    """
    fetcher = StubFetcher({p: NOTHING for p in ("https://acme.invalid" + x for x in CAREERS_PATHS)})
    finding = read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.NO_SIGNAL


def test_one_page_read_among_failures_is_still_read() -> None:
    """`/careers` 404s and `/jobs` answers with nothing. The company has been
    read. Reporting UNREACHABLE because SOMETHING failed would be the same
    merge, arrived at from the other side."""
    fetcher = StubFetcher({"https://acme.invalid/jobs": NOTHING})
    finding = read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.NO_SIGNAL


def test_a_site_that_answers_nothing_at_all_is_unreachable() -> None:
    fetcher = StubFetcher({})
    finding = read_site(fetcher, company="acme", domain="acme.invalid")  # type: ignore[arg-type]
    assert finding.outcome is SiteOutcome.UNREACHABLE
    assert finding.detail and "NOT_FOUND" in finding.detail


# =========================================================================
# 3. WHAT IT DOES WITH WHAT IS ALREADY KNOWN
# =========================================================================


def test_a_board_the_registry_already_holds_is_reported_not_reproposed() -> None:
    fetcher = StubFetcher({"https://acme.invalid/careers": BOARD_LINK})
    finding = read_site(
        fetcher,  # type: ignore[arg-type]
        company="acme",
        domain="acme.invalid",
        known=frozenset({"recruitee:acmehealth"}),
    )
    assert finding.outcome is SiteOutcome.ALREADY_KNOWN


# =========================================================================
# 4. IT ASKS THE HOST FIRST
# =========================================================================


class _Refuses:
    """A robots policy that refuses one path and allows the rest."""

    def __init__(self, forbidden: str) -> None:
        self.forbidden = forbidden

    def decide(self, url: str) -> RobotsDecision:
        return RobotsDecision.DISALLOWED if url.endswith(self.forbidden) else RobotsDecision.ALLOWED


class _RefusesEverything:
    def decide(self, url: str) -> RobotsDecision:
        del url
        return RobotsDecision.DISALLOWED


def test_a_refused_path_is_skipped_and_the_next_one_is_tried() -> None:
    """Recorded PER PATH rather than per host: a robots file can forbid
    `/jobs` and allow `/careers`, and refusing the whole company on the first
    disallow would throw away a page we are welcome to read."""
    fetcher = StubFetcher(
        {
            "https://acme.invalid/careers": NOTHING,
            "https://acme.invalid/jobs": BOARD_LINK,
        }
    )
    finding = read_site(
        fetcher,  # type: ignore[arg-type]
        company="acme",
        domain="acme.invalid",
        robots=_Refuses("/careers"),  # type: ignore[arg-type]
    )
    assert "https://acme.invalid/careers" not in fetcher.calls
    assert finding.outcome is SiteOutcome.IDENTITY_RESOLVED


def test_a_host_that_refuses_everything_is_not_reported_as_unreachable() -> None:
    """A decision this product made, not a failure.

    Merging the two would let a policy refusal read as a network problem and be
    retried on every later scan, forever.
    """
    fetcher = StubFetcher({"https://acme.invalid/careers": BOARD_LINK})
    finding = read_site(
        fetcher,  # type: ignore[arg-type]
        company="acme",
        domain="acme.invalid",
        robots=_RefusesEverything(),  # type: ignore[arg-type]
    )
    assert finding.outcome is SiteOutcome.REFUSED_BY_HOST
    assert fetcher.calls == [], "a refused host must not be fetched at all"


# =========================================================================
# 5. IT WRITES NOTHING
# =========================================================================


def test_the_module_cannot_reach_a_database() -> None:
    """A discovery pass that could mark a posting closed would let a company
    changing its website retire jobs that are still open -- and this reads
    pages outside our control, so a redirect or a rebrand is an ordinary
    event.

    Asserted structurally, because a behavioural test would only prove that
    today's code path happens not to write.
    """
    import ast
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "src"
        / "career_agent"
        / "pipeline"
        / "site_discovery.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    }
    assert not any("storage" in name for name in imported), imported
    assert "sqlite3" not in source
