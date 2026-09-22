"""Editing the search preferences without opening a YAML file.

The failure mode being prevented is not "the editor does not work". It is the
editor working and destroying something on the way: the committed baseline, a
signal it does not know about, or the version discipline that keeps old scores
readable.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from career_agent.config.preferences import (
    CATEGORIES,
    PreferenceError,
    read_signals,
    set_patterns,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG = REPO_ROOT / "config"


@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """A copy of the real example, so these tests edit the real shape."""
    target = tmp_path / "config"
    target.mkdir()
    shutil.copy(REAL_CONFIG / "search.worked-example.yaml", target / "search.worked-example.yaml")
    shutil.copy(target / "search.worked-example.yaml", target / "search.local.yaml")
    return target


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_every_category_finds_signals_in_the_real_configuration() -> None:
    """A category whose path no longer resolves would silently offer nothing
    to edit, and the interface would render an empty panel rather than fail."""
    from tests.support import committed_config_dir

    signals = read_signals(committed_config_dir())
    found = {signal.category for signal in signals}
    assert found == set(CATEGORIES), f"categories with no signals: {set(CATEGORIES) - found}"
    for signal in signals:
        assert signal.patterns, f"{signal.signal_id} offers no phrases to edit"


def test_a_write_never_touches_the_committed_example(config_dir: Path) -> None:
    """The example is the shipped baseline and what `git checkout` restores.

    Writing to it would destroy the only copy of the default the moment an
    edit goes wrong, which is exactly when it is needed.
    """
    example = config_dir / "search.worked-example.yaml"
    before = example.read_bytes()

    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    written, _ = set_patterns(config_dir, "desired", signal.signal_id, ["n8n orchestration"])

    assert example.read_bytes() == before, "the committed example was modified"
    assert written.name == "search.local.yaml"


def test_a_write_bumps_the_configuration_version(config_dir: Path) -> None:
    """Scores are only true relative to the configuration that produced them.

    Editing phrases without a bump leaves every stored score attributed to
    bytes that no longer exist, and `rescore` -- which keys on the version --
    would skip every row.
    """
    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    _, first = set_patterns(config_dir, "desired", signal.signal_id, ["one"])
    _, second = set_patterns(config_dir, "desired", signal.signal_id, ["two"])
    assert second == first + 1


def test_the_edit_is_what_comes_back(config_dir: Path) -> None:
    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    set_patterns(config_dir, "desired", signal.signal_id, ["alpha", "beta"])

    again = next(
        s
        for s in read_signals(config_dir)
        if s.signal_id == signal.signal_id and s.category == "desired"
    )
    assert again.patterns == ("alpha", "beta")


def test_signals_the_editor_did_not_touch_survive(config_dir: Path) -> None:
    """The local file is written whole, so an untouched signal must come
    through unchanged rather than being dropped by the round trip."""
    before = {s.signal_id: s.patterns for s in read_signals(config_dir)}
    target = next(s for s in read_signals(config_dir) if s.category == "desired")
    set_patterns(config_dir, "desired", target.signal_id, ["changed"])

    after = {s.signal_id: s.patterns for s in read_signals(config_dir)}
    assert set(after) == set(before), "the round trip lost or invented a signal"
    for signal_id, patterns in before.items():
        if signal_id != target.signal_id:
            assert after[signal_id] == patterns, f"{signal_id} changed and should not have"


def test_hard_exclusions_are_editable_too(config_dir: Path) -> None:
    """They live in a LIST carrying its own ids, not a mapping, so they take a
    different write path and would be the one that silently does nothing."""
    signal = next(s for s in read_signals(config_dir) if s.category == "excluded")
    set_patterns(config_dir, "excluded", signal.signal_id, ["must hold a us clearance"])
    again = next(
        s
        for s in read_signals(config_dir)
        if s.category == "excluded" and s.signal_id == signal.signal_id
    )
    assert again.patterns == ("must hold a us clearance",)


def test_an_unknown_signal_is_refused_rather_than_invented(config_dir: Path) -> None:
    """A typo that creates a signal nothing scores against is the same defect
    as a filter parameter nobody reads: it looks saved and does nothing."""
    with pytest.raises(PreferenceError):
        set_patterns(config_dir, "desired", "no_such_signal", ["x"])
    with pytest.raises(PreferenceError):
        set_patterns(config_dir, "no_such_category", "anything", ["x"])


def test_a_signal_cannot_be_emptied_into_invisibility(config_dir: Path) -> None:
    """Zero phrases matches nothing and cannot be seen to be broken."""
    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    for empty in ([], ["   "], ["", "  "]):
        with pytest.raises(PreferenceError):
            set_patterns(config_dir, "desired", signal.signal_id, empty)


def test_duplicate_phrases_are_collapsed(config_dir: Path) -> None:
    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    set_patterns(config_dir, "desired", signal.signal_id, ["n8n", "N8N", " n8n ", "zapier"])
    again = next(
        s
        for s in read_signals(config_dir)
        if s.category == "desired" and s.signal_id == signal.signal_id
    )
    assert again.patterns == ("n8n", "zapier")


def test_the_written_file_is_valid_configuration(config_dir: Path) -> None:
    """It has to load through the real loader, not merely be valid YAML."""
    from career_agent.config.search_config import load_search_config

    signal = next(s for s in read_signals(config_dir) if s.category == "desired")
    written, version = set_patterns(config_dir, "desired", signal.signal_id, ["n8n"])

    config, path = load_search_config(config_dir)
    assert path == written, "the loader did not prefer the local file"
    assert config.config_version == version
    assert "n8n" in config.lexicon[signal.signal_id].patterns
