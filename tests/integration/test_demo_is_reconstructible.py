"""The demo corpus demonstrates what Career Agent can know from a source.

Not what a fixture author wishes it knew. That sentence is the invariant, and
this file is what makes it enforceable:

    A demo posting's derived facts must be reconstructible solely from the raw
    source payload and metadata that would exist for the provider it imitates.

HOW IT WAS FOUND
----------------
While proving that a status change cannot move a score, the baseline moved on
its own. Measured on a fresh demo database: a forced rescore changed 18 of 19
rows, with `data_confidence` dropping by 10 or 20 on almost every one.

The cause was two builders for one thing. `demo_seed` constructed `JobFacts`
from the corpus FILE -- passing `employment_type` and a full salary block with
a period as keyword arguments -- while `rescore`, the only path the real corpus
ever takes, can read those only out of an archived provider payload through
that provider's own reader. `seed_demo` wrote no payload at all, so on a
rescore both facts vanished.

It was not merely a missing write. The corpus said `imitates_provider:
greenhouse`, and that adapter's field map has ONE mapping -- `location.name`,
no employment-type dimension -- while its `read_compensation` documents that
the period is "None, always" because Greenhouse's `currency_range` states no
interval. The corpus was asserting three facts the provider it claimed to
imitate could not publish.

WHAT CHANGED, AND WHAT DID NOT
-------------------------------
The corpus names a provider per posting and carries a payload shaped like that
provider's real response; `pipeline/facts.py` derives the facts for BOTH
callers. No scoring weight moved, no provider reader was widened, and no
posting kept a fact by fiat.

One number moved as a consequence: `demo-012`, a Greenhouse-imitating
Portuguese posting, lost 10 points of confidence because a board that does not
publish an employment type did not publish one. Its Brazilian regime is
unaffected -- `match/employment.py` still reads `Contratacao CLT` out of the
body and quotes it -- which is the distinction the change makes visible rather
than one it introduced.
"""

from __future__ import annotations

import json
import pathlib
import tempfile
from pathlib import Path

import pytest
from tests.support import SCORE_AUDIT_FIELDS, committed_config_dir, comparable_score

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import load_demo_postings, seed_demo
from career_agent.pipeline.rescore import rescore
from career_agent.providers.registry import capabilities_for, field_map_for
from career_agent.storage.db import connect, migrate

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: Every column of `job_match` that carries meaning about a posting.
#:
#: Read from the table itself rather than listed, so a column added tomorrow is
#: covered without anybody remembering to add it here. Only the two excluded
#: below are outside the assertion, and each is excluded because a rescore is
#: SUPPOSED to change it -- never because it was inconvenient.
EXCLUDED: frozenset[str] = frozenset(
    {
        # The row's own identity. A rescore writes a new row id.
        "id",
        # WHEN the score was computed. A rescore is a second computation and
        # this is the one field whose whole job is to differ.
        "computed_at",
    }
)

#: `result_json` is compared with ONE key removed, not skipped.
#:
#: The blob carries every component, every quote and every confidence item --
#: which is the substance of what a reader is shown, so excluding it would
#: leave the assertion looking thorough and checking almost nothing. It also
#: carries its own copy of `computed_at`, which is the same audit timestamp the
#: column excludes and the only key inside it that a second run must change.
#:
#: Measured before deciding: over nineteen postings, `computed_at` was the sole
#: difference in all nineteen blobs and there was no other.
RESULT_JSON_EXCLUDED: frozenset[str] = SCORE_AUDIT_FIELDS


@pytest.fixture
def seeded():
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="demo-reconstructible")) / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.commit()
    try:
        yield conn, config
    finally:
        conn.close()


def _columns(conn) -> list[str]:
    rows = conn.execute("PRAGMA table_info(job_match)").fetchall()
    names = [str(row["name"]) for row in rows]
    assert names, "job_match has no columns, so this test would assert nothing"
    return [name for name in names if name not in EXCLUDED]


def _snapshot(conn, columns: list[str]) -> dict[str, tuple[object, ...]]:
    listed = ", ".join(columns)
    rows = conn.execute(
        f"SELECT job_id, {listed} FROM job_match ORDER BY job_id, config_version"
    ).fetchall()
    return {
        f"{row['job_id']}:{row['config_version']}": tuple(
            comparable_score(name, row[name]) for name in columns
        )
        for row in rows
    }


# =========================================================================
# 1. THE INVARIANT
# =========================================================================


