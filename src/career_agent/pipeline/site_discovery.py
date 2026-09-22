"""Finding a board by reading the company's own careers page, not by guessing.

WHY THIS EXISTS BESIDE `discover.py`
-------------------------------------
`pipeline/discover.py` derives a small set of plausible identifiers from a
domain and a name, and asks each provider whether one of them is a board. That
is bounded, deterministic and honest, and for the four families it covers it
works because a board identifier is usually the company's own name.

Two families it cannot cover, for two different reasons, and both reasons are
about the same thing: **what an empty answer means.**

One family addresses a board by three values -- a tenant, a site and a numbered
data centre -- none of which can be derived from a domain. Guessing them would
be a namespace walk in three dimensions, which is precisely what
`candidate_identifiers` refuses to be.

The other addresses a board by a slug, and its host ANSWERS FOR A SLUG NOBODY
REGISTERED. A guess that returns no openings is indistinguishable from a real
board with nothing open, so `BoardOutcome.VALID_EMPTY` would be promoting a
board that does not exist. The project already has a name for that shape of
error.

So the identity comes from EVIDENCE the company published on its own site: a
link, a form action, a script or a reference to a vendor endpoint. A board is
promoted because the employer pointed at it.

SIX OUTCOMES, AND THE MIDDLE THREE ARE THE POINT
-------------------------------------------------
    REFUSED_BY_HOST     the host's own robots.txt refuses us
    UNREACHABLE         no careers page could be read
    NO_SIGNAL           read, and nothing named a family we recognise
    PROVIDER_DETECTED   a family is on the page and NO board identity is in it
    IDENTITY_RESOLVED   a family and an identity, and neither was guessed
    ALREADY_KNOWN       the corpus already holds this board

`PROVIDER_DETECTED` is the state a simpler design would not have had, and it is
the one that keeps this honest. A careers page that loads a vendor's widget
proves the vendor and not the board; reporting it as a board would invent an
identity, and reporting it as nothing would throw away a true finding.

WHAT IT NEVER DOES
------------------
**It writes nothing and it closes nothing.** A discovery pass that could mark a
posting closed would let a company changing its website retire jobs that are
still open -- and this reads pages that are outside our control, so a redirect,
a rebrand or a marketing rewrite is an ordinary event. It returns findings; the
caller decides.

It names no vendor. `providers/site_detect.py` holds every pattern, because
`test_provider_neutrality` forbids a vendor host in generic code and is right
to.

AND IT ASKS EACH HOST FIRST. Every source this product reads had its permission
checked by hand and quoted into the catalogue, which is a better answer than a
parser for eighteen vendors and no answer at all for two hundred and eighty
company websites. ADR-0018's fourth and fifth limits are what
`net/robots.py` implements: an explicit restriction obeyed when found, and a
statement about one host never read as covering another.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.net.robots import RobotsDecision, RobotsPolicy
from career_agent.providers.site_detect import SiteSignal, detect

#: The paths a company's openings live behind, in the order they are tried.
#:
#: SMALL, ORDERED AND CLOSED, for the same reason `candidate_identifiers` is:
#: five plausible paths is reading a website, and forty is crawling one. The
#: root is last rather than first because a home page mentions careers far less
#: often than a careers page does, and stopping at the first page that carries a
#: signal means the common case costs ONE request.
CAREERS_PATHS: tuple[str, ...] = (
    "/careers",
    "/jobs",
    "/careers/",
    "/about/careers",
    "/",
)

#: How much of a page is read before giving up on it.
#:
#: A careers page that is a megabyte of marketing has its board link in the
#: first few thousand characters like every other page; what a cap prevents is
#: one pathological response holding a scan of 280 companies open. Generous
#: enough that no honest page is truncated in the middle of a link.
MAX_PAGE_CHARS = 400_000


class SiteOutcome(StrEnum):
    """What reading one company's careers page established."""

    #: The host's own `robots.txt` refuses us, or could not be read at all.
    #:
    #: A SEPARATE OUTCOME from `UNREACHABLE`, and the separation is the point:
    #: one is a site we could not reach and the other is a site we chose not
    #: to. Merging them would let a policy refusal read as a network problem
    #: and be retried forever.
    REFUSED_BY_HOST = "REFUSED_BY_HOST"
    UNREACHABLE = "UNREACHABLE"
    NO_SIGNAL = "NO_SIGNAL"
    PROVIDER_DETECTED = "PROVIDER_DETECTED"
    IDENTITY_RESOLVED = "IDENTITY_RESOLVED"
    ALREADY_KNOWN = "ALREADY_KNOWN"

    @property
    def label(self) -> str:
        return {
            SiteOutcome.REFUSED_BY_HOST: "The host's own robots.txt refuses us",
            SiteOutcome.UNREACHABLE: "No careers page could be read",
            SiteOutcome.NO_SIGNAL: "Read, and nothing named a family we recognise",
            SiteOutcome.PROVIDER_DETECTED: "A family is on the page; no board identity in it",
            SiteOutcome.IDENTITY_RESOLVED: "A family and an identity, neither guessed",
            SiteOutcome.ALREADY_KNOWN: "This board is already in the registry",
        }[self]


