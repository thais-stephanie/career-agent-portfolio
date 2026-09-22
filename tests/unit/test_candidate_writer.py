"""Changing your own search without opening a YAML file.

Three properties, and the tests are grouped by them because each exists to
prevent a different bad afternoon: a settings panel that can rewrite the
matcher, a crash that empties somebody's search, and a typo that makes every
later command refuse to start.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from career_agent.config.candidate_writer import (
    FIELDS,
    current_candidate_fields,
    set_candidate_fields,
)
from career_agent.config.preferences import PreferenceError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A config directory of its own. Never the repository's.

    A test that writes to `config/` replaces the owner's real search and
    bumps `config_version`, detaching her corpus from its scores.
    """
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", tmp_path)
    # This writer fixture explicitly supplies an example-based starting template.
    shutil.copy(tmp_path / "search.worked-example.yaml", tmp_path / "search.starter.yaml")
    shutil.copy(ROOT / "config" / "places.yaml", tmp_path)
    return tmp_path


def document(workspace: Path) -> dict:
    return yaml.safe_load((workspace / "search.local.yaml").read_text(encoding="utf-8"))


# =========================================================================
# 1. ONLY WHAT THE PANEL OWNS
# =========================================================================


def test_a_field_outside_the_whitelist_cannot_be_written(workspace: Path) -> None:
    """The security property, and the reason `FIELDS` is a table rather than
    a dotted path from the request.

    The body of this PATCH arrives from a browser. Without the whitelist it
    could name `scoring.components` and rewrite how matching works from a
    settings panel.
    """
    for forbidden in ("scoring", "eligibility.blockers", "lexicon", "screening"):
        with pytest.raises(PreferenceError, match="can change"):
            set_candidate_fields(workspace, {forbidden: ["anything"]})


def test_the_committed_example_is_never_written(workspace: Path) -> None:
    """It is the shipped baseline and what `git checkout` restores."""
    before = (workspace / "search.worked-example.yaml").read_bytes()
    set_candidate_fields(workspace, {"work_models": ["REMOTE"]})
    assert (workspace / "search.worked-example.yaml").read_bytes() == before
    assert (workspace / "search.local.yaml").exists()


@pytest.mark.parametrize("field", sorted(FIELDS))
def test_every_advertised_field_can_actually_be_read_back(workspace: Path, field: str) -> None:
    """A control the panel draws for a field the document does not hold would
    appear empty and save nothing."""
    assert field in current_candidate_fields(workspace)


# =========================================================================
# 2. A BAD VALUE IS REFUSED, AND SAYS WHICH BOX
# =========================================================================


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"work_models": ["TELEPATHY"]}, "not one of"),
        ({"work_models": "REMOTE"}, "list of choices"),
        ({"work_models": []}, "match nothing"),
        ({"travel_max_pct": 300}, "0 to 100"),
        ({"travel_max_pct": "lots"}, "0 to 100"),
        ({"compensation_target": -1}, "negative"),
        ({"compensation_currency": "Brazilian Real"}, "three-letter"),
        ({"candidate_country": "Brazil"}, "two-letter"),
        ({"require_remote": "yes"}, "yes or no"),
        ({"seniority_preferred": ["ARCHMAGE"]}, "not one of"),
    ],
)
def test_a_bad_value_is_refused_with_a_sentence_a_person_could_act_on(
    workspace: Path, changes: dict, expected: str
) -> None:
    with pytest.raises(PreferenceError, match=expected):
        set_candidate_fields(workspace, changes)


def test_a_refused_change_leaves_the_file_exactly_as_it_was(workspace: Path) -> None:
    set_candidate_fields(workspace, {"work_models": ["REMOTE", "HYBRID"]})
    before = (workspace / "search.local.yaml").read_bytes()

    with pytest.raises(PreferenceError):
        set_candidate_fields(workspace, {"work_models": ["TELEPATHY"]})

    assert (workspace / "search.local.yaml").read_bytes() == before


def test_a_batch_is_all_or_nothing(workspace: Path) -> None:
    """A panel saving four fields must not leave two of them applied.

    And four separate writes would bump the version four times, invalidating
    the corpus three times over for no reason.
    """
    set_candidate_fields(workspace, {"work_models": ["REMOTE"]})
    version_before = document(workspace)["config_version"]

    with pytest.raises(PreferenceError):
        set_candidate_fields(
            workspace,
            {"work_models": ["HYBRID"], "candidate_country": "not a country"},
        )

    after = document(workspace)
    assert after["preferences"]["remote"]["accepted_work_models"] == ["REMOTE"]
    assert after["config_version"] == version_before


def test_an_empty_unwanted_list_is_a_real_answer(workspace: Path) -> None:
    """ "Nothing I would refuse" is a position. The list that must not be empty
    is the one that would otherwise match nothing."""
    set_candidate_fields(workspace, {"contract_unwanted": []})
    assert document(workspace)["preferences"]["contract"]["unwanted"] == []


def test_saying_remote_twice_is_the_same_search_as_saying_it_once(workspace: Path) -> None:
    set_candidate_fields(workspace, {"work_models": ["REMOTE", "HYBRID", "REMOTE"]})
    assert document(workspace)["preferences"]["remote"]["accepted_work_models"] == [
        "REMOTE",
        "HYBRID",
    ]


