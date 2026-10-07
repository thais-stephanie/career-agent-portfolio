"""The rewrite corpus as regression tests (tests/fixtures/rewrite_corpus).

Development cases must all read as labelled: rules were written against them.
Evaluation cases are compared with the outcome RECORDED when they were first
run after the fix (`after-phase1.json`), misses included: a change that moves
one is visible here and has to be explained, and nobody edits the frozen split
to make it pass.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from scripts import rewrite_corpus as corpus

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "rewrite_corpus"
#: sha256 of eval.yaml (LF line ends) as frozen on 2026-10-07, before any fix.
EVAL_SHA256 = "ffa717fa96fb57744aec9d42b4d07fccbdcfc4d8bf08ca1ba0b3cfe84e0a47f3"


def _recorded(split: str) -> dict[str, str]:
    runs = json.loads((FIXTURES / "after-phase1.json").read_text(encoding="utf-8"))
    return {row["id"]: row["got"] for run in runs if run["split"] == split for row in run["rows"]}


def test_the_evaluation_split_is_frozen() -> None:
    text = (FIXTURES / "eval.yaml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert hashlib.sha256(text.encode("utf-8")).hexdigest() == EVAL_SHA256


def test_both_splits_cover_every_dimension_in_both_languages() -> None:
    for split in corpus.SPLITS:
        cases = corpus.load(split)
        assert {c["lang"] for c in cases} == {"en", "pt"}
        assert {"FLAG", "PASS"} == {c["expect"] for c in cases}
        dimensions = {c["dimension"] for c in cases}
        assert {"participation", "agent", "responsibility", "scope", "frequency",
                "causality"} <= dimensions  # fmt: skip


@pytest.mark.parametrize("case", corpus.load("dev"), ids=lambda c: c["id"])
def test_development_case(case: dict) -> None:
    got, codes = corpus.outcome(case)
    assert got == case["expect"], codes


@pytest.mark.parametrize("case", corpus.load("eval"), ids=lambda c: c["id"])
def test_evaluation_case_reads_as_recorded(case: dict) -> None:
    got, codes = corpus.outcome(case)
    assert got == _recorded("eval")[case["id"]], codes


def test_the_recorded_evaluation_says_what_it_missed() -> None:
    """The one faithful control still refused after the fix, kept in view:
    "rebuilding" is not "rebuilt" to a word list, and the amount moved."""
    eval_cases = {c["id"]: c for c in corpus.load("eval")}
    wrong = sorted(k for k, got in _recorded("eval").items() if got != eval_cases[k]["expect"])
    assert wrong == ["ev-32"]