def test_a_forced_rescore_changes_nothing_a_reader_would_see(seeded) -> None:
    """Seed, capture, force a rescore, compare every meaningful column.

    `force=True` on purpose: without it a rescore skips rows that already
    exist for this `(config_id, config_version)` and the test would pass by
    doing nothing.
    """
    conn, config = seeded
    columns = _columns(conn)
    before = _snapshot(conn, columns)
    assert before, "the demo seeded no scores"

    rescore(conn, config, force=True)
    conn.commit()
    after = _snapshot(conn, columns)

    assert set(after) == set(before), "a rescore changed which rows exist"
    moved = {
        key: dict(zip(columns, zip(before[key], after[key], strict=True), strict=True))
        for key in before
        if before[key] != after[key]
    }
    differences = {
        key: {name: pair for name, pair in fields.items() if pair[0] != pair[1]}
        for key, fields in moved.items()
    }
    assert not differences, (
        "seeding and rescoring disagree about these postings, which means the "
        f"corpus asserts facts its provider cannot publish: {differences}"
    )


def test_the_excluded_columns_are_all_real_and_all_explained(seeded) -> None:
    """An exclusion list is only honest while every entry is deliberate.

    A name that no longer exists would silently weaken the assertion above:
    the column it once guarded would have been renamed, and the new name would
    be compared while the old one sat here looking like a decision. This
    caught two on its first run -- `scored_at` and `pipeline_run_id`, which
    were written from memory and are not columns of this table.
    """
    conn, _ = seeded
    present = {str(row["name"]) for row in conn.execute("PRAGMA table_info(job_match)")}
    stale = EXCLUDED - present
    assert not stale, f"these exclusions name columns that do not exist: {sorted(stale)}"

    sample = conn.execute("SELECT result_json FROM job_match LIMIT 1").fetchone()
    assert sample is not None, "no scored row, so the blob exclusion proves nothing"
    inside = set(json.loads(sample["result_json"]))
    missing = RESULT_JSON_EXCLUDED - inside
    assert not missing, f"these keys are not in result_json at all: {sorted(missing)}"


# =========================================================================
# 2. THE RULE THE INVARIANT RESTS ON
# =========================================================================


def test_no_posting_asserts_a_fact_its_provider_cannot_publish() -> None:
    """The corpus is checked against the ADAPTERS, not against a wish.

    A payload key is not enough on its own: a provider that maps no
    employment-type dimension will never read one however it is spelled, and a
    posting carrying `employmentType` for such a provider is a fixture author
    telling the matcher something no collection could.
    """
    from career_agent.domain.provider_values import MetadataDimension

    postings, default_provider = load_demo_postings(DEMO_FILE)
    problems: list[str] = []

    for posting in postings:
        provider = str(posting.get("imitates_provider") or default_provider)
        payload = posting.get("payload") or {}
        capability = capabilities_for(provider)
        dimensions = {mapping.dimension for mapping in field_map_for(provider).mappings}

        if "employmentType" in payload or "commitment" in str(payload.get("categories", "")):
            if MetadataDimension.EMPLOYMENT_TYPE_HINT not in dimensions:
                problems.append(
                    f"{posting['external_id']} states an employment type and "
                    f"{provider} maps no employment-type dimension"
                )
            if not capability.exposes_employment_type:
                problems.append(
                    f"{posting['external_id']} states an employment type and "
                    f"{provider} declares it does not expose one"
                )
        states_pay = "compensation" in payload or "salaryRange" in payload
        if states_pay and not capability.exposes_compensation:
            problems.append(
                f"{posting['external_id']} states compensation and "
                f"{provider} declares it does not expose any"
            )

    assert problems == [], problems


def test_every_asserted_fact_survives_the_readers(seeded) -> None:
    """And the payloads are not merely permitted -- they are READ.

    A payload the reader silently ignores would satisfy the rule above and
    demonstrate nothing: the card would say "not stated" and the invariant
    test would still pass, because both halves would agree about nothing.
    """
    conn, _ = seeded
    rows = conn.execute(
        "SELECT j.external_id, j.provider, jm.employment_type, jm.salary_currency,"
        " jm.salary_period"
        " FROM job_match jm JOIN job j ON j.id = jm.job_id ORDER BY j.external_id"
    ).fetchall()
    by_id = {str(row["external_id"]): row for row in rows}

    postings, default_provider = load_demo_postings(DEMO_FILE)
    for posting in postings:
        external_id = posting["external_id"]
        payload = posting.get("payload") or {}
        row = by_id[external_id]
        if "employmentType" in payload or "commitment" in str(payload.get("categories", "")):
            assert row["employment_type"], (
                f"{external_id} carries an employment type its provider maps, and the"
                " score records none"
            )
        if "compensation" in payload:
            assert row["salary_currency"] and row["salary_period"], (
                f"{external_id} carries a compensation tier with an interval, and the"
                " score records no currency or no period"
            )


def test_the_corpus_exercises_more_than_one_provider() -> None:
    """Several sources, because no single one can show everything.

    A posting that has to demonstrate a salary WITH a period needs an adapter
    that reads an interval; a posting demonstrating what a thin source looks
    like needs one that reads almost nothing. One provider for the whole corpus
    would force one of those two to be a fiction.
    """
    postings, default_provider = load_demo_postings(DEMO_FILE)
    used = {str(posting.get("imitates_provider") or default_provider) for posting in postings}
    assert len(used) >= 2, f"the demo imitates only {used}"
