"""The Jooble adapter, and the two things that make it different.

**Nothing here opens a socket.** Every test runs against
`httpx.MockTransport` or the fixtures in `tests/fixtures/providers/jooble/`.
That is true of every provider test in this project; here it is also a budget.
Jooble documents a LIFETIME limit of 500 requests per key, so a suite that
spent one request per run would consume the whole allowance in a few months of
ordinary development.

**The API key is in the URL.** `POST /api/{key}` makes the request URL a
credential, and a URL is what `FetchError` records and what names a cache file.
Several tests below exist only to prove the key does not reach those places.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher, ResponseCache
from career_agent.providers.base import ProviderKind
from career_agent.providers.jooble import (
    DOMAIN_ENV,
    RESULTS_PER_PAGE,
    JoobleConfigurationError,
    JoobleProvider,
    Query,
    origin_hint,
)
from career_agent.providers.jooble_quota import (
    DOCUMENTED_LIFETIME_LIMIT,
    QuotaExhausted,
    QuotaLedger,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jooble"

#: A key-shaped string that is not a key. Used to prove redaction; it is
#: deliberately obvious so that a failure printing it is unmistakable.
FAKE_KEY = "0000-not-a-real-key-0000"
DOMAIN = "br.jooble.org"


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def provider(
    handler, *, ledger: QuotaLedger | None = None, cache: ResponseCache | None = None
) -> JoobleProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(
        client=client, request_delay_seconds=0, cache=cache, sleep=lambda _s: None
    )
    return JoobleProvider(fetcher, domain=DOMAIN, api_key=FAKE_KEY, ledger=ledger)


def answering(payload: Any, *, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return handler


# =========================================================================
# 1. THE CONTRACT
# =========================================================================


def test_the_request_is_a_post_with_keywords_and_location_in_the_body() -> None:
    seen: list[httpx.Request] = []
    page = provider(answering(fixture("search-page-documented"), seen=seen)).search(
        Query("integration engineer", "São Paulo")
    )

    assert len(seen) == 1
    request = seen[0]
    assert request.method == "POST"
    body = json.loads(request.content)
    assert body["keywords"] == "integration engineer"
    assert body["location"] == "São Paulo"
    assert body["page"] == 1
    assert body["ResultOnPage"] == RESULTS_PER_PAGE
    assert page.total_count == 137


def test_the_endpoint_is_built_from_the_configured_domain() -> None:
    seen: list[httpx.Request] = []
    provider(answering(fixture("search-empty"), seen=seen)).search(Query("k", "l"))
    assert str(seen[0].url).startswith(f"https://{DOMAIN}/api/")


def test_a_row_becomes_a_stub_with_its_upstream_identity() -> None:
    page = provider(answering(fixture("search-page-documented"))).search(Query("k", "l"))
    stubs = [s for s in (JoobleProvider.to_stub(None, row) for row in page.jobs) if s]  # type: ignore[arg-type]

    assert [s.external_id for s in stubs] == [
        "-1234567890123456789",
        "-2222222222222222222",
    ]
    first = stubs[0]
    assert first.title == "Integration Engineer"
    assert first.location_raw == "São Paulo, SP"
    assert first.url.endswith("-1234567890123456789")
    # RFC 3339, offset-aware, UTC -- the `PostingStub` contract, which spells
    # the offset out rather than using `Z`.
    assert first.posted_at == "2026-09-03T08:14:00+00:00"


def test_a_row_that_cannot_be_identified_is_dropped_rather_than_guessed() -> None:
    """The third fixture row has no id. Two runs could not tell it apart from a
    new posting, and an unidentifiable posting is a duplicate waiting to be
    created."""
    page = provider(answering(fixture("search-page-documented"))).search(Query("k", "l"))
    assert len(page.jobs) == 3
    kept = [JoobleProvider.to_stub(None, row) for row in page.jobs]  # type: ignore[arg-type]
    assert kept[2] is None


def test_the_source_field_is_a_sighting_and_not_a_resolvable_origin() -> None:
    """Jooble says WHICH board it found a posting on, by name. A name is
    provenance and cannot resolve a posting to its ATS record, which is exactly
    the difference between this and Speedrun's `apply` URL. ADR-0013."""
    page = provider(answering(fixture("search-page-documented"))).search(Query("k", "l"))
    assert origin_hint(page.jobs[0]) == "Greenhouse"
    assert origin_hint(page.jobs[2]) is None
    assert not origin_hint(page.jobs[0]).startswith("http")


