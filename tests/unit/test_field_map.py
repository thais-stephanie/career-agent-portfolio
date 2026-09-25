"""The provider-metadata transport: paths in, observations out, nothing decided.

This is the mechanism M0 section 6.3 specified and M1B.1 finally built. The
tests below check three separate things, and it is worth keeping them apart:

1. the resolver and serialiser behave (mechanism);
2. each registered adapter's declared paths actually resolve in recorded real
   payloads (the maps are true, not aspirational);
3. an observation can be verified back against the payload it came from, which
   is what `PROVIDER_FIELD` evidence will need at M2.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.domain.enums import MatchKind, MetadataDimension
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    FieldMapping,
    ProviderFieldMap,
    resolve_metadata,
    resolve_path,
    serialise_value,
    verify_provider_field,
)
from career_agent.providers.greenhouse import GREENHOUSE_FIELD_MAP
from career_agent.providers.lever import LEVER_FIELD_MAP
from career_agent.providers.registry import available_providers, get_provider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def _fixture(provider: str, name: str) -> Any:
    """A recorded payload, whichever format the vendor publishes.

    JSON for the four API sources; XML for We Work Remotely, which publishes an
    RSS feed. The adapter parses its own format -- that is what an adapter is --
    so this asks it, rather than teaching this file to read RSS.
    """
    if name.endswith(".xml"):
        from career_agent.providers.wwr import WwrProvider

        text = (FIXTURES / provider / name).read_text(encoding="utf-8")
        return WwrProvider(None).parse(name, "recorded", text).items  # type: ignore[arg-type]
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


#: Providers that cannot be built from a fetcher alone, and what to hand them.
#:
#: Jooble is the only one. Its key is country-bound and its quota is a
#: lifetime, so the registry factory reads the environment and REFUSES when the
#: domain is unset -- which is correct for the product and means this helper
#: cannot go through the registry for it. Placeholders, never a real key: this
#: constructs an adapter to read its field map and never sends anything.
REQUIRES_CONFIGURATION = {"jooble": {"domain": "example.jooble.invalid", "api_key": "not-a-key"}}


def _any_provider(name: str) -> Any:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
        request_delay_seconds=0.0,
    )
    if name in REQUIRES_CONFIGURATION:
        from career_agent.providers.jooble import JoobleProvider

        return JoobleProvider(fetcher, **REQUIRES_CONFIGURATION[name])
    return get_provider(name, fetcher)


# --- path resolution -------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("workplaceType", "remote"),
        ("categories.commitment", "Full-time"),
        ("categories.location", "Remote - US"),
        ("nested.deep.value", 7),
        ("missing", None),
        ("categories.missing", None),
        ("workplaceType.deeper", None),
        ("", None),
    ],
    ids=[
        "top",
        "nested",
        "nested-2",
        "deep",
        "absent",
        "absent-nested",
        "scalar-then-dot",
        "empty",
    ],
)
def test_resolve_path(path: str, expected: object) -> None:
    payload = {
        "workplaceType": "remote",
        "categories": {"commitment": "Full-time", "location": "Remote - US"},
        "nested": {"deep": {"value": 7}},
    }
    assert resolve_path(payload, path) == expected


def test_resolve_path_never_touches_sql() -> None:
    """Hazard A17, restated as a test that reads: this works on a plain dict.

    `payload_json` is opaque storage from SQL's point of view. String-matching
    JSON with LIKE is unportable and wrong, so path resolution happens in Python
    against the parsed payload -- which means it needs no database at all.
    """
    assert resolve_path({"a": {"b": "c"}}, "a.b") == "c"


# --- serialisation ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("remote", "remote"),
        ("  Full-Time  ", "Full-Time"),
        (150000, "150000"),
        (1.5, "1.5"),
        (True, "true"),
        (False, "false"),
        (None, None),
        ("", None),
        ("   ", None),
        ({}, None),
        ([], None),
    ],
    ids=[
        "string",
        "trimmed",
        "int",
        "float",
        "true",
        "false",
        "none",
        "empty",
        "blank",
        "empty-object",
        "empty-list",
    ],
)
def test_serialise_value(value: object, expected: str | None) -> None:
    assert serialise_value(value) == expected


def test_false_serialises_as_false_not_as_zero() -> None:
    """bool is an int subclass. Without the guard `False` would become "0",
    which is a value -- when what the provider said was no."""
    assert serialise_value(False) == "false"
    assert serialise_value(0) == "0"


def test_objects_serialise_whole_with_sorted_keys() -> None:
    """`salaryRange` travels intact rather than flattened, and byte-for-byte
    stably: the producer and the verifier must agree exactly, or M2 evidence
    would fail verification for formatting reasons rather than real ones."""
    a = serialise_value({"min": 150000, "max": 250000, "currency": "USD"})
    b = serialise_value({"currency": "USD", "max": 250000, "min": 150000})

    assert a == b == '{"currency":"USD","max":250000,"min":150000}'
    assert json.loads(a)["min"] == 150000, "still a lossless round trip"


# --- observations ----------------------------------------------------------


def test_an_absent_field_produces_no_observation() -> None:
    """Absence stays absence. Emitting an observation with an empty value would
    be manufacturing a claim the employer never made."""
    field_map = ProviderFieldMap(
        (FieldMapping("workplaceType", MetadataDimension.WORK_MODEL_HINT),)
    )
    for payload in ({}, {"workplaceType": None}, {"workplaceType": ""}, {"other": "x"}):
        assert resolve_metadata("x", field_map, payload) == ()


def test_two_paths_on_one_dimension_stay_two_observations() -> None:
    """Lever asserts location twice, in `country` and in `categories.location`.
    Collapsing them would be deciding which one the employer meant."""
    payload = {"country": "US", "categories": {"location": "Anywhere"}}
    observations = resolve_metadata("lever", LEVER_FIELD_MAP, payload)
    located = [o for o in observations if o.dimension is MetadataDimension.HIRING_LOCATION_HINT]

    assert len(located) == 2
    assert {o.source_value for o in located} == {"US", "Anywhere"}
    assert {o.source_field for o in located} == {"country", "categories.location"}


def test_observations_carry_provenance_not_conclusions() -> None:
    observation = resolve_metadata("lever", LEVER_FIELD_MAP, {"workplaceType": "remote"})[0]

    assert observation.dimension is MetadataDimension.WORK_MODEL_HINT
    assert observation.provider == "lever"
    assert observation.source_field == "workplaceType"
    assert observation.source_value == "remote"
    assert not hasattr(observation, "eligible")
    assert not hasattr(observation, "value"), "there is no interpreted value, only a raw one"


def test_declaration_order_is_preserved() -> None:
    payload = {
        "workplaceType": "remote",
        "country": "BR",
        "categories": {"location": "Anywhere", "commitment": "Full-Time"},
        "salaryRange": {"min": 1, "max": 2},
    }
    fields = [o.source_field for o in resolve_metadata("lever", LEVER_FIELD_MAP, payload)]
    assert fields == list(LEVER_FIELD_MAP.paths())


# --- missing vs not exposed ------------------------------------------------


def test_a_declared_dimension_with_no_value_means_the_employer_did_not_say() -> None:
    payload = {"workplaceType": "remote"}  # a Lever posting with no salary
    observed = {o.dimension for o in resolve_metadata("lever", LEVER_FIELD_MAP, payload)}

    assert MetadataDimension.COMPENSATION_HINT in LEVER_FIELD_MAP.dimensions()
    assert MetadataDimension.COMPENSATION_HINT not in observed
    # Declared but unobserved. Not "this employer pays nothing", and not "Lever
    # has no salary field" -- this posting did not disclose.


def test_an_undeclared_dimension_means_the_provider_cannot_express_it() -> None:
    """The distinction ProviderCapabilities exists for, now visible in the map
    as well: three of the four dimensions are simply not in Greenhouse's."""
    greenhouse = GREENHOUSE_FIELD_MAP.dimensions()

    assert MetadataDimension.HIRING_LOCATION_HINT in greenhouse
    assert MetadataDimension.WORK_MODEL_HINT not in greenhouse
    assert MetadataDimension.COMPENSATION_HINT not in greenhouse
    assert MetadataDimension.EMPLOYMENT_TYPE_HINT not in greenhouse
    assert greenhouse < LEVER_FIELD_MAP.dimensions(), "smaller, not special-cased"


