"""The provider-neutral collection interface.

An adapter's entire job is translation: turn one ATS vendor's representation
into the objects below. Everything downstream of `pipeline/collect.py` is
written against these types and never learns which vendor a job came from.

What belongs inside an adapter: URLs, request shape, JSON paths, paging,
vendor identifiers, field availability.

What must never appear inside an adapter: career intent, eligibility rules,
geographic decisions, software matching, ranking, relevance scoring, or any
semantic interpretation. An adapter converts *shape*, never *meaning*. A test
enforces this by import inspection.

The real test of this abstraction is M1B: adding Lever should mean writing one
file and one config entry, with no change to `pipeline/` or `domain/`.

M1B ran that test and it held -- but it exposed a gap this module now closes.
`ProviderCapabilities` could *declare* that a vendor exposes a remote flag or a
salary range while nothing here could *carry* the value, so it was reachable
only by knowing Lever's JSON paths. `ProviderFieldMap` is the answer the M0
architecture had already specified (section 6.3): the adapter declares which
payload paths belong to which canonical dimension, and generic code resolves
those paths without ever spelling one. See
`docs/architecture/milestone-1b1-provider-metadata.md`.
"""

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite
from typing import Any, Protocol, runtime_checkable

from career_agent.domain.enums import MatchKind, MetadataDimension
from career_agent.domain.paths import resolve_path, serialise_value


class ProviderKind(StrEnum):
    """Whose record this adapter reads.

    The distinction is not cosmetic and it is not about quality. An ATS adapter
    reads the employer's own record, which is the text the employer published.
    An aggregator adapter reads a third party's REPUBLICATION of that text,
    which may be complete, truncated, reformatted or editorially rewritten, and
    which the employer never wrote in that form.

    Everything that later says "where did this come from" has to be able to
    tell those apart. `access_method_for` derives its answer from this rather
    than from a list of vendor names, so a new aggregator cannot silently
    inherit the word "ATS" by being added to the registry.

    It is also the boundary the evidence contract needs. ADR-0002 verifies a
    quote as a contiguous substring of the exact archived text used for
    extraction; that stays true either way, but a reader has to be able to see
    which fingerprints were extracted from employer-original text and which
    were not.
    """

    #: The employer's own applicant tracking system.
    ATS = "ATS"
    #: A third party republishing postings that originate elsewhere.
    AGGREGATOR = "AGGREGATOR"


class RetrievalMode(StrEnum):
    """How an adapter gets from nothing to a list of postings.

    Introduced for Jooble, and only because Jooble made it necessary. Every
    provider before it enumerates: hand Greenhouse a board and it returns that
    employer's postings, all of them, and running it again returns the same set.
    Jooble's contract requires `keywords` and `location` on every request and
    has no "everything" query, so what comes back is the answer to a question
    somebody chose.

    That difference has to reach the interface, because the sentence "we ran
    twenty queries against Jooble" and the sentence "we cover Jooble" are not
    the same claim and only one of them is true. A source read this way carries
    the queries it was asked, and `Source.retrieval_mode` is what stops its
    numbers being read as market coverage.

    Three values, because three adapters shapes exist. `DIRECT_CAREER_PAGE` and
    a manual mode are deliberately absent until something needs them: this enum
    exists to make a real distinction visible, not to enumerate the possible.
    """

    #: A board belongs to one employer and is read to the end. ADR-0008.
    BOARD_EXHAUSTIVE = "BOARD_EXHAUSTIVE"
    #: A paginated feed spanning many employers, read to the end or to a stated
    #: page limit. Coverage is the feed's, not the market's.
    AGGREGATOR_FEED = "AGGREGATOR_FEED"
    #: Results are the answer to a query. Coverage is a property of the
    #: QUESTIONS ASKED and may never be presented as exhaustive.
    QUERY_DRIVEN = "QUERY_DRIVEN"


