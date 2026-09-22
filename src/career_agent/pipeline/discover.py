"""Board discovery: does this company have a board we can actually collect?

Reuses `HttpFetcher` and the registered adapters -- there is no second HTTP
stack here, and no provider-specific branching. Discovery asks the registry for
every provider, builds a `BoardRef`, calls `list_postings`, and maps what comes
back to a typed outcome.

Even the host patterns are neutral: a provider's own `board_url()` is asked what
its public board URLs look like, so a pasted `jobs.lever.co/acme` is matched
without this module ever spelling a vendor's domain. That is the same discipline
the field maps follow, applied one layer up.

The one thing discovery deliberately does NOT do is walk an ATS namespace. A
bounded set of plausible identifiers derived from a domain and a company name is
discovery; iterating a vendor's slug space is something else, and this project
does not do it.
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import BoardRef
from career_agent.providers.registry import get_provider, guessable_providers

#: Identifier used only to ask a provider what its board URLs look like.
_URL_PROBE = "\x00probe\x00"

_NON_ALNUM = re.compile(r"[^a-z0-9]+")
#: Suffixes that are corporate decoration rather than part of the name.
_LEGAL_SUFFIXES = ("inc", "llc", "ltd", "limited", "gmbh", "sa", "bv", "ag", "corp", "co")


class BoardOutcome(StrEnum):
    """What a probe learned. Every one of these is a different thing.

    The distinction that matters most is the same one the collector turns on: a
    board that answered with nothing is real and collectable, and a board that
    failed to answer tells us nothing at all.
    """

    VALID_NONEMPTY = "VALID_NONEMPTY"
    VALID_EMPTY = "VALID_EMPTY"  # a real board with no openings today
    NOT_FOUND = "NOT_FOUND"  # 404 -- no such board
    MALFORMED = "MALFORMED"  # answered, but not in the shape it promises
    TEMPORARY_FAILURE = "TEMPORARY_FAILURE"  # timeout, 5xx, rate limit

    @property
    def is_board(self) -> bool:
        return self in (BoardOutcome.VALID_NONEMPTY, BoardOutcome.VALID_EMPTY)

    @property
    def is_conclusive(self) -> bool:
        """False when the probe says nothing about whether the board exists."""
        return self is not BoardOutcome.TEMPORARY_FAILURE


@dataclass(frozen=True)
class BoardProbe:
    provider: str
    board_identifier: str
    outcome: BoardOutcome
    posting_count: int = 0
    discovery_method: str = "domain_slug"
    detail: str | None = None


@dataclass
class DiscoveryResult:
    """Everything one company's discovery attempt learned."""

    query: str
    canonical_domain: str | None = None
    probes: list[BoardProbe] = field(default_factory=list)

    @property
    def boards(self) -> list[BoardProbe]:
        return [p for p in self.probes if p.outcome.is_board]

    @property
    def best(self) -> BoardProbe | None:
        """The strongest board found: a live one before an empty one."""
        ranked = sorted(
            self.boards,
            key=lambda p: (p.outcome is not BoardOutcome.VALID_NONEMPTY, -p.posting_count),
        )
        return ranked[0] if ranked else None

    @property
    def had_temporary_failure(self) -> bool:
        """True when some probe was inconclusive, so "no board" is not proven."""
        return any(not p.outcome.is_conclusive for p in self.probes)


@dataclass(frozen=True)
class SourcingQuery:
    """One line of a sourcing list, read as what it is.

    Three shapes, because the three are different amounts of evidence and the
    module already treats them that way. A BOARD URL is the strongest -- it is
    a link somebody published rather than an identifier we guessed. A domain is
    next. A name alone is the weakest and is why `name` exists at all.
    """

    name: str | None = None
    domain: str | None = None
    board_url: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.domain or self.board_url or "?"


