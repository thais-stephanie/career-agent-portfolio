"""A word used to FIND a job may never help it SCORE.

This is the founding complaint of the product, arriving from a new direction.
`role_family` used to pay 25 points for a title resembling a preferred one, and
ADR-0014 removed it. A query-driven source reintroduces the same hazard through
a different door: Jooble needs `keywords` on every request, so somebody types
`integration engineer` to decide WHERE TO LOOK, and if that string ever reached
the lexicon the postings it found would be rewarded for having been found.

The separation is structural rather than careful, and that is what these tests
assert. A retrieval query is an argument to a collection command; the lexicon is
a section of the search configuration. There is no file both live in, no type
that converts one into the other, and no code path from a `Query` to a signal.

Read this file as the answer to one question: **if somebody adds a retrieval
query tomorrow, can it change any score?**
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.match.engine import JobFacts, match_job
from career_agent.providers.base import RetrievalMode
from career_agent.providers.jooble import Query
from career_agent.providers.registry import available_providers, retrieval_mode

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
CONFIG, _ = load_search_config(CONFIG_DIR)


# =========================================================================
# 1. THE TWO VOCABULARIES DO NOT MEET
# =========================================================================


def test_the_search_configuration_holds_no_retrieval_queries() -> None:
    """The file that decides FIT has no field that decides WHERE TO LOOK.

    If a `queries` section ever appeared here, a person editing their search
    would be editing their collection at the same time, and the two would drift
    into each other exactly as `role_family` did.
    """
    fields = set(SearchConfig.model_fields)
    for forbidden in ("queries", "retrieval", "search_terms", "keywords"):
        assert forbidden not in fields, (
            f"`SearchConfig.{forbidden}` would put retrieval vocabulary in the "
            f"file that decides compatibility."
        )


def test_nothing_in_the_matcher_imports_a_retrieval_type() -> None:
    """`career_agent.match` is the only code that produces a score.

    It must not be able to see a `Query` at all -- not to be careful with one,
    but to have no way of reading one. The same shape of test as
    `test_local_ai_never_imports_the_hosted_ledger`, and for the same reason:
    an invariant enforced by the import graph cannot be broken by a later
    edit that looks reasonable in isolation.
    """
    offenders: list[str] = []
    for module in sorted((SRC / "match").glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                "career_agent.providers"
            ):
                offenders.append(f"{module.name} imports {node.module}")
            if isinstance(node, ast.Import):
                offenders.extend(
                    f"{module.name} imports {alias.name}"
                    for alias in node.names
                    if alias.name.startswith("career_agent.providers")
                )
    # `providers.base` is allowed for `CompensationBand`, which is a posting
    # FACT the scorer reads, not a retrieval concept. Anything else is a leak.
    real = [row for row in offenders if not row.endswith("career_agent.providers.base")]
    assert not real, real


def test_the_query_type_offers_no_conversion_to_a_signal() -> None:
    """A `Query` is two strings and a label. It has no method that produces a
    lexicon entry, a signal id, or anything the scorer consumes."""
    exported = {name for name in dir(Query) if not name.startswith("_")}
    assert exported == {"keywords", "location", "as_dict", "label"}


# =========================================================================
# 2. A FOUND POSTING SCORES ONLY WHAT ITS BODY EARNS
# =========================================================================

BODY = (
    "You will own our CRM architecture and the workflow automation on top of "
    "it, build API integration between HubSpot and internal services, and run "
    "system integration for go-to-market systems."
)


@pytest.mark.parametrize(
    "query_words",
    [
        "integration engineer",
        "business systems analyst",
        "nurse educator",
        "",
    ],
)
def test_the_query_that_found_a_posting_changes_nothing_about_its_score(
    query_words: str,
) -> None:
    """The decisive one, and it is decisive because it is boring.

    Scoring `match_job(config, job)` takes a configuration and a posting. There
    is no third argument, so there is nowhere for the query to enter. This test
    exists to make that structural fact fail loudly if a future signature ever
    grows one.
    """
    baseline = match_job(CONFIG, JobFacts(title="Analyst", description=BODY), computed_at="t")

    # The query is a real one somebody might type, and it goes nowhere: the
    # matcher's inputs are the configuration and the posting.
    query = Query(keywords=query_words, location="Brazil")
    assert query.keywords == query_words

    again = match_job(CONFIG, JobFacts(title="Analyst", description=BODY), computed_at="t")
    assert again.match_score == baseline.match_score


def test_a_posting_whose_title_repeats_the_query_earns_nothing_for_it() -> None:
    """The specific hazard: search for `integration engineer`, get back postings
    titled `Integration Engineer`, and reward them for it. ADR-0014 already
    forbids a title buying fit; this says so in the words of the source that
    makes the mistake tempting."""
    query = Query(keywords="integration engineer", location="Brazil")
    echoed = match_job(
        CONFIG, JobFacts(title=query.keywords.title(), description=BODY), computed_at="t"
    )
    unrelated = match_job(
        CONFIG, JobFacts(title="Operations Wizard", description=BODY), computed_at="t"
    )
    assert echoed.match_score == unrelated.match_score


# =========================================================================
# 3. QUERY-DRIVEN COVERAGE IS NOT MARKET COVERAGE
# =========================================================================


def test_every_registered_provider_declares_how_it_retrieves() -> None:
    for name in available_providers():
        assert retrieval_mode(name) is not None, f"{name} does not say how it retrieves"


def test_no_query_driven_provider_is_probed_for_a_company_board() -> None:
    """Discovery asks "does this employer have a board here". A query-driven
    aggregator has none, and probing for one would spend a permanently limited
    request answering a question about a thing that does not exist.

    The converse does NOT hold, and the pair of concepts is why: We Work
    Remotely is an `AGGREGATOR_FEED` rather than query-driven, and it is
    excluded too, because its feeds are addressed by CATEGORY. `retrieval_mode`
    and `addresses_boards_by_company` answer different questions.
    """
    from career_agent.providers.registry import board_providers

    query_driven = {
        name for name in available_providers() if retrieval_mode(name) is RetrievalMode.QUERY_DRIVEN
    }
    assert query_driven, "this test is not measuring anything"
    assert not (query_driven & set(board_providers()))


def test_no_aggregator_claims_to_address_boards_by_company() -> None:
    """A structural rule, adopted after one adapter inherited its way past it.

    `board_providers()` reads `addresses_boards_by_company` to decide what
    `discover` may probe. Speedrun inherited the default True, so discovery
    asked a talent-network feed "does Reformation have a board here?" -- and
    the feed did not raise. It filtered by an identifier nothing matched and
    returned an EMPTY LIST, which discovery reads as `VALID_EMPTY`: a real
    board with no openings today. Two fictional boards were reported as
    supported in a single run.

    An AGGREGATOR republishes postings that originate elsewhere and has no
    board per employer BY CONSTRUCTION. Asserting it here rather than fixing
    one adapter is what stops the next one inheriting its way back in.
    """
    import importlib
    import pkgutil

    import career_agent.providers as providers_package
    from career_agent.providers.base import ProviderKind

    # The CLASS attribute, never an instance. Constructing a provider is not
    # free -- Jooble refuses to exist without `JOOBLE_DOMAIN`, on purpose --
    # and this is a declaration rather than behaviour.
    checked = 0
    for module in pkgutil.iter_modules(providers_package.__path__):
        loaded = importlib.import_module(f"career_agent.providers.{module.name}")
        for attribute in vars(loaded).values():
            if not isinstance(attribute, type):
                continue
            if getattr(attribute, "kind", None) is not ProviderKind.AGGREGATOR:
                continue
            if attribute.__module__ != loaded.__name__:
                continue  # imported, not declared here
            checked += 1
            assert attribute.addresses_boards_by_company is False, attribute.__name__
    assert checked >= 8, "the scan stopped finding aggregators, which is the failure mode"
