"""Lever adapter: translation only, plus the cases that make Lever different.

Fixtures here are slices of genuine API responses, never hand-written objects.
A hand-written fixture only ever proves the adapter agrees with whatever the
author imagined the vendor does -- which is exactly the mistake M1A made when it
guessed two Greenhouse capabilities from the documentation and got both wrong.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.base import BoardRef, JobProvider
from career_agent.providers.lever import LeverProvider
from career_agent.providers.registry import available_providers, get_provider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "lever"
BOARD = BoardRef(company_slug="example-co", provider="lever", board_identifier="exampleco")


def _provider(handler) -> LeverProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(client=client, request_delay_seconds=0.0, sleep=lambda _s: None)
    return LeverProvider(fetcher)


def _fixture(name: str) -> Any:
    """Recorded response bodies. Deliberately Any: these are raw vendor JSON,
    and the tests below assert their shape rather than assuming it."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _serving(name: str) -> LeverProvider:
    return _provider(lambda _r: httpx.Response(200, json=_fixture(name)))


# --- parsing a real recorded board -----------------------------------------


def test_parses_a_real_recorded_board() -> None:
    stubs = list(_serving("board_small").list_postings(BOARD))

    assert len(stubs) == 2
    for stub in stubs:
        assert len(stub.external_id) == 36, "Lever ids are UUIDs, not integers"
        assert stub.title
        assert stub.url.startswith("https://jobs.lever.co/")
        assert stub.description_html


def test_response_is_a_bare_array_not_a_wrapper_object() -> None:
    """Greenhouse returns {"jobs": [...]}, Lever returns [...]. The adapter
    absorbs that difference and nothing outside it can tell."""
    assert isinstance(_fixture("board_small"), list)
    assert len(list(_serving("board_small").list_postings(BOARD))) == 2


def test_fetch_posting_makes_no_second_request() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_fixture("board_small"))

    provider = _provider(handler)
    stubs = list(provider.list_postings(BOARD))
    postings = [provider.fetch_posting(BOARD, stub) for stub in stubs]

    assert calls["n"] == 1
    assert all(p.has_description for p in postings)


def test_payload_is_carried_through_unmodified() -> None:
    recorded = _fixture("board_small")
    provider = _provider(lambda _r: httpx.Response(200, json=recorded))
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)

    original = next(p for p in recorded if p["id"] == stub.external_id)
    assert posting.payload == original


# --- description assembly: the one hard translation ------------------------


def test_list_sections_are_included_not_dropped() -> None:
    """`description` alone is 22%-58% of a Lever posting. What sits outside it
    is "What You'll Do" and "What We're Looking For" -- the responsibilities and
    the stack. Reading only `description` would mean reading the marketing
    paragraph and discarding the job."""
    provider = _serving("board_small")
    stub = next(iter(provider.list_postings(BOARD)))
    text = provider.fetch_posting(BOARD, stub).description_text

    assert "What You'll Do" in text
    assert "What We're Looking For" in text
    assert "quota" in text.lower(), "the bullet content itself, not just the heading"


def test_section_headings_survive_as_separate_lines() -> None:
    """Flatten the section break and "Salesforce is nice-to-have, not required"
    becomes unreadable to M2 -- which is the distinction the product turns on."""
    provider = _serving("board_small")
    stub = next(iter(provider.list_postings(BOARD)))
    lines = provider.fetch_posting(BOARD, stub).description_text.split("\n")

    assert "What You'll Do" in lines
    heading = lines.index("What You'll Do")
    assert any(line.startswith("- ") for line in lines[heading : heading + 4])


def test_assembled_html_is_exactly_description_lists_additional() -> None:
    """Pinned deliberately, because the failure mode is silent.

    `description == opening + descriptionBody`, confirmed by containment on 1417
    real postings, so appending those two as well would duplicate text -- which
    would corrupt the content hash, pay twice for the same tokens at M2, and
    hand the model the same requirement twice to weigh. Nothing about the output
    would look wrong.
    """
    posting = _fixture("board_small")[0]
    stub = next(iter(_serving("board_small").list_postings(BOARD)))

    expected = "\n".join(
        [posting["description"]]
        + [
            fragment
            for section in posting["lists"]
            for fragment in (f"<h3>{section['text']}</h3>", f"<ul>{section['content']}</ul>")
        ]
    )
    assert stub.description_html == expected
    assert posting["additional"] == "", "this fixture has no trailing block; see palantir below"


def test_additional_block_is_appended_last() -> None:
    posting = _fixture("board_team_only")[0]
    stub = next(iter(_serving("board_team_only").list_postings(BOARD)))
    html = stub.description_html or ""

    assert posting["additional"]
    assert html.endswith(posting["additional"])
    assert html.startswith(posting["description"])


