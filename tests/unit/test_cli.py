"""Acceptance criteria 9 and 15: the CLI commands, exercised end to end.

These run the real commands against a throwaway config directory and database.
Nothing here reaches the network, and no API key is required - which is the
property M0 exists to establish.
"""

import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from career_agent.cli import app
from career_agent.storage.db import connect, discover_migrations, schema_version

#: Derived, never a literal: a new migration must not break unrelated CLI tests.
LATEST_SCHEMA_VERSION = max(m.version for m in discover_migrations())

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_CONFIG_DIR = REPO_ROOT / "config"

runner = CliRunner()


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway project: example configs copied to .local.yaml, empty db."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    for stem in ("profile", "career_facts"):
        shutil.copy(REAL_CONFIG_DIR / f"{stem}.example.yaml", config_dir / f"{stem}.example.yaml")
        shutil.copy(REAL_CONFIG_DIR / f"{stem}.example.yaml", config_dir / f"{stem}.local.yaml")
    # Isolate the test from whatever the developer has configured. Two parts,
    # and the second is the one that actually bites:
    #
    #   1. Clear every vendor key from the environment.
    #   2. Stop `doctor` reading the repository's real `.env`.
    #
    # `load_dotenv()` searches upward from the *calling module's* directory,
    # not from the working directory, so it finds `CareerAgent/.env` however a
    # test is invoked and from wherever. The moment a real key existed on this
    # machine, `test_doctor_reports_a_missing_vendor_credential_as_missing`
    # started failing -- correctly, and for a reason that had nothing to do with
    # the code under test.
    #
    # The subtler half is that the *other* credential test would have started
    # passing for the wrong reason: it asserts PRESENT after planting a fake
    # key, and a real `.env` makes that true regardless. A suite whose result
    # depends on the developer's local configuration is not a suite.
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "GOOGLE_API_KEY",
        "GEMINI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("career_agent.cli.load_dotenv", lambda *a, **k: False)
    return tmp_path


def _run(workspace: Path, *args: str) -> object:
    return runner.invoke(
        app,
        [
            *args,
            "--config-dir",
            str(workspace / "config"),
            "--db",
            str(workspace / "data" / "career.db"),
        ],
    )


# --- migrate ---------------------------------------------------------------


def test_migrate_creates_the_database(workspace: Path) -> None:
    result = runner.invoke(app, ["migrate", "--db", str(workspace / "data" / "career.db")])
    assert result.exit_code == 0, result.output
    assert "applied 0001_init" in result.output
    assert "applied 0002_company_identity" in result.output

    conn = connect(workspace / "data" / "career.db")
    try:
        assert schema_version(conn) == LATEST_SCHEMA_VERSION
    finally:
        conn.close()


def test_migrate_twice_is_a_no_op(workspace: Path) -> None:
    db = str(workspace / "data" / "career.db")
    runner.invoke(app, ["migrate", "--db", db])
    result = runner.invoke(app, ["migrate", "--db", db])
    assert result.exit_code == 0
    assert "already up to date" in result.output


# --- init ------------------------------------------------------------------


def test_init_registers_the_candidate_and_snapshots_the_profile(workspace: Path) -> None:
    result = _run(workspace, "init")
    assert result.exit_code == 0, result.output
    assert "candidate      : example" in result.output
    assert "claims added   : 2" in result.output

    conn = connect(workspace / "data" / "career.db")
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM candidate").fetchone()["n"] == 1
        snapshot = conn.execute("SELECT yaml_snapshot FROM search_profile_version").fetchone()[
            "yaml_snapshot"
        ]
        assert "candidate_key: example" in snapshot
    finally:
        conn.close()


def test_init_is_idempotent(workspace: Path) -> None:
    """Running the pipeline repeatedly must not duplicate versions or claims."""
    _run(workspace, "init")
    result = _run(workspace, "init")
    assert result.exit_code == 0
    assert "claims added   : 0" in result.output

    conn = connect(workspace / "data" / "career.db")
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM search_profile_version").fetchone()["n"] == 1
        assert conn.execute("SELECT COUNT(*) AS n FROM verified_claim").fetchone()["n"] == 2
    finally:
        conn.close()


def test_init_without_a_local_profile_fails_with_instructions(workspace: Path) -> None:
    (workspace / "config" / "profile.local.yaml").unlink()
    result = _run(workspace, "init")
    assert result.exit_code == 1
    assert "profile.local.yaml" in result.output
    assert "Copy-Item" in result.output


# --- doctor ----------------------------------------------------------------


def test_doctor_reports_a_healthy_project(workspace: Path) -> None:
    _run(workspace, "init")
    result = _run(workspace, "doctor")
    assert result.exit_code == 0, result.output

    assert "profile      : OK" in result.output
    assert f"schema     : version {LATEST_SCHEMA_VERSION}" in result.output
    assert "pending    : none" in result.output
    assert "candidate" in result.output
    assert "everything checks out" in result.output


