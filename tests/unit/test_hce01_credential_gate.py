"""HCE01: the generic credential hard-requirement gate.

The owner's ratified constraint (OWNER_CONSTRAINTS.md of OWNER_RATIFIED_V2):
exclude a posting when the employer explicitly REQUIRES a credential the
candidate does not hold. Generic mechanism, never a Salesforce special
case: `credential` joins the gate vocabulary, a blocker declared on it is
matched like every other blocker (quoted, negation respected), silence is
the absence of a disqualification, and the loader refuses a blocker on a
gate the matcher does not evaluate, which is the defect HCE01 found
(`gate: credential` used to load and close nothing).

The owner's EXCLUDE / DO-NOT-EXCLUDE examples are the acceptance set,
read from `tests/fixtures/config/hce01-credential-blocker.yaml`, which is
also the worked example of what she adds to her private configuration.
Nothing here reads `config/search.local.yaml`; nothing writes any
configuration or database.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from pathlib import Path

import pytest
import yaml

from career_agent.config.search_config import Blocker, SearchConfigError, load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.domain.matching import GATE_NAMES, MATCH_SCHEMA_VERSION, GateOutcome
from career_agent.match.gates import (
    CRITICAL_GATES,
    GATE_ORDER,
    UNRESOLVED_REASONS,
    eligibility_status_from,
    evaluate_gates,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests" / "fixtures" / "config" / "hce01-credential-blocker.yaml"


@pytest.fixture(scope="module")
def fixture() -> dict:
    return yaml.safe_load(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def committed_config():
    """The committed worked example, copied so no local file can override it."""
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    loaded, _ = load_search_config(directory, use_example=True)
    return loaded


@pytest.fixture(scope="module")
def config_with_hce01(committed_config, fixture: dict):
    """The committed configuration plus the owner's credential blocker, in
    memory: exactly what her private file would hold, written nowhere."""
    blocker = Blocker.model_validate(fixture["blocker"])
    config = committed_config.model_copy(deep=True)
    config.eligibility.blockers.append(blocker)
    return config


def _gate(gates: tuple[GateOutcome, ...], name: str) -> GateOutcome:
    return next(g for g in gates if g.gate == name)


# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------
def test_credential_is_a_gate_and_the_vocabulary_is_shared() -> None:
    assert GATE_ORDER == GATE_NAMES
    assert GATE_ORDER[5:] == ("credential", "requirement") and len(GATE_ORDER) == 7
    assert set(UNRESOLVED_REASONS) == set(GATE_ORDER)
    assert UNRESOLVED_REASONS["credential"].startswith("The posting does not state")
    assert "credential" not in CRITICAL_GATES, "silence is the absence of a disqualification"
    assert "requirement" not in CRITICAL_GATES
    assert MATCH_SCHEMA_VERSION == 9, "the gates tuple gained members"


def test_a_typed_hard_exclusion_closes_a_gate_now(tmp_path: Path) -> None:
    """`career-agent setup` files a hard exclusion on `requirement`. It used
    to write `other`, which no code evaluated: the phrase loaded and closed
    nothing. The loader refuses that name now, and the written blocker fails
    the gate on a posting that states the phrase."""
    from career_agent.config.setup import Answers, run_setup

    directory = tmp_path / "config"
    directory.mkdir()
    for name in ("search.starter.yaml", "search.worked-example.yaml", "places.yaml"):
        shutil.copy(ROOT / "config" / name, directory)
    run_setup(directory, Answers(hard_exclusions=("must relocate to Zurich",)))
    written = yaml.safe_load((directory / "search.local.yaml").read_text(encoding="utf-8"))
    typed = [b for b in written["eligibility"]["blockers"] if "zurich" in b["id"]]
    assert typed and typed[0]["gate"] == "requirement"
    config, _ = load_search_config(directory)
    gates = evaluate_gates(
        config, {}, "Analyst", "Requirements\n- You must relocate to Zurich within a year.\n"
    )
    gate = _gate(gates, "requirement")
    assert gate.result is GateResult.FAIL and gate.quote
    assert eligibility_status_from(gates) is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    silent = evaluate_gates(config, {}, "Analyst", "Requirements\n- Five years of Python.\n")
    assert _gate(silent, "requirement").result is GateResult.UNRESOLVED
    assert _gate(silent, "requirement").reason == UNRESOLVED_REASONS["requirement"]


def test_loader_refuses_a_blocker_on_an_unknown_gate() -> None:
    with pytest.raises(Exception, match="unknown gate 'credentail'"):
        Blocker(id="x", label="y", gate="credentail", patterns=["a b"])
    for gate in GATE_NAMES:
        assert Blocker(id="x", label="y", gate=gate, patterns=["a b"]).gate == gate
    # Through the file loader as well: a misspelt gate is a refused file,
    # never a blocker that loads and closes nothing.
    directory = Path(tempfile.mkdtemp())
    raw = yaml.safe_load((ROOT / "config" / "search.worked-example.yaml").read_text("utf-8"))
    raw["eligibility"]["blockers"].append(
        {"id": "typo", "label": "typo", "gate": "credentials", "patterns": ["x y"]}
    )
    (directory / "search.worked-example.yaml").write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    with pytest.raises(SearchConfigError, match="unknown gate"):
        load_search_config(directory, use_example=True)


def test_every_committed_blocker_is_on_a_known_gate(committed_config) -> None:
    for blocker in committed_config.eligibility.blockers:
        assert blocker.gate in GATE_NAMES
    assert not any(b.gate == "credential" for b in committed_config.eligibility.blockers), (
        "the committed starter assumes no credential of anybody"
    )


# ---------------------------------------------------------------------------
# The owner's acceptance set
# ---------------------------------------------------------------------------
def test_owner_exclude_examples_fail_the_gate_with_a_quote(config_with_hce01, fixture) -> None:
    for sentence in fixture["exclude"]:
        body = f"About the role.\nWe build CRM integrations.\nRequirements\n- {sentence}.\n"
        gates = evaluate_gates(config_with_hce01, {}, "Business Systems Analyst", body)
        gate = _gate(gates, "credential")
        assert gate.result is GateResult.FAIL, sentence
        assert gate.blocker_id == "salesforce_certification_required"
        assert gate.quote and sentence.lower() in gate.quote.lower()
        assert body[gate.char_start : gate.char_end].lower() in sentence.lower()
        assert eligibility_status_from(gates) is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_owner_do_not_exclude_examples_leave_the_gate_unresolved(
    config_with_hce01, fixture
) -> None:
    for sentence in fixture["do_not_exclude"]:
        body = f"About the role.\nWe build CRM integrations.\nRequirements\n- {sentence}.\n"
        gates = evaluate_gates(config_with_hce01, {}, "Business Systems Analyst", body)
        gate = _gate(gates, "credential")
        assert gate.result is GateResult.UNRESOLVED, sentence
        assert gate.quote is None and gate.blocker_id is None
        assert gate.reason == UNRESOLVED_REASONS["credential"]
        assert eligibility_status_from(gates) is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_negation_is_respected_inside_the_sentence(config_with_hce01) -> None:
    negated = "A Salesforce certification is not required for this role."
    gates = evaluate_gates(config_with_hce01, {}, "Analyst", f"Requirements\n- {negated}\n")
    assert _gate(gates, "credential").result is GateResult.UNRESOLVED
    # The previous bullet's negation says nothing about this one.
    two = "- No travel required.\n- Salesforce certification required.\n"
    gates = evaluate_gates(config_with_hce01, {}, "Analyst", f"Requirements\n{two}")
    assert _gate(gates, "credential").result is GateResult.FAIL


def test_silence_is_not_a_disqualification(config_with_hce01) -> None:
    body = (
        "We hire anywhere in the world.\nResponsibilities\n- Own the CRM.\n"
        "Requirements\n- Five years with business systems.\n"
    )
    gates = evaluate_gates(config_with_hce01, {}, "Analyst", body)
    credential = _gate(gates, "credential")
    assert credential.result is GateResult.UNRESOLVED and credential.quote is None
    status = eligibility_status_from(gates)
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    # And with the geography gate open, an unresolved credential gate does
    # not stand in the way of VERIFIED_ELIGIBLE: only a FAIL refuses.
    if _gate(gates, "geography").result is GateResult.PASS:
        assert status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_without_the_blocker_the_gate_exists_and_never_fails(committed_config, fixture) -> None:
    """The gate is generic: with no credential blocker configured, every
    posting is UNRESOLVED on it, including the owner's EXCLUDE examples."""
    for sentence in fixture["exclude"]:
        gates = evaluate_gates(committed_config, {}, "Analyst", f"Requirements\n- {sentence}.\n")
        gate = _gate(gates, "credential")
        assert gate.result is GateResult.UNRESOLVED and gate.quote is None


