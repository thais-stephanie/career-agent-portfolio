"""The punctuation gate, run from pytest so it cannot quietly stop being run.

Same discipline as `test_frontend_gate.py`: the gate has to pass, AND it has to
be shown to catch something. A checker that returns clean on every input is
indistinguishable from no checker at all, and this one would be especially easy
to break by accident, because the character it looks for is invisible in a diff
review at a glance.

The exclusion list gets its own tests. It is the only part of the gate that can
make a real problem disappear, so it is the part that has to be kept honest: a
path that no longer exists is a hole nobody would notice.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import punctuation_check as guard  # noqa: E402  -- path is set up above

EM = guard.EM_DASH
EN = guard.EN_DASH


# =========================================================================
# the gate itself
# =========================================================================


def test_project_authored_text_carries_no_long_dashes() -> None:
    result = guard.scan_repository(REPO_ROOT)
    assert result.ok, "long dashes in project-authored text:\n" + "\n".join(
        finding.render() for finding in result.findings[:40]
    )


def test_the_gate_reads_a_meaningful_number_of_files() -> None:
    """A gate that scans nothing passes everything.

    The exact number moves with the repository; the point of the assertion is
    that `tracked_files` still returns a tree rather than an empty list, which
    is what a broken `git ls-files` or a wrong root would produce.
    """
    result = guard.scan_repository(REPO_ROOT)
    assert result.files_scanned > 100


@pytest.mark.parametrize("character", [EM, EN], ids=["em-dash", "en-dash"])
def test_the_gate_catches_a_planted_dash(character: str) -> None:
    findings, allowances = guard.scan_text("probe.md", f"a sentence {character} and its tail\n")
    assert not allowances
    assert len(findings) == 1
    assert findings[0].character == character
    assert findings[0].line_number == 1
    assert character in findings[0].render()


def test_a_finding_points_at_the_exact_column() -> None:
    """`file:line:column` is what makes the output actionable rather than a list."""
    findings, _ = guard.scan_text("probe.md", "0123456" + EM + "89")
    assert findings[0].column == 8


# =========================================================================
# the line-level opt-out
# =========================================================================


def test_an_allowed_line_is_not_a_finding() -> None:
    text = f'"{EM}": "-",  # {guard.ALLOW_MARKER}: the character normalised here\n'
    findings, allowances = guard.scan_text("probe.py", text)
    assert not findings
    assert len(allowances) == 1
    assert allowances[0].has_reason


def test_the_marker_also_works_on_the_line_above() -> None:
    """A data line can already be near the line-length limit.

    Forcing the reason onto the same line made ruff fail on six real fixtures,
    and a gate whose only remedy breaks another gate is one people delete.
    """
    text = f"# {guard.ALLOW_MARKER}: employer-written fixture\nsalary = '10{EN}20%'\n"
    findings, allowances = guard.scan_text("probe.py", text)
    assert not findings
    assert len(allowances) == 1


def test_an_unexplained_allowance_is_reported() -> None:
    """The opt-out has to cost a sentence, or it becomes the default."""
    findings, allowances = guard.scan_text("probe.py", f'"{EM}"  # {guard.ALLOW_MARKER}\n')
    assert not findings
    assert allowances and not allowances[0].has_reason


def test_every_allowance_in_this_repository_explains_itself() -> None:
    result = guard.scan_repository(REPO_ROOT)
    unexplained = [a for a in result.allowances if not a.has_reason]
    assert not unexplained, "allowances with no reason: " + ", ".join(
        f"{a.path}:{a.line_number}" for a in unexplained
    )
    assert result.allowances, "the opt-out exists and is used; if it stops being used, delete it"


# =========================================================================
# the exclusion list, which is the only thing that can hide a real problem
# =========================================================================


def test_every_exclusion_still_points_at_something() -> None:
    """A stale exclusion is a hole in the gate that nobody can see."""
    for exclusion in guard.EXCLUSIONS:
        assert (REPO_ROOT / exclusion.prefix).exists(), (
            f"{exclusion.prefix} is excluded from the punctuation gate but no longer exists. "
            "Delete the exclusion rather than leaving it to cover a future file."
        )


def test_every_exclusion_records_why() -> None:
    for exclusion in guard.EXCLUSIONS:
        assert len(exclusion.reason.split()) >= 8, (
            f"{exclusion.prefix} is excluded without a real reason. "
            "Excluding a path is a decision about provenance, not a way to quiet the gate."
        )


def test_the_exclusions_cover_the_files_that_must_not_be_rewritten() -> None:
    """The three provenance cases, asserted by example rather than by prose.

    Each of these would be a defect to rewrite: a pinned prompt, an archived
    employer posting, and a vendor payload kept verbatim.
    """
    for path in (
        "src/career_agent/llm/prompts/description_v6.md",
        "companion/resume-tailor/ARCHITECTURE.md",
        "tests/fixtures/providers/lever/board_small.json",
    ):
        assert guard.is_excluded(path) is not None, f"{path} must not be swept"


def test_an_ordinary_source_file_is_not_excluded() -> None:
    """The exclusions are prefixes, so an over-broad one would be easy to write."""
    for path in (
        "src/career_agent/web/presenter.py",
        "src/career_agent/llm/prompts.py",
        "tests/unit/test_verify.py",
        "README.md",
    ):
        assert guard.is_excluded(path) is None, f"{path} should be scanned"


def test_employer_text_really_does_contain_the_character() -> None:
    """The reason the exclusions exist, stated as a fact rather than a belief.

    If this ever stops being true, the protocol fixture exclusion is no
    longer load-bearing and should be reconsidered rather than kept out of
    habit.
    """
    quoted = (REPO_ROOT / "tests/fixtures/providers/lever/board_small.json").read_text(
        encoding="utf-8"
    )
    assert EN in quoted or EM in quoted
