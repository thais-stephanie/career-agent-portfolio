"""`config/companies.yaml` is live configuration, not documentation.

M1B.1 found that `sources.yaml` looked like configuration while being read by
nothing. The active company registry must not be the same kind of fiction: a
person editing that file has to change what the next collection run touches,
with no Python change and no hard-coded duplicate list.

These tests use a temporary registry file rather than the real one, so they
assert the mechanism rather than today's company count.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from career_agent.config.loader import ConfigError
from career_agent.config.registry import apply_registry, load_registry_file
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.storage.db import connect, migrate, transaction

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_REGISTRY = REPO_ROOT / "config" / "companies.yaml"

REGISTRY_TEMPLATE = """\
schema_version: 1
purpose: curated_registry
companies:
{companies}
"""

COMPANY_TEMPLATE = """\
  - slug: {slug}
    name: "{name}"
    canonical_domain: "{domain}"
    hq_country: "US"
    discovery_source: curated_technology_sourcing
    boards:
{boards}
"""

BOARD_TEMPLATE = """\
      - provider: {provider}
        board_identifier: "{identifier}"
        discovery_method: "domain_slug"
        verified_at: "2026-08-26T00:00:00Z"
"""


def write_registry(path: Path, companies: list[tuple[str, str, list[tuple[str, str]]]]) -> Path:
    blocks = [
        COMPANY_TEMPLATE.format(
            slug=slug,
            name=slug.title(),
            domain=domain,
            boards="".join(BOARD_TEMPLATE.format(provider=p, identifier=i) for p, i in boards),
        )
        for slug, domain, boards in companies
    ]
    path.write_text(REGISTRY_TEMPLATE.format(companies="".join(blocks)), encoding="utf-8")
    return path


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    yield connection
    connection.close()


def load(conn: sqlite3.Connection, path: Path) -> None:
    with transaction(conn):
        apply_registry(conn, load_registry_file(path))


def collect(conn: sqlite3.Connection, seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={"jobs": [], "apiVersion": "1"})

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0.0,
        backoff_seconds=0.0,
        sleep=lambda _s: None,
    )
    return Collector(conn, fetcher).collect_all(use_cache=False)


def test_editing_the_registry_changes_what_is_collected(conn, tmp_path) -> None:
    """The whole point. Add a company to the file, and the next run touches it."""
    path = write_registry(
        tmp_path / "companies.yaml", [("acme", "acme.com", [("greenhouse", "acme")])]
    )
    load(conn, path)
    first: list[str] = []
    collect(conn, first)
    assert len([u for u in first if "acme" in u]) == 1

    write_registry(
        path,
        [
            ("acme", "acme.com", [("greenhouse", "acme")]),
            ("beta", "beta.io", [("lever", "beta")]),
        ],
    )
    load(conn, path)
    second: list[str] = []
    stats = collect(conn, second)

    assert stats.boards_attempted == 2
    assert any("beta" in u for u in second), "the new company is collected"


def test_removing_a_board_from_the_registry_stops_collecting_it(conn, tmp_path) -> None:
    """Deactivation, not deletion: the company and its history stay, and the
    board simply stops being visited."""
    path = write_registry(
        tmp_path / "companies.yaml",
        [("acme", "acme.com", [("greenhouse", "acme"), ("lever", "acme")])],
    )
    load(conn, path)
    assert collect(conn, []).boards_attempted == 2

    with transaction(conn):
        conn.execute("UPDATE source_board SET active = 0 WHERE provider = 'lever'")
    urls: list[str] = []
    stats = collect(conn, urls)

    assert stats.boards_attempted == 1
    assert not any("lever" in u for u in urls)


def test_one_company_with_two_boards_stays_one_company(conn, tmp_path) -> None:
    """Migrations, acquisitions and separate business units all produce this.
    It must never become two companies -- every per-company count would be
    quietly wrong."""
    path = write_registry(
        tmp_path / "companies.yaml",
        [("acme", "acme.com", [("greenhouse", "acme"), ("ashby", "acme-labs")])],
    )
    load(conn, path)

    assert conn.execute("SELECT COUNT(*) n FROM company").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM source_board").fetchone()["n"] == 2
    assert collect(conn, []).boards_attempted == 2


def test_two_entries_sharing_a_domain_merge_into_one_company(conn, tmp_path) -> None:
    """Exact normalised domain match is strong identity evidence, so a registry
    that spells the slug two ways still describes one company."""
    path = write_registry(
        tmp_path / "companies.yaml",
        [
            ("acme", "acme.com", [("greenhouse", "acme")]),
            ("acme-inc", "acme.com", [("lever", "acme")]),
        ],
    )
    load(conn, path)

    assert conn.execute("SELECT COUNT(*) n FROM company").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM source_board").fetchone()["n"] == 2


def test_similar_names_on_different_domains_stay_separate(conn, tmp_path) -> None:
    """Name similarity is never identity evidence. A false merge destroys the
    evidence needed to undo it, which is worse than a visible duplicate."""
    path = write_registry(
        tmp_path / "companies.yaml",
        [
            ("acme", "acme.com", [("greenhouse", "acme")]),
            ("acmehealth", "acmehealth.com", [("greenhouse", "acmehealth")]),
            ("acmelabs", "acme-labs.io", [("greenhouse", "acmelabs")]),
        ],
    )
    load(conn, path)

    assert conn.execute("SELECT COUNT(*) n FROM company").fetchone()["n"] == 3


def test_two_companies_may_not_claim_the_same_board(tmp_path) -> None:
    """A derived board identifier is a guess, and two companies can guess the
    same one.

    This happened: runway.com (Runway Financial) and runwayml.com (Runway, the
    AI video company) both claimed Ashby board `runway`. Storage keys a board by
    (provider, identifier), so the conflict resolved itself invisibly -- 237
    registry boards became 236 stored boards, one company was left with none,
    and the surviving row named the wrong company. Reading the board settled it,
    but nothing had asked.

    Refusing the load is the right answer. Picking a winner would file one
    company's postings under another company's name, and nothing downstream
    could tell.
    """
    path = write_registry(
        tmp_path / "companies.yaml",
        [
            ("runway", "runway.com", [("ashby", "runway")]),
            ("runwayml", "runwayml.com", [("ashby", "runway")]),
        ],
    )

    with pytest.raises(ConfigError) as error:
        load_registry_file(path)

    message = str(error.value)
    assert "ashby/runway" in message
    assert "runway" in message and "runwayml" in message


def test_the_same_identifier_on_two_providers_is_not_a_conflict(tmp_path) -> None:
    """Board namespaces are per provider. `greenhouse/acme` and `lever/acme` are
    different boards that may legitimately belong to different companies."""
    path = write_registry(
        tmp_path / "companies.yaml",
        [
            ("acme", "acme.com", [("greenhouse", "acme")]),
            ("acme-io", "acme.io", [("lever", "acme")]),
        ],
    )

    registry = load_registry_file(path)

    assert len(registry.companies) == 2


# --- the real registry file -------------------------------------------------


def test_no_board_in_the_real_registry_is_claimed_twice() -> None:
    """The loader enforces this, so a passing load already proves it. Asserted
    separately anyway: this is the property the audit was about, and it should
    fail by name if the rule is ever relaxed."""
    registry = load_registry_file(REAL_REGISTRY)
    claims = [
        (board.provider, board.board_identifier)
        for entry in registry.companies
        for board in entry.boards
    ]

    assert len(claims) == len(set(claims))


def test_the_real_registry_is_valid_and_non_trivial() -> None:
    """It is the file that drives collection, so a syntax error in it is an
    outage rather than a typo."""
    registry = load_registry_file(REAL_REGISTRY)

    assert registry.purpose == "curated_registry"
    assert len(registry.companies) >= 150, "M1D acceptance: 150 active companies"
    assert all(entry.boards for entry in registry.companies)


def test_every_active_company_has_an_identity_anchor_and_provenance() -> None:
    registry = load_registry_file(REAL_REGISTRY)

    assert all(entry.canonical_domain for entry in registry.companies)
    assert all(entry.discovery_source for entry in registry.companies)

    domains = [entry.canonical_domain for entry in registry.companies]
    assert len(domains) == len(set(domains)), "a domain identifies exactly one company"
    slugs = [entry.slug for entry in registry.companies]
    assert len(slugs) == len(set(slugs))


def test_every_board_records_how_and_when_it_was_verified() -> None:
    """A company is in the registry because a real board answered on a recorded
    date -- never because a list said so."""
    registry = load_registry_file(REAL_REGISTRY)
    boards = [board for entry in registry.companies for board in entry.boards]

    assert all(board.discovery_method for board in boards)
    assert all(board.verified_at for board in boards)

    # DERIVED, not listed. This read `{"greenhouse", "lever", "ashby"}` and
    # Recruiterflow became a fourth family on 2026-09-09, so a correct registry
    # failed a test about provenance for a reason that had nothing to do with
    # provenance.
    #
    # `board_providers()` is the right question anyway, and a stronger one: the
    # registry may only name providers this runner can actually ASK about. A
    # row for a feed provider would be a board `collect` skips forever, which
    # is exactly the confusion the planner fix was about.
    from career_agent.providers.registry import board_providers

    assert {board.provider for board in boards} <= set(board_providers())


def test_the_registry_contains_no_candidate_private_data() -> None:
    """Company data is public and git-tracked; the candidate's is not. A
    registry entry may say why a company was prioritised, never anything about
    the person searching."""
    text = REAL_REGISTRY.read_text(encoding="utf-8").lower()

    for leak in ("salary", "compensation", "residence", "candidate_key", "passport", "visa_status"):
        assert leak not in text, f"{leak!r} has no business in a company registry"


def test_the_seed_registry_is_still_separate() -> None:
    """The engineering validation set does not move every time the real registry
    grows, so provider regression tests keep a stable, deliberately awkward
    corpus."""
    seed = load_registry_file(REPO_ROOT / "config" / "companies.seed.yaml")

    assert seed.purpose == "engineering_validation_only"
    assert len(seed.companies) < len(load_registry_file(REAL_REGISTRY).companies)