@dataclass(frozen=True)
class BoardRef:
    """One company's job board at one provider."""

    company_slug: str
    provider: str
    board_identifier: str
    board_url: str | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    """What this adapter, *as configured*, actually supplies.

    Not what the vendor could theoretically supply. The distinction matters
    because it is how downstream code tells "this posting omitted it" apart from
    "this provider never exposes it".

    Concretely: if `exposes_posted_date` is False, a missing date is a known
    limitation and M5 freshness falls back to `first_seen_at` with the signal
    labelled. If it were True, a missing date would be genuinely unknown. Get
    this wrong and either every job from one provider is unfairly penalised, or
    missing dates silently read as "fresh".
    """

    full_description_in_list: bool
    #: Whether the employer's WHOLE description can be obtained at all.
    #:
    #: A different question from the one above, and the two were conflated
    #: until a Jooble connector made the difference matter. Speedrun answers
    #: False to `full_description_in_list` because its list response carries no
    #: description -- and then fetches one per posting, so the stored text is
    #: complete. Jooble answers False for a reason no second request can fix:
    #: its documented contract has no detail endpoint, so a `snippet` is all
    #: there will ever be.
    #:
    #: Required, with no default. An adapter that has not said whether it can
    #: obtain a full description is an adapter whose postings nobody has
    #: decided how to read, and a permissive default would answer the question
    #: with a shrug -- in the direction that makes an excerpt look like a
    #: posting.
    obtains_full_description: bool
    exposes_posted_date: bool
    exposes_department: bool
    exposes_compensation: bool
    exposes_location_structured: bool
    exposes_remote_flag: bool
    exposes_employment_type: bool
    #: Whether the location field is the EMPLOYER'S ANSWER to "where can you
    #: hire?" rather than a place the company has a desk.
    #:
    #: False for every ATS, and that default is the safe one. A Greenhouse
    #: posting saying `San Francisco, CA` names an office, and reading an
    #: office as a hiring scope is precisely the conflation invariant 3 exists
    #: to forbid -- it is how a candidate in Brazil ends up told she is
    #: eligible for a job in California.
    #:
    #: True where the board ASKS. We Work Remotely's `region` element is filled
    #: in by the employer answering that question and nothing else, which is
    #: why `Anywhere in the World` and `USA Only` are both values it takes.
    publishes_hiring_scope: bool = False


@dataclass(frozen=True)
class FieldMapping:
    """One payload path, and the dimension it speaks to.

    Deliberately not a place to put a converter. M0 section 6.3 originally gave
    each entry a `normaliser` (`bool_remote`, `location_string`); M1B.1 drops it,
    because `bool_remote` turning "remote" into True has already discarded the
    difference between "the employer said remote" and "this candidate can work
    there". Normalisation is interpretation, interpretation must carry status and
    evidence, and the object that carries those is `ExtractedField` at M2.

    `path` is dotted and addresses objects only (`categories.commitment`). Array
    indexing is deferred until a provider actually needs it -- adding syntax
    nothing uses is how a path language quietly becomes a query language.
    """

    path: str
    dimension: MetadataDimension


@dataclass(frozen=True)
class ProviderFieldMap:
    """Which payload paths carry meaningful structured metadata.

    Declares *where* meaning might live. It never assigns meaning -- that
    sentence is lifted from M0 section 6.4 because it is the whole boundary.

    Many-to-one is intentional and used: Lever asserts location twice, in
    `country` and in `categories.location`. Collapsing them would be deciding
    which one the employer meant. Both travel; M3 weighs them.

    An empty map is a statement -- "this vendor exposes nothing structured" --
    and must be a deliberate one, not an oversight.
    """

    mappings: tuple[FieldMapping, ...] = ()

    def dimensions(self) -> frozenset[MetadataDimension]:
        """Every dimension this provider can speak to at all.

        This is what keeps "the posting omitted it" apart from "the provider has
        no such concept": a dimension in here with no observation is an employer
        that did not say, and a dimension absent from here is a vendor that
        cannot express it.
        """
        return frozenset(m.dimension for m in self.mappings)

    def paths(self) -> tuple[str, ...]:
        return tuple(m.path for m in self.mappings)


@dataclass(frozen=True)
class ProviderMetadataObservation:
    """One thing a provider's payload asserted, with its provenance attached.

    Everything a `PROVIDER_FIELD` evidence row needs except `payload_hash`,
    which belongs to the stored row rather than to the adapter and is paired in
    at M2.

    This is an observation, never a conclusion. `work_model_hint = "remote"` is
    a claim the employer's ATS record makes; whether the candidate may actually
    work there is decided much later, against the description text, and the two
    disagree often.
    """

    dimension: MetadataDimension
    provider: str
    source_field: str
    source_value: str