def read_sourcing_list(text: str) -> tuple[SourcingQuery, ...]:
    """A plain sourcing list as queries. Blank lines and `#` comments ignored.

    Kept here rather than in the command because it is the only new judgement
    in that command, and a judgement that lives in a CLI function is one that
    gets tested by running the CLI.

    `Name | domain` is the ordinary line. A line containing a slash is read as
    a board URL, which is deliberate: `boards.greenhouse.io/acme` names a board
    and `acme.com` names a company, and reading the first as a domain would
    throw away the strongest evidence discovery can be handed.
    """
    queries: list[SourcingQuery] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            written_name, _, written_domain = line.partition("|")
            queries.append(
                SourcingQuery(
                    name=written_name.strip() or None,
                    domain=written_domain.strip() or None,
                )
            )
        elif "/" in line:
            queries.append(SourcingQuery(board_url=line))
        else:
            queries.append(SourcingQuery(domain=line))
    return tuple(queries)


def normalise_domain(value: str | None) -> str | None:
    """`https://WWW.Acme.com/careers` and `acme.com` are the same company.

    Lowercased, scheme and `www.` and path and port removed. This is the string
    company identity is anchored on, so it has to be boring and total: two
    entries that mean the same company must produce the same output, and two
    that do not must not.
    """
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if "//" not in text:
        text = f"//{text}"
    host = urlsplit(text).netloc or urlsplit(text).path
    host = host.split("/")[0].split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def candidate_identifiers(
    domain: str | None = None, name: str | None = None, extra: tuple[str, ...] = ()
) -> tuple[str, ...]:
    """A small, bounded, deterministic set of plausible board identifiers.

    From `acme.com` and `Acme Health Inc`: `acme`, `acmehealth`, `acme-health`.
    Order is evidence order -- the domain label first, because a company's
    domain is a stronger signal than a guess at how it writes its own name.

    Bounded on purpose. Five plausible identifiers is discovery; two hundred
    permutations is walking someone's namespace, and the difference matters more
    than the extra coverage would.
    """
    found: list[str] = []

    def add(value: str) -> None:
        cleaned = value.strip().lower()
        if cleaned and cleaned not in found:
            found.append(cleaned)

    for value in extra:
        add(value)

    normalised = normalise_domain(domain)
    if normalised:
        label = normalised.split(".")[0]
        add(label)
        # A hyphenated domain label is also commonly written solid.
        add(label.replace("-", ""))

    if name:
        words = [w for w in _NON_ALNUM.sub(" ", name.lower()).split() if w]
        while words and words[-1] in _LEGAL_SUFFIXES:
            words.pop()
        if words:
            add("".join(words))
            if len(words) > 1:
                add("-".join(words))
            add(words[0])

    return tuple(found)


def probe_board(fetcher: HttpFetcher, provider_name: str, identifier: str) -> BoardProbe:
    """Ask one provider whether one identifier is a board.

    The mapping from failure to outcome is the same distinction the collector
    depends on, which is why it reuses the provider adapters rather than a
    parallel implementation: a probe that says VALID_EMPTY has to mean exactly
    what an empty collection pass means.
    """
    provider = get_provider(provider_name, fetcher)
    board = BoardRef(company_slug=identifier, provider=provider_name, board_identifier=identifier)
    try:
        postings = list(provider.list_postings(board))
    except FetchError as exc:
        outcome = {
            FetchErrorCategory.NOT_FOUND: BoardOutcome.NOT_FOUND,
            FetchErrorCategory.MALFORMED: BoardOutcome.MALFORMED,
        }.get(exc.category, BoardOutcome.TEMPORARY_FAILURE)
        return BoardProbe(provider_name, identifier, outcome, detail=exc.message[:160])

    outcome = BoardOutcome.VALID_NONEMPTY if postings else BoardOutcome.VALID_EMPTY
    return BoardProbe(provider_name, identifier, outcome, posting_count=len(postings))


def _public_prefixes(fetcher: HttpFetcher) -> dict[str, tuple[str, str]]:
    """Each provider's public board URL, split around where the identifier goes.

    Derived by asking every adapter for `board_url()` with a probe identifier,
    so this module learns the shape without ever spelling a vendor host. Adding
    a provider extends discovery automatically.
    """
    prefixes: dict[str, tuple[str, str]] = {}
    for name in guessable_providers():
        provider = get_provider(name, fetcher)
        url = provider.board_url(
            BoardRef(company_slug=_URL_PROBE, provider=name, board_identifier=_URL_PROBE)
        )
        head, _, tail = url.partition(_URL_PROBE)
        if head:
            prefixes[name] = (head.lower(), tail.lower())
    return prefixes


