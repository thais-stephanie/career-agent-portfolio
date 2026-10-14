"""Eligibility: the posting's statements are read once, each profile decides.

What a posting SAYS about who may take it (a scope clause, an exclusion, a
residence, authorization, citizenship or sponsorship requirement, a
clearance, an office it must be attended at) is the same for everybody and
is read once (`gates.read_eligibility_facts`, ADR-0037). Where the person
lives, what they are authorized for and whether they need a visa are theirs,
and only the decision reads them. Nothing about a person is ever written into
a posting's facts.

The decision from shared facts must be exactly the decision production makes
reading the posting for each profile by itself (readers-12): asserted for
every posting and profile pair below.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.config.setup import Answers, apply_answers
from career_agent.domain.enums import EligibilityStatus
from career_agent.match.engine import JobFacts, match_job, read_shared
from career_agent.matching_v2 import features as feat

ROOT = Path(__file__).resolve().parents[2]


def wizard(country: str, **eligibility: object) -> SearchConfig:
    starter = yaml.safe_load((ROOT / "config" / "search.starter.yaml").read_text("utf-8"))
    answers = Answers(role_examples=("customer support",), residence_country=country)
    mapping, _ = apply_answers(starter, answers)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "search.local.yaml").write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        config, _ = load_search_config(Path(tmp))
    if eligibility:
        config = config.model_copy(
            update={"eligibility": config.eligibility.model_copy(update=eligibility)}
        )
    return config


PROFILES = {
    "brazil": wizard("BR"),
    "colombia": wizard("CO"),
    "united_states": wizard("US"),
    "portugal": wizard("PT"),
    "brazil_authorized_us": wizard("BR", authorized_countries=["BR", "US"]),
    "brazil_needs_us_visa": wizard("BR", authorized_countries=["BR"], sponsorship_needed=["US"]),
    "brazil_not_authorized_us": wizard("BR", not_authorized_countries=["US"]),
}

#: Invented postings, one per situation the owner listed (2026-10-14).
POSTINGS = {
    "silence": JobFacts(
        title="Support Specialist", job_id="p-silence",
        description="Responsibilities\nAnswer customer tickets by chat and email.\n",
    ),
    "worldwide": JobFacts(
        title="Support Specialist", job_id="p-world",
        description="We hire globally. This role is fully remote worldwide.\n"
        "Responsibilities\nAnswer customer tickets.\n",
    ),
    "latam": JobFacts(
        title="Support Specialist", job_id="p-latam",
        description="This role is remote and open to candidates based in Latin America.\n",
    ),
    "explicit_list": JobFacts(
        title="Support Specialist", job_id="p-list",
        description="Open to candidates based in Argentina, Colombia or Mexico.\n",
    ),
    "exclusion": JobFacts(
        title="Support Specialist", job_id="p-excl",
        description="Remote across Latin America, except Brazil.\n",
    ),
    "residence_us": JobFacts(
        title="Support Specialist", job_id="p-res",
        description="You must be authorized to work in the United States.\n"
        "We are unable to sponsor visas.\n",
    ),
    "citizenship": JobFacts(
        title="Analyst", job_id="p-cit",
        description="U.S. citizenship is required for this position.\n",
    ),
    "sponsorship_onsite": JobFacts(
        title="Analyst", job_id="p-spon", workplace_type="ONSITE", location_raw="Austin, TX",
        description="We do not sponsor visas. This role is based in our Austin office.\n",
    ),
    "clearance": JobFacts(
        title="Engineer", job_id="p-clear",
        description="This role may require a security clearance.\n",
    ),
    # Mattermost-like: a remote role whose board publishes a U.S. location,
    # with team prose about working across regions.
    "mattermost_like": JobFacts(
        title="Senior Engineer", job_id="p-mm", workplace_type="REMOTE",
        location_raw="Remote - United States",
        description="We work remotely across the US, Europe, LatAm and beyond.\n"
        "Responsibilities\nBuild features.\n",
    ),
    # ElevenLabs-like: a remote role published in one Latin American country,
    # with a global-hiring sentence elsewhere in the advert.
    "elevenlabs_like": JobFacts(
        title="Solutions Engineer", job_id="p-el", workplace_type="REMOTE",
        location_raw="Colombia",
        description="We hire globally and work remotely from anywhere in the world.\n",
    ),
    "location_conflict": JobFacts(
        title="Coordinator", job_id="p-conf", workplace_type="REMOTE",
        location_raw="Remote - Mexico",
        description="This position is open to candidates based in Brazil.\n",
    ),
    "office_attendance": JobFacts(
        title="Coordinator", job_id="p-office", location_raw="Lisbon, Portugal",
        description="You must work from our Lisbon office three days a week.\n",
    ),
}  # fmt: skip

HEADINGS = (("responsibilities",), ("requirements",))


@pytest.fixture(scope="module")
def shared() -> dict[str, feat.JobFeatures]:
    machinery = next(iter(PROFILES.values())).negation
    return {
        key: feat.extract(job, f"h-{key}", HEADINGS, negation=machinery)
        for key, job in POSTINGS.items()
    }


def _decide(config: SearchConfig, job: JobFacts, reading) -> tuple:
    r = match_job(config, job, computed_at="t", shared=reading)
    return r.eligibility_status, tuple((g.gate, g.result, g.code, g.quote) for g in r.gates)


@pytest.mark.parametrize("posting", sorted(POSTINGS))
def test_one_reading_decides_exactly_as_each_profile_reading_it_alone(posting, shared) -> None:
    job = POSTINGS[posting]
    reading = feat.shared_from_facts(job, shared[posting])
    assert reading.eligibility is not None
    for name, config in PROFILES.items():
        alone = match_job(config, job, computed_at="t")
        expected = (
            alone.eligibility_status,
            tuple((g.gate, g.result, g.code, g.quote) for g in alone.gates),
        )
        assert _decide(config, job, reading) == expected, (posting, name)


def test_the_facts_hold_nothing_about_a_person(shared) -> None:
    for key, f in shared.items():
        text = str(f.payloads["eligibility_facts"])
        for word in ("authorized_countries", "sponsorship_needed", "candidate_country"):
            assert word not in text, key


def test_the_same_posting_answers_differently_for_different_people(shared) -> None:
    def status(posting: str, profile: str) -> EligibilityStatus:
        job = POSTINGS[posting]
        reading = feat.shared_from_facts(job, shared[posting])
        return _decide(PROFILES[profile], job, reading)[0]

    eligible = EligibilityStatus.VERIFIED_ELIGIBLE
    refused = EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    unresolved = EligibilityStatus.UNRESOLVED
    assert status("silence", "brazil") is unresolved, "absence is never permission"
    assert status("explicit_list", "brazil") is refused
    assert status("explicit_list", "colombia") is eligible
    assert status("exclusion", "brazil") is refused
    assert status("exclusion", "colombia") is not refused
    assert status("residence_us", "brazil_not_authorized_us") is refused
    assert status("residence_us", "brazil_authorized_us") is not refused
    assert status("sponsorship_onsite", "brazil_needs_us_visa") is refused
    assert status("sponsorship_onsite", "brazil_authorized_us") is not refused
    assert status("mattermost_like", "united_states") is eligible
    assert status("mattermost_like", "brazil") is unresolved
    assert status("elevenlabs_like", "brazil") is unresolved
    assert status("office_attendance", "portugal") is not refused
    assert status("office_attendance", "brazil") is refused
    assert status("worldwide", "brazil_authorized_us") is eligible


def test_a_profile_with_other_negation_lists_reads_the_posting_itself(shared) -> None:
    job = POSTINGS["exclusion"]
    config = PROFILES["brazil"]
    cues = [*config.negation.cues_en, "unless otherwise noted"]
    negation = config.negation.model_copy(update={"cues_en": cues})
    other = config.model_copy(update={"negation": negation})
    reading = feat.shared_from_facts(job, shared["exclusion"])
    alone = read_shared(job, list(HEADINGS[0]), list(HEADINGS[1]), negation)
    assert reading.eligibility is not None and alone.eligibility is not None
    assert reading.eligibility.negation != alone.eligibility.negation
    assert _decide(other, job, reading) == _decide(other, job, alone)