#: The periods compensation may be stated per, as the matcher already spells
#: them. Deliberately NOT a new StrEnum: `JobFacts.salary_period` is a plain
#: string compared against `preferences.compensation.period`, whose configured
#: vocabulary is exactly these three words. A second home for the same three
#: values would need a conversion at every boundary, and the first time someone
#: forgot one the posting would silently score `salary_unknown`.
COMPENSATION_PERIODS: frozenset[str] = frozenset({"YEAR", "MONTH", "HOUR"})

#: A plain decimal, and deliberately nothing else. Anchored, no exponent, no
#: separator, no currency symbol.
_PLAIN_DECIMAL = re.compile(r"[+-]?\d+(?:\.\d+)?\Z")


def parse_amount(value: Any) -> float | None:
    """One payload number, or None -- the ONE parser all three adapters use.

    It is shared rather than copied because the copies had already diverged
    from reality. Each adapter grew its own `_number` that accepted `int` and
    `float` and rejected everything else, which was true of the two vendors
    that were read at the time and false of the third: Greenhouse states
    `{"min_value": "320000.0", "max_value": "400000.0"}` -- **strings** -- so a
    reader written to the old shape would have returned None on all 413
    postings that name a range, silently, with no test failing. The same trap
    is one serialisation change away for the other two, and a single function
    is what makes that a shared, tested behaviour instead of three private
    ones.

    `bool` is excluded on purpose: it is an `int` in Python, and `True`
    becoming a salary of 1.0 is the kind of silent nonsense that survives every
    test nobody wrote.

    Strings are accepted only in plain decimal form. `"1e5"`, `"320,000"`,
    `"$320000"`, `"inf"` and `"nan"` all return None rather than a guess --
    every one of them would be an interpretation of a format no vendor we read
    actually emits, and inventing a number is worse than reporting none. All
    2,618 numeric strings in the archived Greenhouse corpus match this shape.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        number = float(value)
        return number if isfinite(number) else None
    if isinstance(value, str) and _PLAIN_DECIMAL.match(value.strip()):
        return float(value.strip())
    return None


def stated_amounts(
    minimum: float | None, maximum: float | None
) -> tuple[float | None, float | None]:
    """The bounds a payload actually stated, with an all-zero band read as none.

    One Lever posting in the corpus carries `{"min": 0, "max": 0}`, which is a
    placeholder for "no range" rather than an offer of nothing: it was awarding
    the `salary_known` confidence point and rendering a card that read "0 - 0".
    A band whose every stated bound is zero is therefore not a stated band.

    A zero FLOOR beside a real ceiling survives -- `0 - 100000` is a range an
    employer could mean -- because only the all-zero case is unambiguously a
    placeholder.
    """
    stated = [bound for bound in (minimum, maximum) if bound is not None]
    if stated and not any(stated):
        return (None, None)
    return (minimum, maximum)


@dataclass(frozen=True)
class CompensationBand:
    """One currency's worth of pay, as one payload stated it.

    A payload may state several: Ashby writes a regional offer per currency,
    and Greenhouse writes one custom field per country. They are alternatives
    the employer named, not pieces of a single range, and which of them is the
    relevant one depends on a preference the adapter has no business knowing.
    So every one of them travels and `match.score` chooses.
    """

    min_value: float | None = None
    max_value: float | None = None
    currency: str | None = None
    period: str | None = None

    @property
    def has_amounts(self) -> bool:
        return self.min_value is not None or self.max_value is not None


@dataclass(frozen=True)
class CompensationHint:
    """What a provider's payload says about pay, uninterpreted.

    A hint, like every other provider observation: the employer's ATS record
    asserts a range, and whether it clears the candidate's target is decided
    later, by `match.score`, against a configured preference.

    Every field except `source_field` is optional, and absence is preserved
    rather than filled in. A payload that states a minimum and no maximum has
    `max_value=None` -- never a copy of the minimum, which would invent a
    closed band the employer never wrote and let a floor read as a ceiling.

    `raw_text` is display text the vendor composed for a human ("$205K - $300K
    - Offers Equity"). It travels so the interface can show what the posting
    showed, and it is never parsed into numbers: a rendered string is a
    rounding of a measurement, not the measurement, and turning "$205K" back
    into 205000 would assert a precision nobody stated.

    `period` is one of `COMPENSATION_PERIODS`, or None when the vendor stated
    an interval outside that vocabulary, or none at all. None is the honest
    answer in both cases: a fortnightly rate compared against a monthly target
    is a wrong number, and `salary_unknown` is the correct score for a figure
    we cannot compare.

    `source_field` names the payload field the numbers came from, and it is
    load-bearing rather than decorative: Greenhouse carries a published offer
    range and an internal budget figure in the same list of custom fields, and
    "what we budgeted" is a different claim from "what we published". The field
    name is what lets the interface, and anyone auditing a score, see which one
    was read.

    **`alternate_bands` holds the OTHER currencies this payload stated.** The
    lead band is the four flat fields above, unchanged, so every existing
    reader of `min_value` still works. What is new is that a payload offering
    EUR 110,500-181,500 *and* USD 170,350-275,550 no longer discards the second
    one just because it came second in the JSON. The adapter cannot know which
    currency the candidate targets -- that is a preference -- so it carries
    both and `match.score` picks. Empty for every single-currency payload,
    which is 2,846 of the 2,865 Ashby ones and 394 of the 413 Greenhouse ones.
    """

    source_field: str
    min_value: float | None = None
    max_value: float | None = None
    currency: str | None = None
    period: str | None = None
    raw_text: str | None = None
    alternate_bands: tuple[CompensationBand, ...] = ()

    @property
    def has_amounts(self) -> bool:
        """Whether this hint carries numbers at all, as opposed to only text."""
        return self.min_value is not None or self.max_value is not None

    @property
    def lead_band(self) -> CompensationBand:
        """The flat fields, as a band. The one a single-currency payload states."""
        return CompensationBand(
            min_value=self.min_value,
            max_value=self.max_value,
            currency=self.currency,
            period=self.period,
        )

    @property
    def bands(self) -> tuple[CompensationBand, ...]:
        """Every band this payload stated, lead first. Declaration order after."""
        return (self.lead_band, *self.alternate_bands)


def canonical_url(url: str) -> str:
    """One URL, reduced to the form two sources can be compared on.

    Lowercases the scheme and host, drops the query string and the fragment,
    and removes a trailing slash. Everything else is left exactly as it is,
    because a posting path is opaque and normalising it further would be
    guessing at a vendor's routing.

    The query string is what forces this to exist. An aggregator's canonical
    job URL comes back carrying its own attribution parameters, so the same
    posting reached two ways produces two strings that differ only in tracking.
    Comparing those raw would make every job look new.

    Case: hosts are case-insensitive by RFC 3986 and paths are not. One vendor
    in this corpus publishes the same board as both `Abridge` and `abridge` in
    the PATH, so lowercasing the whole URL would be tempting; it is not done,
    because a path that happens to collide for one vendor is not a licence to
    assert that two different paths are one posting for every other.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url.strip())
    if not parts.scheme or not parts.netloc:
        return url.strip().rstrip("/")
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


