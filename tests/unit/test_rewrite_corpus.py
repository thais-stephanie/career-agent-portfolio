"""The rewrite corpus as regression tests (tests/fixtures/rewrite_corpus).

Development cases must all read as labelled: rules were written against them.

The evaluation split has ONE independent report, `after-phase1.json`: the
frozen cases run once after the phase 1 fix (20/20 distortions, 15/16
controls, ev-32 refused). It is kept as it was. Every later run is a
REGRESSION record, not an evaluation: `regression-ev32.json` is the split
read again after ev-32's own false positive was fixed, so it says nothing
independent about ev-32 (nor about ev-03 and ev-17, which informed fixes in
phase 1). Evaluation cases are compared with the latest regression record: a
change that moves one is visible here, and nobody edits the frozen split.
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
#: The one independent evaluation report, and the latest regression record.
EVALUATION, REGRESSION = "after-phase1.json", "regression-ev32.json"


def _recorded(name: str, split: str = "eval") -> dict[str, str]:
    runs = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return {row["id"]: row["got"] for run in runs if run["split"] == split for row in run["rows"]}


def _wrong(name: str) -> list[str]:
    expect = {c["id"]: c["expect"] for c in corpus.load("eval")}
    return sorted(k for k, got in _recorded(name).items() if got != expect[k])


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
def test_evaluation_case_reads_as_the_latest_regression_record(case: dict) -> None:
    got, codes = corpus.outcome(case)
    assert got == _recorded(REGRESSION)[case["id"]], codes


def test_the_independent_evaluation_report_is_kept_as_it_was() -> None:
    """The one faithful control the phase 1 fix still refused, in the report
    that was independent: kept, never rewritten by a later fix."""
    assert _wrong(EVALUATION) == ["ev-32"]


def test_ev32_is_fixed_as_a_regression_not_as_an_evaluation() -> None:
    """"Cut first reply time by 20% by rebuilding the Zendesk macros" from
    "Rebuilt the Zendesk macros that cut first reply time by 20%": the
    inflection is the evidence's own word, and so is the amount."""
    case = next(c for c in corpus.load("eval") if c["id"] == "ev-32")
    assert corpus.outcome(case) == ("PASS", [])
    assert _wrong(REGRESSION) == []
