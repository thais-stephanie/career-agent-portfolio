"""The person's contact details: gentle checks, and no reach into scoring."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from career_agent.storage.contact import ContactError, clean, display_location, missing_required

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"


def test_optional_fields_are_optional_and_phone_is_free_text() -> None:
    out = clean(
        {"full_name": "Ana", "email": "ana@example.com", "phone": "(81) 9 9999-0000 ramal 2"}
    )
    assert out["phone"] == "(81) 9 9999-0000 ramal 2"
    assert out["portfolio_url"] == out["github_url"] == ""
    assert missing_required(out) == []
    assert missing_required(clean({})) == ["full_name", "email"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("email", "ana"),
        ("email", "ana@example"),
        ("linkedin_url", "my linkedin"),
        ("github_url", "x"),
    ],
)
def test_a_detail_that_is_not_one_is_refused_with_its_field(field: str, value: str) -> None:
    with pytest.raises(ContactError) as caught:
        clean({field: value})
    assert caught.value.field == field


def test_a_web_address_without_a_scheme_gets_one() -> None:
    assert (
        clean({"linkedin_url": "linkedin.com/in/ana"})["linkedin_url"]
        == "https://linkedin.com/in/ana"
    )
    assert clean({"portfolio_url": "http://ana.dev"})["portfolio_url"] == "http://ana.dev"


def test_the_resume_place_joins_only_what_was_given() -> None:
    assert display_location({"city": "Recife", "country": "Brazil"}) == "Recife, Brazil"
    assert display_location({}) == ""


def test_nothing_that_reads_a_posting_reads_the_contact_record() -> None:
    """Contact details are background: never a search answer (invariant 4)."""
    offenders = []
    for package in ("match", "pipeline", "sources", "providers"):
        for path in (SRC / package).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = (
                    [node.module or ""]
                    if isinstance(node, ast.ImportFrom)
                    else [alias.name for alias in node.names]
                    if isinstance(node, ast.Import)
                    else []
                )
                if any(name.endswith("storage.contact") for name in names):
                    offenders.append(str(path))
    assert offenders == []
