"""What a stranger gets, and what a stranger must not get.

`config/search.worked-example.yaml` is a WORKED example, which is a polite way
of saying it is one person's real job search: forty-nine phrases about HubSpot,
n8n and RevOps, a country, fourteen title rules, and twenty kinds of work they
will not do. It used to be where `career-agent setup` began when there was no
local file, and because the wizard only ever ADDS to the lexicon, a new user's
search ended up being the owner's search plus their own words.

So the defaults moved. Everything below is about the difference, and it is
written from the outside: a temporary directory with the repository's committed
config files in it, and nothing else.

The system/user boundary lives here too. It is the Career-Ops DATA_CONTRACT
idea, at the size this repository actually needs: a committed path and a private
path may never be the same path, and an update may not write to a private one.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from career_agent.config.search_config import (
    SEARCH_STEM,
    example_search_path,
    load_search_config,
    local_search_path,
    starter_search_path,
)
from career_agent.config.setup import Answers, base_config, run_setup

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"

#: Everything a stranger inherited from the owner, by the name they would use.
#: Named individually rather than diffed, so a regression says WHICH one came
#: back rather than that something did.
OWNER_MARKERS: dict[str, tuple[str, ...]] = {
    "Brazil": ("BR",),
    "HubSpot": ("hubspot", "operations hub", "marketing hub"),
    "n8n and the iPaaS stack": ("n8n", "workato", "zapier"),
    "RevOps": ("revops", "revenue operations", "gtm engineering"),
    "the owner's title taxonomy": ("business systems", "forward deployed", "shared services"),
    "the owner's blockers": ("us_residence_required", "security_clearance_required"),
    "work the owner will not do": ("quota_carrying_sales", "selling_hubspot", "people_management"),
}


@pytest.fixture
def committed_config(tmp_path: Path) -> Path:
    """The repository's COMMITTED configuration, and nothing private.

    A copy rather than the real directory, because the owner's own
    `search.local.yaml` lives there and a stranger does not have one. Testing
    against the real directory would test this machine.
    """
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for name in (
        f"{SEARCH_STEM}.worked-example.yaml",
        f"{SEARCH_STEM}.starter.yaml",
        "places.yaml",
    ):
        source = CONFIG_DIR / name
        if source.exists():
            shutil.copyfile(source, config_dir / name)
    return config_dir


def _text_of(raw: dict[str, Any]) -> str:
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=True).lower()


# =========================================================================
# 1. WHAT A FRESH USER STARTS FROM
# =========================================================================


def test_setup_starts_from_the_neutral_starter_by_default(committed_config: Path) -> None:
    _, started_from = base_config(committed_config)
    assert started_from == starter_search_path(committed_config).name


@pytest.mark.parametrize("marker", sorted(OWNER_MARKERS))
def test_a_fresh_user_inherits_none_of_the_owners_search(
    committed_config: Path, marker: str
) -> None:
    """The seven things measured as inherited, one test each."""
    base, _ = base_config(committed_config)
    text = _text_of(base)
    for needle in OWNER_MARKERS[marker]:
        assert needle not in text, f"a fresh search still carries {marker}: {needle!r}"


def test_a_fresh_user_inherits_no_seniority_or_work_model_preference(
    committed_config: Path,
) -> None:
    """These are the ones the starter kept longest, because their SECTIONS are
    required by the loader and emptying the section looked like the only way to
    empty the values. It was not."""
    base, _ = base_config(committed_config)
    preferences = base["preferences"]
    assert preferences["seniority"]["preferred"] == []
    assert preferences["remote"]["accepted_work_models"] == []
    assert preferences["contract"]["preferred"] == []
    assert base["regions"]["target"] == [], "a stranger in Poland aimed at Latin America"


def test_the_worked_example_is_available_but_only_when_asked_for(
    committed_config: Path,
) -> None:
    base, started_from = base_config(committed_config, worked_example=True)
    assert started_from == example_search_path(committed_config).name
    assert "hubspot" in _text_of(base), "the worked example is still the worked example"


def test_an_existing_local_file_wins_over_both(committed_config: Path) -> None:
    """Re-running the wizard is an edit. Neither flag may discard settings; that
    is what `forget settings` is for, and it asks first."""
    local = local_search_path(committed_config)
    shutil.copyfile(example_search_path(committed_config), local)
    raw = yaml.safe_load(local.read_text(encoding="utf-8"))
    raw["config_id"] = "already-mine"
    local.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")

    for flag in (False, True):
        base, started_from = base_config(committed_config, worked_example=flag)
        assert started_from == local.name
        assert base["config_id"] == "already-mine"


def test_a_fresh_search_loads_through_the_real_loader(committed_config: Path) -> None:
    """A starting point that does not load is worse than no starting point."""
    result = run_setup(committed_config, Answers(role_examples=["integration engineer"]))
    config, path = load_search_config(committed_config)
    assert path.name == local_search_path(committed_config).name
    # 2, not 1: the answers changed the starter, and a change bumps the
    # version because every stored score is keyed on it.
    assert result.config_version == 2
    assert config.lexicon, "the answers became signals"
    assert "hubspot_platform" not in config.lexicon


def test_the_wizard_only_adds_so_a_neutral_base_is_the_whole_protection(
    committed_config: Path,
) -> None:
    """`_apply_signals` never removes a signal, which is correct -- it must not
    delete phrases somebody typed last month. It is also why the BASE is the
    only thing standing between a new user and the owner's lexicon."""
    run_setup(committed_config, Answers(role_examples=["react developer"]))
    config, _ = load_search_config(committed_config)
    assert any("react" in s.patterns[0].lower() for s in config.lexicon.values())
    assert not any("hubspot" in p.lower() for s in config.lexicon.values() for p in s.patterns)