def test_an_empty_map_is_a_valid_and_complete_answer() -> None:
    empty = ProviderFieldMap()
    assert empty.dimensions() == frozenset()
    assert resolve_metadata("someone", empty, {"anything": "here"}) == ()


def test_greenhouse_maps_location_although_it_is_not_structured() -> None:
    """These answer different questions and it looks contradictory until stated.

    The capability says the value is free text rather than a structured code.
    The mapping says that free text is still the provider's location assertion.
    Mapping it does not upgrade it.
    """
    provider = _any_provider("greenhouse")
    assert provider.capabilities.exposes_location_structured is False
    assert MetadataDimension.HIRING_LOCATION_HINT in provider.field_map.dimensions()


# --- the maps are true, not aspirational -----------------------------------

RECORDED = {
    # Documentation-derived rather than recorded, and labelled as such in the
    # fixture itself. Jooble documents a LIFETIME quota of 500 requests per
    # key, so a recording is not free the way every other fixture here was: the
    # shape came from the vendor's published contract, and a live probe
    # confirms it once rather than on demand.
    "jooble": [("search-page-documented", lambda d: d["jobs"][:2])],
    # XML rather than JSON, so it is loaded by `_recorded_rows` below rather
    # than by the shared JSON reader. A feed is still a recorded payload.
    "wwr": [("feed-programming.xml", list)],
    "greenhouse": [("board_small", lambda d: d["jobs"])],
    # CAPTURED from a real response, 2026-09-07, and trimmed. Unlike Get on
    # Board and Jooble below, nothing on `himalayas.app` restricts this reader
    # -- its robots.txt is one `User-agent: *` block with `Allow: /`, names no
    # AI crawler and does not mention this path -- so the fixture is a
    # recording rather than a reconstruction. Descriptions are truncated to
    # keep the file small; every other field is verbatim.
    "himalayas": [("feed-page1", lambda d: d["jobs"])],
    # Also CAPTURED, 2026-09-07, and trimmed. `www.workingnomads.com` is one
    # `User-agent: *` block with an empty `Disallow:`. The feed is a bare JSON
    # list rather than an envelope, so the extractor is the identity.
    "workingnomads": [("feed", lambda d: d)],
    "lever": [
        ("board_small", lambda d: d),
        ("board_team_only", lambda d: d),
        ("board_description_only", lambda d: d),
    ],
    "ashby": [
        ("board_rich", lambda d: d["jobs"]),
        ("board_sparse", lambda d: d["jobs"]),
    ],
    # The detail payloads, NOT the listing page, and that is the whole point of
    # naming them separately. This source returns two different shapes: a
    # listing entry with no description and no `comp_summary`, and a detail
    # record that carries both. The field map declares `comp_summary`, which
    # exists only on the second, so resolving the map against the listing page
    # would report a declared path as unresolvable and be right to.
    "speedrun": [
        ("job_details", lambda d: d["jobs"]),
    ],
    # Contract-derived rather than recorded, and labelled in the fixture and in
    # `tests/unit/test_getonbrd.py` as well as here. `www.getonbrd.com`
    # disallows `ClaudeBot` by name and the agent that wrote the adapter is
    # Claude, so no live response was captured. Same category as Jooble above:
    # the shape came from a published contract rather than from a recording,
    # and the owner's first bounded retrieval is what replaces it.
    "getonbrd": [("category-programming-page1", lambda d: d["data"])],
    # Contract-derived for a stronger reason than any other row here: this
    # product refuses to call Torre at all, on the `robots.txt` line recorded
    # in `providers/torre.py`. A recording could not be made without doing the
    # thing the adapter exists to decline.
    "torre": [("opportunities-search", lambda d: d["results"])],
    # Contract-derived, and the fixture text is INVENTED rather than trimmed
    # from a response. `jobicy.com/robots.txt` answers 403 behind an
    # interactive challenge that was not solved, so nothing was recorded here;
    # the SHAPE comes from the owner's own bounded research pass and the
    # permission comes from a first-party grant on `jobicy.com/jobs-rss-feed`,
    # not from robots.txt. Same category as Jooble and Get on Board above.
    "jobicy": [("window-latam", lambda d: d["jobs"])],
    # RECORDED, live, on 2026-09-09, which puts it in the same category as
    # Himalayas rather than with the three contract-derived rows above. Nothing
    # on `employability-portal.gupy.io` restricts this reader: the host serves
    # no robots.txt at all and names no crawler, so the response could be
    # captured without doing anything the adapter exists to decline.
    #
    # Two partitions rather than one, because `workplaceType` is the axis this
    # adapter pages on and the two slices carry different values of the field
    # the whole source was built for.
    "gupy": [
        ("feed-remote-page1", lambda d: d["data"]),
        ("feed-onsite-page1", lambda d: d["data"]),
    ],
    # RECORDED live 2026-09-09, and the payload is the schema.org `JobPosting`
    # block the vendor publishes rather than anything this reader composed.
    # Five records of eight attempted: the other three answered HTTP 500, which
    # is this source's ordinary case rather than a bad capture.
    "programathor": [("postings", lambda d: d["postings"])],
    # Captured 2026-09-05, BEFORE this work, and not re-fetched. `remoteok.com`
    # names `ClaudeBot` with `Disallow: /` and the agent maintaining this file
    # is ClaudeBot, so the first live call is the owner's. Same category as Get
    # on Board.
    #
    # The field map is empty by decision, so this row exercises the fixture
    # loader rather than any path resolution: the test below asserts that every
    # DECLARED path resolves, and declaring none is a claim about the vendor
    # rather than an omission.
    "remoteok": [("feed", lambda d: [r for r in d if "legal" not in r])],
    # SYNTHETIC, in the shape python-jobspy returns. LinkedIn forbids
    # automated reading (see the catalogue), so no real response is recorded
    # in this repository; the adapter is experimental and off by default.
    "linkedin": [("search", lambda d: d)],
    # RECORDED live 2026-09-09. Nothing in this vendor's robots file restricts
    # this reader and no AI crawler is named, so the response could be captured
    # without doing anything the adapter exists to decline.
    "arbeitnow": [
        ("page1", lambda d: d["data"]),
        ("page2", lambda d: d["data"]),
    ],
    # RECORDED live 2026-09-09, and trimmed to a deliberate spread: every value
    # of `workplace`, a non-United-States row, and a part-time row beside the
    # full-time ones. `jobs.workable.com/robots.txt` is one `User-agent: *`
    # group that names no AI crawler and does not mention `ClaudeBot`, and the
    # API path is not among the ones it excludes.
    "workable": [
        ("search-page1", lambda d: d["jobs"]),
        ("search-page2", lambda d: d["jobs"]),
    ],
    # RECORDED live 2026-09-09, and the permission here is the strongest of
    # any source in this table: `recruiterflow.com/robots.txt` is
    # `User-agent: *` with `Allow: /` and then names `Anthropic` and `Claude`
    # explicitly, each with `Allow: /`. The rows are the board blob's own
    # entries with the department they sat under folded in, which is what the
    # adapter stores.
    "recruiterflow": [("board-rows", lambda d: d["rows"])],
    # RECORDED live 2026-09-09. `avlistalent.com/robots.txt` allows `/jobs`
    # by name and excludes only an admin, internal, marketing and scorecard
    # area. The feed is a bare JSON list, so the extractor is the identity.
    "avlis": [("jobs", lambda d: d)],
    # CONTRACT-DERIVED AND INVENTED, which puts it with Get on Board, Jooble,
    # Jobicy and Torre rather than with the recorded rows. The shape is the
    # contract measured on 2026-09-05 against two real tenants; no response was
    # captured, because a capture would have meant calling a stranger's board
    # to satisfy a test rather than to collect anything.
    #
    # Each entry is the MERGED payload the adapter stores -- the listing fields
    # plus `jobPostingInfo` from the detail request -- because the declared map
    # reads from both halves and a listing-only fixture would report two real
    # paths as unresolvable.
    "workday": [("board-postings", lambda d: d["postings"])],
    # Recorded from Tellent's production payload on 2026-09-10. These are
    # the adapter's archived fields, including its explicit code translations.
    "recruitee": [("board-postings", lambda d: d["postings"])],
    # CAPTURED live 2026-09-11 from the public window, with bodies trimmed.
    # The vendor's own API page grants access "so that developers can share
    # our jobs further", which is what makes a capture permitted here.
    "remotive": [("window", lambda d: d["jobs"])],
    # CAPTURED live 2026-09-11 from the API the vendor publishes for agents.
    # Metadata rows only: the API carries no advert and no page was read.
    "jobgether": [("page", lambda d: d["jobs"])],
    # CAPTURED live 2026-09-11 from the documented API v2, trimmed. The rows
    # are the adapter's stubs' payloads: `employment_type` is derived from
    # `contract_type` by the adapter.
    # CAPTURED live 2026-09-11 from one posting page's JSON-LD, trimmed. The
    # rows are the adapter's stubs' payloads: `applicantLocations` and
    # `employment_type` are derived by the adapter.
    "dynamitejobs": [("payloads", lambda d: d["postings"])],
    "fourdayweek": [
        (
            "page",
            lambda d: [
                dict(row, employment_type={"permanent": "Full-time"}.get(row.get("contract_type")))
                for row in d["data"]
            ],
        )
    ],
    # CAPTURED live 2026-09-11 from the vendor's own tenant feed
    # (`career.teamtailor.com/jobs.rss`), trimmed. The adapter's `workplace`
    # key is derived from `remoteStatus`, so the rows here are the adapter's
    # parsed payloads rather than raw XML.
    "teamtailor": [("payloads", lambda d: d["payloads"])],
    # CAPTURED live 2026-09-11 from the board Rippling's own careers page
    # links, merged list-plus-detail as the adapter stores it.
    "rippling": [("payloads", lambda d: d["payloads"])],
    # CAPTURED live 2026-09-11 from the Careers API behind TripleTen's hosted
    # board, trimmed to four vacancies and token-scrubbed; the rows are the
    # adapter's stored payloads, one per base uid with every location
    # variant folded in and `workplace` / `employment_type` derived.
    "comeet": [("payloads", lambda d: d["payloads"])],
}


