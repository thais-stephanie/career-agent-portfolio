"""Provider name to adapter.

Adding Lever at M1B should mean one new file plus one entry here -- that is the
whole test of whether the abstraction works.

M1B result: it did. The Lever entry below is the entire integration cost outside
`providers/lever.py`. `pipeline/`, `storage/`, `domain/`, `net/` and the CLI were
not touched, and no code anywhere branches on a provider name.

M1C repeated the test with a third, structurally different vendor and got the
same answer: one new file, one entry here. Three envelope shapes, three
timestamp encodings, three organisational models and three description layouts,
and this dict is still the whole seam.

V3 added a FOURTH entry that is not an ATS at all, and that is where the seam
finally had to widen. An aggregator republishes other systems' postings, so two
questions appeared that three ATS adapters never had to answer:

* whose record is this? `_KINDS` carries it, and `access_method_for` derives
  its answer from that rather than from a list of vendor names, so an
  aggregator cannot inherit the word "ATS" by being added to this file;
* is this URL one of yours? `_URL_RECOGNISERS` is how an aggregator's apply
  link resolves to an EXACT `(provider, external_id)` without generic code
  spelling a hostname, and without any fuzzy title matching.

Both follow the shape `_COMPENSATION_READERS` already established: reachable
without a fetcher, and a provider may say "I have no such thing" by being
absent.
"""

from collections.abc import Callable
from typing import Any

from career_agent.domain.enums import ContentCompleteness
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers import (
    ashby,
    comeet,
    dynamitejobs,
    fourdayweek,
    greenhouse,
    himalayas,
    jobgether,
    jobicy,
    jooble,
    lever,
    programathor,
    recruitee,
    remotive,
    rippling,
    speedrun,
    teamtailor,
    torre,
    workable,
    workday,
)
from career_agent.providers.arbeitnow import ArbeitnowProvider
from career_agent.providers.ashby import AshbyProvider
from career_agent.providers.avlis import AvlisProvider
from career_agent.providers.base import (
    CompensationReader,
    JobProvider,
    PostingUrlRecogniser,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RetrievalMode,
    canonical_url,
)
from career_agent.providers.comeet import ComeetProvider
from career_agent.providers.dynamitejobs import DynamiteJobsProvider
from career_agent.providers.fourdayweek import FourDayWeekProvider
from career_agent.providers.getonbrd import GetonbrdProvider
from career_agent.providers.greenhouse import GreenhouseProvider
from career_agent.providers.gupy import GupyProvider
from career_agent.providers.himalayas import HimalayasProvider
from career_agent.providers.jobgether import JobgetherProvider
from career_agent.providers.jobicy import JobicyProvider
from career_agent.providers.jooble import JoobleProvider
from career_agent.providers.lever import LeverProvider
from career_agent.providers.programathor import ProgramathorProvider
from career_agent.providers.recruitee import RecruiteeProvider
from career_agent.providers.recruiterflow import RecruiterflowProvider
from career_agent.providers.remoteok import RemoteOkProvider
from career_agent.providers.remotive import RemotiveProvider
from career_agent.providers.rippling import RipplingProvider
from career_agent.providers.speedrun import SpeedrunProvider
from career_agent.providers.teamtailor import TeamtailorProvider
from career_agent.providers.torre import TorreProvider
from career_agent.providers.workable_xml import WorkableXmlProvider as WorkableProvider
from career_agent.providers.workday import WorkdayProvider
from career_agent.providers.workingnomads import WorkingNomadsProvider
from career_agent.providers.wwr import WwrProvider


class UnknownProviderError(KeyError):
    """A board names a provider that has no adapter."""


def poll_interval_seconds(provider: str) -> int:
    """Existing provider-wide cooldowns, shared with read-only admission plans."""
    return {
        remotive.RemotiveProvider.name: remotive.MIN_SECONDS_BETWEEN_POLLS,
        jobicy.JobicyProvider.name: jobicy.MIN_SECONDS_BETWEEN_POLLS,
    }.get(provider, 0)


