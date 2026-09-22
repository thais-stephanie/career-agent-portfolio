"""Greenhouse adapter: translation only, and the import boundary that keeps it so."""

import ast
import json
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import BoardRef, JobProvider
from career_agent.providers.greenhouse import GreenhouseProvider
from career_agent.providers.registry import UnknownProviderError, get_provider

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "providers"
    / "greenhouse"
    / "board_small.json"
)
BOARD = BoardRef(company_slug="example-co", provider="greenhouse", board_identifier="exampleco")


def _provider(handler) -> GreenhouseProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(client=client, request_delay_seconds=0.0, sleep=lambda _s: None)
    return GreenhouseProvider(fetcher)


def _recorded() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


# --- parsing a real recorded board -----------------------------------------


def test_parses_a_real_recorded_board() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_recorded()))
    stubs = list(provider.list_postings(BOARD))

    assert len(stubs) == 2
    for stub in stubs:
        assert stub.external_id.isdigit()
        assert stub.title
        assert stub.url.startswith("http")
        assert stub.description_html


def test_fetch_posting_makes_no_second_request() -> None:
    """content=true already supplied the description, so completing a stub is a
    local conversion. One request per board, not one per posting."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_recorded())

    provider = _provider(handler)
    stubs = list(provider.list_postings(BOARD))
    postings = [provider.fetch_posting(BOARD, stub) for stub in stubs]

    assert calls["n"] == 1
    assert all(p.has_description for p in postings)


def test_description_is_converted_to_readable_text() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=_recorded()))
    posting = next(provider.fetch_posting(BOARD, stub) for stub in provider.list_postings(BOARD))
    assert "<p>" not in posting.description_text
    assert "&lt;" not in posting.description_text
    assert len(posting.description_text) > 200
    assert posting.description_html, "original HTML must be preserved alongside the text"


def test_payload_is_carried_through_unmodified() -> None:
    recorded = _recorded()
    provider = _provider(lambda _r: httpx.Response(200, json=recorded))
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)

    original = next(j for j in recorded["jobs"] if str(j["id"]) == stub.external_id)
    assert posting.payload == original


def test_updated_at_is_not_treated_as_a_posting_date() -> None:
    """A modification time is not a publication date. Mapping it to posted_at
    would make every edited posting look brand new -- and across 414 real
    postings the two values differ 83% of the time."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Business Systems Analyst",
                "absolute_url": "https://example.com/1",
                "updated_at": "2026-08-20T10:00:00Z",
                "first_published": "2026-06-01T09:00:00Z",
                "location": {"name": "Remote - United States"},
                "content": "<p>Text</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    assert stub.posted_at == "2026-06-01T09:00:00+00:00"
    assert stub.payload["updated_at"] == "2026-08-20T10:00:00Z", "still archived for later"


