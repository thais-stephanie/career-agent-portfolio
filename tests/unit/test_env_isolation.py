"""The suite never runs under the owner's product switches."""

from __future__ import annotations

import os
import re
from pathlib import Path

from tests.conftest import LOCAL_PRODUCT_SWITCHES

SRC = Path(__file__).resolve().parents[2] / "src"


def test_every_product_switch_is_cleared_for_the_suite() -> None:
    named = {
        name
        for path in SRC.rglob("*.py")
        for name in re.findall(r"CAREER_AGENT_[A-Z_]+", path.read_text(encoding="utf-8"))
    }
    assert named, "the scan found nothing; it is reading the wrong place"
    assert named <= set(LOCAL_PRODUCT_SWITCHES), sorted(named - set(LOCAL_PRODUCT_SWITCHES))


def test_none_of_them_is_set_while_tests_run() -> None:
    assert not [name for name in LOCAL_PRODUCT_SWITCHES if name in os.environ]