#: How one adapter recognises its OWN posting URLs.
#:
#: Given any URL, it returns the provider-native `external_id` when that URL is
#: one of this provider's postings, and None when it is not. It opens no socket
#: and reads no payload: it is pure string work over a shape the adapter
#: already knows, because it is the shape the adapter itself builds.
#:
#: This exists so that cross-source identity can be DETERMINISTIC. An
#: aggregator publishes the employer's real apply link; if an adapter can look
#: at that link and say "that is my posting 228d2bfe-...", then the same job
#: reached two ways resolves to one row by exact key, with no fuzzy title
#: matching anywhere near it. ADR-0008 closes by warning that no fuzzy job
#: merging exists in this system; this keeps it that way.
#:
#: It lives beside `CompensationReader` in the registry rather than on the
#: protocol for the same two reasons: it must be reachable without a fetcher,
#: and a provider must be able to say "I have no such thing" by being absent.
PostingUrlRecogniser = Callable[[str], "str | None"]


#: An adapter's compensation reader: stored payload in, one hint or None out.
#:
#: `None` means "this provider did not tell us", which is not the same fact as
#: "this posting states no pay" and must never be scored as one. Generic code
#: gets `None` for a provider with no reader at all and for a payload with no
#: compensation in it, and treats both as unknown -- the only reading that is
#: true of each.
CompensationReader = Callable[[Any], "CompensationHint | None"]


