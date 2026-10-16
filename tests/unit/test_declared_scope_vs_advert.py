"""An aggregator's generic label never outranks the advert's narrower words.

Found 2026-10-16: an aggregator's `Anywhere in the World` beside an advert
headed `Remote - North America` read VERIFIED_ELIGIBLE for a candidate in
Brazil. The employer's own narrower line makes it "to confirm": never a pass,
and alone never a refusal. Nothing else about eligibility moved; the other
four cases below pin answers that were already right.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus
from career_agent.match.engine import JobFacts, match_job

CONFIG, _ = load_search_config(committed_config_dir())


def _status(scope: str, text: str) -> tuple[EligibilityStatus, str | None]:
    result = match_job(
        CONFIG,
        JobFacts(
            title="Support Specialist",
            description=text,
            declared_hiring_scope=scope,
            location_raw=scope,
        ),
        computed_at="frozen",
    )
    geography = next(g for g in result.gates if g.gate == "geography")
    return result.eligibility_status, geography.code


def test_the_candidate_of_the_committed_configuration_is_in_brazil() -> None:
    assert CONFIG.eligibility.candidate_country == "BR"


def test_anywhere_against_remote_north_america_is_to_confirm() -> None:
    status, code = _status(
        "Anywhere in the World",
        "Remote - North America\n\nWe are hiring a support specialist (fictional).",
    )
    assert status is EligibilityStatus.UNRESOLVED
    assert code == "DESCRIPTION_LOCATION_ELSEWHERE"


@pytest.mark.parametrize(
    ("scope", "text", "expected"),
    [
        # The advert's own explicit restriction refuses, as before.
        ("Remote", "Remote, US only. You must be located in the United States.",
         EligibilityStatus.VERIFIED_NOT_ELIGIBLE),
        # A region label beside a single named country is never a pass
        # (refused or to confirm, depending on what the candidate declared).
        ("LATAM", "Remote across Latin America. Candidates must be based in Mexico.",
         None),
        # Worldwide, and the advert names Brazil as welcome.
        ("Worldwide", "Remote worldwide; we welcome candidates in Brazil.",
         EligibilityStatus.VERIFIED_ELIGIBLE),
        # Metadata and advert agree.
        ("Anywhere in the World", "Fully remote, open to candidates anywhere in the world.",
         EligibilityStatus.VERIFIED_ELIGIBLE),
    ],
)  # fmt: skip
def test_the_other_shapes_keep_their_answers(scope, text, expected) -> None:
    status = _status(scope, text)[0]
    if expected is None:
        assert status is not EligibilityStatus.VERIFIED_ELIGIBLE
    else:
        assert status is expected


def test_a_remote_line_that_includes_the_candidate_is_no_conflict() -> None:
    status, code = _status("Anywhere in the World", "Remote - Latin America\n\nSupport role.")
    assert code != "DESCRIPTION_LOCATION_ELSEWHERE"
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_remote_first_culture_is_not_a_location_line() -> None:
    status, code = _status(
        "Anywhere in the World", "We are a remote-first company.\n\nOpen to candidates anywhere."
    )
    assert code != "DESCRIPTION_LOCATION_ELSEWHERE"


def test_a_remote_first_bullet_is_not_a_location_line() -> None:
    """A bullet `- Remote-first culture, ...` once read as a place."""
    from career_agent.match.gates import _REMOTE_LINE

    newline = chr(10)
    assert _REMOTE_LINE.findall("- Remote-first culture, enabling collaboration") == []
    header = "Chief Actuary" + newline * 2 + "Remote - USA" + newline
    assert [v.strip() for v in _REMOTE_LINE.findall(header)] == ["USA"]
    assert [v.strip() for v in _REMOTE_LINE.findall("Remote: EMEA")] == ["EMEA"]