def test_missing_publication_date_is_none_not_a_substitute() -> None:
    """Greenhouse exposes first_published, so its absence on a given posting is
    genuinely unknown -- never quietly backfilled from updated_at."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Analyst",
                "absolute_url": "https://example.com/1",
                "updated_at": "2026-08-20T10:00:00Z",
                "content": "<p>Text</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert next(iter(provider.list_postings(BOARD))).posted_at is None
    assert GreenhouseProvider.capabilities.exposes_posted_date is True


def test_department_is_taken_from_the_list_response() -> None:
    """Measured against the real corpus: departments is present on 412/414
    postings in the content=true listing, contrary to the brief's assumption
    that it required the per-job detail endpoint."""
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Revenue Systems Analyst",
                "absolute_url": "https://example.com/1",
                "departments": [{"id": 9, "name": "Revenue Operations", "parent_id": None}],
                "content": "<p>Text</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    assert stub.department == "Revenue Operations"
    assert GreenhouseProvider.capabilities.exposes_department is True


def test_multiple_departments_are_joined_and_hierarchy_stays_in_the_payload() -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Analyst",
                "absolute_url": "https://example.com/1",
                "departments": [
                    {"id": 1, "name": "Engineering", "child_ids": [2], "parent_id": None},
                    {"id": 2, "name": "Platform", "child_ids": [], "parent_id": 1},
                ],
                "content": "<p>Text</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    assert stub.department == "Engineering / Platform"
    assert stub.payload["departments"][1]["parent_id"] == 1, "hierarchy preserved as provenance"


@pytest.mark.parametrize(
    "departments",
    [[], None, "Engineering", [{"id": 1}], [{"name": "  "}]],
    ids=["empty", "null", "string", "no-name", "blank-name"],
)
def test_empty_or_malformed_departments_yield_none(departments: object) -> None:
    payload = {
        "jobs": [
            {
                "id": 1,
                "title": "Analyst",
                "absolute_url": "https://example.com/1",
                "departments": departments,
                "content": "<p>Text</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert next(iter(provider.list_postings(BOARD))).department is None


def test_location_string_is_recorded_not_interpreted() -> None:
    """The adapter stores what Greenhouse said. Whether the candidate can work
    there is decided by M3 resolution, after weighing this against what the
    description itself says - and they frequently disagree."""
    payload = {
        "jobs": [
            {
                "id": 2,
                "title": "GTM Systems Analyst",
                "absolute_url": "https://example.com/2",
                "location": {"name": "Remote - United States"},
                "content": "<p>We hire anywhere in the world.</p>",
            }
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    assert stub.location_raw == "Remote - United States"
    assert GreenhouseProvider.capabilities.exposes_location_structured is False


# --- failures --------------------------------------------------------------


def test_empty_board_yields_nothing_and_does_not_raise() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"jobs": []}))
    assert list(provider.list_postings(BOARD)) == []


def test_missing_jobs_key_is_malformed() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.MALFORMED


def test_jobs_not_a_list_is_malformed() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"jobs": "nope"}))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.MALFORMED


def test_unknown_board_raises_not_found() -> None:
    provider = _provider(lambda _r: httpx.Response(404))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.NOT_FOUND


def test_one_unusable_entry_does_not_lose_the_whole_board() -> None:
    """Losing one malformed posting is better than losing a company."""
    payload = {
        "jobs": [
            {"id": 1, "title": "Good", "absolute_url": "https://x/1", "content": "<p>a</p>"},
            {"no_id": True},
            "not even an object",
            {"id": 2, "title": "", "content": "<p>b</p>"},
            {"id": 3, "title": "Also good", "absolute_url": "https://x/3", "content": "<p>c</p>"},
        ]
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stubs = list(provider.list_postings(BOARD))
    assert [s.external_id for s in stubs] == ["1", "3"]


# --- protocol and boundaries -----------------------------------------------


def test_greenhouse_satisfies_the_provider_protocol() -> None:
    provider = _provider(lambda _r: httpx.Response(200, json={"jobs": []}))
    assert isinstance(provider, JobProvider)


def test_registry_resolves_greenhouse_and_rejects_unknown() -> None:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
        request_delay_seconds=0.0,
    )
    assert isinstance(get_provider("greenhouse", fetcher), GreenhouseProvider)
    with pytest.raises(UnknownProviderError):
        # A REAL ATS WITH NO ADAPTER HERE, and it has to stay that way for this
        # assertion to mean anything. It used to be Workday, which became a
        # registered provider on 2026-09-10 -- so the test that an unknown name
        # is refused started passing a known one to `get_provider` and failed,
        # correctly, rather than quietly asserting nothing.
        get_provider("bamboohr", fetcher)


FORBIDDEN_IN_PROVIDERS = {
    "career_agent.domain.profile",
    "career_agent.domain.claims",
    "career_agent.domain.countries",
    "career_agent.pipeline",
    "career_agent.storage",
    "career_agent.config",
}

PROVIDERS_DIR = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "providers"


@pytest.mark.parametrize("module_path", sorted(PROVIDERS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_providers_do_not_import_intent_eligibility_or_scoring(module_path: Path) -> None:
    """An adapter converts shape, never meaning. If a provider ever needs career
    intent or eligibility, the design has gone wrong - and M1B's Lever adapter
    is the real test of that boundary."""
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module)

    offenders = sorted(
        name
        for name in imported
        if any(name == root or name.startswith(root + ".") for root in FORBIDDEN_IN_PROVIDERS)
    )
    assert not offenders, f"{module_path.name} imports {offenders}"