@dataclass(frozen=True)
class PostingStub:
    """A posting as the provider's listing describes it.

    `description_html` is populated when the provider returns descriptions in
    the list response (`full_description_in_list`); otherwise the pipeline calls
    `fetch_posting` for each stub.

    **`posted_at` contract (M1B.1 / A7.2).** RFC 3339, offset-aware, normalised
    to UTC, second resolution -- `2026-02-28T14:04:24+00:00`, always 25
    characters. Every adapter converts through
    `career_agent.domain.timestamps.to_rfc3339_utc`; none passes a
    provider-native encoding through. Two vendors writing two encodings into one
    column would make every later freshness calculation a guess. The native
    value stays in the archived payload, addressable and verifiable like any
    other field.
    """

    external_id: str
    title: str
    url: str
    location_raw: str | None = None
    department: str | None = None
    posted_at: str | None = None
    description_html: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawPosting:
    """A complete posting, ready to be persisted.

    `payload` is the provider's response for this posting, unmodified. It is
    archived verbatim so that later stages can answer "what did the provider
    actually return when we observed this?" even after normalisation changes.
    """

    stub: PostingStub
    description_html: str
    description_text: str
    payload: dict[str, Any]

    @property
    def has_description(self) -> bool:
        return bool(self.description_text.strip())


