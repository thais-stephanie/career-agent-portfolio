"""Loading configuration, and the two rules that govern it.

**Rule 1: local files only, never a silent fallback to the example.**

The obvious design is "use profile.local.yaml if present, otherwise fall back to
profile.example.yaml". We deliberately do not do that. If the local file were
ever missing or misnamed, the system would run a full job search against
placeholder preferences and produce a digest that looks entirely normal. That
is the silent-wrong-answer failure this architecture exists to prevent,
reappearing in configuration. A missing local file is a hard error naming the
copy command.

**Rule 2: validation errors name the offending line.**

``residence_country: BRZ`` must not be a shrug. pydantic reports the path
(``candidate_geography.residence_country``) and the reason, and this module
formats that into something readable, because a config error you cannot locate
is barely better than no error at all.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.profile import SearchProfile
from career_agent.yaml_io import safe_load

PROFILE_STEM = "profile"
CAREER_FACTS_STEM = "career_facts"


class ConfigError(RuntimeError):
    """Configuration is missing, unreadable, or invalid."""


@dataclass(frozen=True)
class LoadedConfig:
    """What was loaded, and from where. `doctor` reports the paths so there is
    never any doubt about which file the system actually read."""

    profile: SearchProfile
    profile_path: Path
    career_facts: "CareerFacts | None"
    career_facts_path: Path | None


class CareerFacts(BaseModel):
    """The whole of career_facts.local.yaml."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    candidate_key: str = Field(min_length=1)
    verified_claims: list[VerifiedClaim] = Field(default_factory=list)

    def model_post_init(self, _context: Any) -> None:
        keys = [c.claim_key for c in self.verified_claims]
        duplicates = {k for k in keys if keys.count(k) > 1}
        if duplicates:
            raise ValueError(
                f"claim_key listed more than once: {sorted(duplicates)}. "
                "Corrections belong in a new revision, not a second entry."
            )


def local_path(config_dir: Path, stem: str) -> Path:
    return config_dir / f"{stem}.local.yaml"


def example_path(config_dir: Path, stem: str) -> Path:
    return config_dir / f"{stem}.example.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"could not read {path}: {exc}") from exc

    try:
        parsed = safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc

    if parsed is None:
        raise ConfigError(f"{path.name} is empty")
    if not isinstance(parsed, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    return parsed


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path.name} is not valid:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def _require_local(config_dir: Path, stem: str) -> Path:
    path = local_path(config_dir, stem)
    if path.exists():
        return path

    example = example_path(config_dir, stem)
    raise ConfigError(
        f"{path} not found.\n"
        f"Copy the example and edit your copy:\n"
        f"    Copy-Item {example} {path}\n"
        f"The .local.yaml file is gitignored and is never committed."
    )


def load_profile(config_dir: Path) -> tuple[SearchProfile, Path]:
    """Load and validate profile.local.yaml. Never falls back to the example."""
    path = _require_local(config_dir, PROFILE_STEM)
    try:
        return SearchProfile.model_validate(_read_yaml(path)), path
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def load_career_facts(config_dir: Path) -> tuple[CareerFacts | None, Path | None]:
    """Load career_facts.local.yaml if it exists.

    Unlike the profile, this one is genuinely optional: nothing before Resume
    Intelligence reads it, so an absent file is a normal state rather than a
    misconfiguration.
    """
    path = local_path(config_dir, CAREER_FACTS_STEM)
    if not path.exists():
        return None, None
    try:
        return CareerFacts.model_validate(_read_yaml(path)), path
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def load_config(config_dir: Path) -> LoadedConfig:
    profile, profile_path = load_profile(config_dir)
    facts, facts_path = load_career_facts(config_dir)

    if facts is not None and facts.candidate_key != profile.candidate_key:
        raise ConfigError(
            f"candidate_key mismatch: profile says {profile.candidate_key!r} but "
            f"career facts say {facts.candidate_key!r}. Claims would attach to the "
            "wrong candidate."
        )
    return LoadedConfig(
        profile=profile,
        profile_path=profile_path,
        career_facts=facts,
        career_facts_path=facts_path,
    )


def load_example_profile(config_dir: Path) -> SearchProfile:
    """Validate the committed example.

    Used only by the test suite, to stop the schema documentation rotting as
    the models change. It is never a runtime fallback.
    """
    path = example_path(config_dir, PROFILE_STEM)
    try:
        return SearchProfile.model_validate(_read_yaml(path))
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc


def profile_yaml_text(path: Path) -> str:
    """The exact bytes to snapshot into search_profile_version."""
    return path.read_text(encoding="utf-8")