def _jooble(fetcher: HttpFetcher) -> JobProvider:
    """Jooble, built from the environment, or a refusal that names what is missing.

    The only adapter here that cannot be constructed from a fetcher alone. Its
    key is country-bound and its quota is a lifetime, so both the credential
    and the DOMAIN are required configuration with no defaults -- see
    `providers/jooble.py`. A factory that quietly defaulted the domain would
    make this a United States connector for an owner in Brazil, and would find
    out by spending a permanently limited request.

    Reading the environment here rather than at import time keeps the refusal
    where a caller can act on it, and keeps the module importable on a machine
    that has never heard of Jooble.
    """
    import os

    return JoobleProvider(
        fetcher,
        domain=os.environ.get(jooble.DOMAIN_ENV, ""),
        api_key=os.environ.get(jooble.KEY_ENV, ""),
    )


_FACTORIES: dict[str, Callable[[HttpFetcher], JobProvider]] = {
    "arbeitnow": ArbeitnowProvider,
    "avlis": AvlisProvider,
    "recruiterflow": RecruiterflowProvider,
    "workable": WorkableProvider,
    "ashby": AshbyProvider,
    "dynamitejobs": DynamiteJobsProvider,
    "fourdayweek": FourDayWeekProvider,
    "getonbrd": GetonbrdProvider,
    "greenhouse": GreenhouseProvider,
    "gupy": GupyProvider,
    "himalayas": HimalayasProvider,
    "jobgether": JobgetherProvider,
    "jobicy": JobicyProvider,
    "jooble": _jooble,
    "lever": LeverProvider,
    "programathor": ProgramathorProvider,
    "remoteok": RemoteOkProvider,
    "remotive": RemotiveProvider,
    "speedrun": SpeedrunProvider,
    "torre": TorreProvider,
    "workingnomads": WorkingNomadsProvider,
    "recruitee": RecruiteeProvider,
    "rippling": RipplingProvider,
    "comeet": ComeetProvider,
    "teamtailor": TeamtailorProvider,
    "workday": WorkdayProvider,
    "wwr": WwrProvider,
}

#: The same adapters, reachable without a fetcher.
#:
#: M2 needs to know which payload paths carry meaning in order to *interpret* an
#: already-archived payload. It never fetches anything. Going through
#: `get_provider` would mean constructing an HTTP client inside a deliberately
#: offline path -- harmless today, and exactly the sort of thing that later
#: turns "run the extraction" into "open a socket" without anyone deciding to.
#:
#: A test asserts these keys match `_FACTORIES`, so a fourth provider cannot be
#: registered in one dict and forgotten in the other.
_FIELD_MAPS: dict[str, ProviderFieldMap] = {
    "arbeitnow": ArbeitnowProvider.field_map,
    "avlis": AvlisProvider.field_map,
    "recruiterflow": RecruiterflowProvider.field_map,
    "workable": WorkableProvider.field_map,
    "ashby": AshbyProvider.field_map,
    "dynamitejobs": DynamiteJobsProvider.field_map,
    "fourdayweek": FourDayWeekProvider.field_map,
    "getonbrd": GetonbrdProvider.field_map,
    "greenhouse": GreenhouseProvider.field_map,
    "gupy": GupyProvider.field_map,
    "himalayas": HimalayasProvider.field_map,
    "jobgether": JobgetherProvider.field_map,
    "jobicy": JobicyProvider.field_map,
    "jooble": JoobleProvider.field_map,
    "lever": LeverProvider.field_map,
    "programathor": ProgramathorProvider.field_map,
    "remoteok": RemoteOkProvider.field_map,
    "remotive": RemotiveProvider.field_map,
    "speedrun": SpeedrunProvider.field_map,
    "torre": TorreProvider.field_map,
    "workingnomads": WorkingNomadsProvider.field_map,
    "recruitee": RecruiteeProvider.field_map,
    "rippling": RipplingProvider.field_map,
    "comeet": ComeetProvider.field_map,
    "teamtailor": TeamtailorProvider.field_map,
    "workday": WorkdayProvider.field_map,
    "wwr": WwrProvider.field_map,
}