def test_fetching_a_posting_makes_no_request_and_returns_the_snippet() -> None:
    """There is no per-posting endpoint in the documented contract. The search
    response is the whole record, and the shortness of `snippet` is declared by
    `full_description_in_list=False` rather than smoothed over."""
    seen: list[httpx.Request] = []
    subject = provider(answering(fixture("search-page-documented"), seen=seen))
    page = subject.search(Query("k", "l"))
    stub = subject.to_stub(page.jobs[0])
    assert stub is not None

    before = len(seen)
    raw = subject.fetch_posting(None, stub)  # type: ignore[arg-type]

    assert len(seen) == before, "fetch_posting spent a request"
    assert raw.has_description
    assert raw.description_text.startswith("You will build and maintain REST API")
    assert raw.payload == stub.payload


def test_an_empty_result_is_a_success_and_not_a_failure() -> None:
    """The distinction every collector in this project depends on: an empty
    answer must never be read as a reason to close jobs."""
    page = provider(answering(fixture("search-empty"))).search(Query("nothing", "nowhere"))
    assert page.jobs == ()
    assert page.total_count == 0
    assert page.has_more is False


def test_it_declares_itself_an_aggregator() -> None:
    assert JoobleProvider.kind is ProviderKind.AGGREGATOR
    assert JoobleProvider.capabilities.full_description_in_list is False
    assert JoobleProvider.capabilities.exposes_remote_flag is False


# =========================================================================
# 2. THE KEY MUST NOT ESCAPE
# =========================================================================


def test_a_failure_never_records_the_key() -> None:
    subject = provider(answering({"error": "no"}, status=500))
    with pytest.raises(FetchError) as error:
        subject.search(Query("k", "l"))

    rendered = json.dumps(error.value.as_dict())
    assert FAKE_KEY not in rendered
    assert "{key}" in rendered, "the recorded URL should be the redacted one"


def test_the_recorded_statistics_never_carry_the_key() -> None:
    client = httpx.Client(transport=httpx.MockTransport(answering({"x": 1}, status=500)))
    fetcher = HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None)
    subject = JoobleProvider(fetcher, domain=DOMAIN, api_key=FAKE_KEY)
    with pytest.raises(FetchError):
        subject.search(Query("k", "l"))
    assert FAKE_KEY not in json.dumps(fetcher.stats.as_dict())


def test_the_cache_key_carries_neither_the_key_nor_a_hash_of_it(tmp_path: Path) -> None:
    cache = ResponseCache(tmp_path)
    subject = provider(answering(fixture("search-empty")), cache=cache)
    subject.search(Query("k", "l"))

    names = [p.name for p in tmp_path.rglob("*.json")]
    assert names, "nothing was cached"
    assert all(FAKE_KEY not in name for name in names)
    # The key is not in the digest input either: the same query under a
    # different key must produce the same cache entry, because a cache entry is
    # about a QUESTION and not about who asked it.
    other = JoobleProvider(subject._fetcher, domain=DOMAIN, api_key="a-different-key")
    assert other._cache_key({"keywords": "k"}) == subject._cache_key({"keywords": "k"})


def test_two_domains_do_not_share_a_cache_entry() -> None:
    """The same question answered by two countries is two answers."""
    a = provider(answering(fixture("search-empty")))
    b = JoobleProvider(a._fetcher, domain="uk.jooble.org", api_key=FAKE_KEY)
    assert a._cache_key({"keywords": "k"}) != b._cache_key({"keywords": "k"})


# =========================================================================
# 3. THE COUNTRY IS CONFIGURATION, NOT A GUESS
# =========================================================================


def test_a_missing_domain_refuses_and_names_the_variable() -> None:
    """A key is bound to one country, and which one cannot be read from the key.
    Discovering it by trying would spend a request from a lifetime quota to
    learn something the owner already knows."""
    with pytest.raises(JoobleConfigurationError) as error:
        JoobleProvider(None, domain="", api_key=FAKE_KEY)  # type: ignore[arg-type]

    message = str(error.value)
    assert DOMAIN_ENV in message
    assert "bound to ONE country" in message
    assert FAKE_KEY not in message


def test_a_missing_key_refuses_without_naming_a_value() -> None:
    with pytest.raises(JoobleConfigurationError) as error:
        JoobleProvider(None, domain=DOMAIN, api_key="")  # type: ignore[arg-type]
    assert "JOOBLE_API_KEY" in str(error.value)


# =========================================================================
# 4. THE QUOTA
# =========================================================================


def test_the_ledger_refuses_before_a_request_is_sent(tmp_path: Path) -> None:
    """A quota failure learned from a 403 has already cost the thing the check
    protects."""
    ledger = QuotaLedger(tmp_path / "usage.json", limit=2)
    ledger.reserve()
    ledger.reserve()

    seen: list[httpx.Request] = []
    subject = provider(answering(fixture("search-empty"), seen=seen), ledger=ledger)

    with pytest.raises(QuotaExhausted):
        subject.search(Query("k", "l"))
    assert seen == [], "a request was sent after the quota was spent"