def test_every_registered_provider_has_recorded_fixtures() -> None:
    """A guard on the coverage test below: adding a provider without recording
    fixtures for it must fail loudly rather than skip silently."""
    assert set(available_providers()) == set(RECORDED)


@pytest.mark.parametrize("provider_name", available_providers())
def test_every_declared_path_resolves_in_a_recorded_payload(provider_name: str) -> None:
    """A path that never resolves is either a typo or a field the vendor
    renamed, and either way it is a silent hole until something checks.

    This is the test that makes declaring the map in Python safer than declaring
    it in YAML: a mistyped path fails here rather than going quiet until M2.
    """
    provider = _any_provider(provider_name)
    postings = [
        posting
        for name, extract in RECORDED[provider_name]
        for posting in extract(_fixture(provider_name, name))
    ]
    assert postings, "no recorded payloads for this provider"

    resolved = {
        observation.source_field
        for posting in postings
        for observation in resolve_metadata(provider_name, provider.field_map, posting)
    }
    missing = sorted(set(provider.field_map.paths()) - resolved)
    assert not missing, f"{provider_name} declares paths no recorded payload answers: {missing}"


@pytest.mark.parametrize("provider_name", available_providers())
def test_declared_paths_are_unique_and_non_empty(provider_name: str) -> None:
    paths = _any_provider(provider_name).field_map.paths()
    assert all(p and not p.startswith(".") and not p.endswith(".") for p in paths)
    assert len(paths) == len(set(paths)), "a path declared twice is a copy-paste, not a decision"