def test_a_board_with_no_lists_still_yields_its_description() -> None:
    """toptal publishes no `lists` and no `additional` at all. Assembly must not
    quietly require them."""
    provider = _serving("board_description_only")
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)

    assert stub.payload["lists"] == []
    assert len(posting.description_text) > 1000
    assert "<div>" not in posting.description_text


def test_section_heading_markup_is_escaped_not_injected() -> None:
    payload = [
        {
            "id": "u" * 36,
            "text": "Analyst",
            "hostedUrl": "https://jobs.lever.co/x/1",
            "categories": {"team": "Ops"},
            "description": "<p>Intro</p>",
            "lists": [{"text": "R&D <script>", "content": "<li>a</li>"}],
        }
    ]
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    assert "<h3>R&amp;D &lt;script&gt;</h3>" in (stub.description_html or "")
    assert "R&D <script>" in provider.fetch_posting(BOARD, stub).description_text


# --- department: two levels, and the coarse one is often missing -----------


def test_department_and_team_are_joined() -> None:
    stub = next(iter(_serving("board_description_only").list_postings(BOARD)))
    assert stub.payload["categories"]["department"] == "Executive"
    assert stub.payload["categories"]["team"] == "Executive"
    assert stub.department == "Executive", "an identical repeat is dropped, not printed twice"


def test_team_alone_is_used_when_the_board_publishes_no_department() -> None:
    """palantir (308 postings) and alloy (5) publish no `department` at all.
    Mapping department -> department naively would blank the org signal on 14%
    of sampled postings, with no error anywhere."""
    stubs = list(_serving("board_team_only").list_postings(BOARD))

    assert all("department" not in s.payload["categories"] for s in stubs)
    assert [s.department for s in stubs] == ["Administrative", "Administrative"]


@pytest.mark.parametrize(
    ("categories", "expected"),
    [
        ({"department": "Engineering", "team": "Platform"}, "Engineering / Platform"),
        ({"team": "Platform"}, "Platform"),
        ({"department": "Engineering"}, "Engineering"),
        ({"department": "Ops", "team": "Ops"}, "Ops"),
        ({"department": "  ", "team": "Platform"}, "Platform"),
        ({}, None),
        ({"department": None, "team": None}, None),
    ],
    ids=["both", "team-only", "dept-only", "identical", "blank-dept", "absent", "null"],
)
def test_department_mapping_table(categories: dict, expected: str | None) -> None:
    payload = [
        {
            "id": "d" * 36,
            "text": "Analyst",
            "hostedUrl": "https://jobs.lever.co/x/1",
            "categories": categories,
            "description": "<p>Text</p>",
        }
    ]
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert next(iter(provider.list_postings(BOARD))).department == expected


# --- timestamps: two providers, two encodings, one column ------------------


def test_epoch_milliseconds_become_rfc_3339() -> None:
    stub = next(iter(_serving("board_small").list_postings(BOARD)))
    assert stub.payload["createdAt"] == 1519801128216
    assert stub.posted_at == "2018-02-28T06:58:48+00:00"


def test_both_providers_posted_at_parse_with_one_call() -> None:
    """The reason the conversion exists at all. Greenhouse emits
    `2026-02-28T09:04:24-05:00`; Lever emits an integer. If `posted_at` held
    both encodings, every later freshness calculation would be a guess."""
    lever_value = next(iter(_serving("board_small").list_postings(BOARD))).posted_at
    greenhouse_value = "2026-02-28T09:04:24-05:00"

    for value in (lever_value, greenhouse_value):
        assert value is not None
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None, "offset-aware, so the two are comparable"
    assert len(lever_value or "") == len(greenhouse_value) == 25


@pytest.mark.parametrize(
    "created_at",
    [None, "1519801128216", True, {"t": 1}, [], 10**20],
    ids=["null", "string", "bool", "object", "list", "out-of-range"],
)
def test_an_unusable_timestamp_is_none_not_a_fabricated_date(created_at: object) -> None:
    """Absence of a timestamp is a fact. Substituting "now" would make an
    unknown posting look brand new, which is the freshness equivalent of
    treating silence as permission."""
    payload = [
        {
            "id": "t" * 36,
            "text": "Analyst",
            "hostedUrl": "https://jobs.lever.co/x/1",
            "categories": {"team": "Ops"},
            "description": "<p>Text</p>",
            "createdAt": created_at,
        }
    ]
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert next(iter(provider.list_postings(BOARD))).posted_at is None


# --- observations recorded, never concluded from ---------------------------


def test_workplace_type_and_country_are_recorded_but_not_interpreted() -> None:
    """`workplaceType: "remote"` is something the employer asserted, not
    eligibility. Toptal makes the point: a posting located "Anywhere" carries
    `country: "US"`, and one located "Europe, South America" carries "BR".
    The adapter stores the claim so M3 can later contradict it."""
    stub = next(iter(_serving("board_small").list_postings(BOARD)))

    assert stub.location_raw == "Remote - United States"
    assert stub.payload["workplaceType"] == "remote"
    assert stub.payload["country"] == "US"
    assert not hasattr(stub, "is_remote"), "collection never decides remoteness"
    assert not hasattr(stub, "eligible_countries")


