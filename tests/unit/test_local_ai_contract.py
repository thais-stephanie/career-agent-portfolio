"""The compact local enrichment contract, and the verification that is its point.

Two properties are being defended here, and they are not the same property:

1. **A quote that does not exist is dropped, loudly.** Not repaired, not
   downgraded, not silently shortened away -- dropped and recorded with a
   reason, so a rising drop rate is visible.
2. **The document never carries a score.** A numeric field would make the local
   model a ranker, and ADR-0001 says the model observes while code decides.
   `test_no_numeric_field_anywhere` is that invariant written as an assertion,
   so adding one is a test failure rather than a judgement call.
"""

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from career_agent.local_ai.contract import (
    LOCAL_ENRICHMENT_SCHEMA_VERSION,
    MAX_TECHNOLOGIES,
    EvidencedItem,
    LocalEnrichment,
    VerifiedEnrichment,
    json_schema,
    verify,
)

SOURCE = (
    "Senior Business Systems Engineer at Northwind.\n"
    "You will own our Salesforce instance and build integrations in Python.\n"
    "We expect strong SQL, and an on-call rotation of one week in four.\n"
    "This role does not offer visa sponsorship.\n"
)


def _item(text: str, quote: str) -> EvidencedItem:
    return EvidencedItem(text=text, quote=quote)


# --- shape ---------------------------------------------------------------


def test_unknown_field_is_refused() -> None:
    """extra='forbid': a model that invents a field fails loudly, not quietly."""
    with pytest.raises(ValidationError):
        LocalEnrichment.model_validate({"summary": "x", "match_score": 0.9})


def test_models_are_frozen() -> None:
    enrichment = LocalEnrichment(summary="ok")
    with pytest.raises(ValidationError):
        enrichment.summary = "changed"  # type: ignore[misc]


def test_list_caps_are_enforced() -> None:
    too_many = [_item(f"tool {i}", "Python") for i in range(MAX_TECHNOLOGIES + 1)]
    with pytest.raises(ValidationError):
        LocalEnrichment(summary="ok", technologies=too_many)


def test_summary_cap_is_enforced() -> None:
    with pytest.raises(ValidationError):
        LocalEnrichment(summary="x" * 401)


def test_no_numeric_field_anywhere() -> None:
    """The enrichment never carries a score. This is the boundary, as a test."""
    # `schema_version` is the one integer, and it is an identity rather than a
    # measurement: it says which shape this document has, not how good it is.
    identities = {"schema_version"}
    for model in (LocalEnrichment, VerifiedEnrichment, EvidencedItem):
        for name, field in model.model_fields.items():
            if name in identities:
                continue
            assert field.annotation not in (int, float), (
                f"{model.__name__}.{name} is numeric. The local enrichment is an "
                "observation, never a score: scoring is deterministic Python."
            )


def test_schema_version_is_pinned() -> None:
    assert LOCAL_ENRICHMENT_SCHEMA_VERSION == 1


# --- the JSON schema handed to Ollama ------------------------------------


def test_json_schema_is_flat_and_complete() -> None:
    """No $ref and no $defs: Ollama compiles `format` to a grammar, and
    reference indirection is the first thing that support thins out on."""
    schema = json_schema()
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(LocalEnrichment.model_fields)
    assert schema["properties"]["recommended_action"]["enum"] == [
        "READ_IN_FULL",
        "SKIM",
        "DEPRIORITISE",
    ]


def test_json_schema_is_a_fresh_object_each_call() -> None:
    """It goes into a request body a caller may mutate; sharing it would let
    one request corrupt the next."""
    first, second = json_schema(), json_schema()
    assert first == second
    first["properties"]["summary"]["maxLength"] = 1
    assert second["properties"]["summary"]["maxLength"] == 400


# --- verification --------------------------------------------------------


def test_exact_quote_survives() -> None:
    enrichment = LocalEnrichment(
        summary="Owns Salesforce, builds integrations.",
        technologies=[_item("Salesforce", "own our Salesforce instance")],
    )
    verified = verify(enrichment, SOURCE)
    assert [i.text for i in verified.technologies] == ["Salesforce"]
    assert verified.rejected == []


def test_typography_differences_are_forgiven() -> None:
    """The repo's one normalisation, reused rather than reimplemented: curly
    quotes, collapsed whitespace and case are formatting, not facts."""
    source = "We use “Python”\n and    SQL every day."
    enrichment = LocalEnrichment(
        summary="Python and SQL.",
        technologies=[_item("Python", 'we use "python" and sql every day.')],
    )
    verified = verify(enrichment, source)
    assert len(verified.technologies) == 1
    assert verified.rejected == []


