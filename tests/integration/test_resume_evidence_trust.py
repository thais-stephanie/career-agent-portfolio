"""PR 5A: evidence is checked wherever a resume is ACCEPTED, and history stays readable.

`ResumeStore` asks `evidence.unconfirmed_lines` on create, save and
checkpoint, so no route, Master path or migration can write a line citing
evidence this profile has not confirmed now. Reading never asks: a revision
whose evidence was retired later still loads, and restoring it trusts
nothing until the person decides. Synthetic data only.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_editor_api import call, make_api
from tests.support_resume import confirm_cited, exportable

from career_agent.clock import new_id
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.resume_doc.models import ResumeDocument, canonical_json, sha256_text
from career_agent.resume_doc.store import EvidenceNotConfirmed, ResumeStore
from career_agent.storage.db import connect, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError

KINDS = ["k-missing", "k-draft", "k-other-profile"]


def _claim(conn: Any, key: str, *, verified: bool = True) -> None:
    ClaimRepo(conn).add(
        ensure_candidate(conn),
        VerifiedClaim(
            claim_key=key,
            claim_type=ClaimType.EMPLOYMENT,
            text=f"Synthetic {key}",
            source=ClaimSource.SELF_ATTESTED,
            verified=verified,
        ),
    )


def _retire(conn: Any, key: str) -> None:
    repo, candidate = ClaimRepo(conn), ensure_candidate(conn)
    with transaction(conn):
        repo.supersede(candidate, repo.history(candidate, key)[-1].next_revision(verified=False))


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    api = make_api(tmp_path, "a")
    with connect(api.config.db_path) as conn, transaction(conn):
        _claim(conn, "k-ok")
        _claim(conn, "k-draft", verified=False)
    other = make_api(tmp_path, "b")
    with connect(other.config.db_path) as conn, transaction(conn):
        _claim(conn, "k-other-profile")
    return api


def _citing(key: str) -> ResumeDocument:
    """A synthetic document whose summary cites `key`."""
    doc = exportable().model_dump(mode="json")
    doc["summary"] = {
        "id": new_id(),
        "text": "Connected billing to support.",
        "origin": "RULE_REWRITE",
        "evidence_ids": [key],
    }
    return ResumeDocument.model_validate(doc)


def _stored(api: JobsApi, key: str = "k-ok") -> tuple[Any, ResumeDocument]:
    doc = _citing(key)
    conn = connect(api.config.db_path)
    with transaction(conn):
        confirm_cited(conn, doc.model_copy(update={"summary": None}))
    ResumeStore(conn).create_document(doc)
    return conn, doc


def _smuggle(conn: Any, doc: ResumeDocument) -> None:
    """A working copy written around the store, as an older build or a broken
    writer could have: the next acceptance must still refuse it."""
    body = canonical_json(doc)
    with transaction(conn):
        conn.execute(
            "UPDATE resume_document SET working_json = ?, working_sha256 = ? WHERE id = ?",
            (body, sha256_text(body), doc.id),
        )


# ------------------------------------------------------------- checkpoint


@pytest.mark.parametrize("key", KINDS)
def test_a_checkpoint_of_evidence_not_confirmed_here_is_refused(api: JobsApi, key: str) -> None:
    conn, doc = _stored(api)
    _smuggle(conn, _citing(key).model_copy(update={"id": doc.id}))
    store = ResumeStore(conn)
    before = store.list_revisions(doc.id)
    with pytest.raises(ApiError) as refused:
        call(api, "POST", f"/documents/{doc.id}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    assert refused.value.status == 400 and refused.value.code == "evidence_not_confirmed"
    assert store.list_revisions(doc.id) == before, "no revision was written"
    conn.close()


def test_a_checkpoint_of_confirmed_evidence_is_accepted(api: JobsApi) -> None:
    conn, doc = _stored(api)
    edited = doc.model_copy(update={"title": "Edited"})
    sha = ResumeStore(conn).get_document(doc.id).working_sha256
    ResumeStore(conn).save_working_copy(doc.id, edited, expected_sha256=sha)
    out = call(api, "POST", f"/documents/{doc.id}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    assert out == {"revision": 2, "reason": "MANUAL_CHECKPOINT"}
    conn.close()


@pytest.mark.parametrize("key", KINDS)
def test_the_store_refuses_every_write_of_it(api: JobsApi, key: str) -> None:
    conn, doc = _stored(api)
    store = ResumeStore(conn)
    sha = store.get_document(doc.id).working_sha256
    bad = _citing(key)
    with pytest.raises(EvidenceNotConfirmed) as refused:
        store.create_document(bad)
    assert refused.value.lines == [bad.summary.id]  # type: ignore[union-attr]
    with pytest.raises(EvidenceNotConfirmed):
        store.save_working_copy(doc.id, bad.model_copy(update={"id": doc.id}), expected_sha256=sha)
    assert store.get_document(doc.id).working_sha256 == sha
    conn.close()


# --------------------------------------------- entries that name one claim


@pytest.mark.parametrize("section", ["projects", "education", "certifications"])
def test_an_entry_claim_key_is_a_claim_this_profile_confirms(api: JobsApi, section: str) -> None:
    conn, doc = _stored(api)
    store = ResumeStore(conn)
    data = doc.model_dump(mode="json")
    entry = {
        "projects": {"id": new_id(), "name": "Synthetic project"},
        "education": {"id": new_id(), "institution": "Synthetic University"},
        "certifications": {"id": new_id(), "name": "Synthetic certificate"},
    }[section]
    for key, accepted in (("k-missing", False), ("k-draft", False), ("k-ok", True)):
        data[section] = [{**entry, "claim_key": key}]
        sha = store.get_document(doc.id).working_sha256
        body = {"document": data, "expected_sha256": sha}
        if accepted:
            call(api, "PATCH", f"/documents/{doc.id}/working", body)
            continue
        with pytest.raises(ApiError) as refused:
            call(api, "PATCH", f"/documents/{doc.id}/working", body)
        assert refused.value.code == "evidence_not_confirmed"
        assert refused.value.data == {"lines": [entry["id"]]}
    data[section] = [{**entry, "claim_key": None}]  # the person's own entry cites nothing
    sha = store.get_document(doc.id).working_sha256
    call(api, "PATCH", f"/documents/{doc.id}/working", {"document": data, "expected_sha256": sha})
    conn.close()


# -------------------------------------------------------- history, restore


def test_history_stays_readable_after_its_evidence_is_retired(api: JobsApi) -> None:
    conn, doc = _stored(api)
    _retire(conn, "k-ok")
    store = ResumeStore(conn)
    [first] = store.list_revisions(doc.id)
    assert store.get_revision(first.id).content.summary.evidence_ids == ["k-ok"]  # type: ignore[union-attr]
    assert call(api, "GET", f"/documents/{doc.id}")["document"]["summary"]["evidence_ids"] == [
        "k-ok"
    ]
    conn.close()


def test_restoring_retired_evidence_puts_it_back_and_trusts_nothing(api: JobsApi) -> None:
    conn, doc = _stored(api)
    store = ResumeStore(conn)
    [old] = store.list_revisions(doc.id)
    later = doc.model_copy(update={"title": "Later"})
    store.save_working_copy(doc.id, later, expected_sha256=old.content_sha256)
    store.checkpoint_revision(doc.id, "MANUAL_CHECKPOINT")
    _retire(conn, "k-ok")

    restored = store.restore_revision(
        doc.id, old.id, expected_sha256=store.get_document(doc.id).working_sha256
    )
    working = store.get_document(doc.id)
    assert restored.content_sha256 == old.content_sha256 == working.working_sha256
    assert [r.reason for r in store.list_revisions(doc.id)] == [
        "CREATED",
        "MANUAL_CHECKPOINT",
        "RESTORED",
    ]
    line = working.working.summary.id  # type: ignore[union-attr]
    # Nothing accepts the restored line as evidence again...
    with pytest.raises(EvidenceNotConfirmed) as held:
        store.checkpoint_revision(doc.id, "MANUAL_CHECKPOINT")
    assert held.value.lines == [line]
    with pytest.raises(ApiError) as refused:
        call(
            api,
            "POST",
            f"/documents/{doc.id}/exports",
            {"format": "JSON", "expected_sha256": working.working_sha256},
        )
    assert refused.value.code == "evidence_not_confirmed"
    # ...until the person keeps it as their own words.
    data = working.working.model_dump(mode="json")
    data["summary"] = {**data["summary"], "origin": "USER_AUTHORED", "evidence_ids": []}
    call(
        api,
        "PATCH",
        f"/documents/{doc.id}/working",
        {"document": data, "expected_sha256": working.working_sha256},
    )
    assert store.checkpoint_revision(doc.id, "MANUAL_CHECKPOINT").reason == "MANUAL_CHECKPOINT"
    conn.close()


# ---------------------------------------------------------------- Master


def test_a_master_cannot_be_written_with_evidence_not_confirmed(api: JobsApi) -> None:
    conn = connect(api.config.db_path)
    with transaction(conn):
        confirm_cited(conn, exportable())
    store = ResumeStore(conn)
    master = {**_citing("k-missing").model_dump(mode="json"), "kind": "MASTER"}
    with pytest.raises(EvidenceNotConfirmed):
        store.create_document(ResumeDocument.model_validate(master))
    assert store.current_master() is None
    good = _citing("k-ok").model_dump(mode="json")
    made = store.create_document(ResumeDocument.model_validate({**good, "kind": "MASTER"}))
    _retire(conn, "k-ok")
    with pytest.raises(ApiError) as refused:
        api.handle_api(
            "PATCH",
            "/api/resume/master/identity",
            {},
            {
                "identity": {**good["identity"], "full_name": "Morgan Changed"},
                "expected_sha256": made.working_sha256,
            },
        )
    assert refused.value.status == 400 and refused.value.code == "evidence_not_confirmed"
    assert store.get_document(made.id).working_sha256 == made.working_sha256
    conn.close()
