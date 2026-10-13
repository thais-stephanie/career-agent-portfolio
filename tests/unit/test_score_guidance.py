"""The guidance about the % says only what the formula does."""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "web" / "static" / "js"


def test_no_text_calls_the_percentage_a_hiring_chance():
    catalogue = (STATIC / "i18n.js").read_text("utf-8").casefold()
    for line in catalogue.splitlines():
        if "chance" in line and ("%" in line or "match" in line):
            assert "not a chance" in line or "não é chance" in line or "não é um palpite" in line, (
                line
            )
