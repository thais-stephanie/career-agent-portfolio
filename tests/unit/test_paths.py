"""`domain/paths.py` must stay provider-agnostic.

It was moved out of `providers/base.py` at M2 so the domain-layer verifier could
share one implementation with the collector rather than keeping a second copy of
a byte-exact serialisation contract. That move is only safe while the module
knows nothing about any particular vendor: the moment a Greenhouse path or an
Ashby special case appears here, provider neutrality has leaked into the domain
through the back door -- and it would leak somewhere the provider-neutrality
guard was not looking.
"""

import ast
from pathlib import Path

import pytest

from career_agent.domain.paths import resolve_path, serialise_value

SOURCE = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "domain" / "paths.py"
TEXT = SOURCE.read_text(encoding="utf-8")


@pytest.mark.parametrize("vendor", ["greenhouse", "lever", "ashby", "workday", "smartrecruiters"])
def test_no_vendor_is_named_in_the_module(vendor: str) -> None:
    """Not even in a comment. A named vendor here is the first sign that a
    generic utility has started carrying a special case."""
    assert vendor not in TEXT.lower(), (
        f"{vendor!r} appears in domain/paths.py; this module resolves paths and knows "
        "nothing about who produced them"
    )


def test_no_concrete_payload_path_is_hard_coded() -> None:
    """String literals here should be structural, never vendor field paths.

    `location.name` and `categories.commitment` belong in a ProviderFieldMap.
    """
    literals = [
        node.value
        for node in ast.walk(ast.parse(TEXT))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    suspicious = [
        text
        for text in literals
        if "." in text and " " not in text and text.count(".") >= 1 and len(text) > 4
    ]
    assert not suspicious, f"looks like a vendor payload path: {suspicious}"


def test_the_module_imports_nothing_but_the_standard_library() -> None:
    """Pure by construction: no I/O, no siblings, no vendor SDK.

    `domain/test_domain_purity.py` already forbids sibling imports across the
    whole layer. This is narrower and states the intent for this module
    specifically, because it is the one that got moved.
    """
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(TEXT))
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        node.module.split(".")[0]
        for node in ast.walk(ast.parse(TEXT))
        if isinstance(node, ast.ImportFrom) and node.module
    }

    assert imports <= {"json", "typing", "__future__"}, f"unexpected imports: {imports}"


# --- behaviour the two sides of the contract both depend on ------------------


def test_a_missing_path_resolves_to_nothing_rather_than_raising() -> None:
    """The collector and the verifier both walk paths that may not exist, and
    neither wants an exception for the ordinary case of an absent field."""
    assert resolve_path({"a": {"b": 1}}, "a.c") is None
    assert resolve_path({"a": 1}, "a.b.c") is None
    assert resolve_path({}, "anything") is None


def test_false_does_not_serialise_as_zero() -> None:
    """bool is an int subclass, so an int branch placed first turns an
    employer's explicit "no" into the string "0"."""
    assert serialise_value(False) == "false"
    assert serialise_value(True) == "true"
    assert serialise_value(0) == "0"


def test_emptiness_stays_absence() -> None:
    """An empty value is not an observation. Recording one would manufacture a
    claim the employer never made."""
    for empty in (None, "", "   ", {}, [], ()):
        assert serialise_value(empty) is None


def test_structures_serialise_stably() -> None:
    """The collector produces `source_value` and the verifier re-derives it
    later. If key order differed between them, real evidence would fail
    verification for formatting reasons and look like hallucination."""
    first = serialise_value({"min": 100, "max": 200, "currency": "USD"})
    second = serialise_value({"currency": "USD", "max": 200, "min": 100})

    assert first == second
