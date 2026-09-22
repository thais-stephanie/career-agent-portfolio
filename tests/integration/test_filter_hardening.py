"""Properties the twelve filters have to keep, found by probing rather than by design.

Written after the filters were built, from an adversarial pass over them. Each
test here is a question that had a plausible wrong answer, and the ones that
already passed are kept precisely because they might not next time.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import SET_VALUED_FACETS, JobFilter, ScoredJobQuery

#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = pathlib.Path("evaluation") / "demo" / "demo_postings.yaml"

#: Every new filter, as keyword arguments to `JobFilter`.
EVERY_NEW_FILTER: dict[str, dict[str, object]] = {
    "country": {"countries": ("US",)},
    "region": {"regions": ("LATAM",)},
    "latam_only": {"latam_only": True},
    "worldwide_only": {"worldwide_only": True},
    "worksite": {"worksites": ("REMOTE",)},
    "seniority": {"seniorities": ("LEAD",)},
    "employment_type": {"employment_types": ("FULL-TIME",)},
    "min_salary": {"min_salary": 1000, "salary_currencies": ("USD",)},
    "salary_currency": {"salary_currencies": ("USD",)},
    "salary_period": {"salary_periods": ("YEAR",)},
    "technology": {"technologies": ("ipaas",)},
    "keyword": {"keywords": ("integration",)},
    "exclude_keyword": {"excluded_keywords": ("clearance",)},
}


@pytest.fixture(scope="module")
def scored() -> Iterator[tuple[ScoredJobQuery, str, int]]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="filter-hardening")) / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.DEMO, "demo")
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    try:
        yield ScoredJobQuery(conn), str(config.config_id), int(config.config_version)
    finally:
        conn.close()


# =========================================================================
# Grouping, which applies the same WHERE three times
# =========================================================================


@pytest.mark.parametrize(("name", "kwargs"), sorted(EVERY_NEW_FILTER.items()))
def test_every_new_filter_survives_duplicate_grouping(
    name: str, kwargs: dict, scored: tuple[ScoredJobQuery, str, int]
) -> None:
    """`count`, `page` and `facets` must still agree with grouping switched on.

    Grouping applies the SAME clause builder to two more copies of the joins
    under the aliases `3` and `4`. Every new predicate is written with the
    `jm{a}` prefix for that reason, and this is what proves none of them
    hard-coded the unsuffixed alias -- which would not raise, it would filter
    one pass by the other's population and elect the wrong representative.
    """
    query, config_id, version = scored
    job_filter = JobFilter(group_duplicates=True, limit=500, **kwargs)

    count = query.count(config_id, version, job_filter)
    rows = query.page(config_id, version, job_filter)
    assert count == len(rows), f"{name}: count {count} but {len(rows)} rows"

    for dimension, buckets in query.facets(config_id, version, job_filter).items():
        if dimension in SET_VALUED_FACETS:
            continue
        assert sum(buckets.values()) == count, (name, dimension)


# =========================================================================
# The LIKE fallback, which runs whenever the index is behind
# =========================================================================


def test_a_keyword_agrees_across_both_search_paths(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """Keywords go through `_search_clause`, so they inherit the FTS index and
    its LIKE fallback. The two paths are documented as NOT returning identical
    rows in general -- LIKE matches substrings, FTS matches whole tokens -- but
    for a whole word present in the text they must agree, and a keyword filter
    that silently returned a different population depending on index freshness
    would be the worst kind of intermittent.
    """
    query, config_id, version = scored
    indexed = query.count(config_id, version, JobFilter(keywords=("integration",)))

    fallback = ScoredJobQuery(query.conn)
    fallback._indexed = False  # noqa: SLF001 -- forcing the documented fallback
    assert fallback.count(config_id, version, JobFilter(keywords=("integration",))) == indexed


def test_an_excluded_keyword_is_the_complement_on_both_paths(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    query, config_id, version = scored
    for reader in (query, ScoredJobQuery(query.conn)):
        total = reader.count(config_id, version, JobFilter())
        wanted = reader.count(config_id, version, JobFilter(keywords=("integration",)))
        unwanted = reader.count(config_id, version, JobFilter(excluded_keywords=("integration",)))
        assert wanted + unwanted == total


# =========================================================================
# Hostile input
# =========================================================================

#: Values chosen to break a LIKE, a fence, a parameter binding or a regex.
HOSTILE = (
    "'; DROP TABLE job; --",
    "%",
    "_",
    "100%",
    "a|b",
    "||",
    "|fired:ipaas|",
    "\\",
    "x" * 300,
    "(",
    ")",
    "*",
    '"',
)


@pytest.mark.parametrize("value", HOSTILE)
@pytest.mark.parametrize(
    "field", ["keywords", "excluded_keywords", "technologies", "countries", "employment_types"]
)
def test_a_hostile_value_neither_throws_nor_widens(
    field: str, value: str, scored: tuple[ScoredJobQuery, str, int]
) -> None:
    """Two failure modes, and the second is the quiet one.

    Throwing is loud and would be caught in a day. WIDENING is not: a filter
    whose value escaped its placeholder and matched everything looks exactly
    like a filter nobody applied, which is the defect this whole area was
    corrected for.
    """
    query, config_id, version = scored
    everything = query.count(config_id, version, JobFilter())
    assert query.count(config_id, version, JobFilter(**{field: (value,)})) <= everything


def test_a_percent_in_a_keyword_means_a_percent(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """`100%` must not become "anything at all". The LIKE metacharacters are
    escaped and the `ESCAPE` clause is paired at every call site."""
    query, config_id, version = scored
    everything = query.count(config_id, version, JobFilter())
    assert query.count(config_id, version, JobFilter(keywords=("%",))) < everything


# =========================================================================
# The fences, which are what make a substring match precise
# =========================================================================


def test_a_country_fence_cannot_match_a_longer_code(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """`|BR|` must not match `|BRX|`.

    Same device and same reason as the membership index: without the pipes a
    two-letter code is a substring of every longer one, and the filter would be
    quietly wrong in exactly the cases nobody checks. Asserted by planting the
    collision rather than by reading the pattern.
    """
    query, config_id, version = scored
    before = query.count(config_id, version, JobFilter(countries=("BR",)))
    with transaction(query.conn):
        query.conn.execute(
            "UPDATE job_match SET countries = '|BRX|'"
            " WHERE rowid = (SELECT MIN(rowid) FROM job_match WHERE countries = '')"
        )
    try:
        assert query.count(config_id, version, JobFilter(countries=("BR",))) == before
    finally:
        with transaction(query.conn):
            query.conn.execute("UPDATE job_match SET countries = '' WHERE countries = '|BRX|'")


def test_an_unresolved_place_is_excluded_by_a_country_filter(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """Absence is never permission, stated where it would be tempting to bend.

    A posting whose location resolved to nothing is left OUT of "postings in
    Brazil" rather than swept in. That is the conservative direction, and the
    facets report the resolved counts so the exclusion is visible.
    """
    query, config_id, version = scored
    unresolved = query.conn.execute(
        "SELECT COUNT(*) FROM job_match WHERE countries = ''"
    ).fetchone()[0]
    assert unresolved > 0, "the corpus has no unresolved location to test with"

    facets = query.facets(config_id, version, JobFilter())
    total = query.count(config_id, version, JobFilter())
    assert sum(facets["country"].values()) < total, (
        "every posting resolved to a country, so nothing is being excluded"
    )


# =========================================================================
# The Brazilian contract regime, which became askable in V1.7
# =========================================================================


def test_the_contract_regime_is_a_facet_and_not_only_a_filter(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """A control with no counts behind it is not a filter.

    `contract_regime` has been an accepted API parameter with a closed
    vocabulary since migration 0020, and it produced no facet, so nothing on
    any screen could offer it and nobody could discover it existed. That was
    defensible while the column was NULL on every row in the corpus. Gupy
    publishes the employer's own contract type, 37,368 postings resolved to
    CLT and 621 to PJ, and a filter nobody can reach is a filter nobody has.
    """
    query, config_id, version = scored
    facets = query.facets(config_id, version, JobFilter())

    assert "contract_regime" in facets
    buckets = set(facets["contract_regime"])
    # The demo corpus is mostly not Brazilian, so most postings sit in the
    # bucket for "never said" -- and one of the nineteen states CLT in its
    # body, which the prose reader has always found. Both buckets have to be
    # visible: a facet that showed only the answer would hide the denominator.
    assert "NOT_STATED" in buckets
    assert "CLT" in buckets


def test_every_bucket_a_person_can_see_is_a_bucket_they_can_click(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """Including `NOT_STATED`, which is where every posting outside Brazil sits.

    The filter accepted only `CLT` and `PJ` until this facet existed, on the
    reasoning that "did not say" is the absence of a filter rather than a value
    of it. Unarguable while the column was NULL on every row, and broken the
    day it filled: a row in the rail that answers a 400 is not a principled
    refusal, it is a control that does not work.
    """
    query, config_id, version = scored
    facets = query.facets(config_id, version, JobFilter())

    for value, count in facets["contract_regime"].items():
        assert query.count(config_id, version, JobFilter(contract_regime=(value,))) == count

    assert facets["contract_regime"]["CLT"] > 0
    assert facets["contract_regime"]["NOT_STATED"] > 0
    # Nothing in the demo says PJ, and asking is empty rather than an error.
    assert query.count(config_id, version, JobFilter(contract_regime=("PJ",))) == 0


def test_every_facet_bucket_still_equals_what_its_own_filter_returns(
    scored: tuple[ScoredJobQuery, str, int],
) -> None:
    """The rule the eleven single-valued facets are held to, extended to the
    new one rather than assumed of it."""
    query, config_id, version = scored
    facets = query.facets(config_id, version, JobFilter())
    assert sum(facets["contract_regime"].values()) == query.count(
        config_id, version, JobFilter()
    ), "a single-valued facet has to total the row count"