#: Whose record each adapter reads.
#:
#: Every registered provider must appear, and a test asserts the keys match
#: `_FACTORIES`. There is deliberately no default: an adapter that does not say
#: whose record it reads is an adapter whose provenance nobody decided, and the
#: cost of guessing wrong is an aggregator's republished text being presented as
#: the employer's own.
#: What each adapter actually supplies. Asked by the matcher, which must not
#: name a provider: `tests/unit/test_provider_neutrality.py` is right to
#: forbid that, and the answer to "may I read this location as a scope?" is a
#: property of the adapter rather than of the matcher.
_CAPABILITIES: dict[str, ProviderCapabilities] = {
    "arbeitnow": ArbeitnowProvider.capabilities,
    "avlis": AvlisProvider.capabilities,
    "recruiterflow": RecruiterflowProvider.capabilities,
    "workable": WorkableProvider.capabilities,
    "ashby": AshbyProvider.capabilities,
    "dynamitejobs": DynamiteJobsProvider.capabilities,
    "fourdayweek": FourDayWeekProvider.capabilities,
    "getonbrd": GetonbrdProvider.capabilities,
    "greenhouse": GreenhouseProvider.capabilities,
    "gupy": GupyProvider.capabilities,
    "himalayas": HimalayasProvider.capabilities,
    "jobgether": JobgetherProvider.capabilities,
    "jobicy": JobicyProvider.capabilities,
    "jooble": JoobleProvider.capabilities,
    "lever": LeverProvider.capabilities,
    "programathor": ProgramathorProvider.capabilities,
    "remoteok": RemoteOkProvider.capabilities,
    "remotive": RemotiveProvider.capabilities,
    "speedrun": SpeedrunProvider.capabilities,
    "torre": TorreProvider.capabilities,
    "workingnomads": WorkingNomadsProvider.capabilities,
    "recruitee": RecruiteeProvider.capabilities,
    "rippling": RipplingProvider.capabilities,
    "comeet": ComeetProvider.capabilities,
    "teamtailor": TeamtailorProvider.capabilities,
    "workday": WorkdayProvider.capabilities,
    "wwr": WwrProvider.capabilities,
}


_KINDS: dict[str, ProviderKind] = {
    "arbeitnow": ArbeitnowProvider.kind,
    "avlis": AvlisProvider.kind,
    "recruiterflow": RecruiterflowProvider.kind,
    "workable": WorkableProvider.kind,
    "ashby": AshbyProvider.kind,
    "dynamitejobs": DynamiteJobsProvider.kind,
    "fourdayweek": FourDayWeekProvider.kind,
    "getonbrd": GetonbrdProvider.kind,
    "greenhouse": GreenhouseProvider.kind,
    "gupy": GupyProvider.kind,
    "himalayas": HimalayasProvider.kind,
    "jobgether": JobgetherProvider.kind,
    "jobicy": JobicyProvider.kind,
    "jooble": JoobleProvider.kind,
    "lever": LeverProvider.kind,
    "programathor": ProgramathorProvider.kind,
    "remoteok": RemoteOkProvider.kind,
    "remotive": RemotiveProvider.kind,
    "speedrun": SpeedrunProvider.kind,
    "torre": TorreProvider.kind,
    "workingnomads": WorkingNomadsProvider.kind,
    "recruitee": RecruiteeProvider.kind,
    "rippling": RipplingProvider.kind,
    "comeet": ComeetProvider.kind,
    "teamtailor": TeamtailorProvider.kind,
    "workday": WorkdayProvider.kind,
    "wwr": WwrProvider.kind,
}


