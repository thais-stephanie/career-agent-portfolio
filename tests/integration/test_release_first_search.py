"""Neutral first-search writes: browser boundary, atomic recovery and no owner defaults."""

import shutil
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


@pytest.fixture
def first_api(tmp_path):
    config = tmp_path / "config"
    source = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(source, config, ignore=shutil.ignore_patterns("*.local.*"))
    conn = connect(tmp_path / "personal.db")
    migrate(conn)
    conn.close()
    return JobsApi(ServerConfig(db_path=tmp_path / "personal.db", config_dir=config), quiet=True)


@pytest.mark.parametrize(
    "country,phrase",
    [("BR", "calendar management"), ("BR", "customer service"), ("US", "customer onboarding")],
)
def test_first_search_preserves_candidate_choices(first_api, country, phrase):
    api = first_api
    api.handle_api(
        "PATCH",
        "/api/profile",
        {},
        {"changes": {"candidate_country": country, "eligible_countries": [country]}},
    )
    before, _ = load_search_config(api.config.config_dir)
    result = api.handle_api(
        "POST", "/api/first-search", {}, {"role_examples": [phrase], "skills": ["Spreadsheets"]}
    )
    assert result["saved"]
    after, path = load_search_config(api.config.config_dir)
    assert after.config_version > before.config_version
    assert after.eligibility == before.eligibility
    assert any(phrase in signal.patterns for signal in after.lexicon.values())
    assert not any(
        word in path.read_text(encoding="utf-8").lower()
        for word in ("hubspot", "salesforce", "thais")
    )
    assert path.with_suffix(path.suffix + ".backup").exists()
    saved = path.read_bytes()
    with pytest.raises(ApiError) as caught:
        api.handle_api(
            "POST", "/api/first-search", {}, {"role_examples": ["other work"], "skills": []}
        )
    assert caught.value.status == 409
    assert path.read_bytes() == saved


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"role_examples": [], "skills": []},
        {"role_examples": ["a" * 101], "skills": []},
        # The bounds the browser now explains line by line stay on the server.
        {"role_examples": [f"work {n}" for n in range(21)], "skills": []},
        {"role_examples": ["work"], "skills": ["b" * 101]},
        {"role_examples": "bad", "skills": []},
        {"role_examples": ["work"], "skills": [], "scoring": {}},
    ],
)
def test_invalid_first_search_writes_nothing(first_api, body):
    with pytest.raises(ApiError):
        first_api.handle_api("POST", "/api/first-search", {}, body)
    assert not (first_api.config.config_dir / "search.local.yaml").exists()


def test_malformed_private_config_start_explains_failure(first_api):
    from typer.testing import CliRunner

    from career_agent.cli import app

    path = first_api.config.config_dir / "search.local.yaml"
    path.write_text("eligibility: [broken", encoding="utf-8")
    result = CliRunner().invoke(
        app,
        [
            "serve",
            "--db",
            str(first_api.config.db_path),
            "--config-dir",
            str(first_api.config.config_dir),
            "--no-open",
        ],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "yaml" in result.output.lower() or "config" in result.output.lower()
    assert path.read_text(encoding="utf-8") == "eligibility: [broken"


def test_missing_config_explains_failure_without_creating_preferences(first_api):
    from typer.testing import CliRunner

    from career_agent.cli import app

    missing = first_api.config.config_dir.parent / "missing-config"
    result = CliRunner().invoke(
        app,
        ["serve", "--db", str(first_api.config.db_path), "--config-dir", str(missing), "--no-open"],
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert result.output.strip()
    assert not missing.exists()


@pytest.mark.parametrize("raises", [False, True])
def test_no_default_browser_prints_a_recovery_url(first_api, monkeypatch, capsys, raises):
    import webbrowser
    from types import SimpleNamespace

    from career_agent.cli_local import serve_command
    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.storage.db import transaction

    conn = connect(first_api.config.db_path)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "test")
    conn.close()

    def open_browser(url):
        if raises:
            raise webbrowser.Error("no browser")
        return False

    monkeypatch.setattr(webbrowser, "open", open_browser)
    monkeypatch.setattr(
        "career_agent.web.server.build_server",
        lambda api: SimpleNamespace(serve_forever=lambda: None, server_close=lambda: None),
    )
    serve_command(db=first_api.config.db_path, config_dir=first_api.config.config_dir)
    output = capsys.readouterr().out
    assert "No default browser could be opened" in output
    assert "http://127.0.0.1:8765/" in output
