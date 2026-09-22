"""Board discovery: five outcomes, bounded guessing, no second HTTP stack."""

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.discover import (
    BoardOutcome,
    candidate_identifiers,
    discover,
    identifier_from_url,
    normalise_domain,
    probe_board,
)


def _fetcher(handler) -> HttpFetcher:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return HttpFetcher(
        client=client, request_delay_seconds=0.0, backoff_seconds=0.0, sleep=lambda _s: None
    )


def _empty(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(404)


# --- domain normalisation: the identity anchor ------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("acme.com", "acme.com"),
        ("ACME.com", "acme.com"),
        ("www.acme.com", "acme.com"),
        ("https://www.Acme.com/careers", "acme.com"),
        ("http://acme.com:8443/x?y=1", "acme.com"),
        ("  acme.com  ", "acme.com"),
        ("careers.acme.com", "careers.acme.com"),
        ("acme.com.br", "acme.com.br"),
        ("", None),
        (None, None),
        ("   ", None),
    ],
    ids=[
        "plain",
        "case",
        "www",
        "url",
        "port-and-query",
        "whitespace",
        "subdomain-kept",
        "cctld",
        "empty",
        "none",
        "blank",
    ],
)
def test_normalise_domain(raw: str | None, expected: str | None) -> None:
    assert normalise_domain(raw) == expected


def test_a_subdomain_is_not_the_same_company() -> None:
    """Identity is exact-match on the normalised domain. `careers.acme.com` may
    well be Acme, but proving it is a review decision, not a string rule --
    and a false merge destroys the evidence needed to undo it."""
    assert normalise_domain("careers.acme.com") != normalise_domain("acme.com")


# --- candidate identifiers: bounded, deterministic, evidence-ordered --------


def test_the_domain_label_comes_first() -> None:
    """A company's domain is stronger evidence than a guess at how it writes
    its own name."""
    assert candidate_identifiers("acme.com", "Acme Health Inc")[0] == "acme"


def test_identifiers_from_a_domain_and_a_name() -> None:
    assert candidate_identifiers("acme-health.com", "Acme Health Inc") == (
        "acme-health",
        "acmehealth",
        "acme",
    )


def test_legal_suffixes_are_dropped() -> None:
    for suffix in ("Inc", "LLC", "Ltd", "GmbH", "Corp"):
        assert "acme" in candidate_identifiers(None, f"Acme {suffix}")


def test_explicitly_supplied_identifiers_are_tried_first() -> None:
    assert candidate_identifiers("acme.com", "Acme", extra=("acmejobs",))[0] == "acmejobs"


def test_the_candidate_set_stays_small() -> None:
    """Five plausible identifiers is discovery. Two hundred permutations is
    walking someone's namespace, and the difference matters more than the extra
    coverage would."""
    for domain, name in (
        ("some-very-long-company-name.com", "Some Very Long Company Name Limited"),
        ("a.co", "A"),
        ("x-y-z.io", "X Y Z Technologies GmbH"),
    ):
        assert len(candidate_identifiers(domain, name)) <= 5


def test_nothing_in_yields_nothing_out() -> None:
    assert candidate_identifiers(None, None) == ()
    assert candidate_identifiers("", "") == ()


# --- board URLs are matched without naming a vendor -------------------------


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://boards.greenhouse.io/acme", ("greenhouse", "acme")),
        ("https://jobs.lever.co/acme", ("lever", "acme")),
        ("https://jobs.ashbyhq.com/acme", ("ashby", "acme")),
        ("boards.greenhouse.io/acme", ("greenhouse", "acme")),
        ("https://jobs.lever.co/acme/0000-1111-2222", ("lever", "acme")),
        ("https://jobs.ashbyhq.com/acme?utm_source=x", ("ashby", "acme")),
        ("https://acme.com/careers", None),
        ("https://boards.greenhouse.io/", None),
        ("", None),
    ],
    ids=["gh", "lv", "ab", "no-scheme", "deep-link", "query", "own-site", "no-slug", "empty"],
)
def test_identifier_from_url(url: str, expected: tuple[str, str] | None) -> None:
    """The prefixes are derived by asking each adapter for its own board_url(),
    so discovery matches a pasted link without ever spelling a vendor host."""
    assert identifier_from_url(_fetcher(_empty), url) == expected


