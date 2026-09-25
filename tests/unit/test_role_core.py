"""Role-core alignment: the benchmark floors, and that nothing scores with it.

See evaluation/role_alignment/README.md for the measurement and the decision.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from career_agent.discovery.aliases import plan_aliases
from career_agent.discovery.anchors import Anchor
from career_agent.match.role_core import Alignment, IntentCores, classify, role_core
from career_agent.yaml_io import safe_load

ROOT = Path(__file__).resolve().parents[2]
PERSONAS = ROOT / "evaluation" / "role_alignment" / "personas.yaml"


def _confusion() -> Counter[tuple[str, str]]:
    data = safe_load(PERSONAS.read_text(encoding="utf-8"))
    confusion: Counter[tuple[str, str]] = Counter()
    for person in data["personas"]:
        anchors = [Anchor(text=r) for r in person["roles"]]
        roles = [a.text for a in anchors] + [a.text for a in plan_aliases(anchors)]
        intent = IntentCores.of(roles, person["work"])
        for title, label in person["titles"].items():
            confusion[(label, classify(title, intent).value)] += 1
    return confusion


def test_the_benchmark_floors_hold() -> None:
    confusion = _confusion()
    total = sum(confusion.values())
    correct = sum(n for (truth, got), n in confusion.items() if truth == got)
    called_aligned = sum(n for (_, got), n in confusion.items() if got == "ALIGNED")
    assert total == 90
    assert correct / total >= 0.75
    # The one output precise enough to be worth anything: never a different job.
    assert confusion[("ALIGNED", "ALIGNED")] == called_aligned
    assert confusion[("OUTSIDE", "ALIGNED")] == 0


def test_without_a_named_role_a_title_is_unresolved_not_outside() -> None:
    intent = IntentCores.of([], ["workflow automation"])
    assert classify("Account Executive", intent) is Alignment.UNRESOLVED
    assert classify("Workflow Automation Specialist", intent) is Alignment.ALIGNED
    assert classify("General application", IntentCores.of(["Nurse"])) is Alignment.UNRESOLVED


def test_role_core_reads_titles_the_way_people_write_them() -> None:
    assert role_core("Manager, Customer Success") == ("customer", "success", "manager")
    assert role_core("Senior SDR (Remote) - LATAM") == ("sales", "development", "representative")


def test_nothing_that_scores_gates_or_hides_a_posting_imports_it() -> None:
    offenders = [
        str(path.relative_to(ROOT))
        for path in (ROOT / "src" / "career_agent").rglob("*.py")
        if path.name != "role_core.py" and "match.role_core" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_the_classifier_reads_a_hostile_title_in_linear_time() -> None:
    import time

    started = time.monotonic()
    classify("a" + " " * 20_000 + "b" + "(" * 20_000, IntentCores.of(["Nurse"]))
    assert time.monotonic() - started < 1.0
