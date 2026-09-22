"""The ownership matrix, held to the code it describes.

`config/ownership.py` is a table of who owns which fact. A table like that is
worth exactly as much as the tests that stop it becoming fiction, and there are
three ways it could:

  * it says a fact is editable and no writer accepts it;
  * a writer accepts a field the table has never heard of, so a control could
    exist that no ownership decision was ever made about;
  * a MACHINERY field becomes editable from a settings panel, which is the
    hazard the writer's whitelist exists for.

All three are asserted below, against the real tables rather than against a
copy of them.
"""

from __future__ import annotations

from career_agent.config.candidate_writer import FIELDS
from career_agent.config.ownership import FACTS, Home, Owner, candidate_facts, displaced, summary


def test_every_editable_fact_is_a_field_the_writer_accepts() -> None:
    """A table that promises an edit no writer performs is worse than silence:
    it is the screen telling somebody a control exists."""
    for fact in FACTS:
        if fact.field is None:
            continue
        assert fact.field in FIELDS, f"{fact.subject!r} names a field the writer does not have"


def test_every_field_the_writer_accepts_appears_in_the_matrix() -> None:
    """The other direction, and the one that matters more. A field added to the
    writer and not to this table is a control whose ownership nobody decided --
    which is how machinery ends up editable from a settings panel."""
    described = {fact.field for fact in FACTS if fact.field is not None}
    missing = sorted(set(FIELDS) - described)
    assert not missing, f"the writer accepts fields no ownership decision covers: {missing}"


def test_nothing_the_machinery_owns_is_editable_from_a_screen() -> None:
    """`scoring.components`, `thresholds` and the taxonomy decide how every
    posting is read. Changing one says nothing about the person."""
    for fact in FACTS:
        if fact.owner is Owner.MACHINERY:
            assert fact.field is None, f"{fact.subject!r} is machinery and is editable"


def test_a_fact_that_is_not_editable_says_why() -> None:
    """A read-only row with no reason reads as an oversight, and a person
    cannot tell an oversight from a decision."""
    for fact in FACTS:
        if fact.field is None:
            assert fact.reason, f"{fact.subject!r} is read-only and does not say why"


def test_the_candidate_owns_more_than_the_machinery_does() -> None:
    """Not an arbitrary ratio: it is the shape of the product. If the machinery
    ever owned most of what this program knows, it would have stopped being a
    tool for one person's search."""
    counts = summary()
    assert counts["candidate_facts"] > counts["machinery_facts"]


def test_the_migration_is_counted_rather_than_pretended_away() -> None:
    """Candidate facts living in the search configuration are what a future
    migration moves. The number is not zero, and this test exists so that
    nobody has to take that on trust."""
    moved = displaced()
    assert moved, "the matrix claims the ownership boundary is already finished"
    for fact in moved:
        assert fact.owner is Owner.CANDIDATE
        assert fact.home is Home.SEARCH_CONFIG
    assert summary()["candidate_facts_in_the_search_file"] == len(moved)


def test_no_candidate_fact_is_homeless() -> None:
    for fact in candidate_facts():
        assert fact.path, f"{fact.subject!r} does not say where it lives"


def test_where_you_can_be_hired_from_is_editable() -> None:
    """The single most consequential candidate fact in the configuration, and
    the one a person is most likely to get wrong on a first run. It was
    readable on the profile screen and editable only by opening the file."""
    fields = {fact.subject: fact for fact in FACTS}
    assert fields["Where you can be hired from"].field == "eligible_scopes"
    assert fields["Countries that may hire you directly"].field == "eligible_countries"


# =========================================================================
# The drift check, which is what would have caught the `USA Only` defect
# =========================================================================


def test_doctor_names_phrases_the_private_config_is_missing(tmp_path, capsys) -> None:
    """A phrase you do not have is a posting that reads as silent.

    `USA Only` is what We Work Remotely publishes in the field where an
    employer answers "where may we hire?". A private configuration written
    before that source existed has no pattern for it, so the gate stays
    UNRESOLVED and a posting that rules the candidate out looks like one that
    did not say. Nothing on any screen could show that, which is why it is
    doctor's job.
    """
    import yaml

    from career_agent.cli import _report_pattern_drift

    shipped = {
        "eligibility": {
            "blockers": [
                {
                    "id": "us_residence_required",
                    "patterns": ["us only", "usa only", "united states only"],
                }
            ]
        }
    }
    mine = {"eligibility": {"blockers": [{"id": "us_residence_required", "patterns": ["us only"]}]}}
    (tmp_path / "search.worked-example.yaml").write_text(yaml.safe_dump(shipped), encoding="utf-8")
    (tmp_path / "search.local.yaml").write_text(yaml.safe_dump(mine), encoding="utf-8")

    assert _report_pattern_drift(tmp_path) is True
    printed = capsys.readouterr().out
    assert "usa only" in printed
    assert "united states only" in printed


def test_a_private_config_that_matches_the_example_reports_nothing(tmp_path, capsys) -> None:
    import yaml

    from career_agent.cli import _report_pattern_drift

    same = {
        "eligibility": {
            "blockers": [{"id": "us_residence_required", "patterns": ["us only", "usa only"]}]
        }
    }
    (tmp_path / "search.worked-example.yaml").write_text(yaml.safe_dump(same), encoding="utf-8")
    (tmp_path / "search.local.yaml").write_text(yaml.safe_dump(same), encoding="utf-8")

    assert _report_pattern_drift(tmp_path) is False
    assert capsys.readouterr().out == ""


def test_a_blocker_the_private_config_never_had_is_not_drift(tmp_path, capsys) -> None:
    """Somebody who deleted a whole blocker meant to delete it. Drift is about
    a group that exists on both sides and has fallen behind, not about a
    decision to stop using one."""
    import yaml

    from career_agent.cli import _report_pattern_drift

    (tmp_path / "search.worked-example.yaml").write_text(
        yaml.safe_dump(
            {"eligibility": {"blockers": [{"id": "travel_incompatible", "patterns": ["a", "b"]}]}}
        ),
        encoding="utf-8",
    )
    (tmp_path / "search.local.yaml").write_text(
        yaml.safe_dump({"eligibility": {"blockers": []}}), encoding="utf-8"
    )

    assert _report_pattern_drift(tmp_path) is False