#: How each adapter reaches postings at all. See `RetrievalMode`.
#:
#: Registered beside `_KINDS` rather than read off the class at every call, for
#: the same reason: one place per question, and a test that the keys match
#: `_FACTORIES` so a new adapter cannot be half-registered.
_RETRIEVAL_MODES: dict[str, RetrievalMode] = {
    "arbeitnow": ArbeitnowProvider.retrieval_mode,
    "avlis": AvlisProvider.retrieval_mode,
    "recruiterflow": RecruiterflowProvider.retrieval_mode,
    "workable": WorkableProvider.retrieval_mode,
    "ashby": AshbyProvider.retrieval_mode,
    "dynamitejobs": DynamiteJobsProvider.retrieval_mode,
    "fourdayweek": FourDayWeekProvider.retrieval_mode,
    "getonbrd": GetonbrdProvider.retrieval_mode,
    "greenhouse": GreenhouseProvider.retrieval_mode,
    "gupy": GupyProvider.retrieval_mode,
    "himalayas": HimalayasProvider.retrieval_mode,
    "jobgether": JobgetherProvider.retrieval_mode,
    "jobicy": JobicyProvider.retrieval_mode,
    "jooble": JoobleProvider.retrieval_mode,
    "lever": LeverProvider.retrieval_mode,
    "programathor": ProgramathorProvider.retrieval_mode,
    "remoteok": RemoteOkProvider.retrieval_mode,
    "remotive": RemotiveProvider.retrieval_mode,
    "speedrun": SpeedrunProvider.retrieval_mode,
    "torre": TorreProvider.retrieval_mode,
    "workingnomads": WorkingNomadsProvider.retrieval_mode,
    "recruitee": RecruiteeProvider.retrieval_mode,
    "rippling": RipplingProvider.retrieval_mode,
    "comeet": ComeetProvider.retrieval_mode,
    "teamtailor": TeamtailorProvider.retrieval_mode,
    "workday": WorkdayProvider.retrieval_mode,
    "wwr": WwrProvider.retrieval_mode,
}


def retrieval_mode(name: str) -> RetrievalMode | None:
    """How a provider reaches postings, or None when it is not registered."""
    return _RETRIEVAL_MODES.get(name)


#: How each adapter recognises its OWN posting URLs.
#:
#: This is what makes cross-source identity deterministic. An aggregator
#: publishes the employer's real apply link; asking every adapter "is this
#: yours?" turns that link into an exact `(provider, external_id)` key, so the
#: same posting reached two ways collapses to one row without fuzzy matching.
#: ADR-0008 closes by warning that no fuzzy job merging exists here, and this is
#: how it stays that way.
#:
#: Measured over the archived corpus: 21,136 of 21,136 job URLs are recognised
#: by exactly one adapter, each returning an id byte-identical to the stored
#: `external_id`, and no adapter claims another's URLs.
_URL_RECOGNISERS: dict[str, PostingUrlRecogniser] = {
    "ashby": ashby.recognise_posting_url,
    "comeet": comeet.recognise_posting_url,
    "greenhouse": greenhouse.recognise_posting_url,
    "lever": lever.recognise_posting_url,
    "speedrun": speedrun.recognise_posting_url,
    "torre": torre.recognise_posting_url,
    "workable": workable.recognise_posting_url,
    "workday": workday.recognise_posting_url,
}


#: The optional compensation capability, keyed the same way and for the same
#: reason: scoring reads archived payloads and must never build an HTTP client.
#:
#: Membership is the statement. A provider IN this dict can be asked what a
#: payload says about pay; a provider ABSENT from it has no reader, and the
#: answer to "what does it say?" is "this provider does not tell us" -- which
#: is not the same fact as "the posting states no salary" and must not be
#: scored as one.
#:
#: All three are here now, and the third one is a correction. Greenhouse was
#: absent on the reasoning that `?pay_transparency=true` is a list parameter
#: the collector does not send, so no archived payload could carry pay. The
#: parameter half is still true; the conclusion was false. Boards publish a
#: range as an ordinary custom field, which arrives with no flag asked for, and
#: 633 of the 10,156 archived payloads carry one. Absence here was asserting a
#: fact about the corpus that the corpus contradicted -- which is worse than a
#: missing reader, because absence is the mechanism this dict uses to speak.
#:
#: Unlike `_FIELD_MAPS` this deliberately does NOT have to cover every
#: registered provider, so no test asserts the keys match `_FACTORIES` -- the
#: point of the capability is that a vendor may not have it.
_COMPENSATION_READERS: dict[str, CompensationReader] = {
    "recruitee": recruitee.read_compensation,
    "ashby": ashby.read_compensation,
    "dynamitejobs": dynamitejobs.read_compensation,
    "fourdayweek": fourdayweek.read_compensation,
    "greenhouse": greenhouse.read_compensation,
    "himalayas": himalayas.read_compensation,
    "jobgether": jobgether.read_compensation,
    "jobicy": jobicy.read_compensation,
    "remotive": remotive.read_compensation,
    "rippling": rippling.read_compensation,
    "comeet": comeet.read_compensation,
    "lever": lever.read_compensation,
    "programathor": programathor.read_compensation,
    "speedrun": speedrun.read_compensation,
}


