"""Ashby adapter: translation only, and the three places measurement overruled
the obvious choice.

Fixtures are slices of genuine API responses. The postings in them were selected
rather than sliced off the top, so that between them they exercise every path the
field map declares -- a fixture that happened to omit one would make the coverage
test pass by testing nothing.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.providers.ashby import AshbyProvider
from career_agent.providers.base import BoardRef, JobProvider
from career_agent.providers.registry import available_providers, get_provider

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "ashby"
BOARD = BoardRef(company_slug="example-co", provider="ashby", board_identifier="exampleco")


def _provider(handler) -> AshbyProvider:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    fetcher = HttpFetcher(client=client, request_delay_seconds=0.0, sleep=lambda _s: None)
    return AshbyProvider(fetcher)


def _fixture(name: str) -> Any:
    """Recorded response bodies, deliberately Any: raw vendor JSON whose shape
    the tests assert rather than assume."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


def _serving(name: str) -> AshbyProvider:
    return _provider(lambda _r: httpx.Response(200, json=_fixture(name)))


# --- parsing a real recorded board -----------------------------------------


def test_parses_a_real_recorded_board() -> None:
    stubs = list(_serving("board_rich").list_postings(BOARD))

    assert len(stubs) == 2
    for stub in stubs:
        assert len(stub.external_id) == 36, "Ashby ids are UUIDs"
        assert stub.title
        assert stub.url.startswith("https://jobs.ashbyhq.com/")
        assert stub.description_html


def test_the_envelope_is_a_third_distinct_shape() -> None:
    """Greenhouse wraps in {"jobs": ...}, Lever returns a bare array, Ashby adds
    an apiVersion. Three vendors, three envelopes, all absorbed here."""
    body = _fixture("board_rich")
    assert sorted(body) == ["apiVersion", "jobs"]
    assert len(list(_serving("board_rich").list_postings(BOARD))) == 2


def test_the_request_always_asks_for_compensation() -> None:
    """Measured: without includeCompensation=true the `compensation` key is
    absent from every posting (0/14), and with it, present on all of them.
    Declaring exposes_compensation while not sending the flag would be false
    advertising."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_fixture("board_rich"))

    list(_provider(handler).list_postings(BOARD))

    assert len(seen) == 1
    assert seen[0].endswith("/exampleco?includeCompensation=true")


def test_fetch_posting_makes_no_second_request() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_fixture("board_rich"))

    provider = _provider(handler)
    stubs = list(provider.list_postings(BOARD))
    postings = [provider.fetch_posting(BOARD, stub) for stub in stubs]

    assert calls["n"] == 1
    assert all(p.has_description for p in postings)


def test_payload_is_carried_through_unmodified() -> None:
    recorded = _fixture("board_rich")
    provider = _provider(lambda _r: httpx.Response(200, json=recorded))
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)

    original = next(p for p in recorded["jobs"] if p["id"] == stub.external_id)
    assert posting.payload == original


# --- the description: one field, but not the obvious one -------------------


def test_the_description_comes_from_html_not_plain() -> None:
    """Measured over 3105 postings: descriptionPlain UPPERCASES every heading
    ("ABOUT ABRIDGE" where the HTML says "About Abridge") and is longer on 666
    postings, shorter on none. Taking the friendlier-sounding field would have
    upper-cased every heading in the corpus and broken any M2 evidence quote
    that spans one."""
    posting = _fixture("board_rich")["jobs"][0]
    stub = next(iter(_serving("board_rich").list_postings(BOARD)))

    assert stub.description_html == posting["descriptionHtml"]
    assert stub.description_html != posting["descriptionPlain"]
    assert posting["descriptionPlain"], "the rejected field is still archived"


def test_no_assembly_is_needed_and_that_is_measured_not_assumed() -> None:
    """Lever needed four fields concatenated. Ashby has exactly one description
    in two encodings and no other prose field anywhere in the payload."""
    posting = _fixture("board_rich")["jobs"][0]
    prose_keys = {k for k, v in posting.items() if isinstance(v, str) and len(v) > 400}

    assert prose_keys == {"descriptionHtml", "descriptionPlain"}


def test_description_is_converted_to_readable_text() -> None:
    provider = _serving("board_rich")
    posting = next(provider.fetch_posting(BOARD, s) for s in provider.list_postings(BOARD))

    assert "<p>" not in posting.description_text
    assert "&lt;" not in posting.description_text
    assert "&nbsp;" not in posting.description_text
    assert len(posting.description_text) > 500
    assert posting.description_html, "original markup preserved alongside the text"


def test_ashby_list_items_keep_their_bullet_markers() -> None:
    """Was a pinned KNOWN LIMITATION during M1C; resolved by M1C.1.

    Ashby authors list items as `<li><p>text</p></li>` on 99.5% of postings. The
    shared normaliser used to emit the marker and the block newline back to
    back, stranding "-" on its own line -- 19468 such lines, 24.2% of the stored
    Ashby corpus. M1C.1 fixed that in `domain/normalize.py` rather than in this
    adapter, because the live corpus showed the same defect at all three
    providers: a rendering bug, not a vendor quirk.

    The old assertion here was `stranded > 0`, written deliberately to fail on
    the day the fix landed. It did.
    """
    provider = _serving("board_rich")
    text = next(provider.fetch_posting(BOARD, s) for s in provider.list_postings(BOARD))
    lines = text.description_text.split("\n")

    stranded = [ln for ln in lines if ln.strip() == "-"]
    inline = [ln for ln in lines if ln.startswith("- ") and len(ln) > 2]

    assert stranded == []
    assert len(inline) > 5, "a real Ashby posting is mostly lists"
    assert "<li>" in _fixture("board_rich")["jobs"][0]["descriptionHtml"]


# --- observations recorded, never concluded from ---------------------------


def test_work_model_comes_from_workplace_type_not_is_remote() -> None:
    """The measurement that decided this: `isRemote` is true on 2153 postings --
    every Remote one (761) PLUS every Hybrid one (1392). The vendor has already
    collapsed hybrid into remote. Importing that would mean importing someone
    else's judgement as data, in the exact place the product refuses to."""
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "h" * 36,
                "title": "Analyst",
                "jobUrl": "https://jobs.ashbyhq.com/x/1",
                "department": "Ops",
                "team": "Ops",
                "location": "Anywhere",
                "workplaceType": "Hybrid",
                "isRemote": True,
                "employmentType": "FullTime",
                "publishedAt": "2026-06-01T09:00:00.000+00:00",
                "descriptionHtml": "<p>Text</p>",
            }
        ],
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))

    from career_agent.providers.ashby import ASHBY_FIELD_MAP
    from career_agent.providers.base import resolve_metadata

    observations = {
        o.source_field: o.source_value
        for o in resolve_metadata("ashby", ASHBY_FIELD_MAP, stub.payload)
    }

    assert observations["workplaceType"] == "Hybrid"
    assert "isRemote" not in observations, "the lossy derivative must not travel"
    assert stub.payload["isRemote"] is True, "but it is still archived"