def test_a_credential_fail_reads_like_the_other_exclusionary_gates(config_with_hce01) -> None:
    gates = evaluate_gates(
        config_with_hce01, {}, "Analyst", "Requirements\n- Salesforce certification required.\n"
    )
    gate = _gate(gates, "credential")
    assert gate.reason.endswith("The posting states it in so many words.")
    assert [g.gate for g in gates] == list(GATE_ORDER)


# ---------------------------------------------------------------------------
# Boundaries
# ---------------------------------------------------------------------------
def test_search_fit_names_no_gate() -> None:
    """HCE01 lives in eligibility; Functional Alignment never reads it."""
    package = ROOT / "src" / "career_agent" / "searchfit"
    if not package.exists():
        # `main` carries no Search Fit package (the lane is unmerged; the
        # research program is closed). The rule stands for the day it lands.
        return
    for path in package.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("career_agent.match"), path.name
        strings = {
            n.value
            for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        assert "credential" not in strings and "salesforce" not in strings, path.name


def test_engine_names_no_credential() -> None:
    """The generic engine knows the gate and not the credential."""
    for rel in ("src/career_agent/match/gates.py", "src/career_agent/config/search_config.py"):
        source = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "salesforce" not in source, rel
    for rel in ("config/search.starter.yaml", "config/search.worked-example.yaml"):
        assert "gate: credential" not in (ROOT / rel).read_text(encoding="utf-8"), rel


# ---------------------------------------------------------------------------
# The integration matrix (docs/checkpoints/hce01-integration.md), ADR-0029
# ---------------------------------------------------------------------------
def test_a_credential_blocker_round_trips_through_a_file_as_gate_credential(
    tmp_path: Path, fixture: dict
) -> None:
    """Matrix 2: a configured credential exclusion serialises to
    `gate: credential`, loads through the real loader from a file, and the
    loaded blocker is the one written; the private file is the only place it
    is meant to live and nothing in the repository writes it there."""
    directory = tmp_path / "config"
    directory.mkdir()
    for name in ("search.worked-example.yaml", "places.yaml"):
        shutil.copy(ROOT / "config" / name, directory)
    data = yaml.safe_load((directory / "search.worked-example.yaml").read_text(encoding="utf-8"))
    data["eligibility"]["blockers"].append(dict(fixture["blocker"]))
    data["config_version"] = int(data["config_version"]) + 1
    (directory / "search.local.yaml").write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8"
    )
    written = yaml.safe_load((directory / "search.local.yaml").read_text(encoding="utf-8"))
    mine = [b for b in written["eligibility"]["blockers"] if b["gate"] == "credential"]
    assert len(mine) == 1 and mine[0]["id"] == fixture["blocker"]["id"]
    config, path = load_search_config(directory)
    assert path.name == "search.local.yaml"
    loaded = [b for b in config.eligibility.blockers if b.gate == "credential"]
    assert len(loaded) == 1
    assert loaded[0].patterns == fixture["blocker"]["patterns"]
    assert loaded[0].negation_sensitive is True
    gates = evaluate_gates(config, {}, "Analyst", "Salesforce certification required.\n")
    assert _gate(gates, "credential").result is GateResult.FAIL


