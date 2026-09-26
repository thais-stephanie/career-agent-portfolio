"""The seniority precedence and the MID fallback, scored.

1. A level the posting states (title grade or leadership word) is used.
2. A level the body genuinely determines is used.
3. Otherwise the level is MID / PLENO, and Search Fit scores it AS MID.

Posting completeness still records whether the posting stated a level: that
is information quality, and it never reduces Search Fit.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import Seniority, SenioritySource
from career_agent.match.score import _seniority_component, measurable_items
from career_agent.match.seniority import read_seniority

CONFIG, _ = load_search_config(committed_config_dir())

CASES = [
    ("Mid-level Analyst", "You will own our internal tools.", Seniority.MID, True),
    ("Senior Analyst", "You will own our internal tools.", Seniority.SENIOR, True),
    ("Junior Analyst", "You will own our internal tools.", Seniority.JUNIOR, True),
    ("Analyst", "This is a senior-level position. You will own our tools.", Seniority.SENIOR, True),
    ("Analyst", "You will own our internal tools.", Seniority.MID, False),
]


@pytest.mark.parametrize(("title", "body", "level", "stated"), CASES)
def test_the_precedence_reads_the_level_or_falls_back_to_mid(
    title: str, body: str, level: Seniority, stated: bool
) -> None:
    reading = read_seniority(title, body)
    assert reading.value is level
    assert reading.is_evidence is stated
    if not stated:
        assert reading.source is SenioritySource.DEFAULT


def _with_preferences(preferred: list[Seniority], excluded: list[Seniority] | None = None):
    seniority = CONFIG.preferences.seniority.model_copy(
        update={"preferred": preferred, "excluded": excluded or []}
    )
    preferences = CONFIG.preferences.model_copy(update={"seniority": seniority})
    return CONFIG.model_copy(update={"preferences": preferences})


@pytest.mark.parametrize(
    ("preferred", "excluded", "expected_share"),
    [
        ([Seniority.MID], [], 1.0),
        ([Seniority.MID, Seniority.SENIOR], [], 1.0),
        ([Seniority.SENIOR], [], 0.5),
        ([Seniority.JUNIOR], [], 0.5),
        ([Seniority.STAFF], [], 0.2),
        ([Seniority.SENIOR], [Seniority.MID], 0.0),
    ],
)
def test_the_mid_fallback_is_scored_exactly_like_a_stated_mid(
    preferred: list[Seniority], excluded: list[Seniority], expected_share: float
) -> None:
    config = _with_preferences(preferred, excluded)
    unstated = _seniority_component(config, read_seniority("Analyst", "You will own tools."))
    stated = _seniority_component(
        config, read_seniority("Mid-level Analyst", "You will own tools.")
    )
    maximum = config.scoring.components.seniority.max
    assert unstated.points == stated.points == pytest.approx(maximum * expected_share)


def test_without_preferences_the_fallback_uses_the_mid_row_of_the_table() -> None:
    config = _with_preferences([])
    unstated = _seniority_component(config, read_seniority("Analyst", "You will own tools."))
    assert unstated.points == config.scoring.components.seniority.points_for(Seniority.MID)
    assert unstated.points > 0


def test_completeness_still_says_the_level_was_not_stated() -> None:
    """Information quality, kept apart: an omission lowers completeness only."""
    silent = read_seniority("Analyst", "You will own tools.")
    items = measurable_items(
        description="You will own tools.",
        location_raw="",
        employment_type="",
        salary_stated=False,
        geography_resolved=False,
        seniority=silent,
        posted_at="",
    )
    awarded, _yes, no = items["seniority_determinable"]
    assert awarded is False
    assert "mid-level" in no