@runtime_checkable
class JobProvider(Protocol):
    """The contract every ATS adapter implements.

    **One capability is optional and deliberately not a method here.** An
    adapter may also publish a module-level `read_compensation(payload)`
    matching `CompensationReader`, registered in
    `providers.registry._COMPENSATION_READERS` beside `_FIELD_MAPS`. Two
    reasons it lives there rather than on this protocol:

    1. It must be reachable WITHOUT a fetcher. Scoring reads archived payloads
       and opens no socket (`tests/integration/test_rescore_is_offline.py`);
       going through `get_provider` would mean constructing an HTTP client
       inside a deliberately offline path. `field_map_for` already established
       this shape and this is the second use of it.
    2. Greenhouse must be able to say "I have nothing" out loud. A protocol
       method every adapter inherits would make "this vendor exposes no pay
       data" and "this posting stated none" the same silence, and those are
       different facts the product is required to keep apart.
    """

    name: str
    #: Whose record this reads. Defaulting is deliberately NOT offered: an
    #: adapter that does not say is an adapter whose provenance nobody decided.
    kind: ProviderKind
    capabilities: ProviderCapabilities
    field_map: ProviderFieldMap

    #: How this adapter reaches postings. Defaults to the shape every adapter
    #: had before Jooble, so an existing provider needs no edit and a new one
    #: that differs has to say so.
    retrieval_mode: RetrievalMode = RetrievalMode.BOARD_EXHAUSTIVE

    #: Whether `list_postings` can be given ONE EMPLOYER and answer about them.
    #:
    #: Separate from `retrieval_mode`, and the pair is not redundant. Speedrun
    #: is an `AGGREGATOR_FEED` and CAN be asked about one company, because its
    #: feed takes a company filter. We Work Remotely is the same mode and
    #: cannot: its feeds are CATEGORIES, so `board_identifier` names a feed
    #: there and asking it about `acme` is a question in the wrong vocabulary.
    #:
    #: `discover` is the caller that cares. It asks "does this employer have a
    #: board here", and a provider that cannot answer that should not be probed
    #: with a company slug at all.
    addresses_boards_by_company: bool = True

    #: Whether an identifier for this family can be DERIVED from a company name.
    #:
    #: Separate from `addresses_boards_by_company`, and Workday is why. That
    #: family HAS a board per employer -- so `collect` must walk it -- and its
    #: identity is a tenant, a numbered data centre AND a site, so a probe
    #: assembled from a company slug would have to invent two of the three.
    #: Whatever answered would then be read as evidence that the board exists.
    #:
    #: `discover` asks this before probing. A family that answers False is
    #: reached only by a board somebody RESOLVED: `scan-career-sites` reads what
    #: an employer published on its own careers page, which is the same standard
    #: ADR-0013 sets for a posting's identity and ADR-0002 sets for a quote.
    identifier_is_guessable: bool = True

    #: Whether this family's host ANSWERS for an identifier nobody registered.
    #:
    #: Recruitee is why it exists: `nobody.recruitee.com/api/offers/` returns
    #: an empty list rather than a 404, so a board that answered with nothing
    #: cannot be told from a board that does not exist. A caller validating
    #: an identity against such a family must find the posting it came for
    #: in the listing; answering proves nothing. Every other family answers a
    #: wrong identifier with an error (measured per adapter), so for them a
    #: board that answered is a board.
    answers_for_unknown_identifiers: bool = False

    #: Entries this adapter READ and could not turn into a posting.
    #:
    #: Per INSTANCE, and `pipeline.collect` builds one adapter per board, so
    #: reading it after `list_postings` gives that board's count.
    #:
    #: It exists because the promise was already written down and was not kept.
    #: `greenhouse._to_stub` says "a single unusable entry is skipped rather
    #: than failing the whole board... the skipped count surfaces in the run
    #: statistics, so it is never silent" -- and
    #: `CollectionStats.postings_skipped_malformed` was declared in 2026 and
    #: never incremented once. Every ATS adapter dropped malformed entries in
    #: total silence for the life of the product, under a docstring saying it
    #: did not. Found on 2026-09-09 by `career-agent ingestion-report`, which
    #: reads that counter.
    postings_skipped: int = 0

    def board_url(self, board: BoardRef) -> str:
        """The human-facing board URL, for the digest and for diagnostics."""
        ...

    def validate_board(self, board: BoardRef) -> str | None:
        """Why this adapter cannot be asked for this board, or None.

        **OPTIONAL, and answered WITHOUT a request.** The default is None:
        most adapters take an employer slug and cannot know whether an
        employer exists until they ask.

        It exists because `board_identifier` does not mean the same thing for
        every family. An ATS adapter reads it as an employer; a feed adapter
        reads it as a CATEGORY. `source_board` holds both, so the generic
        collector walks one table containing two vocabularies, and a value
        from the wrong one used to reach the adapter mid-run and end the whole
        collection with `unknown WWR feed '6sense'`.

        A provider that has a closed vocabulary should say so here, so that
        `pipeline.collect` can reject the plan entry before any socket opens
        and carry on with every other source.

        A real `return` rather than `...`: an empty body in a Protocol is an
        ABSTRACT method, and every existing adapter would stop being an
        instance of this contract for the sake of a hook they do not need.
        """
        del board
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every posting currently on the board.

        Raises `career_agent.net.fetcher.FetchError` on failure. It must never
        return an empty iterator to signal a problem: an empty board and a
        failed request are different outcomes, and conflating them would let a
        network blip close a company's entire posting history.
        """
        ...

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """Complete a stub into a persistable posting.

        When the provider already returned the description in the listing, this
        is a cheap local conversion rather than a second request.
        """
        ...


# -- resolving a field map against a payload ------------------------------
#
# These three functions are the entire metadata transport. They are here rather
# than in `pipeline/` for one reason: everything below is provider-NEUTRAL, and
# it is the only code allowed to turn a path into a value. Generic stages ask a
# provider for its `field_map` and hand it straight back here, so no module
# outside `providers/` ever spells a vendor path. A test generated from the
# registered field maps enforces exactly that.


# `resolve_path` and `serialise_value` moved to `domain/paths.py` at M2 and are
# re-exported here so every existing caller and test keeps working unchanged.
# The verifier that proves this evidence lives in the domain layer, which may
# not import siblings -- and two implementations of a byte-exact serialisation
# contract is precisely how the producer and the prover drift apart.


def resolve_metadata(
    provider: str, field_map: ProviderFieldMap, payload: Any
) -> tuple[ProviderMetadataObservation, ...]:
    """Every declared path that this payload actually answers.

    Declaration order is preserved, and duplicates are not collapsed: two paths
    mapped to one dimension produce two observations, because they are two
    separate assertions the employer made.
    """
    observations: list[ProviderMetadataObservation] = []
    for mapping in field_map.mappings:
        serialised = serialise_value(resolve_path(payload, mapping.path))
        if serialised is None:
            continue
        observations.append(
            ProviderMetadataObservation(
                dimension=mapping.dimension,
                provider=provider,
                source_field=mapping.path,
                source_value=serialised,
            )
        )
    return tuple(observations)


def verify_provider_field(payload: Any, source_field: str, source_value: str) -> MatchKind:
    """Prove that this payload really did contain this value at this path.

    The other half of the contract, shipped now rather than at M2 on purpose:
    the code that emits `source_field`/`source_value` and the code that later
    proves them are tested against each other today, against real archived
    payloads, instead of being written months apart and hoping they agree.

    `FIELD_MATCH` when the path resolves and the canonical form is identical;
    `NOT_FOUND` when the path does not resolve, or resolves to something else.
    There is deliberately no fuzzy tier: a provider field either said this or it
    did not, and "almost" is not evidence.
    """
    serialised = serialise_value(resolve_path(payload, source_field))
    if serialised is not None and serialised == source_value:
        return MatchKind.FIELD_MATCH
    return MatchKind.NOT_FOUND
