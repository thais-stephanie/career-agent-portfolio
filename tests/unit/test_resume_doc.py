"""ResumeDocument 1.0: the model's own rules, its hash and its published schema.

Synthetic people and jobs only.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc.models import (
    Bullet,
    Identity,
    Origin,
    UnsupportedSchemaVersion,
    canonical_json,
    content_sha256,
    upgrade_resume_document,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def sparse(**extra: Any) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "id": new_id(),
        "kind": "SCRATCH",
        "title": "Blank",
        "language": "en",
        "provenance": {"created_from": "SCRATCH"},
        **extra,
    }


def rich() -> dict[str, Any]:
    custom = new_id()
    return {
        "schema_version": "1.0",
        "id": new_id(),
        "kind": "TAILORED",
        "title": "Exemplo Digital, Data Analyst V1",
        "language": "pt-BR",
        "identity": {
            "full_name": "Riley Synthetic",
            "email": "riley@example.invalid",
            "city": "Curitiba",
            "country": "BR",
            "links": [{"id": new_id(), "kind": "GITHUB", "url": "https://example.invalid/r"}],
            "show": {"phone": False},
        },
        "target": {"jd_snapshot_id": new_id(), "title": "Data Analyst", "company": "Exemplo"},
        "headline": {"id": new_id(), "text": "Analyst", "origin": "USER_AUTHORED"},
        "experience": [
            {
                "id": new_id(),
                "employer": "Northwind Synthetic",
                "display_title": "Data Analyst",
                "source_title": "Reporting Assistant",
                "start": {"year": 2020, "month": 3},
                "current": True,
                "experience_id": "exp-1",
                "bullets": [
                    {
                        "id": new_id(),
                        "text": "Built weekly reports.",
                        "origin": "EVIDENCE_VERBATIM",
                        "evidence_ids": ["claim-1@1"],
                        "requirement_ids": ["r1"],
                    },
                    {
                        "id": new_id(),
                        "text": "Automated the weekly reports.",
                        "origin": "AI_REWRITE",
                        "evidence_ids": ["claim-1@1"],
                        "override": "EDITED",
                        "original_text": "Automated reports.",
                        "flags": ["UNSUPPORTED_TERM"],
                    },
                ],
            }
        ],
        "education": [{"id": new_id(), "institution": "Synthetic University", "degree": "BSc"}],
        "certifications": [{"id": new_id(), "name": "Synthetic Cert", "issued": {"year": 2021}}],
        "skills": [
            {
                "id": new_id(),
                "name": "Tools",
                "items": [
                    {"id": new_id(), "label": "SQL", "origin": "IMPORTED"},
                    {
                        "id": new_id(),
                        "label": "Python",
                        "origin": "RULE_REWRITE",
                        "evidence_ids": ["c2"],
                    },
                ],
            }
        ],
        "custom_sections": [{"id": custom, "heading": "Volunteering", "items": []}],
        "layout": {
            "section_order": ["headline", "experience", "skills", f"custom:{custom}"],
            "hidden_sections": ["summary", "projects"],
            "headings": {"experience": "Experiência"},
        },
        "design": {"template": "compact", "page": {"size": "LETTER", "margins_mm": 12}},
        "provenance": {"created_from": "TAILOR"},
    }


def test_a_rich_and_a_sparse_document_load() -> None:
    doc = upgrade_resume_document(rich())
    assert doc.experience[0].source_title == "Reporting Assistant"
    assert doc.target is not None and doc.target.title == "Data Analyst"
    blank = upgrade_resume_document(sparse())
    assert blank.experience == [] and blank.identity.full_name == ""


def test_unknown_fields_are_refused_everywhere() -> None:
    with pytest.raises(ValidationError):
        upgrade_resume_document(sparse(photo="x.png"))
    with pytest.raises(ValidationError):
        upgrade_resume_document(sparse(identity={"full_name": "R", "age": 30}))


def test_an_unknown_schema_version_fails_clearly() -> None:
    with pytest.raises(UnsupportedSchemaVersion, match="2.0"):
        upgrade_resume_document(sparse(schema_version="2.0"))
    with pytest.raises(UnsupportedSchemaVersion):
        upgrade_resume_document({k: v for k, v in sparse().items() if k != "schema_version"})


def test_a_positional_id_is_not_an_id() -> None:
    with pytest.raises(ValidationError):
        upgrade_resume_document(sparse(id="experience-3"))
    with pytest.raises(ValidationError):
        Bullet(id="b001", text="x", origin=Origin.USER_AUTHORED)


def test_two_items_cannot_share_an_id() -> None:
    data = rich()
    data["education"][0]["id"] = data["experience"][0]["id"]
    with pytest.raises(ValidationError, match="share an id"):
        upgrade_resume_document(data)
    data = rich()
    data["headline"]["id"] = data["id"]
    with pytest.raises(ValidationError, match="share an id"):
        upgrade_resume_document(data)


def test_a_placeholder_name_loads_and_is_a_finding_never_a_default() -> None:
    assert Identity().full_name == ""
    assert Identity().name_finding() == "NAME_MISSING"
    you = upgrade_resume_document(sparse(identity={"full_name": "You"}))
    assert you.identity.full_name == "You"
    assert you.identity.name_finding() == "NAME_PLACEHOLDER"
    assert Identity(full_name=" meu  perfil ").name_finding() == "NAME_PLACEHOLDER"
    assert Identity(full_name="Riley Synthetic").name_finding() is None


def test_dates_are_structured_and_ordered() -> None:
    data = rich()
    entry = data["experience"][0]
    entry["start"] = "Mar 2020 - now"
    with pytest.raises(ValidationError):
        upgrade_resume_document(data)
    entry.update(start={"year": 2021}, end={"year": 2020, "month": 5}, current=False)
    with pytest.raises(ValidationError, match="before start"):
        upgrade_resume_document(data)
    entry.update(start={"year": 2020}, end={"year": 2021}, current=True)
    with pytest.raises(ValidationError, match="current role"):
        upgrade_resume_document(data)
    entry.update(start={"year": 2020, "month": 13}, end=None)
    with pytest.raises(ValidationError):
        upgrade_resume_document(data)


@pytest.mark.parametrize("origin", ["EVIDENCE_VERBATIM", "RULE_REWRITE", "AI_REWRITE"])
def test_text_that_claims_evidence_must_name_it(origin: str) -> None:
    with pytest.raises(ValidationError, match="evidence"):
        Bullet(id=new_id(), text="Led everything.", origin=Origin(origin))
    for blank in ([""], ["   "]):
        with pytest.raises(ValidationError):
            Bullet(id=new_id(), text="Led everything.", origin=Origin(origin), evidence_ids=blank)
    data = rich()
    data["skills"][0]["items"][1].update(origin=origin, evidence_ids=[])
    with pytest.raises(ValidationError, match="evidence"):
        upgrade_resume_document(data)


@pytest.mark.parametrize("origin", ["USER_AUTHORED", "IMPORTED"])
def test_typed_or_imported_text_may_stand_alone(origin: str) -> None:
    assert Bullet(id=new_id(), text="Ran a club.", origin=Origin(origin)).evidence_ids == []


def test_original_text_belongs_to_an_edited_block() -> None:
    with pytest.raises(ValidationError, match="original_text"):
        Bullet(id=new_id(), text="a", origin=Origin.USER_AUTHORED, original_text="b")


def test_target_only_on_a_tailored_document_and_layout_names_real_sections() -> None:
    data = rich()
    data["kind"] = "MASTER"
    with pytest.raises(ValidationError, match="target"):
        upgrade_resume_document(data)
    data = sparse(kind="TAILORED")
    with pytest.raises(ValidationError, match="target"):
        upgrade_resume_document(data)
    data = rich()
    data["layout"]["headings"][f"custom:{new_id()}"] = "Ghost"
    with pytest.raises(ValidationError, match="custom sections"):
        upgrade_resume_document(data)


def test_design_is_a_closed_vocabulary() -> None:
    for design in (
        {"template": "fancy"},
        {"page": {"size": "A3"}},
        {"page": {"margins_mm": 2}},
        {"typography": {"font": "C:/Windows/Fonts/evil.ttf"}},
    ):
        with pytest.raises(ValidationError):
            upgrade_resume_document(sparse(design=design))


def test_the_hash_does_not_depend_on_key_order_or_whitespace() -> None:
    data = rich()
    reordered = json.loads(json.dumps(data, indent=4), object_pairs_hook=lambda p: dict(p[::-1]))
    a, b = upgrade_resume_document(data), upgrade_resume_document(json.dumps(reordered, indent=3))
    assert canonical_json(a) == canonical_json(b)
    assert content_sha256(a) == content_sha256(b)
    hidden = data["layout"]["hidden_sections"]
    data["layout"]["hidden_sections"] = hidden[::-1]
    assert content_sha256(upgrade_resume_document(data)) == content_sha256(a)
    data["title"] = "Another title"
    assert content_sha256(upgrade_resume_document(data)) != content_sha256(a)


def test_a_document_round_trips_through_its_canonical_json() -> None:
    doc = upgrade_resume_document(rich())
    assert upgrade_resume_document(canonical_json(doc)) == doc


def test_the_committed_schema_is_what_the_generator_writes() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    from make_resume_schema import DESTINATION, render  # noqa: PLC0415

    assert DESTINATION == REPO_ROOT / "schemas" / "resume-document.v1.json"
    assert DESTINATION.read_text(encoding="utf-8") == render(), (
        "schemas/resume-document.v1.json is stale. Run:\n"
        "  uv run python scripts/make_resume_schema.py"
    )
    schema = json.loads(render())
    assert schema["additionalProperties"] is False
    assert all(
        d.get("additionalProperties") is False
        for d in schema["$defs"].values()
        if d.get("type") == "object"
    )
    identity = schema["$defs"]["Identity"]["properties"]
    assert not {"photo", "age", "gender", "marital_status", "nationality"} & set(identity)


def test_unicode_equivalent_text_hashes_alike_and_is_stored_as_typed() -> None:
    composed, decomposed = (
        "Jos\N{LATIN SMALL LETTER E WITH ACUTE}",
        "Jose\N{COMBINING ACUTE ACCENT}",
    )
    assert composed != decomposed
    a = upgrade_resume_document(sparse(identity={"full_name": composed}))
    b = a.model_copy(update={"identity": Identity(full_name=decomposed)})
    assert content_sha256(a) == content_sha256(b)
    assert canonical_json(a) != canonical_json(b), "only the hash is normalised"
    assert b.identity.full_name == decomposed
    c = a.model_copy(update={"identity": Identity(full_name="Jose")})
    assert content_sha256(c) != content_sha256(a), "an accent is not whitespace"