# --- M2 readiness: an observation can be proved against its payload --------


def test_a_real_lever_posting_round_trips_through_verification() -> None:
    """What a PROVIDER_FIELD evidence row will do at M2, done today.

    Shipping the verifier alongside the producer is the point: the code that
    emits source_field/source_value and the code that later proves them are
    tested against each other now, on a recorded payload, rather than written
    months apart and hoped to agree.
    """
    posting = _fixture("lever", "board_small")[0]
    observations = resolve_metadata("lever", LEVER_FIELD_MAP, posting)
    assert observations

    for observation in observations:
        assert (
            verify_provider_field(posting, observation.source_field, observation.source_value)
            is MatchKind.FIELD_MATCH
        )


def test_the_compensation_object_verifies_whole() -> None:
    posting = _fixture("lever", "board_small")[0]
    salary = next(
        o
        for o in resolve_metadata("lever", LEVER_FIELD_MAP, posting)
        if o.dimension is MetadataDimension.COMPENSATION_HINT
    )

    assert '"currency":"USD"' in salary.source_value
    assert (
        verify_provider_field(posting, "salaryRange", salary.source_value) is MatchKind.FIELD_MATCH
    )


@pytest.mark.parametrize(
    ("path", "value"),
    [
        ("workplaceType", "onsite"),
        ("workplaceType", "Remote"),
        ("nope", "remote"),
        ("categories.nope", "x"),
        ("workplaceType", ""),
    ],
    ids=["wrong-value", "wrong-case", "no-such-path", "no-such-nested", "empty-claim"],
)
def test_verification_fails_closed(path: str, value: str) -> None:
    """No fuzzy tier on purpose: a provider field either said this or it did
    not, and "almost" is not evidence."""
    payload = {"workplaceType": "remote", "categories": {"commitment": "Full-time"}}
    assert verify_provider_field(payload, path, value) is MatchKind.NOT_FOUND


