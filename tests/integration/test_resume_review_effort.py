"""How much a first export of an IMPORTED resume asks of the person, and how
little the next version asks. Synthetic files through the real import path.

What is asked about is what the resume CLAIMS: each line, each skill, each
role (title, employer, dates). Never the name, contact details, the document
title, section headings or skill group names: those are structure.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_editor_api import call, make_api
from tests.integration.test_resume_import_api import read, save
from tests.support_import import SIMPLE, docx, long_senior

from career_agent.web.api import JobsApi


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return make_api(tmp_path, "a")


def _review(api: JobsApi, made: dict[str, Any]) -> dict[str, Any]:
    return call(api, "POST", f"/documents/{made['id']}/review",
                {"expected_sha256": made["sha256"]})  # fmt: skip


def _first_export(api: JobsApi, made: dict[str, Any]) -> tuple[list[dict], float]:
    """Every waiting statement acknowledged one by one, then a DOCX: the
    statements asked about and the machine time it took (no human time)."""
    started = time.perf_counter()
    found = _review(api, made)["items"]
    waiting = [i for i in found if not i["acknowledged"]]
    for item in waiting:
        call(api, "POST", f"/documents/{made['id']}/acknowledgements",
             {"ref": item["ref"], "expected_sha256": made["sha256"]})  # fmt: skip
    out = call(api, "POST", f"/documents/{made['id']}/exports",
               {"format": "DOCX", "expected_sha256": made["sha256"]})  # fmt: skip
    # Every statement in the file is the person's own, decided now or before.
    assert out["declared"] == len(found)
    return waiting, time.perf_counter() - started


@pytest.mark.parametrize(("name", "spec"), [("simple", SIMPLE), ("senior", long_senior())])
def test_a_first_export_asks_about_claims_only_and_the_next_version_about_its_changes(
    api: JobsApi, name: str, spec: Any
) -> None:
    saved = save(api, read(api, docx(spec)))
    made = call(api, "GET", f"/documents/{saved['id']}")
    doc = made["document"]
    waiting, seconds = _first_export(api, made)

    # Structure is never asked about.
    texts = {i["text"] for i in waiting}
    structure = {doc["title"], doc["identity"]["full_name"]}
    structure |= {g["name"] for g in doc["skills"]}
    structure |= {c["heading"] for c in doc["custom_sections"]}
    assert not texts & {s for s in structure if s}, texts & structure
    assert {i["section"] for i in waiting} <= {
        "headline", "summary", "experience", "projects", "education", "certifications",
        "skills", "custom",
    }  # fmt: skip
    lines = sum(len(e["bullets"]) for s in ("experience", "projects", "education") for e in doc[s])
    entries = sum(len(doc[s]) for s in ("experience", "projects", "education", "certifications"))
    skills = sum(len(g["items"]) for g in doc["skills"])
    on_top = int(bool(doc["summary"])) + int(bool(doc["headline"]))
    print(f"\nREVIEW {name}: {len(waiting)} statements ({lines} lines, {entries} entries, "
          f"{skills} skills, summary/headline {on_top}) in {seconds:.2f} s")  # fmt: skip

    # A second version with one line reworded asks about that line only.
    copy = call(api, "POST", f"/documents/{made['id']}/copy", {"title": "For one job"})
    changed = {**copy["document"]}
    first = next(e for e in changed["experience"] if e["bullets"])
    first["bullets"][0]["text"] = first["bullets"][0]["text"].rstrip(".") + " for the whole team."
    saved_copy = call(api, "PATCH", f"/documents/{copy['id']}/working",
                      {"document": changed, "expected_sha256": copy["sha256"]})  # fmt: skip
    again = {**copy, "sha256": saved_copy["sha256"]}
    second, seconds = _first_export(api, again)
    assert [i["ref"] for i in second] == [first["bullets"][0]["id"]]
    print(f"REVIEW {name} second version: {len(second)} statement in {seconds:.2f} s")
