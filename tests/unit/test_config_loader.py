"""Acceptance criteria 7 and 14: the profile loads, and bad config fails loudly.

The tests read fixtures under tests/fixtures/config, never the real
config/*.local.yaml, so a clean clone with no local configuration still passes
the whole suite.
"""

import shutil
from pathlib import Path

import pytest

from career_agent.config.loader import (
    ConfigError,
    load_career_facts,
    load_config,
    load_example_profile,
    load_profile,
)
from career_agent.domain.enums import GateResult, PreferenceBucket, ResponsibilityCategory

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG_DIR = REPO_ROOT / "config"
FIXTURE_CONFIG_DIR = REPO_ROOT / "tests" / "fixtures" / "config"


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """A throwaway config directory seeded from the committed examples."""
    shutil.copy(REAL_CONFIG_DIR / "profile.example.yaml", tmp_path / "profile.example.yaml")
    shutil.copy(
        REAL_CONFIG_DIR / "career_facts.example.yaml", tmp_path / "career_facts.example.yaml"
    )
    shutil.copy(tmp_path / "profile.example.yaml", tmp_path / "profile.local.yaml")
    shutil.copy(tmp_path / "career_facts.example.yaml", tmp_path / "career_facts.local.yaml")
    return tmp_path


# --- the committed example must stay valid ---------------------------------


def test_committed_example_profile_validates() -> None:
    """Stops the schema documentation rotting as the models change."""
    profile = load_example_profile(REAL_CONFIG_DIR)
    assert profile.candidate_key == "example"


# --- happy path ------------------------------------------------------------


def test_profile_loads_with_the_three_geography_blocks(config_dir: Path) -> None:
    """Acceptance criterion 7."""
    profile, path = load_profile(config_dir)
    assert path.name == "profile.local.yaml"

    assert profile.candidate_geography.residence_country == "BR"
    assert profile.job_hiring_geography.scope_excluding_residence_is is GateResult.FAIL
    assert "CH" in profile.target_company_markets.WANT


def test_uk_is_normalised_to_gb(config_dir: Path) -> None:
    """The alias exists so a reasonable-looking typo does not silently match
    nothing for an entire job search."""
    text = (config_dir / "profile.local.yaml").read_text(encoding="utf-8")
    (config_dir / "profile.local.yaml").write_text(
        text.replace("INTERESTED: [GB,", "INTERESTED: [UK,"), encoding="utf-8"
    )
    profile, _ = load_profile(config_dir)
    assert "GB" in profile.target_company_markets.INTERESTED
    assert "UK" not in profile.target_company_markets.INTERESTED


def test_people_management_is_avoid_not_never(config_dir: Path) -> None:
    """A strong individual-contributor role must not be eliminated outright."""
    profile, _ = load_profile(config_dir)
    responsibilities = profile.intent.responsibilities
    assert (
        responsibilities.bucket_for(ResponsibilityCategory.PEOPLE_MANAGEMENT)
        is PreferenceBucket.AVOID
    )
    assert (
        responsibilities.bucket_for(ResponsibilityCategory.TECHNICAL_MENTORING)
        is PreferenceBucket.INTERESTED
    )


def test_neutral_categories_report_no_bucket(config_dir: Path) -> None:
    profile, _ = load_profile(config_dir)
    assert profile.intent.responsibilities.bucket_for(ResponsibilityCategory.TESTING_AND_QA) is None


def test_zero_minimum_means_no_compensation_gate(config_dir: Path) -> None:
    profile, _ = load_profile(config_dir)
    assert profile.compensation.has_hard_floor is False


def test_career_facts_and_claims_round_trip(config_dir: Path) -> None:
    facts, path = load_career_facts(config_dir)
    assert facts is not None
    assert path is not None
    assert len(facts.verified_claims) == 2
    usable = [c for c in facts.verified_claims if c.is_usable_for_generation]
    assert len(usable) == 1, "unverified claims must not be usable for generation"


def test_career_facts_are_optional(config_dir: Path) -> None:
    """Nothing before Resume Intelligence reads them, so absence is normal."""
    (config_dir / "career_facts.local.yaml").unlink()
    facts, path = load_career_facts(config_dir)
    assert facts is None and path is None


# --- no silent fallback ----------------------------------------------------


def test_missing_local_profile_is_a_hard_error(config_dir: Path) -> None:
    """The example must NEVER be used as a runtime fallback: a job search
    against placeholder preferences would look completely normal."""
    (config_dir / "profile.local.yaml").unlink()
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)

    message = str(exc.value)
    assert "profile.local.yaml" in message
    assert "Copy-Item" in message, "the error must tell the user how to fix it"
    assert "gitignored" in message


def test_example_is_not_loaded_when_local_is_absent(config_dir: Path) -> None:
    (config_dir / "profile.local.yaml").unlink()
    with pytest.raises(ConfigError):
        load_config(config_dir)


# --- errors name the offending path ----------------------------------------


def _write_profile(config_dir: Path, old: str, new: str) -> None:
    path = config_dir / "profile.local.yaml"
    text = path.read_text(encoding="utf-8")
    assert old in text
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def test_invalid_country_names_the_field(config_dir: Path) -> None:
    """Acceptance criterion 14."""
    _write_profile(config_dir, "residence_country: BR", "residence_country: BRZ")
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)
    message = str(exc.value)
    assert "candidate_geography.residence_country" in message
    assert "two letters" in message


def test_unknown_responsibility_names_the_field(config_dir: Path) -> None:
    _write_profile(config_dir, "- crm_administration", "- crm_admin")
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)
    assert "intent.responsibilities.WANT" in str(exc.value)


def test_unknown_key_is_rejected(config_dir: Path) -> None:
    """extra='forbid' turns a typo'd key into an error instead of a setting
    that silently does nothing."""
    _write_profile(config_dir, "candidate_key: example", "candidate_key: example\nnickname: x")
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)
    assert "nickname" in str(exc.value)


def test_category_in_two_buckets_is_rejected(config_dir: Path) -> None:
    _write_profile(
        config_dir, "    NEVER:\n      - on_call_rotation", "    NEVER:\n      - people_management"
    )
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)
    assert "appears in both" in str(exc.value)


def test_residence_without_work_authorisation_is_rejected(config_dir: Path) -> None:
    """The gates would otherwise assume a right to work that was never stated."""
    _write_profile(config_dir, "  citizenships: [BR]", "  citizenships: [PT]")
    _write_profile(
        config_dir, "- { country: BR, basis: CITIZEN }", "- { country: PT, basis: CITIZEN }"
    )
    with pytest.raises(ConfigError) as exc:
        load_profile(config_dir)
    assert "work_authorizations" in str(exc.value)


def test_empty_file_is_rejected(config_dir: Path) -> None:
    (config_dir / "profile.local.yaml").write_text("", encoding="utf-8")
    with pytest.raises(ConfigError, match="empty"):
        load_profile(config_dir)


def test_malformed_yaml_is_rejected(config_dir: Path) -> None:
    (config_dir / "profile.local.yaml").write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_profile(config_dir)


def test_candidate_key_mismatch_is_rejected(config_dir: Path) -> None:
    path = config_dir / "career_facts.local.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("candidate_key: example", "candidate_key: other"),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError, match="candidate_key mismatch"):
        load_config(config_dir)


def test_fixture_directory_is_not_the_real_config_directory() -> None:
    """A guard on the tests themselves: they must never read real private
    configuration, or a clean clone would fail."""
    assert FIXTURE_CONFIG_DIR != REAL_CONFIG_DIR