# --- the documented mirror stays honest ------------------------------------


def test_sources_yaml_mirrors_the_adapter_field_maps() -> None:
    """`config/sources.yaml` documents the maps for review; the adapters own
    them. A mirror that can drift is worse than no mirror, so it cannot.

    If this fails, fix the YAML -- the adapter is the authority.
    """
    import yaml

    config_dir = Path(__file__).resolve().parents[2] / "config"
    parsed = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))

    for provider_name in available_providers():
        documented = parsed["providers"][provider_name].get("field_map") or {}
        actual = {
            m.path: m.dimension.value for m in _any_provider(provider_name).field_map.mappings
        }
        assert documented == actual, f"{provider_name} mirror has drifted from its adapter"


def test_the_yaml_still_declares_the_providers_it_documents() -> None:
    """A guard on the guard: if the file lost its providers section the mirror
    test above would pass by comparing nothing."""
    import yaml

    config_dir = Path(__file__).resolve().parents[2] / "config"
    parsed = yaml.safe_load((config_dir / "sources.yaml").read_text(encoding="utf-8"))
    assert set(available_providers()) <= set(parsed["providers"])
    assert parsed["providers"]["lever"]["field_map"], "lever's mirror must not be empty"


# --- M1C: the resolver against a third provider -----------------------------


