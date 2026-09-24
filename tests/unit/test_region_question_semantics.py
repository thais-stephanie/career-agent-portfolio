"""Why the setup asks about a hiring region only when it can add something.

`gates._region_verdict` admits a region that contains any confirmed country by
itself; a region answer adds evidence only through where she lives. These pin
that reading, which is what the setup's visibility rule relies on, and that
skipping the question leaves eligibility unknown rather than refused.
"""

from __future__ import annotations

from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.match.engine import JobFacts, match_job

BASE, _ = load_search_config(committed_config_dir())
BODY = "Build internal tools. We hire anywhere in Latin America."


def config_with(*, home: str, confirmed: list[str], scopes: list[str]) -> SearchConfig:
    eligibility = BASE.eligibility.model_copy(
        update={
            "candidate_country": home,
            "eligible_countries": confirmed,
            "eligible_scopes": scopes,
        }
    )
    return BASE.model_copy(update={"eligibility": eligibility})


def status(config: SearchConfig, location: str) -> str:
    job = JobFacts(title="Analyst", description=BODY, location_raw=location)
    return str(match_job(config, job, computed_at="2026-09-24T00:00:00Z").eligibility_status)


def test_a_confirmed_country_already_admits_every_region_containing_it() -> None:
    confirmed = config_with(home="BR", confirmed=["BR"], scopes=[])
    for region in ("Remote, LATAM", "Latin America", "Remote (LATAM only)"):
        assert status(confirmed, region) == "VERIFIED_ELIGIBLE", region
    # Ticking a region on top changes nothing: the question would be redundant.
    with_scope = config_with(home="BR", confirmed=["BR"], scopes=["LATAM"])
    assert status(with_scope, "Remote, LATAM") == status(confirmed, "Remote, LATAM")


def test_with_nothing_confirmed_a_region_answer_adds_evidence() -> None:
    unknown = config_with(home="BR", confirmed=[], scopes=[])
    answered = config_with(home="BR", confirmed=[], scopes=["LATAM"])
    assert status(answered, "Remote, LATAM") == "VERIFIED_ELIGIBLE"
    assert status(unknown, "Remote, LATAM") != status(answered, "Remote, LATAM")


def test_skipping_the_region_question_leaves_eligibility_unresolved() -> None:
    """Residence is not eligibility, and an unticked region is not a refusal."""
    skipped = config_with(home="BR", confirmed=[], scopes=[])
    assert status(skipped, "Remote, LATAM") == "UNRESOLVED"