#: Where each reader looks. Declared for one reason: the neutrality guard.
#:
#: `field_map.paths()` lists only the paths that carry a canonical DIMENSION,
#: and pay is an amount rather than a dimension, so `compensation.compensationTiers`,
#: `salaryRange` and `metadata` appeared in no declared list anywhere. Generic
#: code could have reached straight into a payload for a salary and passed
#: `test_provider_neutrality.py`, which derives its forbidden-path list from the
#: field maps alone. It now derives it from `declared_payload_paths()`.
#:
#: One caveat, stated rather than papered over: `metadata` is an ordinary
#: English word, and the guard matches single-word paths by whole-string
#: equality. `pipeline/collect.py` uses "metadata" as a key in its own run
#: statistics and `llm/vendors/openai_compatible.py` reads an OpenRouter error
#: field of that name; neither has anything to do with a job board. Guarding
#: the bare word would fire on both, and a guard that cries wolf gets deleted.
#: The guard therefore covers `currency_range` -- the vendor-specific
#: discriminator that generic code would actually have to spell to interpret
#: Greenhouse pay -- and the bare key stays declared here for honesty about
#: what the reader touches.
_COMPENSATION_PATHS: dict[str, tuple[str, ...]] = {
    "recruitee": ("salary",),
    "ashby": ashby.COMPENSATION_PATHS,
    "greenhouse": greenhouse.COMPENSATION_PATHS,
    "lever": lever.COMPENSATION_PATHS,
    "speedrun": speedrun.COMPENSATION_PATHS,
}


#: Which adapters can be asked about ONE EMPLOYER. See
#: `JobProvider.addresses_boards_by_company`.
_BY_COMPANY: dict[str, bool] = {
    "recruitee": RecruiteeProvider.addresses_boards_by_company,
    "rippling": RipplingProvider.addresses_boards_by_company,
    "comeet": ComeetProvider.addresses_boards_by_company,
    "teamtailor": TeamtailorProvider.addresses_boards_by_company,
    "ashby": AshbyProvider.addresses_boards_by_company,
    "avlis": AvlisProvider.addresses_boards_by_company,
    "recruiterflow": RecruiterflowProvider.addresses_boards_by_company,
    "dynamitejobs": DynamiteJobsProvider.addresses_boards_by_company,
    "fourdayweek": FourDayWeekProvider.addresses_boards_by_company,
    "getonbrd": GetonbrdProvider.addresses_boards_by_company,
    "greenhouse": GreenhouseProvider.addresses_boards_by_company,
    "jooble": JoobleProvider.addresses_boards_by_company,
    "lever": LeverProvider.addresses_boards_by_company,
    "speedrun": SpeedrunProvider.addresses_boards_by_company,
    "torre": TorreProvider.addresses_boards_by_company,
    "workday": WorkdayProvider.addresses_boards_by_company,
    "wwr": WwrProvider.addresses_boards_by_company,
}


#: Which adapters' board identifiers can be GUESSED from a company name.
#:
#: A SECOND question, and the pair is not redundant. `addresses_boards_by_company`
#: asks whether a family has a board per employer at all -- which decides what
#: `collect` walks. This asks whether an identifier can be DERIVED from a company
#: slug, which decides what `discover` may probe.
#:
#: Four families answer yes: hand Greenhouse `acme` and it either has that board
#: or it does not, and a 404 is a real answer. Workday answers no, and it is why
#: this dict exists: a board there is a tenant, a numbered data centre AND a
#: site, so a probe would have to invent two of the three and read whatever
#: answered as evidence. Its boards arrive from `scan-career-sites`, which reads
#: what the employer published -- the same rule ADR-0013 applies to a posting.
_GUESSABLE: dict[str, bool] = {
    name: getattr(_FACTORIES[name], "identifier_is_guessable", True)
    for name, by_company in _BY_COMPANY.items()
    if by_company
}


