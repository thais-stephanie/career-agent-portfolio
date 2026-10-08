"""The review before an export: unsupported wording and the person's own statements.

Synthetic people and evidence, temporary profile databases, DOCX only (no
browser needed). An export is refused while any statement waits; the person
edits it, puts back the evidence's wording, or acknowledges it as their own,
and the acknowledgement is recorded apart from the document.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_editor_api import call, make_api

from career_agent.clock import new_id
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.resume_doc.store import forget_resume_data
from career_agent.storage.db import connect, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError

EVIDENCE = "Built lead routing in HubSpot for 4 regional teams."
TYPED = "Mentored two new analysts on our reporting stack."


def _claim(conn: Any, key: str, text: str, tools: list[str]) -> None:
    ClaimRepo(conn).add(
        ensure_candidate(conn),
        VerifiedClaim(claim_key=key, claim_type=ClaimType.EMPLOYMENT, text=text,
                      source=ClaimSource.SELF_ATTESTED, verified=True, tools=tools),
    )  # fmt: skip


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    api = make_api(tmp_path, "a")
    with connect(api.config.db_path) as conn, transaction(conn):
        _claim(conn, "k-routing", EVIDENCE, ["HubSpot"])
        _claim(
            conn, "k-other", "Built HubSpot lead routing for 4 teams in two regions.", ["HubSpot"]
        )
    return api


def _document(*, typed: bool = True) -> dict[str, Any]:
    bullets = [{"id": new_id(), "text": EVIDENCE, "origin": "EVIDENCE_VERBATIM",
                "evidence_ids": ["k-routing"]}]  # fmt: skip
    if typed:
        bullets.append({"id": new_id(), "text": TYPED, "origin": "USER_AUTHORED"})
    return {
        "schema_version": "1.0",
        "id": new_id(),
        "kind": "SCRATCH",
        "title": "Review",
        "language": "en",
        "identity": {"full_name": "Robin Synthetic", "email": "robin@example.invalid"},
        "experience": [{
            "id": new_id(), "employer": "Northwind Systems", "display_title": "Analyst",
            "experience_id": "exp-synthetic", "start": {"year": 2022}, "current": True,
            "bullets": bullets,
        }],
        "provenance": {"created_from": "SCRATCH"},
    }  # fmt: skip


def _create(api: JobsApi, doc: dict[str, Any]) -> dict[str, Any]:
    made = call(api, "POST", "/documents", {"title": "Review"})
    fixed = ("id", "kind", "provenance", "schema_version")
    body = {
        "document": {**made["document"], **{k: v for k, v in doc.items() if k not in fixed}},
        "expected_sha256": made["sha256"],
    }
    call(api, "PATCH", f"/documents/{made['id']}/working", body)
    return call(api, "GET", f"/documents/{made['id']}")


def _save(api: JobsApi, made: dict[str, Any], change: Any) -> dict[str, Any]:
    doc = made["document"]
    change(doc)
    body = {"document": doc, "expected_sha256": made["sha256"]}
    call(api, "PATCH", f"/documents/{made['id']}/working", body)
    return call(api, "GET", f"/documents/{made['id']}")


def _review(api: JobsApi, made: dict[str, Any]) -> dict[str, Any]:
    return call(api, "POST", f"/documents/{made['id']}/review",
                {"expected_sha256": made["sha256"]})  # fmt: skip


def _ack(api: JobsApi, made: dict[str, Any], ref: str, path: str = "acknowledgements") -> dict:
    body = {"ref": ref, "expected_sha256": made["sha256"]}
    return call(api, "POST", f"/documents/{made['id']}/{path}", body)["item"]


def _export(api: JobsApi, made: dict[str, Any]) -> dict[str, Any]:
    body = {"format": "DOCX", "expected_sha256": made["sha256"]}
    return call(api, "POST", f"/documents/{made['id']}/exports", body)


def _bullet(made: dict[str, Any], n: int) -> dict[str, Any]:
    return made["document"]["experience"][0]["bullets"][n]


def _reword(text: str, n: int = 0) -> Any:
    def change(doc: dict[str, Any]) -> None:
        line = doc["experience"][0]["bullets"][n]
        if line["override"] == "NONE" and line["origin"] != "USER_AUTHORED":
            line["original_text"] = line["text"]
        line["override"] = "EDITED" if line["origin"] != "USER_AUTHORED" else "NONE"
        line["text"] = text

    return change


def test_a_statement_the_person_wrote_waits_until_they_say_it_is_theirs(api: JobsApi) -> None:
    made = _create(api, _document())
    typed = _bullet(made, 1)
    found = _review(api, made)
    assert found["waiting"] == 1
    item = found["items"][0]
    assert (item["ref"], item["kind"], item["acknowledged"]) == (typed["id"], "WRITTEN", False)
    with pytest.raises(ApiError) as refused:
        _export(api, made)
    assert refused.value.status == 409 and refused.value.code == "review_required"
    assert [i["ref"] for i in refused.value.data["items"]] == [typed["id"]]
    _ack(api, made, typed["id"])
    out = _export(api, made)
    assert out["verified"] is True and out["declared"] == 1


def test_an_unchanged_statement_is_not_asked_about_again(api: JobsApi) -> None:
    made = _create(api, _document())
    _ack(api, made, _bullet(made, 1)["id"])
    _export(api, made)
    _export(api, made)  # the same saved copy, again: nothing to decide
    # Another resume of this profile holding the same statement.
    other = _create(api, _document())
    assert _review(api, other)["waiting"] == 0
    _export(api, other)


def test_a_changed_word_or_number_is_a_new_statement_and_spacing_is_not(api: JobsApi) -> None:
    made = _create(api, _document())
    _ack(api, made, _bullet(made, 1)["id"])
    made = _save(api, made, _reword("  mentored two new analysts on our  reporting stack ", 1))
    assert _review(api, made)["waiting"] == 0
    made = _save(api, made, _reword("Mentored five new analysts on our reporting stack.", 1))
    assert _review(api, made)["waiting"] == 1


def test_a_rewording_its_evidence_does_not_hold_says_why_and_keeps_its_provenance(
    api: JobsApi,
) -> None:
    made = _create(api, _document(typed=False))
    assert _review(api, made)["items"] == []
    made = _save(
        api, made, _reword("Designed and built lead routing in HubSpot for 4 regional teams.")
    )
    item = _review(api, made)["items"][0]
    assert item["kind"] == "NOT_AS_WORDED"
    assert "UNSUPPORTED_RESPONSIBILITY" in item["reasons"]
    assert item["original_text"] == EVIDENCE and item["evidence_ids"] == ["k-routing"]
    acked = _ack(api, made, item["ref"])
    assert acked["acknowledged"] is True
    # Recorded apart: the line keeps its origin, evidence and original wording.
    line = _bullet(call(api, "GET", f"/documents/{made['id']}"), 0)
    assert (line["origin"], line["evidence_ids"], line["original_text"]) == (
        "EVIDENCE_VERBATIM",
        ["k-routing"],
        EVIDENCE,
    )
    with connect(api.config.db_path) as conn:
        rows = conn.execute(
            "SELECT statement_text, document_id, item_ref, withdrawn_at"
            " FROM resume_statement_acknowledgement"
        ).fetchall()
    assert [tuple(r) for r in rows] == [(line["text"], made["id"], line["id"], None)]


def test_putting_back_the_evidence_wording_needs_no_decision(api: JobsApi) -> None:
    made = _create(api, _document(typed=False))
    made = _save(api, made, _reword("Designed lead routing in HubSpot for 4 regional teams."))
    assert _review(api, made)["waiting"] == 1

    def put_back(doc: dict[str, Any]) -> None:
        line = doc["experience"][0]["bullets"][0]
        line.update(text=line["original_text"], override="NONE", original_text=None)

    made = _save(api, made, put_back)
    assert _review(api, made)["items"] == []
    _export(api, made)


def test_citing_other_evidence_needs_a_new_decision(api: JobsApi) -> None:
    made = _create(api, _document(typed=False))
    made = _save(api, made, _reword("Designed lead routing in HubSpot for 4 regional teams."))
    _ack(api, made, _bullet(made, 0)["id"])
    assert _review(api, made)["waiting"] == 0

    def cite_other(doc: dict[str, Any]) -> None:
        doc["experience"][0]["bullets"][0]["evidence_ids"] = ["k-other"]

    made = _save(api, made, cite_other)
    assert _review(api, made)["waiting"] == 1


def test_a_withdrawn_acknowledgement_is_kept_and_asked_again(api: JobsApi) -> None:
    made = _create(api, _document())
    ref = _bullet(made, 1)["id"]
    _ack(api, made, ref)
    assert _ack(api, made, ref, "acknowledgements/withdraw")["acknowledged"] is False
    assert _review(api, made)["waiting"] == 1
    _ack(api, made, ref)
    with connect(api.config.db_path) as conn:
        rows = conn.execute(
            "SELECT withdrawn_at IS NULL FROM resume_statement_acknowledgement"
            " ORDER BY acknowledged_at, id"
        ).fetchall()
    assert sorted(r[0] for r in rows) == [0, 1]


def test_a_stale_page_or_a_statement_that_needs_nothing_is_refused(api: JobsApi) -> None:
    made = _create(api, _document())
    with pytest.raises(ApiError) as stale:
        call(api, "POST", f"/documents/{made['id']}/acknowledgements",
             {"ref": _bullet(made, 1)["id"], "expected_sha256": "0" * 64})  # fmt: skip
    assert stale.value.status == 409
    with pytest.raises(ApiError) as nothing:
        _ack(api, made, _bullet(made, 0)["id"])  # its evidence holds it as worded
    assert nothing.value.status == 404
    with connect(api.config.db_path) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM resume_statement_acknowledgement").fetchone()[0] == 0
        )


def test_entries_and_skills_the_person_typed_are_statements_too(api: JobsApi) -> None:
    doc = _document(typed=False)
    doc["experience"][0]["experience_id"] = None
    doc["skills"] = [{"id": new_id(), "name": "Tools", "items": [
        {"id": new_id(), "label": "Kubernetes", "origin": "USER_AUTHORED"}]}]  # fmt: skip
    doc["certifications"] = [{"id": new_id(), "name": "Synthetic Cloud Architect"}]
    made = _create(api, doc)
    kinds = {(i["section"], i["kind"]) for i in _review(api, made)["items"]}
    assert kinds == {
        ("experience", "WRITTEN"),
        ("skills", "WRITTEN"),
        ("certifications", "WRITTEN"),
    }


def test_forgetting_resume_data_forgets_acknowledgements(api: JobsApi) -> None:
    made = _create(api, _document())
    _ack(api, made, _bullet(made, 1)["id"])
    with connect(api.config.db_path) as conn:
        forget_resume_data(conn)
        assert (
            conn.execute("SELECT COUNT(*) FROM resume_statement_acknowledgement").fetchone()[0] == 0
        )


# ------------------------------------------- across versions and profiles


def test_the_same_words_under_another_role_are_another_statement(api: JobsApi) -> None:
    made = _create(api, _document())
    _ack(api, made, _bullet(made, 1)["id"])
    elsewhere = _document()
    role = elsewhere["experience"][0]
    role.update(experience_id="exp-another", employer="Contoso Labs")
    role["bullets"] = [role["bullets"][1]]  # only the typed line, now under another role
    moved = _create(api, elsewhere)
    found = _review(api, moved)
    assert found["waiting"] == 1 and found["items"][0]["text"] == TYPED


def test_another_profile_never_reuses_an_acknowledgement(api: JobsApi, tmp_path: Path) -> None:
    typed_only = _document()
    typed_only["experience"][0]["bullets"] = typed_only["experience"][0]["bullets"][1:]
    made = _create(api, typed_only)
    _ack(api, made, _bullet(made, 0)["id"])
    other = make_api(tmp_path, "b")  # its own database
    theirs = _create(other, typed_only)
    assert _review(other, theirs)["waiting"] == 1


def test_revised_evidence_makes_an_acknowledged_rewording_a_new_statement(api: JobsApi) -> None:
    made = _create(api, _document(typed=False))
    made = _save(api, made, _reword("Designed lead routing in HubSpot for 4 regional teams."))
    _ack(api, made, _bullet(made, 0)["id"])
    assert _review(api, made)["waiting"] == 0
    with connect(api.config.db_path) as conn, transaction(conn):
        candidate, repo = ensure_candidate(conn), ClaimRepo(conn)
        current = repo.history(candidate, "k-routing")[-1]
        repo.supersede(
            candidate,
            current.next_revision(text="Built lead routing in HubSpot for 5 regional teams."),
        )
    assert _review(api, made)["waiting"] == 1
    # Provenance and history stay: the first acknowledgement is still recorded,
    # it simply no longer names this statement.
    with connect(api.config.db_path) as conn:
        rows = conn.execute(
            "SELECT item_ref, withdrawn_at FROM resume_statement_acknowledgement"
        ).fetchall()
    assert [tuple(r) for r in rows] == [(_bullet(made, 0)["id"], None)]