# --- the five outcomes ------------------------------------------------------


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (
            httpx.Response(200, json={"jobs": [{"id": 1, "title": "A", "content": "x"}]}),
            BoardOutcome.VALID_NONEMPTY,
        ),
        (httpx.Response(200, json={"jobs": []}), BoardOutcome.VALID_EMPTY),
        (httpx.Response(404), BoardOutcome.NOT_FOUND),
        (httpx.Response(200, json={"nope": True}), BoardOutcome.MALFORMED),
        (httpx.Response(500), BoardOutcome.TEMPORARY_FAILURE),
        (httpx.Response(429), BoardOutcome.TEMPORARY_FAILURE),
    ],
    ids=["nonempty", "empty", "404", "malformed", "500", "rate-limited"],
)
def test_probe_outcomes(response: httpx.Response, expected: BoardOutcome) -> None:
    fetcher = _fetcher(
        lambda _r: httpx.Response(
            response.status_code, content=response.content, headers=response.headers
        )
    )
    assert probe_board(fetcher, "greenhouse", "acme").outcome is expected


def test_a_timeout_is_temporary_not_a_missing_board() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    probe = probe_board(_fetcher(handler), "greenhouse", "acme")
    assert probe.outcome is BoardOutcome.TEMPORARY_FAILURE
    assert not probe.outcome.is_conclusive


def test_an_empty_board_is_still_a_board() -> None:
    """The same distinction the collector turns on. A board that answered with
    nothing is real and collectable; one that failed to answer says nothing."""
    assert BoardOutcome.VALID_EMPTY.is_board
    assert BoardOutcome.VALID_EMPTY.is_conclusive
    assert not BoardOutcome.NOT_FOUND.is_board
    assert BoardOutcome.NOT_FOUND.is_conclusive
    assert not BoardOutcome.TEMPORARY_FAILURE.is_board
    assert not BoardOutcome.TEMPORARY_FAILURE.is_conclusive


# --- discovery end to end ---------------------------------------------------


def _routed(live: dict[tuple[str, str], object]):
    """A transport that answers only for the (host-fragment, identifier) pairs given."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for (fragment, identifier), payload in live.items():
            if fragment in url and identifier in url:
                return httpx.Response(200, json=payload)
        return httpx.Response(404)

    return handler


def test_discovery_stops_at_the_first_board_by_default() -> None:
    """Sourcing wants one validated board, and every extra probe is a request
    someone else pays to serve."""
    handler = _routed({("lever.co", "acme"): [{"id": "a" * 36, "text": "Role"}]})
    result = discover(_fetcher(handler), domain="acme.com", name="Acme")

    assert result.best is not None
    assert (result.best.provider, result.best.board_identifier) == ("lever", "acme")
    assert result.best.outcome is BoardOutcome.VALID_NONEMPTY
    assert len(result.probes) <= 3, "it stopped rather than sweeping every provider"


def test_all_boards_finds_a_company_on_two_providers() -> None:
    """One company, two boards -- migrations, acquisitions, separate units. It
    must never become two companies."""
    handler = _routed(
        {
            ("greenhouse.io", "acme"): {"jobs": [{"id": 1, "title": "A", "content": "x"}]},
            ("ashbyhq.com", "acme"): {"apiVersion": "1", "jobs": []},
        }
    )
    result = discover(_fetcher(handler), domain="acme.com", stop_on_first=False)

    found = {(b.provider, b.outcome) for b in result.boards}
    assert found == {
        ("greenhouse", BoardOutcome.VALID_NONEMPTY),
        ("ashby", BoardOutcome.VALID_EMPTY),
    }
    assert result.best is not None
    assert result.best.provider == "greenhouse", "a live board outranks an empty one"


def test_a_supplied_board_url_is_validated_not_trusted() -> None:
    handler = _routed({("lever.co", "acmecorp"): [{"id": "b" * 36, "text": "Role"}]})
    result = discover(_fetcher(handler), board_url="https://jobs.lever.co/acmecorp")

    assert result.best is not None
    assert result.best.board_identifier == "acmecorp"
    assert result.best.discovery_method == "board_url", "stronger evidence than a guess"


def test_a_board_url_that_does_not_answer_falls_through_to_guessing() -> None:
    handler = _routed({("greenhouse.io", "acme"): {"jobs": []}})
    result = discover(
        _fetcher(handler), domain="acme.com", board_url="https://jobs.lever.co/wrong-slug"
    )

    assert result.best is not None
    assert result.best.provider == "greenhouse"
    assert result.best.discovery_method == "domain_slug"


def test_nothing_found_is_reported_as_nothing_found() -> None:
    result = discover(_fetcher(_empty), domain="acme.com", name="Acme")
    assert result.boards == []
    assert result.best is None
    assert not result.had_temporary_failure


def test_an_inconclusive_probe_is_not_proof_of_absence() -> None:
    """ "We could not reach it" and "it does not exist" are different answers,
    and recording the first as the second would drop a real company."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    result = discover(_fetcher(handler), domain="acme.com")

    assert result.boards == []
    assert result.had_temporary_failure