# =========================================================================
# 3. THE WRITE ITSELF
# =========================================================================


def test_every_change_bumps_the_version(workspace: Path) -> None:
    """A score is only true relative to the configuration that produced it."""
    _, first, _ = set_candidate_fields(workspace, {"work_models": ["REMOTE"]})
    _, second, _ = set_candidate_fields(workspace, {"work_models": ["HYBRID"]})
    assert second == first + 1


def test_a_save_that_changes_nothing_bumps_nothing(workspace: Path) -> None:
    """**What the version costs is why this matters.**

    It is not an edit counter. It is what a stored score is TRUE RELATIVE TO,
    so raising it detaches every row in `job_match`: on the real corpus that
    is 19,469 rows and about seven minutes of rescoring, and until it runs the
    Jobs list opens empty saying "these jobs have not been scored yet".

    Pressing Save on a panel you only came to look at used to cost exactly
    that -- and the screen gave no hint the cause was a button that appeared
    to do nothing.
    """
    _, first, _ = set_candidate_fields(workspace, {"work_models": ["REMOTE"]})
    before = (workspace / "search.local.yaml").read_bytes()

    path, again, applied = set_candidate_fields(workspace, {"work_models": ["REMOTE"]})

    assert again == first, "re-saving an unchanged value moved the version"
    assert path.read_bytes() == before, "the file was rewritten for no change"
    # Still reports the field it was asked about: re-saving is an ordinary
    # thing to do and must not read as a refusal.
    assert applied == ["work_models"]


def test_an_unchanged_field_beside_a_changed_one_still_bumps_once(workspace: Path) -> None:
    """The guard is about the DOCUMENT, never about which fields were named."""
    _, first, _ = set_candidate_fields(
        workspace, {"work_models": ["REMOTE"], "compensation_currency": "BRL"}
    )

    _, second, _ = set_candidate_fields(
        workspace, {"work_models": ["REMOTE"], "compensation_currency": "EUR"}
    )

    assert second == first + 1


def test_the_first_save_writes_the_file_even_when_it_changes_nothing(workspace: Path) -> None:
    """The one exception, and it has to be an exception.

    Before any save there is no local file: the values come from the committed
    example. Saving one of them back unchanged still has to MATERIALISE the
    local file, or the panel would report success over a file that does not
    exist and the next command would read the example again.
    """
    assert not (workspace / "search.local.yaml").exists()
    current = current_candidate_fields(workspace)

    path, _, _ = set_candidate_fields(workspace, {"work_models": list(current["work_models"])})

    assert path.exists()
    assert path.name == "search.local.yaml"


def test_the_previous_version_is_kept_beside_the_new_one(workspace: Path) -> None:
    set_candidate_fields(workspace, {"compensation_currency": "BRL"})
    set_candidate_fields(workspace, {"compensation_currency": "EUR"})

    backup = workspace / "search.local.yaml.backup"
    assert backup.exists(), "the previous version was not kept"
    restored = yaml.safe_load(backup.read_text(encoding="utf-8"))
    assert restored["preferences"]["compensation"]["currency"] == "BRL"


def test_a_document_the_loader_cannot_read_is_never_written(workspace: Path) -> None:
    """Validation runs against the REAL loader, in a directory of its own.

    A file that fails to load is worse than an unchanged one: every later
    command refuses, and the person who mistyped in a dropdown has no way to
    learn that is what happened.
    """
    from career_agent.config import candidate_writer

    original = dict(candidate_writer.FIELDS)
    candidate_writer.FIELDS["candidate_country"] = {
        # A path into a section the loader validates strictly, with a value
        # the schema will reject.
        "path": ("thresholds", "shortlist_min_score"),
        "kind": "currency",
        "label": "Broken on purpose",
    }
    try:
        with pytest.raises(PreferenceError, match="cannot read"):
            set_candidate_fields(workspace, {"candidate_country": "ABC"})
        assert not (workspace / "search.local.yaml").exists(), (
            "a document the loader rejects reached the disk"
        )
    finally:
        candidate_writer.FIELDS.clear()
        candidate_writer.FIELDS.update(original)


def test_no_temporary_file_is_left_behind(workspace: Path) -> None:
    set_candidate_fields(workspace, {"work_models": ["REMOTE"]})
    assert not list(workspace.glob(".tmp-*")), "a temporary file survived the write"


def test_what_was_written_is_what_reads_back(workspace: Path) -> None:
    """The round trip, through the loader every other command uses."""
    from career_agent.config.search_config import load_search_config

    set_candidate_fields(
        workspace,
        {
            "work_models": ["REMOTE"],
            "seniority_preferred": ["SENIOR", "LEAD"],
            "candidate_country": "pt",
            "travel_max_pct": 0,
        },
    )
    config, path = load_search_config(workspace)
    assert path.name == "search.local.yaml"
    assert config.eligibility.candidate_country == "PT"
    assert current_candidate_fields(workspace)["seniority_preferred"] == ["SENIOR", "LEAD"]
    assert current_candidate_fields(workspace)["travel_max_pct"] == 0