def guessable_providers() -> list[str]:
    """Board families `discover` may probe with an identifier it constructed."""
    return sorted(name for name, guessable in _GUESSABLE.items() if guessable)


def answering_for_unknown_providers() -> list[str]:
    """Board families whose host answers for an identifier nobody registered.

    See `JobProvider.answers_for_unknown_identifiers`. For these a listing
    that merely answers is no evidence of a board; the posting a caller came
    for must be in it.
    """
    return sorted(
        name
        for name, by_company in _BY_COMPANY.items()
        if by_company and getattr(_FACTORIES[name], "answers_for_unknown_identifiers", False)
    )


#: Board families employer board discovery asks about an aggregator's
#: employer. A subset of `guessable_providers()`: the three whose public board
#: is one JSON request and whose identifier is usually the company's name.
_EMPLOYER_PROBE: tuple[str, ...] = ("ashby", "greenhouse", "lever")
#: Feeds whose every posting already sits on the employer's own ATS board, so
#: their employers are not leads for finding one.
_FEEDS_OF_EMPLOYER_BOARDS: frozenset[str] = frozenset({"workable"})


def employer_probe_providers() -> tuple[str, ...]:
    """The families `pipeline.employer_boards` may probe, in probing order."""
    guessable = set(guessable_providers())
    return tuple(name for name in _EMPLOYER_PROBE if name in guessable)


def employer_lead_providers() -> tuple[str, ...]:
    """Aggregators whose employers may have an ATS board nobody has found yet."""
    boards = set(board_providers())
    return tuple(
        sorted(
            name
            for name, kind in _KINDS.items()
            if kind is ProviderKind.AGGREGATOR
            and name not in boards
            and name not in _FEEDS_OF_EMPLOYER_BOARDS
        )
    )


def board_providers() -> list[str]:
    """Providers `discover` can ask "does this employer have a board here".

    Smaller than `available_providers()`, and for two different reasons. Jooble
    has no boards at all and probing it would spend a permanently limited
    request. We Work Remotely has feeds addressed by CATEGORY, so a company
    slug is a question in the wrong vocabulary and raises rather than answers.
    """
    return sorted(name for name, by_company in _BY_COMPANY.items() if by_company)


def available_providers() -> list[str]:
    return sorted(_FACTORIES)


def get_provider(name: str, fetcher: HttpFetcher) -> JobProvider:
    try:
        factory = _FACTORIES[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"no adapter for provider {name!r}; available: {', '.join(available_providers())}"
        ) from exc
    return factory(fetcher)


def field_map_for(name: str) -> ProviderFieldMap:
    """Which payload paths carry meaning for this provider. No network involved."""
    try:
        return _FIELD_MAPS[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"no adapter for provider {name!r}; available: {', '.join(available_providers())}"
        ) from exc


def provider_kind(name: str) -> ProviderKind | None:
    """Whose record this provider reads, or None for a name with no adapter.

    None rather than raising, for the same reason `compensation_reader_for`
    returns None: this is asked about every row in the corpus, including
    `manual_import`, which is a posting a person pasted and has no adapter.
    """
    return _KINDS.get(name)


#: How to read the hiring scope out of a stored payload, for the providers
#: that publish one. Each is the adapter's own reader, so a vendor default
#: (Jobicy's `Anywhere`) is refused where the adapter refuses it. WWR's
#: `region` is a plain string and needs no reader beyond the field itself.
_HIRING_SCOPE_READERS: dict[str, Callable[[Any], str | None]] = {
    "himalayas": himalayas.hiring_scope,
    "jobicy": jobicy.hiring_scope,
    "remotive": remotive.hiring_scope,
    "jobgether": jobgether.hiring_scope,
    "dynamitejobs": dynamitejobs.hiring_scope,
    "fourdayweek": fourdayweek.hiring_scope,
    "wwr": lambda payload: (
        str(payload.get("region") or "").strip() or None if isinstance(payload, dict) else None
    ),
}