def test_an_array_field_travels_whole_without_indexing() -> None:
    """Ashby is the first provider with array-shaped metadata, and the reason no
    path-language extension was needed.

    `secondaryLocations` is an array on 26.9% of postings, length 1 to 8+. It
    resolves as a list and serialises whole with sorted keys, so the entire
    array becomes one observation that verifies exactly. Indexing into it --
    `secondaryLocations[0]` -- would mean choosing which location matters, which
    is a decision about the job rather than about its shape.
    """
    from career_agent.providers.ashby import ASHBY_FIELD_MAP

    posting = next(
        p for p in _fixture("ashby", "board_rich")["jobs"] if p.get("secondaryLocations")
    )
    observation = next(
        o
        for o in resolve_metadata("ashby", ASHBY_FIELD_MAP, posting)
        if o.source_field == "secondaryLocations"
    )

    assert observation.source_value.startswith("[{")
    assert json.loads(observation.source_value) == posting["secondaryLocations"], "lossless"
    assert (
        verify_provider_field(posting, "secondaryLocations", observation.source_value)
        is MatchKind.FIELD_MATCH
    )


def test_a_three_level_dotted_path_still_resolves() -> None:
    """`address.postalAddress.addressCountry` is the deepest path any provider
    declares. Plain dotted syntax reaches it."""
    from career_agent.providers.ashby import ASHBY_FIELD_MAP

    posting = _fixture("ashby", "board_rich")["jobs"][0]
    observation = next(
        o
        for o in resolve_metadata("ashby", ASHBY_FIELD_MAP, posting)
        if o.source_field == "address.postalAddress.addressCountry"
    )
    assert observation.source_value == posting["address"]["postalAddress"]["addressCountry"]


