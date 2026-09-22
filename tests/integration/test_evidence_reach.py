"""Confirming a fact about yourself does not move a single recommendation.

WHY THIS FILE EXISTS
--------------------
The owner's intake package holds 309 proposals. Answering them is an hour of
somebody's evening, and the reasonable expectation afterwards is that the list
of recommended jobs looks different.

It will not. A posting is scored from what the EMPLOYER wrote about the work,
and eligibility is decided against the countries and scopes in her
PREFERENCES. `career_agent.match` reads a `VerifiedClaim` in exactly two
modules -- `preparation.py`, which answers one posting's requirements, and
`resume.py`, which orders those answers -- and neither of them scores
anything.

That is a good design and a terrible surprise. So the evidence screen says it
out loud, in three lines under the heading "Where a confirmed fact is used",
and THIS FILE IS WHAT MAKES THOSE LINES TRUE. If a scoring module ever learns
to read a claim, the assertions below fail and whoever wired it has to change
the copy in the same commit rather than leaving a screen that lies quietly.

The reverse is a defect too, and it has its own assertion: preparation must
KEEP reading claims. A screen promising "every requirement is answered from
what you have confirmed" over a preparation surface that stopped consulting
them would be the same failure pointing the other way.

THE THREE FORMS, THE SAME DISCIPLINE AS `test_interest_is_not_fit`
------------------------------------------------------------------
  STRUCTURAL    `JobFacts` is the complete list of what the matcher may know,
                and it names no fact about the candidate.
  ARCHITECTURAL no scoring module may name `VerifiedClaim` at all.
  EMPIRICAL     confirm a fact, force a rescore, and every column of every
                row is byte-identical.
"""

from __future__ import annotations

import ast
import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir, comparable_score

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
MATCH_PACKAGE = REPO_ROOT / "src" / "career_agent" / "match"

#: The two modules that legitimately read what she has confirmed, and the only
#: two. Both are the PREPARATION surface: they answer one posting's stated
#: requirements against her evidence and order the answers. Neither produces a
#: score, a band, a confidence or an eligibility verdict.
MAY_READ_A_CLAIM = frozenset({"preparation.py", "resume.py"})

SCORE_COLUMNS = (
    "match_score",
    "data_confidence",
    "eligibility_status",
    "fit_band",
    "screening_state",
    "title_class",
    "result_json",
)


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="evidence-reach")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def _every_score(conn) -> dict[str, tuple]:
    columns = ", ".join(SCORE_COLUMNS)
    rows = conn.execute(f"SELECT job_id, {columns} FROM job_match").fetchall()
    return {
        row["job_id"]: tuple(comparable_score(name, row[name]) for name in SCORE_COLUMNS)
        for row in rows
    }


# =========================================================================
# 1. STRUCTURAL: the matcher is not told anything about her
# =========================================================================


def test_the_matchers_whole_input_names_no_fact_about_the_candidate() -> None:
    """`JobFacts` is everything the deterministic matcher may know. A field it
    does not have is a fact it cannot use, however convenient that would be."""
    from career_agent.match.engine import JobFacts

    fields = set(JobFacts.__dataclass_fields__)
    forbidden = {"claims", "evidence", "candidate", "candidate_id", "verified_claims"}
    assert not (fields & forbidden), sorted(fields & forbidden)


def test_no_scoring_module_can_name_a_confirmed_fact() -> None:
    """The architectural half, and the one that would catch the regression
    while somebody was still writing it.

    Two modules are exempt by name rather than by pattern, because an
    allowance that matches a shape is an allowance that grows on its own.
    """
    offenders: list[str] = []
    for path in sorted(MATCH_PACKAGE.rglob("*.py")):
        if path.name in MAY_READ_A_CLAIM:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names |= {alias.name for alias in node.names}
        if "VerifiedClaim" in names or "verified_claim" in names:
            offenders.append(path.name)
    assert offenders == [], (
        f"{offenders} can reach a confirmed fact. The evidence screen promises "
        "that confirming something does not move the recommendations; either "
        "undo this or change the copy that says so."
    )


def test_preparation_still_reads_them() -> None:
    """The same promise pointing the other way.

    "Every requirement is answered from what you have confirmed" is the
    POSITIVE half of the block on screen, and a preparation surface that
    stopped consulting her evidence would falsify it just as thoroughly.
    """
    import inspect

    from career_agent.match import preparation

    signature = inspect.signature(preparation.prepare)
    assert "claims" in signature.parameters, (
        "prepare() stopped taking evidence, so the positive half is now false"
    )


# =========================================================================
# 2. EMPIRICAL: nothing moves
# =========================================================================


def test_confirming_a_fact_and_rescoring_leaves_every_row_byte_identical(
    api: JobsApi,
) -> None:
    """The whole corpus, not one posting.

    A defect that read her evidence would most plausibly show up as a handful
    of rows moving rather than all of them, and asserting one job would be a
    one-in-nineteen chance of noticing.
    """
    config, _ = load_search_config(CONFIG_DIR)
    conn = connect(api.config.db_path)
    try:
        rescore(conn, config, force=True)
        conn.commit()
        before = _every_score(conn)
        assert before, "nothing was scored, so this proves nothing"
    finally:
        conn.close()

    api.create_claim(
        query={},
        body={
            "text": "Built and ran integration platforms connecting CRM and billing.",
            "claim_type": "ACHIEVEMENT",
        },
    )

    conn = connect(api.config.db_path)
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM verified_claim").fetchone()["n"] == 1
        rescore(conn, config, force=True)
        conn.commit()
        after = _every_score(conn)
    finally:
        conn.close()

    moved = [job for job, row in after.items() if before.get(job) != row]
    assert moved == [], f"confirming one fact moved {len(moved)} scores"