def hiring_scope_of(name: str | None, payload: Any) -> str | None:
    """The hiring scope a stored payload from `name` states, or None.

    None for a provider that publishes no scope, whatever the payload holds:
    an office in a Greenhouse payload is never read as one. Used when a
    posting held from one provider was also SEEN by a scope-publishing
    aggregator, so the sighting's scope can reach the gate (2026-09-11).
    """
    if not name or not publishes_hiring_scope(name):
        return None
    reader = _HIRING_SCOPE_READERS.get(name)
    return reader(payload) if reader else None


def publishes_hiring_scope(name: str | None) -> bool:
    """Whether this provider's location field is a STATED HIRING SCOPE.

    The question the matcher must ask before letting a board's location field
    anywhere near the eligibility gate. `San Francisco, CA` on a Greenhouse
    posting is an office; `Anywhere in the World` in a We Work Remotely
    `region` element is the employer answering where it may hire. Reading the
    first as the second is how a candidate in Brazil is told she is eligible
    for a job in California, which is the mistake invariant 3 exists to name.

    False for an unknown name, and for None, which is what `manual_import`
    supplies. Absence is never permission here either.
    """
    if not name:
        return False
    capabilities = _CAPABILITIES.get(name)
    return bool(capabilities and capabilities.publishes_hiring_scope)


def capabilities_for(name: str) -> ProviderCapabilities:
    """What this adapter, as configured, actually supplies.

    The registry already held the answer and only ever exposed one field of it
    -- `publishes_hiring_scope` -- through a boolean helper. The whole record
    is worth asking for: "does this source publish an employment type at all"
    is the question that separates a posting which omitted one from a source
    that has nowhere to put one, and that distinction is what
    `ProviderCapabilities` was written to carry.

    Raises for a name with no adapter, like `field_map_for` above and for the
    same reason: a caller asking about a provider that does not exist has a
    defect, and a permissive default would answer it with a shrug.
    """
    try:
        return _CAPABILITIES[name]
    except KeyError as exc:
        raise UnknownProviderError(
            f"no adapter for provider {name!r}; available: {', '.join(available_providers())}"
        ) from exc


def content_completeness_for(provider: str | None, *, has_text: bool) -> ContentCompleteness:
    """How much of the employer's posting this system can hold, for one source.

    Two inputs and no guessing. The ADAPTER answers whether the whole
    description is obtainable at all, and the JOB answers whether any text was
    stored. Neither can be inferred from the other: a source that could supply
    everything still yields nothing for a posting collected before its body
    arrived, and a source that can only ever supply an excerpt yields one
    reliably.

    UNKNOWN for a provider with no adapter, which is `manual_import`: a person
    pasted a description and only they know whether they pasted all of it.
    Guessing FULL_CONTENT there would put this system's word behind somebody
    else's copy and paste.
    """
    if not has_text:
        return ContentCompleteness.METADATA_ONLY
    if not provider:
        return ContentCompleteness.UNKNOWN
    capabilities = _CAPABILITIES.get(provider)
    if capabilities is None:
        return ContentCompleteness.UNKNOWN
    return (
        ContentCompleteness.FULL_CONTENT
        if capabilities.obtains_full_description
        else ContentCompleteness.PARTIAL_CONTENT
    )


#: How each BOARD family recognises a posting URL on one of its own hosted
#: boards, and names the board.
#:
#: The other half of ADR-0013, for discovery rather than identity. An
#: aggregator that republishes ATS postings publishes the employer's real
#: apply link, and on a vendor-hosted board that link carries the board
#: identifier in its host or path. Asking every family "is this one of your
#: boards?" turns a published URL into an exact `(provider, board_identifier)`
#: -- an identity somebody PUBLISHED, which is the only kind a family with
#: `identifier_is_guessable = False` (Workday, Recruitee) may ever receive.
#:
#: Each recogniser returns `(board_identifier, external_id | None)`; the id is
#: in the family's stored form when the URL carries it, so the posting can be
#: looked for on the board exactly, and None when the URL does not.
BoardUrlRecogniser = Callable[[str], tuple[str, str | None] | None]

