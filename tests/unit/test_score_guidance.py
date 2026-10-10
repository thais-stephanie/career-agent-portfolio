"""The guidance about the % says only what the formula does."""

from __future__ import annotations

import re
from pathlib import Path

from career_agent.match.score import COUNTED_SIGNALS

STATIC = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "web" / "static" / "js"


def test_the_work_slots_the_interface_names_are_the_ones_the_score_pays():
    found = re.search(
        r"export const WORK_SLOTS = (\d+);", (STATIC / "detail.js").read_text("utf-8")
    )
    assert found and int(found.group(1)) == COUNTED_SIGNALS["responsibilities"]


def test_no_text_calls_the_percentage_a_hiring_chance():
    catalogue = (STATIC / "i18n.js").read_text("utf-8").casefold()
    for line in catalogue.splitlines():
        if "chance" in line and ("%" in line or "match" in line):
            assert "not a chance" in line or "não é chance" in line or "não é um palpite" in line, (
                line
            )