# -- reading a sourcing list -------------------------------------------------
#
# Added V1.7, when board discovery became a command. All 234 companies in the
# registry carry `discovery_source: curated_technology_sourcing`, which is not
# a fault in any single entry and is a fault in the set -- and growing it was
# an afternoon of manual probing, which is why it had not happened.


def test_a_sourcing_line_is_read_as_a_name_and_a_domain() -> None:
    from career_agent.pipeline.discover import read_sourcing_list

    queries = read_sourcing_list("Warby Parker | warbyparker.com\n")
    assert len(queries) == 1
    assert queries[0].name == "Warby Parker"
    assert queries[0].domain == "warbyparker.com"
    assert queries[0].board_url is None


def test_a_line_with_a_path_is_a_board_url_and_not_a_domain() -> None:
    """The strongest evidence discovery can be handed, and the easiest to throw
    away. `boards.greenhouse.io/acme` names a BOARD somebody published;
    `acme.com` names a company we would then have to guess an identifier for.
    Reading the first as the second discards a validated answer."""
    from career_agent.pipeline.discover import read_sourcing_list

    queries = read_sourcing_list("boards.greenhouse.io/acme\n")
    assert queries[0].board_url == "boards.greenhouse.io/acme"
    assert queries[0].domain is None


def test_comments_and_blank_lines_are_not_companies() -> None:
    """The list is grouped by WHY each group is there, and those headings must
    not become ninety failed probes."""
    from career_agent.pipeline.discover import read_sourcing_list

    text = "# -- fashion and retail ----\n\n   \nGlossier | glossier.com\n"
    assert len(read_sourcing_list(text)) == 1


def test_a_bare_domain_needs_no_name() -> None:
    from career_agent.pipeline.discover import read_sourcing_list

    queries = read_sourcing_list("chobani.com\n")
    assert queries[0].domain == "chobani.com"
    assert queries[0].name is None
    assert queries[0].label == "chobani.com"


def test_a_name_with_no_domain_is_still_a_query() -> None:
    """Weak evidence is still evidence: `candidate_identifiers` derives
    plausible identifiers from a name alone, which is the entire reason `name`
    is a parameter."""
    from career_agent.pipeline.discover import read_sourcing_list

    queries = read_sourcing_list("Teach For America |\n")
    assert queries[0].name == "Teach For America"
    assert queries[0].domain is None


def test_public_sourcing_template_does_not_ship_a_personal_watchlist() -> None:
    """Public installations start with an empty candidate-company list."""
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / "config" / "company_candidates.yaml"
    assert yaml.safe_load(path.read_text(encoding="utf-8")) == {"candidates": []}
