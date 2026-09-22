"""The soundness argument for score replay, as a test rather than a claim.

`match.replay` recomputes the arithmetic of a score over readings that were
stored under a DIFFERENT configuration version. That is only sound while one
thing is true:

    the configuration sections `match/score.py` reads are read by nothing else.

If a scorer ever starts reading the lexicon, or a reader starts reading the
weights, then a "scoring-only" edit silently changes something replay carries
over unchanged, and every replayed score is wrong in a way no equivalence test
over today's corpus would necessarily catch.

So it is asserted structurally, by walking the syntax tree, on the model of
`tests/unit/test_domain_purity.py`. A violation is a build failure at the
moment somebody writes it, not a mystery six months later.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.match.identity import (
    IDENTITY_FIELDS,
    REPLAY_SAFE_SECTIONS,
    guarded_sections,
    input_digest,
)

MATCH_DIR = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "match"


@pytest.fixture(scope="module")
def worked_example() -> SearchConfig:
    """The committed worked example, never the owner's private search.

    `committed_config_dir` strips every `*.local.yaml` and then copies the
    worked example into that slot, so a test can load a real, complete
    configuration without depending on a file this repository does not ship.
    """
    config, _ = load_search_config(committed_config_dir())
    return config


#: Modules that participate in producing or consuming a stored reading.
READING_MODULES = (
    "lexicon.py",
    "taxonomy.py",
    "gates.py",
    "seniority.py",
    "experience.py",
    "employment.py",
    "places.py",
    "text.py",
    "engine.py",
)


def _config_sections_read(path: Path) -> set[str]:
    """Every `config.<section>` / `cfg.<section>` attribute a module reads.

    Matches on the ATTRIBUTE name against the real `SearchConfig` field set, so
    an unrelated `self.config.foo` cannot inflate the answer and a renamed
    section cannot silently drop out of the check.
    """
    fields = set(SearchConfig.model_fields)
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        value = node.value
        names = {"config", "cfg", "search_config"}
        if isinstance(value, ast.Name) and value.id in names and node.attr in fields:
            found.add(node.attr)
    return found


def test_the_scorer_reads_only_the_replay_safe_sections():
    """`score.py` may read the four, and must not read anything else."""
    read = _config_sections_read(MATCH_DIR / "score.py")
    outside = read - REPLAY_SAFE_SECTIONS - IDENTITY_FIELDS
    assert not outside, (
        f"match/score.py reads guarded configuration sections {sorted(outside)}."
        " Replay recomputes score.py and carries everything else over unchanged,"
        " so a scorer reading a guarded section makes every replayed score wrong."
    )


@pytest.mark.parametrize("module", READING_MODULES)
def test_no_reading_module_reads_a_replay_safe_section(module: str):
    """A reader must not depend on the weights, or a weight change is a reread."""
    read = _config_sections_read(MATCH_DIR / module)
    overlap = read & REPLAY_SAFE_SECTIONS
    assert not overlap, (
        f"match/{module} reads replay-safe configuration sections {sorted(overlap)}."
        " Either it must stop, or those sections must move into the guarded set"
        " and replay must refuse when they change."
    )


def test_every_section_is_either_safe_guarded_or_identity():
    """No configuration field may be unclassified. The default is guarded."""
    fields = set(SearchConfig.model_fields)
    classified = REPLAY_SAFE_SECTIONS | IDENTITY_FIELDS | set(guarded_sections_of(fields))
    assert classified == fields


def guarded_sections_of(fields: set[str]) -> list[str]:
    return sorted(fields - REPLAY_SAFE_SECTIONS - IDENTITY_FIELDS)


def test_a_new_section_is_guarded_by_default(worked_example):
    """Adding a field to the model must not silently widen what replay accepts."""
    assert "scoring" not in guarded_sections(worked_example)
    assert "lexicon" in guarded_sections(worked_example)
    assert "eligibility" in guarded_sections(worked_example)
    assert "screening" in guarded_sections(worked_example)
    # The identity fields carry no inputs and must not enter the digest, or
    # every version bump would refuse every replay -- which is the whole point.
    for name in IDENTITY_FIELDS:
        assert name not in guarded_sections(worked_example)


def test_the_digest_ignores_a_version_bump_and_a_weight_is_not_ignored(worked_example):
    """The two properties the whole design rests on, stated as one test."""
    bumped = worked_example.model_copy(update={"config_version": worked_example.config_version + 1})
    assert input_digest(bumped) == input_digest(worked_example), (
        "a configuration version bump changed the reading identity; no replay"
        " would ever be possible"
    )

    data = worked_example.model_dump()
    component = data["scoring"]["components"]["responsibilities"]
    component["max"] = component["max"] / 2
    reweighted = SearchConfig.model_validate(data)
    assert input_digest(reweighted) == input_digest(worked_example), (
        "a weight change moved the reading identity; the replay path would"
        " never be taken for the edit it exists to serve"
    )

    data = worked_example.model_dump()
    data["eligibility"]["eligible_countries"] = ["ZZ"]
    elsewhere = SearchConfig.model_validate(data)
    assert input_digest(elsewhere) != input_digest(worked_example), (
        "an eligibility change did not move the reading identity; a replay"
        " would carry stale gate outcomes"
    )


def test_the_reader_identity_participates_in_the_digest(monkeypatch, worked_example):
    """Bumping the reader identity must invalidate every stored reading."""
    from career_agent.match import identity

    before = input_digest(worked_example)
    monkeypatch.setattr(identity, "READER_IDENTITY", "readers-999")
    assert input_digest(worked_example) != before