def test_a_run_budget_of_zero_sends_nothing(tmp_path: Path) -> None:
    ledger = QuotaLedger(tmp_path / "usage.json")
    seen: list[httpx.Request] = []
    subject = provider(answering(fixture("search-empty"), seen=seen), ledger=ledger)

    with pytest.raises(QuotaExhausted):
        subject.search(Query("k", "l"), budget=0)
    assert seen == []
    assert ledger.read().known_local_requests == 0


def test_a_cache_hit_costs_no_quota(tmp_path: Path) -> None:
    """The vendor never saw it, so counting it would overstate the spend and
    understate the budget -- in the direction that wastes the allowance."""
    cache = ResponseCache(tmp_path / "cache")
    ledger = QuotaLedger(tmp_path / "usage.json")
    seen: list[httpx.Request] = []
    subject = provider(
        answering(fixture("search-page-documented"), seen=seen), ledger=ledger, cache=cache
    )

    subject.search(Query("integration engineer", "São Paulo"))
    subject.search(Query("integration engineer", "São Paulo"))

    assert len(seen) == 1
    assert ledger.read().known_local_requests == 1


def test_the_ledger_reports_a_bound_and_never_a_balance(tmp_path: Path) -> None:
    """It counts what THIS MACHINE did. The key may have been used elsewhere, so
    the honest answer to 'how many are left' has the word `upper` in it."""
    ledger = QuotaLedger(tmp_path / "usage.json")
    for _ in range(3):
        ledger.reserve()

    usage = ledger.read()
    assert usage.known_local_requests == 3
    assert usage.documented_limit == DOCUMENTED_LIFETIME_LIMIT
    assert usage.known_remaining_upper_bound == DOCUMENTED_LIFETIME_LIMIT - 3
    assert not hasattr(usage, "remaining")
    assert "upper bound, not a balance" in usage.sentence


def test_an_absent_ledger_reads_as_zero_known_rather_than_zero_made(tmp_path: Path) -> None:
    usage = QuotaLedger(tmp_path / "nothing-here.json").read()
    assert usage.known_local_requests == 0
    assert usage.first_recorded_at is None


def test_the_ledger_holds_no_secret(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    QuotaLedger(path).reserve()
    written = path.read_text(encoding="utf-8")
    assert FAKE_KEY not in written
    assert "key" not in json.loads(written)


# =========================================================================
# 5. PAGINATION IS BOUNDED BY AN EXPLICIT COUNT
# =========================================================================


def test_walking_stops_at_the_page_limit_it_was_given(tmp_path: Path) -> None:
    """Every other paginator here reads until the source says stop. This one
    cannot: 'until it stops' against a lifetime quota is a way to spend all of
    it on one question."""
    payload = {
        "totalCount": 10_000,
        "jobs": [
            dict(fixture("search-page-documented")["jobs"][0]) for _ in range(RESULTS_PER_PAGE)
        ],
    }
    seen: list[httpx.Request] = []
    ledger = QuotaLedger(tmp_path / "usage.json")
    subject = provider(answering(payload, seen=seen), ledger=ledger)

    pages = list(subject.walk(Query("k", "l"), max_pages=3))

    assert len(pages) == 3
    assert len(seen) == 3
    assert ledger.read().known_local_requests == 3


def test_a_short_page_ends_the_walk_whatever_the_total_claims(tmp_path: Path) -> None:
    """The fixture claims 137 matches and serves three.

    Believing the claim would spend request after request against a lifetime
    quota. The discrepancy is recorded instead, as a coverage fact about this
    query rather than as an error.
    """
    seen: list[httpx.Request] = []
    subject = provider(answering(fixture("search-page-documented"), seen=seen))
    pages = list(subject.walk(Query("k", "l"), max_pages=5))

    assert len(pages) == 1
    assert len(seen) == 1
    assert pages[0].total_count == 137
    assert pages[0].truncated is True


def test_a_walk_cannot_exceed_the_budget_it_was_given(tmp_path: Path) -> None:
    payload = {
        "totalCount": 10_000,
        "jobs": [
            dict(fixture("search-page-documented")["jobs"][0]) for _ in range(RESULTS_PER_PAGE)
        ],
    }
    seen: list[httpx.Request] = []
    ledger = QuotaLedger(tmp_path / "usage.json")
    subject = provider(answering(payload, seen=seen), ledger=ledger)

    with pytest.raises(QuotaExhausted):
        list(subject.walk(Query("k", "l"), max_pages=10, budget=2))
    assert len(seen) == 2


# =========================================================================
# 6. IDEMPOTENCE
# =========================================================================


def test_the_same_answer_twice_produces_the_same_stubs() -> None:
    """Fixture replay stands in for a second live pass, because a second live
    pass costs a request from a permanent budget."""
    subject = provider(answering(fixture("search-page-documented")))
    first = [subject.to_stub(row) for row in subject.search(Query("k", "l")).jobs]
    second = [subject.to_stub(row) for row in subject.search(Query("k", "l")).jobs]
    assert first == second
