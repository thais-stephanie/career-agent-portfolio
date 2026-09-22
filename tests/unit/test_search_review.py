"""Legacy review cannot silently replace preferences or fabricate provenance."""

import shutil
from pathlib import Path

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.preferences import PreferenceError, read_signals
from career_agent.config.search_config import load_search_config
from career_agent.config.search_review import apply_review, plan_review, review_search


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    shutil.copytree(committed_config_dir(), tmp_path / "config")
    path = tmp_path / "config"
    shutil.copy(path / "search.worked-example.yaml", path / "search.local.yaml")
    target = path / "search.local.yaml"
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    raw["preferences"]["seniority"]["excluded"] = ["INTERN", "STAFF", "PRINCIPAL", "LEAD"]
    target.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def row(report, path):
    return next(item for item in report["rows"] if item["path"] == path)


def test_review_is_read_only_and_distinguishes_legacy_from_policy(config_dir) -> None:
    before = (config_dir / "search.local.yaml").read_bytes()
    report = review_search(config_dir)
    assert row(report, "lexicon.hubspot_platform.patterns")["origin"] == "inherited_legacy_example"
    assert row(report, "normalisation.casefold")["origin"] == "neutral_product_policy"
    assert (config_dir / "search.local.yaml").read_bytes() == before
    assert not (config_dir / "search-review.local.yaml").exists()


def test_keep_records_review_without_changing_score_version(config_dir) -> None:
    report = review_search(config_dir)
    path = "preferences.seniority.excluded"
    args = {"path": path, "action": "keep", "expected_hash": report["document_hash"]}
    _, plan = plan_review(config_dir, **args)
    apply_review(config_dir, **args, confirmation=plan["confirmation"])
    after = review_search(config_dir)
    assert after["config_version"] == report["config_version"]
    assert after["document_hash"] == report["document_hash"]
    assert row(after, path)["origin"] == "reviewed_by_user"


def test_edit_preview_preserves_other_preferences_and_needs_exact_confirmation(config_dir) -> None:
    report = review_search(config_dir)
    path = "lexicon.hubspot_platform.patterns"
    args = {
        "path": path,
        "action": "edit",
        "expected_hash": report["document_hash"],
        "value": ["my chosen CRM"],
    }
    _, plan = plan_review(config_dir, **args)
    assert review_search(config_dir)["document_hash"] == report["document_hash"]
    with pytest.raises(PreferenceError, match="Confirm"):
        apply_review(config_dir, **args, confirmation="not-the-preview")
    apply_review(config_dir, **args, confirmation=plan["confirmation"])
    after = review_search(config_dir)
    assert after["config_version"] == report["config_version"] + 1
    assert row(after, path)["value"] == ["my chosen CRM"]
    assert (
        row(after, "preferences.seniority.excluded")["value"]
        == row(report, "preferences.seniority.excluded")["value"]
    )
    with pytest.raises(PreferenceError, match="changed since"):
        plan_review(config_dir, **args)


def test_hand_edit_invalidates_review_without_erasing_value(config_dir) -> None:
    report = review_search(config_dir)
    path = "lexicon.hubspot_platform.patterns"
    args = {"path": path, "action": "keep", "expected_hash": report["document_hash"]}
    _, plan = plan_review(config_dir, **args)
    apply_review(config_dir, **args, confirmation=plan["confirmation"])
    target = config_dir / "search.local.yaml"
    data = yaml.safe_load(target.read_text())
    data["lexicon"]["hubspot_platform"]["patterns"] = ["owner edited"]
    target.write_text(yaml.safe_dump(data), encoding="utf-8")
    assert row(review_search(config_dir), path)["origin"] == "changed_locally"


def test_new_neutral_user_cannot_inherit_the_legacy_example(config_dir) -> None:
    (config_dir / "search.local.yaml").unlink()
    config, path = load_search_config(config_dir)
    assert path.name == "search.starter.yaml"
    assert config.lexicon == {}
    assert config.taxonomy.primary == []
    assert config.preferences.seniority.preferred == []
    assert read_signals(config_dir) == []
    assert not any(row["path"].startswith("lexicon.") for row in review_search(config_dir)["rows"])


def test_remove_requires_preview_and_changes_only_the_selected_phrase_assumption(
    config_dir,
) -> None:
    report = review_search(config_dir)
    path = "lexicon.hubspot_platform.patterns"
    args = {"path": path, "action": "remove", "expected_hash": report["document_hash"]}
    _, plan = plan_review(config_dir, **args)
    assert plan["rescore_required"]
    assert row(review_search(config_dir), path)["value"]
    apply_review(config_dir, **args, confirmation=plan["confirmation"])
    after = review_search(config_dir)
    assert row(after, path)["value"] == []
    assert row(after, path)["review"]["action"] == "remove"