def test_workflow_state_reaches_no_gate() -> None:
    """Matrix 8: a gate is a function of the configuration and the posting's
    text and structured fields. `match.gates` imports nothing from storage,
    and `evaluate_gates` has no parameter through which an application
    status, a shortlist or a hide could arrive."""
    import inspect

    source = (ROOT / "src" / "career_agent" / "match" / "gates.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("career_agent.storage"), node.module
            assert not node.module.startswith("career_agent.web"), node.module
    params = set(inspect.signature(evaluate_gates).parameters)
    for forbidden in ("status", "application", "workflow", "tracking", "hidden", "shortlist"):
        assert not any(forbidden in p for p in params), params


def test_posting_facts_stay_candidate_independent() -> None:
    """Matrix 9 (invariant 4): the posting's derived facts know no
    configuration and no gate; a gate outcome lives in `job_match`, never in
    `JobFacts` or the fingerprint."""
    source = (ROOT / "src" / "career_agent" / "pipeline" / "facts.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module != "career_agent.config.search_config", node.module
            assert node.module != "career_agent.match.gates", node.module
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    assert "GATE_NAMES" not in names and "evaluate_gates" not in names


def test_the_committed_configurations_declare_exactly_what_they_mean_to(tmp_path: Path) -> None:
    """Matrix 11 and 12: the starter is neutral (no blocker at all: no
    credential is assumed of anybody); the worked example is somebody's real
    search and declares one blocker on each of the five named gates and none
    on the two new ones; both load under the gate validator."""
    directory = tmp_path / "config"
    directory.mkdir()
    for name in ("search.starter.yaml", "search.worked-example.yaml", "places.yaml"):
        shutil.copy(ROOT / "config" / name, directory)
    starter, _ = load_search_config(directory)
    assert starter.eligibility.blockers == []
    example, _ = load_search_config(directory, use_example=True)
    gates = sorted(b.gate for b in example.eligibility.blockers)
    assert gates == ["clearance", "geography", "travel", "work_authorization", "worksite"]
    assert "credential" not in gates and "requirement" not in gates
    assert set(gates) < set(GATE_NAMES)


def test_no_private_configuration_is_tracked() -> None:
    """Matrix 10: the owner's private file (where her credential blocker
    belongs) is ignored by name, so it can never be committed by accident."""
    import subprocess

    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "config/*.local.yaml" in ignore
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files", "config/search.local.yaml", "config/profile.local.yaml"],
            cwd=ROOT,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git not available")
    assert tracked.strip() == ""


def test_the_schema_and_the_reader_identity_moved_together() -> None:
    """ADR-0029 section 11: the shape of a stored result moved (seven gate
    outcomes) and what the gates produce moved; both identities say so, in
    the same build."""
    from career_agent.match.identity import READER_IDENTITY

    assert MATCH_SCHEMA_VERSION == 9
    assert READER_IDENTITY == "readers-3"
    assert len(GATE_NAMES) == 7 and GATE_ORDER is GATE_NAMES
