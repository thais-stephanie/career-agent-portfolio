"""Release acceptance: a person's own work must affect their match score.

These are intentionally red while RC blocker RC-01 remains unresolved. They
specify a nonzero contribution, not new weights or a persona-specific policy.
"""

import shutil
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.config.setup import Answers, run_setup
from career_agent.match.engine import JobFacts, match_job


@pytest.mark.parametrize(
    "country,work,tool",
    [
        ("BR", "calendar management", "Excel"),
        ("BR", "customer service", "Point of sale"),
        ("US", "customer onboarding", "Zendesk"),
    ],
    ids=["admin-br", "retail-br", "success-us"],
)
def test_neutral_setup_gives_own_work_a_scoring_contribution(tmp_path, country, work, tool):
    directory = tmp_path / "config"
    source = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(source, directory, ignore=shutil.ignore_patterns("*.local.*"))
    run_setup(
        directory,
        Answers(role_examples=(work,), skills=(tool,), residence_country=country),
    )
    config, _ = load_search_config(directory)
    result = match_job(
        config,
        JobFacts(
            title="Team member",
            description=f"Responsibilities\nYou will perform {work}.\n"
            f"Requirements\nExperience with {tool} is required.",
        ),
        computed_at="2026-09-22T00:00:00Z",
    )
    assert sum(signal.fired for signal in result.signals) >= 2
    phrase_points = sum(component.points for component in result.components[:3])
    assert phrase_points > 0, (
        "RC-01: setup recognizes the candidate's work and tools but assigns no scoring weights; "
        f"{country}: {work!r}, {tool!r}; phrase contribution={phrase_points}"
    )
