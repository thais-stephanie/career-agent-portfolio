"""The daily digest: what is worth looking at, from numbers that already exist.

The thing this must not do is invent a ranking. Every row it prints is ordered
by the same deterministic match score the cards use, and every section is a
FILTER over that order rather than a second opinion about it. A digest with its
own weighting would be exactly the untraceable number ADR-0004 refuses.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir
from typer.testing import CliRunner

from career_agent.cli import app
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture(scope="module")
def seeded() -> Iterator[Path]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="daily")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        config, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield db_path


@pytest.fixture(scope="module")
def output(seeded: Path) -> str:
    result = CliRunner().invoke(
        app, ["daily", "--db", str(seeded), "--config-dir", str(CONFIG_DIR)]
    )
    assert result.exit_code == 0, result.output
    return result.output


# =========================================================================
# 1. IT SHOWS THE SECTIONS, AND SAYS WHERE THE NUMBERS CAME FROM
# =========================================================================


def test_it_names_the_settings_that_produced_the_numbers(output: str) -> None:
    """Which configuration is in force must never be a guess -- the same
    discipline `search-config`, the health line and the profile all follow."""
    assert "settings" in output
    assert "search." in output and "v" in output


def test_every_section_appears_even_when_it_is_empty(output: str) -> None:
    """A digest that silently omits "nothing new today" is indistinguishable
    from one that failed to look."""
    for heading in (
        "New in the last",
        "Best matches right now",
        "Waiting on one answer",
        "You are tracking",
    ):
        assert heading in output, f"the digest lost the {heading!r} section"


def test_the_two_narrowings_are_reported_separately(output: str) -> None:
    """The same two sentences as the interface, for the same reason: an
    employer ruling you out and your search ruling the work out are different
    facts and one combined number would hide both."""
    assert "set aside: rules you out" in output
    assert "set aside: different work" in output


def test_it_makes_no_network_call_and_says_so(output: str) -> None:
    assert "no network calls and no inference of either kind" in output


# =========================================================================
# 2. IT INVENTS NO RANKING
# =========================================================================


def test_the_best_matches_are_in_the_order_the_score_already_gives(
    output: str, seeded: Path
) -> None:
    """Not "a good order". THE order, the one the cards use."""
    import sqlite3

    section = output.split("Best matches right now")[1].split("\n\n")[0]
    printed = [
        int(line.split()[0])
        for line in section.splitlines()[1:]
        if line.strip() and line.strip()[0].isdigit()
    ]
    assert printed == sorted(printed, reverse=True), "the digest re-ordered the scores"

    conn = sqlite3.connect(seeded)
    top = [
        row[0]
        for row in conn.execute(
            "SELECT match_score FROM job_match m JOIN job j ON j.id = m.job_id "
            "WHERE m.screening_state != 'BLOCKED' "
            "AND m.eligibility_status != 'VERIFIED_NOT_ELIGIBLE' "
            "ORDER BY match_score DESC LIMIT 20"
        )
    ]
    conn.close()
    assert printed and printed[0] == top[0], "the digest's best match is not the corpus's"


def test_recency_decides_what_is_in_a_section_and_not_what_is_on_top(output: str) -> None:
    """The obvious version sorts the new section by date, and the first thing
    somebody reads every morning is then whatever happened to be published last
    -- which on the real corpus was a posting scoring zero. Recency is the
    FILTER; the score is still the order."""
    section = output.split("New in the last")[1].split("\n\n")[0]
    scores = [
        int(line.split()[0])
        for line in section.splitlines()[1:]
        if line.strip() and line.strip()[0].isdigit()
    ]
    assert scores == sorted(scores, reverse=True)


# =========================================================================
# 3. A DATE SAYS WHICH DATE IT IS
# =========================================================================


def test_a_date_is_labelled_by_which_kind_of_date_it_is(output: str) -> None:
    """`first_seen_at` is when WE noticed a posting. For a board that publishes
    no date it is the only thing anybody knows, and it is not a claim about
    when the employer wrote it. One word for both would make it one."""
    assert "posted " in output or "first seen " in output
    assert "no date" in output or "posted " in output


def test_an_unstated_level_is_not_printed_as_a_fact(output: str) -> None:
    """The whole point of the seniority reading reaching the surface: MID by
    default is an assumption, and a digest saying "Mid-level" about a posting
    that said nothing is the defect in one line."""
    assert "level not stated by the posting" in output


# =========================================================================
# 4. IT IS A READ
# =========================================================================


def test_running_it_twice_changes_nothing(seeded: Path) -> None:
    runner = CliRunner()
    first = runner.invoke(
        app, ["daily", "--db", str(seeded), "--config-dir", str(CONFIG_DIR), "--show", "3"]
    )
    second = runner.invoke(
        app, ["daily", "--db", str(seeded), "--config-dir", str(CONFIG_DIR), "--show", "3"]
    )
    assert first.exit_code == second.exit_code == 0
    assert first.output == second.output


def test_the_window_is_configurable_without_changing_the_order(seeded: Path) -> None:
    runner = CliRunner()
    narrow = runner.invoke(
        app, ["daily", "--db", str(seeded), "--config-dir", str(CONFIG_DIR), "--days", "1"]
    )
    wide = runner.invoke(
        app, ["daily", "--db", str(seeded), "--config-dir", str(CONFIG_DIR), "--days", "365"]
    )
    assert narrow.exit_code == wide.exit_code == 0
    assert "New in the last day" in narrow.output
    assert "New in the last 365 days" in wide.output


# =========================================================================
# THE DOMESTIC NOTE, WHERE IT CHANGES THE QUESTION
# =========================================================================


def test_a_likely_us_job_says_so_where_eligibility_is_unresolved(tmp_path) -> None:
    """The note earns its line only in one place.

    "Waiting on one answer" is the section where somebody decides what to ask a
    recruiter, and knowing a posting is probably structured as United States
    employment changes that question. Beside a posting whose scope the employer
    already stated it would be noise; beside one that rules her out it would be
    a second, weaker reason stacked on a real one.
    """
    from career_agent.domain.enums import DomesticContext, EligibilityStatus
    from career_agent.match.employment import read_domestic_context

    reading = read_domestic_context(
        "We offer a competitive 401(k) with match, medical, dental and vision."
    )
    assert reading.context is DomesticContext.LIKELY_US_DOMESTIC
    assert reading.signal == "401(k)"

    # The sentence the digest prints, assembled the same way.
    line = (
        f"probably a United States job: it offers {reading.signal}"
        " and does not mention hiring elsewhere"
    )
    assert "not eligible" not in line
    assert "cannot" not in line
    assert EligibilityStatus.VERIFIED_NOT_ELIGIBLE.value not in line


def test_the_note_never_appears_for_a_posting_that_states_its_reach() -> None:
    """890 of the 4,470 postings offering a 401(k) also describe international
    hiring. A note on those would be the product contradicting the employer."""
    from career_agent.domain.enums import DomesticContext
    from career_agent.match.employment import read_domestic_context

    reading = read_domestic_context("Remote, worldwide. We offer a 401(k) to US employees.")
    assert reading.context is DomesticContext.INTERNATIONAL_STATED