def test_capabilities_match_what_the_sampled_payloads_actually_contained() -> None:
    caps = LeverProvider.capabilities

    assert caps.full_description_in_list is True
    assert caps.exposes_posted_date is True
    assert caps.exposes_department is True
    assert caps.exposes_remote_flag is True
    assert caps.exposes_employment_type is True
    assert caps.exposes_location_structured is True
    assert caps.exposes_compensation is True


def test_capability_true_does_not_mean_present_on_every_posting() -> None:
    """`salaryRange` is on 3.4% of postings pooled -- and on 100% of one board.
    The flag answers "does the provider have a place to put it?", so a missing
    value reads as "this employer did not disclose" rather than as a tooling
    limitation. Setting it False would erase a real signal."""
    with_salary = _fixture("board_small")[0]
    without_salary = _fixture("board_team_only")[0]

    assert with_salary["salaryRange"]["currency"] == "USD"
    assert without_salary.get("salaryRange") is None
    assert LeverProvider.capabilities.exposes_compensation is True


# --- empty board versus failed board: the safety-critical distinction ------


def test_empty_board_yields_nothing_and_does_not_raise() -> None:
    """A real board with no openings answers HTTP 200 with `[]`. That IS grounds
    for closing, and it is only distinguishable because the request succeeded."""
    assert _fixture("board_empty") == []
    assert list(_serving("board_empty").list_postings(BOARD)) == []


def test_a_404_json_body_raises_instead_of_reading_as_an_empty_board() -> None:
    """The trap this test exists for: Lever answers an unknown board with a 404
    whose body is *valid JSON* -- `{"ok": false, "error": "Document not found"}`.
    A parser that only asked "did this parse?" would find no postings in it and
    quietly close every posting the company has."""
    body = _fixture("not_found_body")
    assert isinstance(body, dict) and body["ok"] is False

    provider = _provider(lambda _r: httpx.Response(404, json=body))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.NOT_FOUND


def test_that_same_body_served_with_a_200_is_still_malformed() -> None:
    """Belt and braces: even if a vendor ever served the error object with a
    200, it must never be mistaken for an empty board."""
    provider = _provider(lambda _r: httpx.Response(200, json=_fixture("not_found_body")))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.MALFORMED


@pytest.mark.parametrize(
    "body",
    [{"jobs": []}, {"ok": True}, "a string", 42],
    ids=["greenhouse-shape", "object", "string", "number"],
)
def test_any_non_array_response_is_malformed(body: object) -> None:
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.MALFORMED


def test_one_unusable_entry_does_not_lose_the_whole_board() -> None:
    """Losing one malformed posting is better than losing a company."""
    payload = [
        {
            "id": "a" * 36,
            "text": "Good",
            "hostedUrl": "https://jobs.lever.co/x/a",
            "categories": {"team": "Ops"},
            "description": "<p>a</p>",
        },
        {"no_id": True},
        "not even an object",
        {"id": "b" * 36, "text": "", "description": "<p>b</p>"},
        {
            "id": "c" * 36,
            "text": "Also good",
            "hostedUrl": "https://jobs.lever.co/x/c",
            "categories": {"team": "Ops"},
            "description": "<p>c</p>",
        },
    ]
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert [s.external_id for s in provider.list_postings(BOARD)] == ["a" * 36, "c" * 36]


def test_a_posting_without_hosted_url_falls_back_to_a_constructed_one() -> None:
    payload = [
        {
            "id": "f" * 36,
            "text": "Analyst",
            "categories": {"team": "Ops"},
            "description": "<p>a</p>",
        }
    ]
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))
    assert stub.url == f"https://jobs.lever.co/exampleco/{'f' * 36}"


# --- protocol and registry -------------------------------------------------


def test_lever_satisfies_the_provider_protocol() -> None:
    assert isinstance(_serving("board_empty"), JobProvider)


def test_registry_resolves_lever_without_any_other_change() -> None:
    """The whole integration cost outside providers/lever.py is one dict entry."""
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
        request_delay_seconds=0.0,
    )
    assert isinstance(get_provider("lever", fetcher), LeverProvider)
    assert "lever" in available_providers()


def test_board_url_is_the_public_one_not_the_api_one() -> None:
    provider = _serving("board_empty")
    assert provider.board_url(BOARD) == "https://jobs.lever.co/exampleco"
    assert provider.jobs_url(BOARD) == "https://api.lever.co/v0/postings/exampleco?mode=json"


def test_a_configured_board_url_overrides_the_default() -> None:
    board = BoardRef(
        company_slug="x",
        provider="lever",
        board_identifier="exampleco",
        board_url="https://careers.example.com",
    )
    assert _serving("board_empty").board_url(board) == "https://careers.example.com"
