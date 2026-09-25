"""Loading the company registry into the database.

The registry is authored in YAML rather than managed in the database on
purpose: curating which companies to watch is a judgement activity done in an
editor, and it belongs in git history next to the rest of the configuration.

M1A uses `companies.seed.yaml`, an engineering set chosen for payload variety.
M1D will add the real curated registry; this loader is agnostic about which
file it is given.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from career_agent.config.loader import ConfigError
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo
from career_agent.yaml_io import safe_load


class BoardEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str = Field(min_length=1)
    board_identifier: str = Field(min_length=1)
    board_url: str | None = None
    active: bool = True
    #: How this identifier was arrived at, and when a live probe last confirmed
    #: it. A guessed slug that happens to answer is weaker evidence than one a
    #: careers page published, and the registry should not forget which it was.
    discovery_method: str | None = None
    verified_at: str | None = None


class CompanyEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    slug: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1)
    website: str | None = None
    hq_country: str | None = None
    size_estimate: str | None = None
    stage: str | None = None
    industry: str | None = None
    notes: str | None = None
    #: The identity anchor (M1D). Two entries that agree on a verified domain
    #: are one company, however differently they spell the name.
    canonical_domain: str | None = None
    #: Why this company is monitored, and why it was prioritised. Signal names
    #: only: the candidate's profile never appears in a registry file.
    discovery_source: str | None = None
    priority_reason: str | None = None
    boards: list[BoardEntry] = Field(min_length=1)


class CompanyRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    #: Marks the M1A seed set as an engineering dataset rather than a curated
    #: target list, so nobody later mistakes it for the search universe.
    purpose: str = "curated_registry"
    companies: list[CompanyEntry] = Field(min_length=1)

    @model_validator(mode="after")
    def _boards_have_one_owner(self) -> "CompanyRegistry":
        """A board belongs to exactly one company. Say so out loud.

        Board identifiers are frequently *derived* from a company domain, and a
        derived identifier is a guess. Two companies whose names collide will
        guess the same slug, and only one of them can be right.

        This was not hypothetical. `runway.com` (Runway Financial) and
        `runwayml.com` (Runway, the AI video company) both claimed Ashby board
        `runway`. Storage keys a board by (provider, identifier), so the
        conflict resolved itself silently: 237 registry boards became 236 stored
        boards, one company was left with no board at all, and the surviving row
        was attributed to the wrong company. Reading the board settled it -- the
        postings begin "Runway is a collaborative business planning platform" --
        but nothing in the pipeline had asked.

        Refusing to load is the right response rather than picking a winner.
        The registry is hand-curated configuration, the conflict is visible in
        the file, and a human has to decide which company owns the board. A
        wrong attribution is worse than a failed load: it quietly files another
        company's postings under a name we trust.
        """
        owners: dict[tuple[str, str], list[str]] = {}
        for entry in self.companies:
            for board in entry.boards:
                owners.setdefault((board.provider, board.board_identifier), []).append(entry.slug)

        conflicts = {key: slugs for key, slugs in owners.items() if len(slugs) > 1}
        if conflicts:
            lines = ["a board may be claimed by only one company:"]
            for (provider, identifier), slugs in sorted(conflicts.items()):
                lines.append(f"  {provider}/{identifier} claimed by: {', '.join(sorted(slugs))}")
            raise ValueError("\n".join(lines))
        return self


@dataclass(frozen=True)
class RegistryLoadResult:
    companies: int
    boards: int
    purpose: str


def load_registry_file(path: Path) -> CompanyRegistry:
    if not path.exists():
        raise ConfigError(f"{path} not found")
    try:
        parsed: Any = safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError(f"{path.name} must contain a mapping at the top level")
    try:
        return CompanyRegistry.model_validate(parsed)
    except ValidationError as exc:
        lines = [f"{path.name} is not valid:"]
        for error in exc.errors():
            location = ".".join(str(part) for part in error["loc"]) or "<document>"
            lines.append(f"  {location}: {error['msg']}")
        raise ConfigError("\n".join(lines)) from exc


def sync_registry(conn: Any, registry: CompanyRegistry) -> int:
    """Add the registry's boards that the database does not hold yet.

    The web app never called `apply_registry`, so a personal database created
    by the launcher held no ATS board at all and every board family read
    "Never run" forever (found 2026-09-25: 292 verified boards in the
    registry, 0 in the database). This closes that gap on every start.

    ADDITIVE ONLY. A board already present is never touched: `apply_registry`
    rewrites `active`, which would silently switch back on a board that was
    switched off after it stopped answering. Returns the number of boards added.
    """
    existing = {
        (str(r[0]), str(r[1]))
        for r in conn.execute("SELECT provider, board_identifier FROM source_board").fetchall()
    }
    missing = [
        entry
        for entry in registry.companies
        if any((b.provider, b.board_identifier) not in existing for b in entry.boards)
    ]
    if not missing:
        return 0
    companies = CompanyRepo(conn)
    boards = SourceBoardRepo(conn)
    added = 0
    for entry in missing:
        row = companies.get_by_slug(entry.slug)
        if row is None and entry.canonical_domain:
            # The same company held under another slug. Reused as it is:
            # `CompanyRepo.upsert` would rewrite its name and notes.
            row = conn.execute(
                "SELECT id FROM company WHERE canonical_domain = ?", (entry.canonical_domain,)
            ).fetchone()
        company_id = (
            str(row["id"])
            if row is not None
            else companies.upsert(
                CompanyRecord(
                    slug=entry.slug,
                    name=entry.name,
                    website=entry.website,
                    hq_country=entry.hq_country,
                    size_estimate=entry.size_estimate,
                    stage=entry.stage,
                    industry=entry.industry,
                    notes=entry.notes,
                    canonical_domain=entry.canonical_domain,
                    discovery_source=entry.discovery_source,
                    priority_reason=entry.priority_reason,
                )
            )
        )
        for board in entry.boards:
            if (board.provider, board.board_identifier) in existing:
                continue
            boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=board.provider,
                    board_identifier=board.board_identifier,
                    board_url=board.board_url,
                    active=board.active,
                    discovery_method=board.discovery_method,
                    verified_at=board.verified_at,
                )
            )
            existing.add((board.provider, board.board_identifier))
            added += 1
    return added


def sync_registry_file(conn: Any, config_dir: Any) -> int:
    """`sync_registry` for `config_dir/companies.yaml`, when that file exists."""
    path = Path(config_dir) / "companies.yaml"
    if not path.is_file():
        return 0
    from career_agent.storage.db import transaction

    registry = load_registry_file(path)
    with transaction(conn):
        return sync_registry(conn, registry)


def sync_registry_quietly(conn: Any, config_dir: Any) -> int:
    """`sync_registry_file`, where a broken registry must not stop anything.

    Called on launch and before Refresh all: a hand-edited `companies.yaml`
    that no longer parses leaves the boards already held exactly as they are
    and says so on the console, instead of refusing to start.
    """
    import logging

    try:
        return sync_registry_file(conn, config_dir)
    except (ConfigError, OSError) as exc:
        logging.getLogger(__name__).warning(
            "companies.yaml was not read, so no board was added: %s", type(exc).__name__
        )
        return 0


def apply_registry(conn: Any, registry: CompanyRegistry) -> RegistryLoadResult:
    """Upsert every company and board. Safe to run repeatedly."""
    companies = CompanyRepo(conn)
    boards = SourceBoardRepo(conn)

    board_count = 0
    for entry in registry.companies:
        company_id = companies.upsert(
            CompanyRecord(
                slug=entry.slug,
                name=entry.name,
                website=entry.website,
                hq_country=entry.hq_country,
                size_estimate=entry.size_estimate,
                stage=entry.stage,
                industry=entry.industry,
                notes=entry.notes,
                canonical_domain=entry.canonical_domain,
                discovery_source=entry.discovery_source,
                priority_reason=entry.priority_reason,
            )
        )
        for board in entry.boards:
            boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=board.provider,
                    board_identifier=board.board_identifier,
                    board_url=board.board_url,
                    active=board.active,
                    discovery_method=board.discovery_method,
                    verified_at=board.verified_at,
                )
            )
            board_count += 1

    return RegistryLoadResult(
        companies=len(registry.companies), boards=board_count, purpose=registry.purpose
    )
