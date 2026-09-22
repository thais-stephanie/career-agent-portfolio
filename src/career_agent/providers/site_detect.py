"""Which applicant tracking system a company's own careers page is wired to.

WHY THIS EXISTS, AND WHY IT IS NOT `discover.probe_board`
----------------------------------------------------------
`pipeline/discover.py` finds a board by GUESSING an identifier -- `acme` from
`acme.com` -- and asking each provider whether that identifier is a board. It
is bounded, deterministic and honest, and for four vendors it works because a
board identifier is usually the company's own name.

**It cannot work for the two this module is about**, and for two different
reasons.

A Workday board is not `{company}`. It is a TENANT, a SITE and a numbered data
centre -- `{tenant}.wd5.myworkdayjobs.com/{site}` -- and none of the three is
derivable from a domain. Guessing them would be a namespace walk with three
dimensions, which is the thing `candidate_identifiers` exists to refuse.

A Recruitee board IS `{slug}.recruitee.com`, and that is the trap. The endpoint
answers for a slug nobody has registered, so a guess that returns no openings
is indistinguishable from a real board with nothing open -- and
`BoardOutcome.VALID_EMPTY` would be promoting a board that does not exist. The
project has a name for this shape of error: absence is never permission.

So the identity comes from EVIDENCE the company published: a link, a script, an
embed or an API reference on its own careers page. A board is promoted because
the employer pointed at it, not because a URL answered.

WHY THE PATTERNS LIVE IN `providers/`
--------------------------------------
`tests/unit/test_provider_neutrality.py` forbids generic code from spelling a
vendor host, and it is right to. `pipeline/site_discovery.py` fetches a page
and asks this module what is in it; only this file knows any vendor's name.

WHAT A SIGNAL IS AND IS NOT
----------------------------
A `SiteSignal` says: this family appears on this page, here is the exact text
that says so, and here is the identity IF the text carried one. Those are three
separate facts and the caller must be able to tell them apart -- a page that
loads a vendor's widget without naming a tenant is a DETECTION with no identity,
and reporting it as a board would be inventing one.

Nothing here fetches, validates or promotes anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class SignalKind(StrEnum):
    """How strong the evidence is, worst to best.

    The order is documentation rather than arithmetic, but it is the order a
    caller should prefer: a link a company published to its own board names the
    board, and a script tag says only which vendor is loaded.
    """

    #: A vendor script or widget on the page. Names the family, rarely the board.
    EMBED = "EMBED"
    #: A reference to a vendor API endpoint in the page source.
    API = "API"
    #: A link or a form action pointing at the vendor's board.
    LINK = "LINK"


@dataclass(frozen=True, slots=True)
class SiteSignal:
    """One vendor's fingerprint on one page.

    `identity` is the board this company has THERE, when the evidence carried
    one, and `None` when it did not. The two are never merged: a page can
    prove a family without proving a board, and a caller that treated the
    absence of an identity as an empty board would promote something nobody
    published.

    `evidence` is the matched text, trimmed. It is quoted back in the report so
    a person can check the claim rather than trust it -- the same rule ADR-0002
    applies to a posting's quotes.
    """

    provider: str
    kind: SignalKind
    evidence: str
    identity: str | None = None
    #: The parts of a composite identity, for a vendor whose board is not one
    #: string. Empty for the vendors whose board is a slug.
    parts: tuple[tuple[str, str], ...] = ()


# =========================================================================
# RECRUITEE
# =========================================================================
#
# The board is `{slug}.recruitee.com`, and the API under it is documented as a
# public careers endpoint. What is NOT safe is deriving the slug from a domain:
# the host answers for slugs nobody registered, so a guess cannot be told from
# a real board with nothing open.

#: `https://acme.recruitee.com/...`, in a link, a form action or a script src.
#: The slug is the label, and `careers`/`www` are rejected because both are
#: vendor-owned marketing hosts rather than a customer board.
_RECRUITEE_HOST = re.compile(
    r"https?://(?P<slug>[a-z0-9][a-z0-9-]{0,62})\.recruitee\.com(?P<path>/[^\s\"'<>]*)?",
    re.IGNORECASE,
)

#: Hosts on `recruitee.com` that belong to the VENDOR rather than to a customer.
#: Matching one and calling it a board would file the vendor as an employer.
_RECRUITEE_NOT_A_BOARD = frozenset({"www", "careers", "api", "cdn", "help", "docs", "app"})

#: The careers widget, which names the family and no board. Recruitee's embed
#: script and the attribute its container carries.
_RECRUITEE_EMBED = re.compile(
    r"(?:recruitee[-_]?(?:careers|widget|embed)|data-recruitee|rt-widget)",
    re.IGNORECASE,
)


def _recruitee(html: str) -> list[SiteSignal]:
    found: list[SiteSignal] = []
    seen: set[str] = set()
    for match in _RECRUITEE_HOST.finditer(html):
        slug = match.group("slug").lower()
        if slug in _RECRUITEE_NOT_A_BOARD or slug in seen:
            continue
        seen.add(slug)
        path = (match.group("path") or "").lower()
        found.append(
            SiteSignal(
                provider="recruitee",
                # A page that references the API is pointing at the machine
                # surface rather than at a marketing page, which is stronger
                # evidence that this is where the openings actually live.
                kind=SignalKind.API if "/api/" in path else SignalKind.LINK,
                evidence=match.group(0)[:200],
                identity=slug,
            )
        )
    if not found:
        embed = _RECRUITEE_EMBED.search(html)
        if embed is not None:
            found.append(
                SiteSignal(
                    provider="recruitee",
                    kind=SignalKind.EMBED,
                    evidence=embed.group(0)[:200],
                    # NO IDENTITY, deliberately. The widget names the vendor
                    # and not the board, and a detection without an identity is
                    # a lead rather than a board.
                    identity=None,
                )
            )
    return found


# =========================================================================
# WORKDAY
# =========================================================================
#
# A board is `{tenant}.{shard}.myworkdayjobs.com/{site}`, where `shard` is a
# numbered data centre. Three values, none derivable from a company domain,
# which is exactly why this cannot be guessed and has to be read.

#: The canonical careers host. `{tenant}.wd5.myworkdayjobs.com/en-US/{site}`,
#: and the locale segment is optional because plenty of boards omit it.
_WORKDAY_HOST = re.compile(
    r"https?://(?P<tenant>[a-z0-9][a-z0-9-]{0,62})\.(?P<shard>wd\d+)\."
    r"(?:myworkdayjobs|myworkdaysite)\.com"
    r"(?:/(?:(?P<locale>[a-z]{2}(?:-[A-Za-z]{2})?)/)?(?P<site>[A-Za-z0-9_-]+))?",
    re.IGNORECASE,
)

#: The machine surface a board's own page calls, which names tenant and site
#: unambiguously: `/wday/cxs/{tenant}/{site}/jobs`.
_WORKDAY_CXS = re.compile(
    r"/wday/cxs/(?P<tenant>[A-Za-z0-9_-]+)/(?P<site>[A-Za-z0-9_-]+)/jobs",
    re.IGNORECASE,
)

#: Locale-looking first segments, which are not a site name. Without this the
#: `en-US` in `.../en-US/External` reads as the site and every board detected
#: this way would carry the same wrong identity.
_LOCALE = re.compile(r"^[a-z]{2}(?:-[A-Za-z]{2})?$", re.IGNORECASE)


def _workday(html: str) -> list[SiteSignal]:
    found: list[SiteSignal] = []
    seen: set[tuple[str, str, str]] = set()

    for match in _WORKDAY_CXS.finditer(html):
        tenant = match.group("tenant").lower()
        site = match.group("site")
        key = (tenant, "", site)
        if key in seen:
            continue
        seen.add(key)
        found.append(
            SiteSignal(
                provider="workday",
                kind=SignalKind.API,
                evidence=match.group(0)[:200],
                # THE SHARD IS MISSING from this form, and that is recorded
                # rather than guessed. A board cannot be called without it, so
                # this is an identity that still needs the host to complete it.
                identity=None,
                parts=(("tenant", tenant), ("site", site)),
            )
        )

    for match in _WORKDAY_HOST.finditer(html):
        tenant = match.group("tenant").lower()
        shard = match.group("shard").lower()
        site = match.group("site") or ""
        if site and _LOCALE.match(site):
            site = ""
        key = (tenant, shard, site)
        if key in seen:
            continue
        seen.add(key)
        parts = (("tenant", tenant), ("shard", shard)) + ((("site", site),) if site else ())
        found.append(
            SiteSignal(
                provider="workday",
                kind=SignalKind.LINK,
                evidence=match.group(0)[:200],
                # A board needs all three. A link that names only the tenant is
                # a detection and not an identity, and completing it by picking
                # a likely site name is exactly the invention this module was
                # written to avoid.
                identity=f"{tenant}.{shard}/{site}" if site else None,
                parts=parts,
            )
        )
    return found


# =========================================================================
# TEAMTAILOR
# =========================================================================
#
# A board is `{slug}.teamtailor.com`, or an employer's own host pointed at
# Teamtailor, which this detector cannot see from the outside. What it can see
# is a link to the vendor subdomain, or the vendor's script and widget names.
# An unregistered slug answers 404, so an identity read here can be VERIFIED by
# a probe rather than assumed.

_TEAMTAILOR_HOST = re.compile(
    r"https?://(?P<slug>[a-z0-9][a-z0-9-]{0,62})\.teamtailor\.com(?P<path>/[^\s\"'<>]*)?",
    re.IGNORECASE,
)
_TEAMTAILOR_NOT_A_BOARD = frozenset(
    {"www", "app", "api", "cdn", "help", "docs", "support", "status", "trust", "discover", "blog"}
)
_TEAMTAILOR_EMBED = re.compile(
    r"(?:teamtailor-cdn\.com|scripts\.teamtailor|data-teamtailor|teamtailor[-_]?(?:widget|jobs))",
    re.IGNORECASE,
)


def _teamtailor(html: str) -> list[SiteSignal]:
    found: list[SiteSignal] = []
    seen: set[str] = set()
    for match in _TEAMTAILOR_HOST.finditer(html):
        slug = match.group("slug").lower()
        if slug in _TEAMTAILOR_NOT_A_BOARD or slug in seen:
            continue
        seen.add(slug)
        path = (match.group("path") or "").lower()
        found.append(
            SiteSignal(
                provider="teamtailor",
                kind=SignalKind.API if path.startswith("/jobs") else SignalKind.LINK,
                evidence=match.group(0)[:200],
                identity=slug,
            )
        )
    if not found:
        embed = _TEAMTAILOR_EMBED.search(html)
        if embed is not None:
            found.append(
                SiteSignal(
                    provider="teamtailor",
                    kind=SignalKind.EMBED,
                    evidence=embed.group(0)[:200],
                    identity=None,
                )
            )
    return found


# =========================================================================
# RIPPLING
# =========================================================================
#
# A board is `ats.rippling.com/{slug}/jobs`; a careers page that links its
# openings there has published the slug. Rippling's own page did exactly that
# on 2026-09-11 (649 links to `ats.rippling.com/rippling/jobs/{uuid}`).

_RIPPLING_BOARD = re.compile(
    r"https?://ats\.rippling\.com/(?P<slug>[a-z0-9][a-z0-9-]{0,62})/jobs(?P<path>/[^\s\"'<>]*)?",
    re.IGNORECASE,
)


def _rippling(html: str) -> list[SiteSignal]:
    found: list[SiteSignal] = []
    seen: set[str] = set()
    for match in _RIPPLING_BOARD.finditer(html):
        slug = match.group("slug").lower()
        if slug in seen:
            continue
        seen.add(slug)
        found.append(
            SiteSignal(
                provider="rippling",
                kind=SignalKind.LINK,
                evidence=match.group(0)[:200],
                identity=slug,
            )
        )
    return found


#: Every family this module can recognise, and the function that reads it.
#:
#: A dict rather than a chain of ifs so `detect` names no vendor, and so a
#: third family is one entry.
DETECTORS = {
    "recruitee": _recruitee,
    "rippling": _rippling,
    "teamtailor": _teamtailor,
    "workday": _workday,
}


def families() -> tuple[str, ...]:
    """The provider families site detection can recognise."""
    return tuple(sorted(DETECTORS))


def detect(html: str) -> tuple[SiteSignal, ...]:
    """Every vendor fingerprint on one page, strongest evidence first.

    Order is by `SignalKind`, so a caller taking the first signal for a family
    takes the one most likely to name a real board. Within a kind the order is
    the order they appear in the document, which is stable for a stable page.
    """
    if not html:
        return ()
    signals: list[SiteSignal] = []
    for reader in DETECTORS.values():
        signals.extend(reader(html))
    strength = {SignalKind.LINK: 0, SignalKind.API: 1, SignalKind.EMBED: 2}
    return tuple(sorted(signals, key=lambda signal: strength[signal.kind]))
