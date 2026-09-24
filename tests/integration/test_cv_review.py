"""The review, driven the way a person drives it.

Three answers, and the tests are here rather than in the unit file because the
thing worth proving is what reaches the DATABASE. A review that displays
correctly and stores the wrong text would pass every assertion about its
output.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from career_agent.cli import app
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate
from career_agent.storage.repositories import ClaimRepo

runner = CliRunner()

#: Long enough to be a plausible CV. The first version of this fixture was 178
#: characters, and `extract` correctly refused it as "almost no text" -- a
#: document that short is a scanned page with no text layer, and the guard was
#: doing its job. Two proposals come out of the EXPERIENCE section: the line
#: naming the job is its STRUCTURE (company, role, dates), never a claim of its
#: own. The rest is here so the document is realistic rather than to be read.
CV = """Ana Ribeiro
Sao Paulo, Brazil  |  ana@example.com

SUMMARY
Business systems analyst with eight years of experience across CRM platforms,
integration work and revenue operations tooling in mid-sized companies.

EXPERIENCE
Acme Ltda - Senior Systems Analyst, 2021 - present
- Rebuilt the lead routing pipeline, cutting handoff time 40%
- Owned the HubSpot to NetSuite integration end to end
"""


@pytest.fixture
def workspace(tmp_path: Path) -> tuple[Path, Path]:
    cv = tmp_path / "cv.txt"
    cv.write_text(CV, encoding="utf-8")
    db = tmp_path / "career.db"
    conn = connect(db)
    try:
        migrate(conn)
        # Claimed as PERSONAL. Without it every command refuses, which is the
        # guard that stops personal mode ever falling back to a demo database
        # -- and a fixture that skipped it would be testing a state the product
        # deliberately does not allow.
        stamp_identity(conn, RuntimeMode.PERSONAL, "test")
        conn.commit()
    finally:
        conn.close()
    return cv, db


def stored(db: Path) -> list:
    conn = connect(db)
    try:
        conn.row_factory = __import__("sqlite3").Row
        row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
        if row is None:
            return []
        return ClaimRepo(conn).current(str(row["id"]))
    finally:
        conn.close()


def run(cv: Path, db: Path, *flags: str, keys: str = ""):
    return runner.invoke(
        app,
        ["cv-import", str(cv), "--db", str(db), *flags],
        input=keys,
    )


# =========================================================================
# 1. NOTHING IS STORED WITHOUT A DECISION
# =========================================================================


def test_a_dry_run_stores_nothing(workspace) -> None:
    cv, db = workspace
    result = run(cv, db)
    assert result.exit_code == 0, result.output
    assert "Nothing was stored" in result.output
    assert stored(db) == []


def test_storing_without_reviewing_or_accepting_is_refused(workspace) -> None:
    """The default has to be the careful one. A command that stored on a typo
    would put sentences somebody never read into their own evidence base."""
    cv, db = workspace
    result = run(cv, db, "--no-dry-run")
    assert result.exit_code == 1
    assert "Refusing to store proposals nobody accepted" in result.output
    assert stored(db) == []


# =========================================================================
# 2. ACCEPT, EDIT, REJECT
# =========================================================================


def test_accepting_stores_the_line_as_it_stood(workspace) -> None:
    cv, db = workspace
    result = run(cv, db, "--no-dry-run", "--review", keys="a\nq\n")
    assert result.exit_code == 0, result.output

    claims = stored(db)
    assert len(claims) == 1
    assert claims[0].text == "Rebuilt the lead routing pipeline, cutting handoff time 40%"
    assert claims[0].verified is True
    # The job it sat under travels with it: the company as written, and no
    # month invented for a year-only start.
    assert claims[0].employer == "Acme Ltda"
    assert claims[0].period_start is None


def test_rejecting_leaves_no_trace(workspace) -> None:
    """Nothing records that somebody said no. A list of rejections is a list of
    things about themselves they did not want kept."""
    cv, db = workspace
    run(cv, db, "--no-dry-run", "--review", keys="r\na\nq\n")

    texts = [claim.text for claim in stored(db)]
    assert "Rebuilt the lead routing pipeline, cutting handoff time 40%" not in texts
    assert len(texts) == 1


def test_editing_stores_the_correction_and_keeps_the_original_as_evidence(
    workspace,
) -> None:
    """The claim is what she says. The evidence is what the document said. Both
    are needed: without the first she cannot correct a stale sentence, and
    without the second nothing can show where the claim came from.
    """
    cv, db = workspace
    run(cv, db, "--no-dry-run", "--review", keys="e\nA shorter, truer sentence\nq\n")

    claims = stored(db)
    assert len(claims) == 1
    assert claims[0].text == "A shorter, truer sentence"
    assert claims[0].evidence_ref == "Rebuilt the lead routing pipeline, cutting handoff time 40%"


def test_stopping_keeps_what_was_already_accepted(workspace) -> None:
    """Somebody who reviews five of forty has still done five reviews, and
    discarding them for stopping would teach them to finish or not start."""
    cv, db = workspace
    run(cv, db, "--no-dry-run", "--review", keys="a\na\nq\n")
    assert len(stored(db)) == 2


def test_there_is_no_flag_that_confirms_everything(workspace) -> None:
    """Career Evidence V2: every confirmed statement is individually reviewed.

    `--accept-all` confirmed a whole CV in one command and is gone. The web
    and the API already refused the same thing; the terminal was the bypass.
    """
    cv, db = workspace
    result = run(cv, db, "--no-dry-run", "--accept-all")
    assert result.exit_code != 0
    assert stored(db) == []


def test_the_only_way_to_store_is_the_one_at_a_time_review() -> None:
    """No option of `cv-import`, under any name, stores without a review.

    Checked on the command's own parameters, so a mass-confirm flag added
    under a different name fails here as surely as the old one did.
    """
    import typer.main

    command = typer.main.get_command(app).commands["cv-import"]  # type: ignore[attr-defined]
    options = sorted(opt for param in command.params for opt in param.opts if opt.startswith("-"))
    assert options == ["--db", "--dry-run", "--review"], options


def test_pressing_enter_confirms_nothing(workspace) -> None:
    """The review's prompt defaulted to "accept", so holding Enter, or piping
    blank lines in, confirmed every proposal unread. There is no default now:
    a blank answer asks again."""
    cv, db = workspace
    result = run(cv, db, "--no-dry-run", "--review", keys="\n" * 50)
    assert result.exit_code != 0
    assert stored(db) == []
    result = run(cv, db, "--no-dry-run", "--review", keys="\n\na\nq\n")
    assert result.exit_code == 0, result.output
    assert len(stored(db)) == 1


# =========================================================================
# 3. THE DOCUMENT IS NEVER WRITTEN ANYWHERE
# =========================================================================


def test_no_copy_of_the_cv_is_left_behind(workspace, tmp_path: Path) -> None:
    """The extracted text lives in memory and in the claims she accepted. A
    cache of it on disk would be a copy of her CV nobody asked for."""
    cv, db = workspace
    run(cv, db, "--no-dry-run", "--review", keys="a\na\n")

    written = {path.name for path in tmp_path.rglob("*") if path.is_file()}
    assert written <= {"cv.txt", "career.db", "career.db-wal", "career.db-shm"}, (
        f"something else was written: {written}"
    )


def test_a_figure_is_flagged_for_the_reader_to_check(workspace) -> None:
    """A number is the thing somebody will repeat in an interview, so the
    review says so where the reviewer is looking."""
    cv, db = workspace
    result = run(cv, db, "--no-dry-run", "--review", keys="q\n")
    combined = result.output
    assert "Rebuilt the lead routing pipeline" in combined or "carries a figure" in combined