def test_doctor_names_the_file_it_actually_read(workspace: Path) -> None:
    """A system running on the wrong configuration looks completely normal from
    the outside, so doctor always says which file it used."""
    _run(workspace, "init")
    result = _run(workspace, "doctor")
    assert "profile.local.yaml" in result.output


def test_doctor_flags_a_missing_database(workspace: Path) -> None:
    result = _run(workspace, "doctor")
    assert result.exit_code == 1
    assert "NOT CREATED" in result.output
    assert "attention needed" in result.output


def test_doctor_flags_invalid_config_without_crashing(workspace: Path) -> None:
    path = workspace / "config" / "profile.local.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("residence_country: BR", "residence_country: BRZ"),
        encoding="utf-8",
    )
    result = _run(workspace, "doctor")
    assert result.exit_code == 1
    assert "INVALID or MISSING" in result.output
    assert "candidate_geography.residence_country" in result.output


def test_doctor_reports_key_presence_without_printing_it(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PRESENT or MISSING, per vendor, and never the value.

    Reports every vendor rather than only Anthropic, because the benchmark is
    multi-vendor by design and "is the free Gemini arm runnable?" was a question
    the tool could not answer at all. A secret is planted in each variable and
    the whole output is checked for it: a doctor that leaked a key while
    reporting on keys would be a particularly bad way to find out.
    """
    secret = "sk-secret-value-that-must-not-leak"
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.setenv(name, f"{secret}-{name}")

    _run(workspace, "init")
    result = _run(workspace, "doctor")

    assert "ANTHROPIC        : PRESENT" in result.output
    assert "OPENAI           : PRESENT" in result.output
    assert "GOOGLE / GEMINI  : PRESENT" in result.output
    assert secret not in result.output


def test_doctor_reports_a_missing_vendor_credential_as_missing(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The state that actually blocks a benchmark arm, named plainly.

    Silence about a credential reads as "fine". MISSING reads as what it is,
    and it is the difference between "the free arm is ready to run" and
    "the free arm cannot be attempted yet".
    """
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    _run(workspace, "init")
    result = _run(workspace, "doctor")

    assert "GOOGLE / GEMINI  : MISSING" in result.output


def test_doctor_makes_no_network_call(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`doctor` prints that it made no network call, so it had better be true.

    At M0 this was asserted by httpx not being installed at all. M1A installs an
    HTTP client, so the guarantee is now enforced directly: every outbound
    request is made to explode, and doctor must still succeed.
    """
    import httpx

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("doctor attempted a network request")

    monkeypatch.setattr(httpx.Client, "send", explode)

    _run(workspace, "init")
    result = _run(workspace, "doctor")

    assert result.exit_code == 0, result.output
    assert "no network calls and no LLM calls were made" in result.output


def test_export_names_the_database_when_the_jobs_are_not_in_it(workspace: Path) -> None:
    """The one-flag mistake that would have sent Stage 0 at an empty database.

    `--db` defaults to `data/career.db`, which on a fresh checkout is empty and
    still at schema version 1. The corpus lives in `data/m1d2/career.db`.
    Omitting the flag used to fail with `job '01M0…' has no archived description
    text`: a message about a job, for a problem about a database, which sends
    someone looking at collection instead of at their command line.

    Now the error names the database, its row count, and where the corpus is.
    """
    # Invoked directly: this command takes --db but not --config-dir.
    result = runner.invoke(
        app,
        [
            "export-extraction-batch",
            "--job-id",
            "01M0XZHTNYCEDGDV3JHD7C65EW",
            "--db",
            str(workspace / "data" / "career.db"),
            "--out",
            str(workspace / "batch"),
        ],
    )

    # Typer boxes and wraps the message, so compare on collapsed whitespace.
    output = " ".join(result.output.replace("│", " ").split())

    assert result.exit_code != 0
    assert "not in this database" in output
    assert "0 job(s) in total" in output
    assert "data/m1d2/career.db" in output


def test_doctor_says_which_database_and_whether_it_is_the_one_you_meant(
    workspace: Path,
) -> None:
    """The single most common way to be confused by this product.

    Every extraction command defaults to `data/career.db`, which is empty, and
    the real corpus lives somewhere the flag has to name. Doctor now says so
    when a larger database is sitting next to the empty one.
    """
    _run(workspace, "init")
    bigger = workspace / "data" / "m1d2"
    bigger.mkdir(parents=True, exist_ok=True)
    (bigger / "career.db").write_bytes(b"x" * 10_000_000)

    result = _run(workspace, "doctor")
    assert "this database is empty" in result.output
    assert "Did you mean one of these?" in result.output
    assert "m1d2" in result.output


def test_an_empty_database_on_a_fresh_install_is_not_a_fault(workspace: Path) -> None:
    """Doctor failing on a new project is how somebody concludes the install
    went wrong and starts over. An empty database moments after `init` is the
    correct state, and the only thing that makes it suspicious is a larger one
    beside it."""
    _run(workspace, "init")
    result = _run(workspace, "doctor")
    assert result.exit_code == 0, result.output
    assert "expected before the first collection" in result.output
    assert "Did you mean" not in result.output


# =========================================================================
# `start`: the one command, and the answer it must not second-guess
# =========================================================================


def test_start_opens_the_database_that_was_stated_even_when_it_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stated answer does not get overridden by a helpful default.

    The first version of `start` swapped an empty `CAREER_AGENT_DB` for
    whichever other database had postings. That is the same mistake as letting
    a sentence of prose overrule a field an employer filled in: somebody said
    which database they meant, and the product substituted another.
    """
    from career_agent import cli_local

    empty = tmp_path / "empty.db"
    full = tmp_path / "full.db"
    monkeypatch.setattr(cli_local, "_personal_databases", lambda: [(full, 4_000)])
    monkeypatch.setenv("CAREER_AGENT_DB", str(empty))

    served: dict[str, object] = {}
    monkeypatch.setattr(cli_local, "serve_command", lambda **kw: served.update(kw))
    monkeypatch.setattr(cli_local, "_open_personal", lambda path: _FakeConn(path))

    result = runner.invoke(app, ["start", "--no-open"])

    assert result.exit_code == 0, result.output
    assert served["db"] == empty
    assert "because you asked for it" in result.output
    # And it says where the postings actually are, rather than leaving somebody
    # to conclude the setup failed.
    assert str(full) in result.output


def test_start_falls_back_only_when_nothing_was_stated(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An empty default beside one corpus is the single most common way to be
    confused by this product, and nobody chose the default."""
    from career_agent import cli_local
    from career_agent.runtime import mode

    empty = tmp_path / "empty.db"
    full = tmp_path / "full.db"
    monkeypatch.delenv("CAREER_AGENT_DB", raising=False)
    monkeypatch.setattr(mode, "DEFAULT_PERSONAL_DB_PATH", empty)
    monkeypatch.setattr(cli_local, "_personal_databases", lambda: [(full, 4_000)])

    served: dict[str, object] = {}
    monkeypatch.setattr(cli_local, "serve_command", lambda **kw: served.update(kw))
    monkeypatch.setattr(cli_local, "_open_personal", lambda path: _FakeConn(path))

    result = runner.invoke(app, ["start", "--no-open"])

    assert result.exit_code == 0, result.output
    assert served["db"] == full
    assert "the only one that does" in result.output


def test_start_refuses_to_choose_between_two_populated_databases(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Guessing here is how invented rows end up somewhere real."""
    from career_agent import cli_local
    from career_agent.runtime import mode

    empty = tmp_path / "empty.db"
    monkeypatch.delenv("CAREER_AGENT_DB", raising=False)
    monkeypatch.setattr(mode, "DEFAULT_PERSONAL_DB_PATH", empty)
    monkeypatch.setattr(
        cli_local,
        "_personal_databases",
        lambda: [(tmp_path / "a.db", 10), (tmp_path / "b.db", 20)],
    )
    monkeypatch.setattr(
        cli_local, "serve_command", lambda **kw: pytest.fail("served without being told which")
    )

    result = runner.invoke(app, ["start", "--no-open"])

    assert result.exit_code == 1
    assert "Say which" in result.output


class _FakeConn:
    """Just enough of a connection for `start`'s three counts."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def execute(self, sql: str, *args: object):  # noqa: ANN401
        del sql, args
        return _FakeCursor()

    def close(self) -> None:
        return None


class _FakeCursor:
    def fetchone(self):  # noqa: ANN201
        return {"n": 0, 0: 0}


# =========================================================================
# `discover-remotesource`: preflight by default, and the default fetches nothing
# =========================================================================


def test_discover_remotesource_preflight_makes_no_network_call(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading two thousand employers' pages is a decision, and it costs
    typing `--execute`. Without it the plan is printed and nothing leaves."""
    import httpx

    def explode(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the preflight attempted a network request")

    monkeypatch.setattr(httpx.Client, "send", explode)

    _run(workspace, "init")
    # `init` claims the workspace database as personal; the command resolves
    # it by the same `--db` and takes no `--config-dir`.
    result = runner.invoke(
        app,
        [
            "discover-remotesource",
            "--max-employers",
            "7",
            "--db",
            str(workspace / "data" / "career.db"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "PREFLIGHT. Nothing was fetched." in result.output
    assert "employers this run, at most" in result.output
    assert "workday" in result.output and "recruitee" in result.output