# =========================================================================
# 2. THE SYSTEM / USER BOUNDARY
# =========================================================================
#
# Adopted as a CONCEPT from Career-Ops, whose `DATA_CONTRACT.md` separates
# `SYSTEM_PATHS` from `USER_PATHS` and fails its build if one ever contains the
# other. Their implementation is a JavaScript updater with an allowlist of some
# seventy scripts. This repository has no updater and four private files, so the
# smallest testable version of the same invariant is the right size.

#: Files this repository ships. Updating the project rewrites these.
COMMITTED_CONFIG = (
    "search.worked-example.yaml",
    "search.starter.yaml",
    "profile.example.yaml",
    "career_facts.example.yaml",
    "places.yaml",
    "sources.yaml",
)

#: Files that belong to the person. Nothing shipped may be one of these, and
#: `.gitignore` is what keeps them from being committed by accident.
PRIVATE_CONFIG = (
    "search.local.yaml",
    "profile.local.yaml",
    "career_facts.local.yaml",
)


def test_no_committed_file_is_also_a_private_one() -> None:
    """The whole boundary, in one assertion."""
    assert not set(COMMITTED_CONFIG) & set(PRIVATE_CONFIG)


def test_every_committed_config_file_actually_exists() -> None:
    for name in COMMITTED_CONFIG:
        assert (CONFIG_DIR / name).exists(), f"{name} is declared committed and is missing"


def test_no_private_config_file_is_tracked_by_git() -> None:
    """`.gitignore` covers `config/*.local.yaml` by PATTERN rather than by
    filename, so a private file added later is protected by default."""
    ignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/*.local.yaml" in ignore
    for name in PRIVATE_CONFIG:
        assert name.endswith(".local.yaml"), f"{name} is not covered by the ignore pattern"


def test_writing_a_search_never_touches_a_committed_file(committed_config: Path) -> None:
    """The property that matters on disk: the wizard writes exactly one file,
    and it is the private one."""
    before = {
        name: (committed_config / name).read_bytes()
        for name in COMMITTED_CONFIG
        if (committed_config / name).exists()
    }

    run_setup(committed_config, Answers(role_examples=["data engineer"]))

    for name, content in before.items():
        assert (committed_config / name).read_bytes() == content, f"setup rewrote {name}"
    assert local_search_path(committed_config).exists()