def test_location_is_recorded_not_interpreted() -> None:
    stub = next(iter(_serving("board_rich").list_postings(BOARD)))

    assert stub.location_raw
    assert not hasattr(stub, "is_remote")
    assert not hasattr(stub, "eligible_countries")
    assert AshbyProvider.capabilities.exposes_location_structured is True


# --- department: the third variation on one idea ---------------------------


def test_identical_department_and_team_collapse_to_one() -> None:
    """Ashby supplies both on 3105/3105 and they are the SAME string on 54.2%.
    The rule written for Lever handles it with no special case."""
    stubs = list(_serving("board_sparse").list_postings(BOARD))
    for stub in stubs:
        categories = (stub.payload["department"], stub.payload["team"])
        assert stub.department
        if categories[0] == categories[1]:
            assert stub.department == categories[0]
        else:
            assert stub.department == " / ".join(categories)


def test_department_and_team_are_never_missing_at_ashby() -> None:
    for name in ("board_rich", "board_sparse"):
        for posting in _fixture(name)["jobs"]:
            assert posting["department"] and posting["team"]


# --- capabilities, each backed by a measurement -----------------------------


def test_capabilities_match_the_sampled_payloads() -> None:
    caps = AshbyProvider.capabilities

    assert caps.full_description_in_list is True
    assert caps.exposes_posted_date is True
    assert caps.exposes_department is True
    assert caps.exposes_compensation is True
    assert caps.exposes_location_structured is True
    assert caps.exposes_remote_flag is True
    assert caps.exposes_employment_type is True


def test_a_board_that_never_states_workplace_type_still_leaves_the_flag_true() -> None:
    """One board in the sample (53 postings) has workplaceType null on 100% of
    them. The flag asks whether the vendor has a place to put it, so a missing
    value means this employer did not say -- not that Ashby cannot express it."""
    postings = _fixture("board_sparse")["jobs"]

    assert all(p.get("workplaceType") is None for p in postings)
    assert AshbyProvider.capabilities.exposes_remote_flag is True