def test_fabricated_quote_is_dropped_and_recorded() -> None:
    enrichment = LocalEnrichment(
        summary="Owns Salesforce.",
        technologies=[
            _item("Salesforce", "own our Salesforce instance"),
            _item("Kubernetes", "You will run our Kubernetes clusters"),
        ],
    )
    verified = verify(enrichment, SOURCE)

    assert [i.text for i in verified.technologies] == ["Salesforce"]
    assert len(verified.rejected) == 1
    dropped = verified.rejected[0]
    assert dropped.field == "technologies"
    assert dropped.text == "Kubernetes"
    assert dropped.quote == "You will run our Kubernetes clusters"
    assert dropped.reason  # never an empty reason: a drop rate needs a why


def test_every_evidenced_field_is_checked() -> None:
    enrichment = LocalEnrichment(
        summary="s",
        technologies=[_item("t", "invented one")],
        strengths=[_item("s", "invented two")],
        gaps=[_item("g", "invented three")],
        risk_flags=[_item("r", "invented four")],
    )
    verified = verify(enrichment, SOURCE)
    assert {r.field for r in verified.rejected} == {
        "technologies",
        "strengths",
        "gaps",
        "risk_flags",
    }


def test_verification_carries_the_scalar_fields_through() -> None:
    enrichment = LocalEnrichment(
        summary="Owns Salesforce.",
        recommended_action="SKIM",
        confidence="MEDIUM",
    )
    verified = verify(enrichment, SOURCE)
    assert verified.recommended_action == "SKIM"
    assert verified.confidence == "MEDIUM"
    assert verified.schema_version == LOCAL_ENRICHMENT_SCHEMA_VERSION


# --- acceptability -------------------------------------------------------


def test_empty_summary_is_not_acceptable() -> None:
    verified = verify(
        LocalEnrichment(summary="   ", technologies=[_item("SQL", "strong SQL")]),
        SOURCE,
    )
    assert verified.is_acceptable is False


def test_summary_with_no_items_is_acceptable() -> None:
    """A posting that states nothing worth listing is a legitimate answer."""
    verified = verify(LocalEnrichment(summary="A systems role at Northwind."), SOURCE)
    assert verified.verified_count == 0
    assert verified.is_acceptable is True


def test_more_than_half_dropped_is_not_acceptable() -> None:
    enrichment = LocalEnrichment(
        summary="A systems role.",
        technologies=[
            _item("Salesforce", "own our Salesforce instance"),
            _item("A", "invented one"),
            _item("B", "invented two"),
        ],
    )
    verified = verify(enrichment, SOURCE)
    assert (verified.verified_count, verified.rejected_count) == (1, 2)
    assert verified.is_acceptable is False


def test_exactly_half_dropped_is_still_acceptable() -> None:
    """The rule is 'more than half', and the boundary is stated once, here."""
    enrichment = LocalEnrichment(
        summary="A systems role.",
        technologies=[
            _item("Salesforce", "own our Salesforce instance"),
            _item("A", "invented one"),
        ],
    )
    verified = verify(enrichment, SOURCE)
    assert (verified.verified_count, verified.rejected_count) == (1, 1)
    assert verified.is_acceptable is True


def test_everything_fabricated_is_not_acceptable() -> None:
    enrichment = LocalEnrichment(
        summary="A systems role.",
        technologies=[_item("A", "invented one"), _item("B", "invented two")],
    )
    verified = verify(enrichment, SOURCE)
    assert verified.verified_count == 0
    assert verified.is_acceptable is False


# --- the package boundary ------------------------------------------------


def test_local_ai_imports_nothing_from_the_hosted_stack() -> None:
    """The rule the package exists to hold, checked the way `test_domain_purity`
    checks the domain: by parsing, so an import is caught even on a code path
    that never runs.

    `career_agent.llm` owns the budget ledger, the pacing governor and the
    hosted cache. A local call is free, unmetered and loopback-only; coupling
    the two would either pollute the ledger with structurally free rows or
    teach every hosted mechanism a "local" special case.
    """
    package = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "local_ai"
    offenders: list[str] = []

    for path in sorted(package.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module]
            else:
                continue
            for name in names:
                if name == "career_agent.llm" or name.startswith("career_agent.llm."):
                    offenders.append(f"{path.name}: {name}")

    assert offenders == [], f"local_ai must not import the hosted stack: {offenders}"
