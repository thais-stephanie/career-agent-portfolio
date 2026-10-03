"""Scam signs are read from the ad's own words, and only shown, never scored."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from career_agent.web.caution import caution_signals

MATCH = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "match"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("You must pay a training fee of USD 49 before you start.", ["fee"]),
        ("Antes de começar, é preciso pagar uma taxa de inscrição.", ["fee"]),
        ("Please send your bank account details first.", ["bank"]),
        ("Envie seus dados bancários para iniciar.", ["bank"]),
        ("Contact us only on WhatsApp: +1 555 0100.", ["whatsapp"]),
        ("Buy the starter laptop from us before day one.", ["equipment"]),
        # Denials and ordinary ads say nothing.
        ("We will never ask you to pay a fee.", []),
        ("We send you a laptop; you buy nothing.", []),
        ("Senior engineer, Python, remote. Salary USD 300,000.", []),
        ("", []),
    ],
)
def test_a_sign_is_a_phrase_the_ad_contains(text: str, expected: list[str]) -> None:
    assert caution_signals(text) == expected


def test_no_salary_comparison_is_claimed() -> None:
    # There is no comparable-salary basis, so no such signal exists.
    assert caution_signals("Pay: USD 9,000 a week for data entry.") == []


def test_scoring_never_reads_the_scam_signs() -> None:
    for path in MATCH.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "caution" not in node.module, path