def test_structured_location_is_a_claim_about_shape_not_quality() -> None:
    """address.postalAddress splits locality/region/country into real fields, so
    the shape is structured. The values are not: "United States" appears 1465
    times and "USA" 609 times across the corpus. Recorded as stated; normalising
    it is M3's job."""
    posting = _fixture("board_rich")["jobs"][0]
    country = posting["address"]["postalAddress"]["addressCountry"]

    assert isinstance(country, str) and country
    assert AshbyProvider.capabilities.exposes_location_structured is True


# --- timestamps ------------------------------------------------------------


def test_published_at_loses_its_milliseconds_and_gains_the_contract() -> None:
    """Ashby is the first provider already in UTC -- but with milliseconds, so
    it is 29 characters where the contract says 25."""
    posting = _fixture("board_rich")["jobs"][0]
    stub = next(iter(_serving("board_rich").list_postings(BOARD)))

    assert len(posting["publishedAt"]) == 29
    assert stub.posted_at is not None
    assert len(stub.posted_at) == 25
    assert stub.posted_at.endswith("+00:00")
    assert datetime.fromisoformat(stub.posted_at).utcoffset().total_seconds() == 0
    assert stub.payload["publishedAt"] == posting["publishedAt"], "native form archived"


def test_all_three_providers_agree_on_the_posted_at_shape() -> None:
    ashby = next(iter(_serving("board_rich").list_postings(BOARD))).posted_at
    greenhouse = "2026-02-28T14:04:24+00:00"
    lever = "2018-02-28T06:58:48+00:00"

    for value in (ashby, greenhouse, lever):
        assert value is not None and len(value) == 25
        assert datetime.fromisoformat(value).utcoffset().total_seconds() == 0


def test_no_update_timestamp_exists_to_be_mistaken_for_one() -> None:
    """Greenhouse has updated_at and M1A had to resist mapping it. Ashby has no
    update field at all, so the temptation cannot arise."""
    for posting in _fixture("board_rich")["jobs"]:
        assert not any(k for k in posting if "updat" in k.lower())


def test_a_missing_publication_date_is_none_not_a_substitute() -> None:
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "n" * 36,
                "title": "Analyst",
                "jobUrl": "https://jobs.ashbyhq.com/x/1",
                "department": "Ops",
                "team": "Ops",
                "descriptionHtml": "<p>Text</p>",
            }
        ],
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert next(iter(provider.list_postings(BOARD))).posted_at is None
    assert AshbyProvider.capabilities.exposes_posted_date is True


# --- empty board versus failed board ---------------------------------------


def test_empty_board_yields_nothing_and_does_not_raise() -> None:
    """A real board with no openings answers 200 with an empty jobs array. That
    IS grounds for closing, and it is only distinguishable because the request
    succeeded."""
    body = _fixture("board_empty")
    assert body == {"apiVersion": "1", "jobs": []}
    assert list(_serving("board_empty").list_postings(BOARD)) == []


def test_unknown_board_raises_not_found() -> None:
    """Ashby answers an unknown board with a 404 whose body is plain text --
    less dangerous than Lever's JSON error object, but the status is what the
    fetcher acts on either way."""
    provider = _provider(lambda _r: httpx.Response(404, text="Not Found"))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.NOT_FOUND


@pytest.mark.parametrize(
    "body",
    [
        {"apiVersion": "1"},
        {"jobs": "nope", "apiVersion": "1"},
        [],
        {"ok": False, "error": "Document not found"},
        "a string",
        42,
    ],
    ids=["no-jobs-key", "jobs-not-a-list", "bare-array", "lever-error-shape", "string", "number"],
)
def test_any_unexpected_envelope_is_malformed_never_an_empty_board(body: object) -> None:
    """Relying on a vendor's error format staying inconveniently shaped is not a
    safety property. Anything that is not the expected envelope raises."""
    provider = _provider(lambda _r: httpx.Response(200, json=body))
    with pytest.raises(FetchError) as exc:
        list(provider.list_postings(BOARD))
    assert exc.value.category is FetchErrorCategory.MALFORMED


@pytest.mark.parametrize("status", [429, 500, 502, 503], ids=str)
def test_server_and_rate_limit_failures_raise(status: int) -> None:
    provider = _provider(lambda _r: httpx.Response(status))
    with pytest.raises(FetchError):
        list(provider.list_postings(BOARD))


def test_a_timeout_raises_rather_than_reporting_an_empty_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("board timed out", request=request)

    with pytest.raises(FetchError):
        list(_provider(handler).list_postings(BOARD))