@dataclass(frozen=True, slots=True)
class SiteFinding:
    """What one company's own site said about where its openings live."""

    company: str
    outcome: SiteOutcome
    #: The page the evidence came from. `None` when nothing was read.
    read_url: str | None = None
    signals: tuple[SiteSignal, ...] = ()
    #: Why nothing could be read, for an `UNREACHABLE`. Never a secret, and
    #: never the page body.
    detail: str | None = None

    @property
    def resolved(self) -> tuple[SiteSignal, ...]:
        """Only the signals that carry a board identity."""
        return tuple(signal for signal in self.signals if signal.identity)

    def as_dict(self) -> dict[str, Any]:
        return {
            "company": self.company,
            "outcome": self.outcome.value,
            "outcome_label": self.outcome.label,
            "read_url": self.read_url,
            "detail": self.detail,
            "signals": [
                {
                    "provider": signal.provider,
                    "kind": signal.kind.value,
                    "identity": signal.identity,
                    "parts": dict(signal.parts),
                    # QUOTED BACK, so a person can check the claim rather than
                    # trust it. The same rule ADR-0002 applies to a posting.
                    "evidence": signal.evidence,
                }
                for signal in self.signals
            ],
        }


@dataclass
class ScanSummary:
    """One pass over a set of companies, counted."""

    findings: list[SiteFinding] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        counts = {outcome.value: 0 for outcome in SiteOutcome}
        for finding in self.findings:
            counts[finding.outcome.value] += 1
        return counts

    @property
    def resolved(self) -> list[SiteFinding]:
        return [f for f in self.findings if f.outcome is SiteOutcome.IDENTITY_RESOLVED]


def careers_urls(website: str | None, domain: str | None) -> tuple[str, ...]:
    """The bounded set of pages to try for one company, in order.

    The company's declared `website` wins over a bare domain, because it is
    what the registry actually recorded and may already carry a subdomain or a
    path this product would not have guessed.
    """
    base = (website or "").strip().rstrip("/")
    if not base and domain:
        base = f"https://{domain.strip().rstrip('/')}"
    if not base:
        return ()
    if "//" not in base:
        base = f"https://{base}"
    return tuple(f"{base}{path}" for path in CAREERS_PATHS)


def read_site(
    fetcher: HttpFetcher,
    *,
    company: str,
    website: str | None = None,
    domain: str | None = None,
    known: frozenset[str] = frozenset(),
    robots: RobotsPolicy | None = None,
) -> SiteFinding:
    """Read one company's careers page and report what named a board there.

    STOPS AT THE FIRST PAGE THAT CARRIES A SIGNAL. Every further request is one
    somebody else pays to serve, and a company that names its board on
    `/careers` has answered the question.

    A page that cannot be read is `UNREACHABLE` and is NOT an absence of
    signal: those are different facts and a scan that merged them would report
    a network problem as evidence about a company.

    `known` is the set of `provider:identity` pairs the registry already holds,
    so a company whose board is already collected is reported rather than
    re-proposed.

    **`robots` IS ASKED BEFORE EVERY REQUEST, and passing `None` means it is
    not asked at all.** That default exists for the tests, which hand in a
    fetcher that cannot fetch; every caller that touches the network passes a
    policy, and `scan-career-sites` constructs one it cannot be run without.

    ADR-0018's fourth and fifth limits are what this implements: an explicit
    restriction obeyed when found, and a statement about one host never read as
    covering another. Until now every source's permission was a reviewed human
    observation recorded in the catalogue, which is a better answer for
    eighteen vendors and no answer at all for two hundred and eighty company
    websites.
    """
    urls = careers_urls(website, domain)
    if not urls:
        return SiteFinding(company, SiteOutcome.UNREACHABLE, detail="no website or domain")

    last_error: str | None = None
    #: Whether ANY page was actually read. Tracked rather than inferred from
    #: `last_error`: a company whose `/careers` reads cleanly and names nothing
    #: while its `/jobs` times out has been read, and reporting that as
    #: UNREACHABLE would file a network failure as evidence about a company.
    read_anything = False

    refused: str | None = None
    for url in urls:
        if robots is not None:
            decision = robots.decide(url)
            if decision is not RobotsDecision.ALLOWED:
                # RECORDED PER PATH rather than per host, because a robots file
                # can forbid `/jobs` and allow `/careers`, and refusing the
                # whole company on the first disallow would throw away a page
                # we are welcome to read.
                refused = f"{decision.value} on {url}"
                continue
        try:
            body = fetcher.get_text(url)
        except FetchError as exc:
            last_error = f"{exc.category.value} on {url}"
            continue
        read_anything = True
        signals = detect(body[:MAX_PAGE_CHARS])
        if not signals:
            continue
        if any(f"{s.provider}:{s.identity}" in known for s in signals if s.identity):
            return SiteFinding(company, SiteOutcome.ALREADY_KNOWN, url, signals)
        outcome = (
            SiteOutcome.IDENTITY_RESOLVED
            if any(signal.identity for signal in signals)
            else SiteOutcome.PROVIDER_DETECTED
        )
        return SiteFinding(company, outcome, url, signals)

    if read_anything:
        return SiteFinding(company, SiteOutcome.NO_SIGNAL, detail=last_error or refused)
    if refused is not None and last_error is None:
        # EVERY page was refused and none was even attempted. That is a
        # decision this product made, not a failure, and it must not be
        # reported as one.
        return SiteFinding(company, SiteOutcome.REFUSED_BY_HOST, detail=refused)
    return SiteFinding(company, SiteOutcome.UNREACHABLE, detail=last_error or refused)