def test_every_ashby_observation_verifies_against_its_own_payload() -> None:
    from career_agent.providers.ashby import ASHBY_FIELD_MAP

    checked = 0
    for name in ("board_rich", "board_sparse"):
        for posting in _fixture("ashby", name)["jobs"]:
            for observation in resolve_metadata("ashby", ASHBY_FIELD_MAP, posting):
                assert (
                    verify_provider_field(
                        posting, observation.source_field, observation.source_value
                    )
                    is MatchKind.FIELD_MATCH
                )
                checked += 1
    assert checked >= 10


def test_a_sparse_ashby_board_declares_dimensions_it_does_not_fill() -> None:
    """One board in the sample never states workplace type or country. The
    dimensions stay declared, so the absence reads as "this employer did not
    say" rather than as "Ashby cannot express it"."""
    from career_agent.providers.ashby import ASHBY_FIELD_MAP

    posting = _fixture("ashby", "board_sparse")["jobs"][0]
    observed = {o.dimension for o in resolve_metadata("ashby", ASHBY_FIELD_MAP, posting)}

    assert posting.get("workplaceType") is None
    assert MetadataDimension.WORK_MODEL_HINT in ASHBY_FIELD_MAP.dimensions()
    assert MetadataDimension.WORK_MODEL_HINT not in observed


def test_all_three_providers_speak_the_same_four_dimensions() -> None:
    """No new MetadataDimension was needed for a third vendor. Ashby exposes no
    visa, work-authorisation, clearance, seniority, travel or timezone field in
    any of the 3105 sampled postings."""
    from career_agent.domain.enums import METADATA_DIMENSION_VOCABULARY_VERSION

    declared = {name: _any_provider(name).field_map.dimensions() for name in available_providers()}
    everything = frozenset().union(*declared.values())

    assert everything == frozenset(MetadataDimension)
    assert len(everything) == 4
    assert declared["greenhouse"] < declared["lever"]
    assert declared["ashby"] == declared["lever"]
    assert METADATA_DIMENSION_VOCABULARY_VERSION == 1
