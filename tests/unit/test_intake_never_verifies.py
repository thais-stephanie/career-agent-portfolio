"""Nothing in `career_agent.intake` may confirm a claim.

WHY THIS FILE IS SEPARATE AND WHY IT EXISTS AT ALL
---------------------------------------------------
`tests/unit/test_cv_intake.py` already walks the `cv` package and asserts that
exactly one call site sets `verified=True`. That test guards the path where
THIS PROGRAM read the document.

The intake package guards a different and more dangerous path: a file that
somebody else's model wrote about somebody's career. If `intake/store.py`
constructed its own `VerifiedClaim(verified=True)`, an assistant's reading of a
CV could become a verified fact through a code path no existing test watches --
and the product's central promise, that only the candidate confirms, would be
false in exactly the place it matters most.

So the assertion is structural and counted in the syntax tree rather than
grepped, for the reason the sibling test records: a grep finds the docstring
promising this and passes on it, which is a check that cannot tell a promise
from a violation.
"""

from __future__ import annotations

import ast
from pathlib import Path

INTAKE = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "intake"


def _confirming_calls(root: Path) -> list[str]:
    found: list[str] = []
    for module in sorted(root.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "verified"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    function = node.func
                    name = getattr(function, "id", None) or getattr(function, "attr", "?")
                    found.append(f"{module.name}:{name}")
    return found


def test_no_module_in_the_intake_package_sets_verified_true() -> None:
    assert _confirming_calls(INTAKE) == [], (
        "a module in career_agent.intake confirms a claim directly. Confirmation "
        "belongs to cv.propose.to_claim; a package written by somebody else's model "
        "must not be able to reach a verified fact without going through it."
    )


def test_the_intake_package_never_constructs_a_verified_claim_at_all() -> None:
    """Stronger than the line above, and worth having.

    A `VerifiedClaim(...)` built here with `verified` merely omitted still
    bypasses the one function whose job is to be the place confirmation
    happens. `store.confirm` builds a `cv.propose.Proposal` and hands it over
    instead, which is why nothing here needs the domain object's constructor.
    """
    offenders: list[str] = []
    for module in sorted(INTAKE.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
                if name == "VerifiedClaim":
                    offenders.append(module.name)

    assert offenders == [], (
        f"{offenders} construct a VerifiedClaim. Hand a cv.propose.Proposal to "
        "cv.propose.to_claim instead, so there stays exactly one place a claim "
        "becomes confirmed."
    )