def test_one_unusable_entry_does_not_lose_the_whole_board() -> None:
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "a" * 36,
                "title": "Good",
                "jobUrl": "https://jobs.ashbyhq.com/x/a",
                "department": "Ops",
                "team": "Ops",
                "descriptionHtml": "<p>a</p>",
            },
            {"no_id": True},
            "not even an object",
            {"id": "b" * 36, "title": "", "descriptionHtml": "<p>b</p>"},
            {
                "id": "c" * 36,
                "title": "Also good",
                "jobUrl": "https://jobs.ashbyhq.com/x/c",
                "department": "Ops",
                "team": "Ops",
                "descriptionHtml": "<p>c</p>",
            },
        ],
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    assert [s.external_id for s in provider.list_postings(BOARD)] == ["a" * 36, "c" * 36]


def test_a_posting_without_job_url_falls_back_to_a_constructed_one() -> None:
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "f" * 36,
                "title": "Analyst",
                "department": "Ops",
                "team": "Ops",
                "descriptionHtml": "<p>a</p>",
            }
        ],
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))
    assert stub.url == f"https://jobs.ashbyhq.com/exampleco/{'f' * 36}"


def test_unicode_survives_the_round_trip() -> None:
    payload = {
        "apiVersion": "1",
        "jobs": [
            {
                "id": "u" * 36,
                # punctuation-check: allow: employer-written fixture
                "title": "Ingénieur Sénior — São Paulo",
                "jobUrl": "https://jobs.ashbyhq.com/x/u",
                "department": "Ingénierie",
                "team": "Plateforme",
                "location": "São Paulo",
                # punctuation-check: allow: employer-written fixture
                "descriptionHtml": "<p>Salário: R$&nbsp;20.000 &amp; benefícios — remoto</p>",
            }
        ],
    }
    provider = _provider(lambda _r: httpx.Response(200, json=payload))
    stub = next(iter(provider.list_postings(BOARD)))
    text = provider.fetch_posting(BOARD, stub).description_text

    # punctuation-check: allow: employer-written fixture
    assert stub.title == "Ingénieur Sénior — São Paulo"
    assert stub.department == "Ingénierie / Plateforme"
    # punctuation-check: allow: employer-written fixture
    assert text == "Salário: R$ 20.000 & benefícios — remoto"
    assert "�" not in text


# --- pagination: there is none, and that is measured ------------------------


def test_one_request_is_the_whole_board() -> None:
    """Measured on a 751-posting board: limit, offset, page and cursor are all
    silently ignored and the complete listing comes back regardless. The
    envelope carries no cursor, no hasMore and no total -- there is nothing to
    page with, and therefore nothing to tear."""
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json=_fixture("board_rich"))

    stubs = list(_provider(handler).list_postings(BOARD))

    assert len(urls) == 1
    assert "limit" not in urls[0] and "offset" not in urls[0] and "cursor" not in urls[0]
    assert len(stubs) == 2
    assert sorted(_fixture("board_rich")) == ["apiVersion", "jobs"], "no paging keys"


# --- protocol and registry -------------------------------------------------


def test_ashby_satisfies_the_provider_protocol() -> None:
    assert isinstance(_serving("board_empty"), JobProvider)


def test_registry_resolves_ashby_without_any_other_change() -> None:
    """Third provider, same integration cost: one dict entry.

    The assertion used to pin the registry to exactly three names. That was the
    right shape while every provider was an ATS, and it stopped being right when
    V3 registered an aggregator: the claim this test makes is about ASHBY
    resolving, not about the registry never growing again.

    What is still pinned is the part that would actually break the M1C result:
    the three ATS adapters are all present, all distinct, and reachable by name.
    """
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200))),
        request_delay_seconds=0.0,
    )
    assert isinstance(get_provider("ashby", fetcher), AshbyProvider)
    assert {"ashby", "greenhouse", "lever"} <= set(available_providers())
    assert available_providers() == sorted(set(available_providers()))


def test_board_url_is_the_public_one_not_the_api_one() -> None:
    provider = _serving("board_empty")
    assert provider.board_url(BOARD) == "https://jobs.ashbyhq.com/exampleco"
    assert provider.jobs_url(BOARD) == (
        "https://api.ashbyhq.com/posting-api/job-board/exampleco?includeCompensation=true"
    )


def test_a_configured_board_url_overrides_the_default() -> None:
    board = BoardRef(
        company_slug="x",
        provider="ashby",
        board_identifier="exampleco",
        board_url="https://careers.example.com",
    )
    assert _serving("board_empty").board_url(board) == "https://careers.example.com"