_BOARD_RECOGNISERS: dict[str, BoardUrlRecogniser] = {
    "ashby": ashby.recognise_board_url,
    "greenhouse": greenhouse.recognise_board_url,
    "lever": lever.recognise_board_url,
    "recruitee": recruitee.recognise_board_url,
    "rippling": rippling.recognise_board_url,
    "comeet": comeet.recognise_board_url,
    "teamtailor": teamtailor.recognise_board_url,
    "workable": workable.recognise_board_url,
    "workday": workday.recognise_board_url,
}


def identify_board_url(url: str) -> tuple[str, str, str | None] | None:
    """Which family's hosted board this posting URL is on, and which board.

    Returns `(provider, board_identifier, external_id)` or None. Ties are
    refused for the reason `identify_posting_url` gives: two families
    claiming one URL means the answer is "we cannot say", never "the first
    one in the dictionary".
    """
    if not url or not url.strip():
        return None
    matches = [
        (name, found[0], found[1])
        for name, recognise in sorted(_BOARD_RECOGNISERS.items())
        if (found := recognise(url)) is not None
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def board_recogniser_families() -> tuple[str, ...]:
    """The families a published posting URL can name a board for."""
    return tuple(sorted(_BOARD_RECOGNISERS))


def identify_posting_url(url: str) -> tuple[str, str] | None:
    """Which registered provider's posting this URL is, and its native id.

    Returns `(provider, external_id)` or None. This is the whole cross-source
    identity mechanism, and it is deterministic: an adapter either recognises
    the shape it builds itself, or it does not. There is no similarity score
    anywhere in it.

    **Ties are refused rather than guessed.** If two adapters claimed one URL,
    the honest answer is that we cannot say which posting it is, and returning
    the first match in dictionary order would make identity depend on
    declaration order. Measured over the corpus no URL is claimed twice, and a
    test pins that; this branch exists so a future adapter with a loose pattern
    fails loudly instead of quietly stealing another vendor's postings.
    """
    if not url or not url.strip():
        return None
    matches = [
        (name, external_id)
        for name, recognise in sorted(_URL_RECOGNISERS.items())
        if (external_id := recognise(url)) is not None
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def canonical_posting_url(url: str) -> str:
    """One URL reduced to the form two sources can be compared on.

    Re-exported so callers that need the weaker, string-level identity do not
    reach into `providers.base` for it. The strong identity is
    `identify_posting_url`; this is the fallback for a URL no adapter claims.
    """
    return canonical_url(url)


def compensation_reader_for(name: str) -> CompensationReader | None:
    """How to read pay out of this provider's payload, if it can be read at all.

    Returns None rather than raising, and the difference from `field_map_for`
    is deliberate. That function is asked about a provider we collected from,
    so an unknown name there is a bug. This one is asked about every row in the
    corpus, including `manual_import` -- a posting a person pasted, which has
    no adapter and never will. Raising would make the ordinary case an
    exception to swallow, and a swallowed exception is where "we did not ask"
    starts looking like "there was nothing to find".
    """
    return _COMPENSATION_READERS.get(name)


def compensation_paths_for(name: str) -> tuple[str, ...]:
    """Which payload paths this provider's compensation reader touches.

    Empty for a provider with no reader, and for `manual_import`, for the same
    reason `compensation_reader_for` returns None rather than raising.
    """
    return _COMPENSATION_PATHS.get(name, ())


def declared_payload_paths() -> set[str]:
    """Every vendor payload path any adapter admits to reading. No network.

    The union of the field maps and the compensation readers, and the union is
    the point: two mechanisms read payloads, and a guard built from one of them
    covers half the surface. Generic code must spell none of these.
    """
    return {path for field_map in _FIELD_MAPS.values() for path in field_map.paths()} | {
        path for paths in _COMPENSATION_PATHS.values() for path in paths
    }