def _split_board_url(text: str, prefix: str, tail: str) -> str | None:
    """The identifier a board URL carries under one provider's shape, or None.

    Two shapes exist. A PATH board (`boards.greenhouse.io/{id}`) has a host in
    its prefix and the identifier as the first path segment after it. A
    SUBDOMAIN board (`{id}.teamtailor.com`) has a prefix that is only the
    scheme, and the identifier is the host's first label -- and a prefix that
    is only the scheme must never be allowed to match every URL on the web,
    which is what a plain `startswith` did before the first subdomain family
    was registered on 2026-09-11.
    """
    if not text.startswith(prefix):
        return None
    remainder = text[len(prefix) :]
    if prefix in ("https://", "http://"):
        host = remainder.split("/")[0].split("?")[0].split("#")[0]
        suffix = tail.split("/")[0]
        if not suffix.startswith(".") or not host.endswith(suffix):
            return None
        label = host[: -len(suffix)]
        return label if label and "." not in label else None
    identifier = remainder.split("?")[0].split("#")[0].strip("/").split("/")[0]
    return identifier or None


def identifier_from_url(fetcher: HttpFetcher, url: str) -> tuple[str, str] | None:
    """`(provider, identifier)` for a board URL, or None if it is not one.

    The strongest evidence discovery has: a URL a curated source or a careers
    page actually published, rather than an identifier we guessed. Query strings
    and trailing path segments are ignored, so a link to a specific posting
    still yields its board.
    """
    text = (url or "").strip().lower()
    if not text:
        return None
    if "//" not in text:
        text = f"https://{text}"

    for provider_name, (prefix, tail) in _public_prefixes(fetcher).items():
        identifier = _split_board_url(text, prefix, tail)
        if identifier:
            return provider_name, identifier
    return None


def discover(
    fetcher: HttpFetcher,
    *,
    domain: str | None = None,
    name: str | None = None,
    board_url: str | None = None,
    identifiers: tuple[str, ...] = (),
    providers: tuple[str, ...] | None = None,
    stop_on_first: bool = True,
) -> DiscoveryResult:
    """Find a collectable board for one company.

    Evidence order, strongest first:

    1. a supplied board URL -- validated, never guessed;
    2. identifiers derived from the domain label and the company name.

    `stop_on_first` stops at the first live board, which is what sourcing wants:
    one validated board is enough to promote a company, and every extra probe is
    a request someone else pays to serve. Set it False to find every board a
    company has, which is what a company with boards on two providers needs.
    """
    result = DiscoveryResult(
        query=board_url or domain or name or "", canonical_domain=normalise_domain(domain)
    )
    # GUESSABLE board providers, because discovery asks 'does this company
    # have a job board here' by CONSTRUCTING an identifier. A query-driven
    # aggregator has no board to find, and probing one would spend a request
    # answering a question about a thing it does not have. A family whose
    # identity cannot be derived from a company name is excluded for a
    # stronger reason: the probe would have to invent it.
    provider_names = providers or tuple(guessable_providers())

    if board_url:
        match = identifier_from_url(fetcher, board_url)
        if match is not None:
            provider_name, identifier = match
            probe = probe_board(fetcher, provider_name, identifier)
            result.probes.append(
                BoardProbe(
                    provider_name,
                    identifier,
                    probe.outcome,
                    probe.posting_count,
                    discovery_method="board_url",
                    detail=probe.detail,
                )
            )
            if probe.outcome.is_board and stop_on_first:
                return result

    for identifier in candidate_identifiers(domain, name, identifiers):
        for provider_name in provider_names:
            if any(
                p.provider == provider_name and p.board_identifier == identifier
                for p in result.probes
            ):
                continue
            probe = probe_board(fetcher, provider_name, identifier)
            result.probes.append(probe)
            if probe.outcome.is_board and stop_on_first:
                return result
    return result


def as_dict(result: DiscoveryResult) -> dict[str, Any]:
    return {
        "query": result.query,
        "canonical_domain": result.canonical_domain,
        "probes": [
            {
                "provider": p.provider,
                "board_identifier": p.board_identifier,
                "outcome": p.outcome.value,
                "posting_count": p.posting_count,
                "discovery_method": p.discovery_method,
                "detail": p.detail,
            }
            for p in result.probes
        ],
    }
