"""The search configuration loads, and every dangling reference is caught at load.

A typo in this file has no runtime symptom worth the name: a weight keyed by a
signal that does not exist simply never fires, and the job scores a few points
lower than it should for the rest of the corpus's life. So the tests below are
mostly about refusal -- an unknown fragment, an unknown signal, a budget nothing
can be a percentage of -- and about the one case where falling back is correct
and must be announced rather than hidden.
"""

from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.search_config import (
    SearchConfigError,
    example_search_path,
    load_search_config,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG_DIR = REPO_ROOT / "config"


def _example_raw() -> dict[str, Any]:
    text = example_search_path(REAL_CONFIG_DIR).read_text(encoding="utf-8")
    parsed: dict[str, Any] = yaml.safe_load(text)
    return parsed


def _config_dir_with(tmp_path: Path, raw: dict[str, Any], stem: str = "local") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / f"search.{stem}.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return tmp_path


# --- resolution ------------------------------------------------------------


def test_load_search_config_requires_explicit_example_opt_in(
    tmp_path: Path,
) -> None:
    """An example-only directory cannot silently choose somebody else's career."""
    config_dir = _config_dir_with(tmp_path, _example_raw(), stem="worked-example")

    with pytest.raises(SearchConfigError, match="explicit"):
        load_search_config(config_dir)
    config, path = load_search_config(config_dir, use_example=True)

    assert path == config_dir / "search.worked-example.yaml"
    assert config.config_id == "personal-alpha"


def test_the_local_file_wins_when_both_exist(tmp_path: Path) -> None:
    raw = _example_raw()
    _config_dir_with(tmp_path, raw, stem="worked-example")
    raw["config_id"] = "mine"
    config_dir = _config_dir_with(tmp_path, raw, stem="local")

    config, path = load_search_config(config_dir)

    assert path.name == "search.local.yaml"
    assert config.config_id == "mine"


def test_a_directory_with_no_search_configuration_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(SearchConfigError) as error:
        load_search_config(tmp_path)
    assert "search.local.yaml" in str(error.value)


def test_the_committed_example_loads_and_validates() -> None:
    """Stops the schema documentation rotting as these models change."""
    config, path = load_search_config(REAL_CONFIG_DIR, use_example=True)
    assert path.name == "search.worked-example.yaml"
    assert config.lexicon and config.taxonomy.primary


# --- fragment expansion ----------------------------------------------------


def test_fragments_are_expanded_at_load_time() -> None:
    # The committed worked example: the real directory would resolve a
    # private `search.local.yaml` on one machine and the starter on another.
    config, _ = load_search_config(committed_config_dir())
    rule = next(r for r in config.taxonomy.primary if r.id == "business_systems")

    assert "$BUSINESS" not in rule.all_of[0]
    assert "business" in rule.all_of[0]
    assert "negocios" in rule.all_of[0]


def test_an_unknown_fragment_is_named_in_the_error(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["taxonomy"]["primary"][0]["all_of"][0] = ["$NOT_A_FRAGMENT"]
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)

    assert "$NOT_A_FRAGMENT" in str(error.value)
    assert "business_systems" in str(error.value)


# --- referential integrity -------------------------------------------------


def test_a_weight_naming_an_undefined_signal_is_refused(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["scoring"]["components"]["responsibilities"]["weights"]["not_a_signal"] = 3
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)

    assert "not_a_signal" in str(error.value)
    assert "scoring.components.responsibilities.weights" in str(error.value)


def test_a_screening_group_naming_an_undefined_signal_is_refused(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["screening"]["required_any_groups"][0]["any_of"] = ["nothing_defines_this"]
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)
    assert "nothing_defines_this" in str(error.value)


def test_an_ambiguity_rule_pointing_nowhere_is_refused(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["taxonomy"]["primary"][0]["ambiguity_rule"] = "no_such_rule"
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)

    assert "no_such_rule" in str(error.value)
    assert "ambiguous_titles" in str(error.value)


def test_a_responsibility_outside_the_shared_vocabulary_is_refused(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["lexicon"]["workflow_automation"]["responsibility"] = "doing_stuff"
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)

    assert "lexicon.workflow_automation.responsibility" in str(error.value)


def test_an_unknown_key_is_refused_rather_than_ignored(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["scoring"]["components"]["responsibilities"]["maximum"] = 25
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)
    assert "maximum" in str(error.value)


# --- budgets ---------------------------------------------------------------


def test_a_component_budget_of_zero_is_refused(tmp_path: Path) -> None:
    """SUPERSEDES `test_component_maxima_that_do_not_sum_to_one_hundred_are_refused`.

    The budget had to be exactly 100 while the score was a bare sum of points.
    Removing `role_family` took 25 off the top and the score became a PERCENTAGE
    of whatever the components declare, so 75 is a perfectly good budget and the
    only unusable one is a budget nothing can be a percentage of.
    """
    raw = _example_raw()
    for name in raw["scoring"]["components"]:
        raw["scoring"]["components"][name]["max"] = 0
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)

    message = str(error.value)
    assert "scoring.components" in message
    assert "not a budget" in message


def test_confidence_points_that_do_not_sum_to_one_hundred_are_refused(tmp_path: Path) -> None:
    raw = _example_raw()
    raw["confidence"]["components"]["salary_known"]["points"] = 11
    config_dir = _config_dir_with(tmp_path, raw)

    with pytest.raises(SearchConfigError) as error:
        load_search_config(config_dir)
    assert "confidence.components" in str(error.value)


# --- digest ----------------------------------------------------------------


def test_the_digest_is_stable_for_the_same_configuration(tmp_path: Path) -> None:
    config_a, _ = load_search_config(_config_dir_with(tmp_path / "a", _example_raw(), "local"))
    config_b, _ = load_search_config(_config_dir_with(tmp_path / "b", _example_raw(), "local"))
    assert config_a.digest == config_b.digest


def test_the_digest_changes_when_a_weight_changes(tmp_path: Path) -> None:
    baseline, _ = load_search_config(_config_dir_with(tmp_path / "a", _example_raw(), "local"))

    raw = _example_raw()
    raw["scoring"]["components"]["responsibilities"]["weights"]["crm_architecture"] = 7
    changed, _ = load_search_config(_config_dir_with(tmp_path / "b", raw, "local"))

    assert changed.digest != baseline.digest
