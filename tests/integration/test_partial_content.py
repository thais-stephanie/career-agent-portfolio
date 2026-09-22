"""An excerpt must not be read as though it were a posting.

Two four-hundred-character bodies can mean opposite things. One is a short
posting: the employer wrote little and there is nothing more to read. The
other is a `snippet` from a source whose documented contract has no detail
endpoint, so the rest cannot be fetched at any price.

`data_confidence` falls for both, correctly and identically --
`description_substantial` wants 1,200 characters and neither has them. That
measurement answers "how much did this posting tell us", which is the right
question and a different one from "is this the posting". A reader deciding
whether to open the employer's link needs the second, because one of the two
has plenty more to say somewhere else.

WHAT THIS FILE PROTECTS, IN BOTH DIRECTIONS
--------------------------------------------
That the fact is RECORDED and queryable, so a card can say so and a filter can
ask -- and that it changes NO SCORE. Discounting a compatibility number by a
provenance fact would blend two of the three measurements ADR-0004 keeps
apart, and it would do it invisibly: a posting would score lower than its text
deserves and nothing on screen would say which half of the number was about
the job.

The second half is the one that needs a test. It is the tempting mistake.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import ContentCompleteness
from career_agent.match.engine import match_job
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.facts import job_facts
from career_agent.providers.registry import (
    available_providers,
    capabilities_for,
    content_completeness_for,
)
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import FACET_COLUMN_NAMES, facet_columns
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
WHEN = "2026-09-06T00:00:00Z"

#: Short enough to fail `description_substantial` either way, and carrying
#: enough of the search's own vocabulary to score above zero. Invented here.
BODY = """
We are hiring a Business Systems Engineer. You will own CRM architecture,
build workflow automation across billing systems, and maintain REST API
integrations between them.
"""


@pytest.fixture(scope="module")
def config():
    loaded, _ = load_search_config(CONFIG_DIR)
    return loaded


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="partial-content")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def facet(result, name: str) -> object:
    return facet_columns(result)[FACET_COLUMN_NAMES.index(name)]


# =========================================================================
# 1. THE READING
# =========================================================================


def test_a_source_that_cannot_fetch_the_rest_reads_as_partial() -> None:
    """Jooble's documented contract is one search call returning a `snippet`.

    There is no detail endpoint in it, so the shortness is permanent and is a
    fact about the SOURCE. The adapter declares that, and this is the reading
    that follows.
    """
    assert capabilities_for("jooble").obtains_full_description is False
    assert content_completeness_for("jooble", has_text=True) is (
        ContentCompleteness.PARTIAL_CONTENT
    )


def test_a_second_request_is_a_cost_and_not_a_limit() -> None:
    """The distinction the second capability flag exists for.

    Speedrun's list response carries no description at all -- so
    `full_description_in_list` is False -- and the adapter then fetches one per
    posting. Reading that flag alone would have labelled every Speedrun
    posting an excerpt, which is the opposite of true.
    """
    capability = capabilities_for("speedrun")
    assert capability.full_description_in_list is False
    assert capability.obtains_full_description is True
    assert content_completeness_for("speedrun", has_text=True) is (ContentCompleteness.FULL_CONTENT)


def test_no_text_at_all_is_metadata_only_whatever_the_source_could_do() -> None:
    """A posting collected before its body arrived is a real state, and it is
    not the same as an excerpt."""
    for provider in sorted(available_providers()):
        assert content_completeness_for(provider, has_text=False) is (
            ContentCompleteness.METADATA_ONLY
        )


def test_a_provider_with_no_adapter_is_unknown_rather_than_assumed_complete() -> None:
    """`manual_import` is a person's copy and paste, and only they know whether
    they pasted all of it. Absence is never permission here either."""
    assert content_completeness_for("manual_import", has_text=True) is (ContentCompleteness.UNKNOWN)
    assert content_completeness_for(None, has_text=True) is ContentCompleteness.UNKNOWN


def test_every_adapter_has_decided(config) -> None:
    """A flag with no default is a question every adapter had to answer.

    This asserts the answers exist rather than what they are: the values are
    each adapter's own evidence, and pinning them here would make this file a
    second opinion about six vendors.
    """
    for provider in sorted(available_providers()):
        assert isinstance(capabilities_for(provider).obtains_full_description, bool)


# =========================================================================
# 2. AND IT MOVES NO NUMBER
# =========================================================================


def test_the_same_text_scores_the_same_however_it_arrived(config) -> None:
    """THE TEMPTING MISTAKE, refused.

    The identical body, once from a source that can supply a whole
    description and once from a source that cannot. Every measurement is
    compared: the score, the confidence, the eligibility verdict and the band.
    A discount applied here would be invisible on screen -- the number would
    simply be lower -- and would mean a card could not be read without knowing
    which aggregator found the job.
    """
    full = job_facts(
        job_id="full",
        title="Business Systems Engineer",
        description=BODY,
        location_raw="Remote, worldwide",
        posted_at="2026-08-30",
        provider="ashby",
        payload=None,
    )
    partial = job_facts(
        job_id="partial",
        title="Business Systems Engineer",
        description=BODY,
        location_raw="Remote, worldwide",
        posted_at="2026-08-30",
        provider="jooble",
        payload=None,
    )
    assert full.content_completeness == ContentCompleteness.FULL_CONTENT.value
    assert partial.content_completeness == ContentCompleteness.PARTIAL_CONTENT.value

    a = match_job(config, full, computed_at=WHEN)
    b = match_job(config, partial, computed_at=WHEN)

    assert a.match_score == b.match_score
    assert a.data_confidence == b.data_confidence
    assert a.eligibility_status is b.eligibility_status
    assert a.fit_band is b.fit_band
    assert a.screening_state is b.screening_state


def test_no_scorer_reads_the_completeness_at_all() -> None:
    """Made structural rather than left to the assertion above.

    A discount added tomorrow would be caught by that test only while the two
    providers happen to differ in nothing else. This asks the stronger
    question: does any scoring module mention the field by name?
    """
    import ast

    package = REPO_ROOT / "src" / "career_agent" / "match"
    offenders: list[str] = []
    for path in sorted(package.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr == "content_completeness":
                offenders.append(f"{path.name}:{node.lineno}")
    # `engine.py` declares the field and passes it into the posting facts. No
    # other module in the package may touch it, and the engine may not branch
    # on it -- which is what the value-equality test above measures.
    assert all(name.startswith("engine.py") for name in offenders), offenders


# =========================================================================
# 3. STORED, AND ASKABLE
# =========================================================================


def test_the_column_carries_the_reading(config) -> None:
    partial = job_facts(
        job_id="partial",
        title="Business Systems Engineer",
        description=BODY,
        location_raw=None,
        posted_at=None,
        provider="jooble",
        payload=None,
    )
    result = match_job(config, partial, computed_at=WHEN)
    assert facet(result, "content_completeness") == ContentCompleteness.PARTIAL_CONTENT.value


def test_the_filter_accepts_the_vocabulary_and_refuses_anything_else(api: JobsApi) -> None:
    for value in ("FULL_CONTENT", "PARTIAL_CONTENT", "METADATA_ONLY", "UNKNOWN"):
        api.handle_api("GET", "/api/jobs", {"content_completeness": [value]}, {})

    with pytest.raises(ApiError) as caught:
        api.handle_api("GET", "/api/jobs", {"content_completeness": ["THIN"]}, {})
    assert caught.value.status == 400


def test_asking_for_completeness_can_never_answer_an_eligibility_question(
    api: JobsApi,
) -> None:
    """Two different columns behind two different parameters, and it stays that
    way: a source's limitation must never read as a verdict on an employer."""
    with pytest.raises(ApiError) as caught:
        api.handle_api("GET", "/api/jobs", {"content_completeness": ["UNRESOLVED"]}, {})
    assert caught.value.status == 400


def test_the_demo_reads_as_full_because_its_sources_supply_whole_postings(
    api: JobsApi,
) -> None:
    """Every provider the demo imitates can obtain a whole description.

    So the column is uniform there, and that is the honest reading rather than
    a gap in coverage: the corpus imitates three ATS adapters and none of them
    is an aggregator returning excerpts.
    """
    payload = api.list_jobs(query={"content_completeness": ["FULL_CONTENT"]}, body={})
    everything = api.list_jobs(query={}, body={})
    assert payload["total"] == everything["total"] > 0
