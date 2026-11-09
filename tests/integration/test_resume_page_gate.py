"""The two-page gate for versions for a job, and skills nothing places, on
synthetic people only. The file's page count is the gate: a fake printer
makes PDFs of a chosen length; one test prints the real PDF end to end.
No model, no network, no real data."""

from __future__ import annotations

import hashlib
import io
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_master import profile
from tests.integration.test_resume_tailor_v1 import AD, avery
from tests.integration.test_resume_tailor_v1_completion import groups, mixed, visible_labels
from tests.support import committed_config_dir
from tests.support_resume import acknowledge_saved, confirm_cited, long

from career_agent.resume_doc import analyze, budget, review
from career_agent.resume_doc.export import PageLimitExceeded, export_revision
from career_agent.resume_doc.master import get_or_create_master
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import NotFound, ResumeStore, ResumeStoreError, StaleDocument
from career_agent.resume_doc.tailor import explain, tailor
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig


def printer(pages: int) -> Any:
    """A stand-in for Edge: a PDF of `pages` blank pages."""
    from pypdf import PdfWriter

    def pdf(html: str) -> tuple[bytes, str]:
        writer = PdfWriter()
        for _ in range(pages):
            writer.add_blank_page(612, 792)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), "fake"

    return pdf


def tailored(conn: sqlite3.Connection) -> str:
    """Avery's version for the synthetic ad, statements acknowledged so only
    the page count stands between it and a file."""
    avery(conn)
    get_or_create_master(conn)
    stored, _ = tailor(conn, ad={"title": "Customer Success Engineer", "text": AD})
    acknowledge_saved(conn, stored.id)
    return stored.id


def lengthened(conn: sqlite3.Connection, document_id: str) -> str:
    """The same version holding sixteen roles: three pages and more."""
    store = ResumeStore(conn)
    stored = store.get_document(document_id)
    data = long().model_dump(mode="json")
    own = stored.working.model_dump(mode="json")
    for key in ("id", "kind", "target", "provenance", "identity"):
        data[key] = own[key]
    doc = upgrade_resume_document(data)
    confirm_cited(conn, doc)
    store.save_working_copy(document_id, doc, expected_sha256=stored.working_sha256)
    acknowledge_saved(conn, document_id)
    return document_id


def counts(conn: sqlite3.Connection, document_id: str) -> tuple[int, int, str]:
    store = ResumeStore(conn)
    return (
        len(store.list_revisions(document_id)),
        len(store.list_exports(document_id)),
        store.get_document(document_id).working_sha256,
    )


# ------------------------------------------------------------------ the gate


@pytest.mark.parametrize("pages", [1, 2])
def test_a_version_for_a_job_within_two_pages_exports(tmp_path: Path, pages: int) -> None:
    conn = profile(tmp_path)
    doc_id = tailored(conn)
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    made = export_revision(conn, doc_id, "PDF", expected_sha256=sha, pdf=printer(pages))
    assert made.page_count == pages


@pytest.mark.parametrize("fmt", ["PDF", "DOCX", "JSON"])
def test_three_pages_leave_no_file_no_revision_in_any_format(tmp_path: Path, fmt: str) -> None:
    """DOCX and JSON are no way around the gate: they are measured as the PDF."""
    conn = profile(tmp_path)
    doc_id = tailored(conn)
    before = counts(conn, doc_id)
    with pytest.raises(PageLimitExceeded) as refused:
        export_revision(conn, doc_id, fmt, expected_sha256=before[2],  # type: ignore[arg-type]
                        pdf=printer(3))  # fmt: skip
    assert (refused.value.pages, refused.value.limit) == (3, 2)
    assert counts(conn, doc_id) == before
    folder = Path(conn.execute("PRAGMA database_list").fetchone()[2]).parent / "resume_exports"
    assert not list(folder.rglob(f"{doc_id}/*")) if folder.exists() else True


def test_the_master_and_other_resumes_are_not_held_to_two_pages(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    tailored(conn)
    master = ResumeStore(conn).current_master()
    assert master is not None
    acknowledge_saved(conn, master.id)
    made = export_revision(conn, master.id, "PDF", expected_sha256=master.working_sha256,
                           pdf=printer(3))  # fmt: skip
    assert made.page_count == 3


def test_the_statement_review_still_comes_first(tmp_path: Path) -> None:
    """The page gate adds no approval and replaces none: a line waiting for
    the person stops the export before anything is printed."""
    conn = profile(tmp_path)
    avery(conn)
    get_or_create_master(conn)
    stored, _ = tailor(conn, ad={"title": "Customer Success Engineer", "text": AD})
    store = ResumeStore(conn)
    data = stored.working.model_dump(mode="json")
    data["experience"][0]["bullets"][0]["text"] = "Ran every onboarding in the company alone."
    store.save_working_copy(stored.id, upgrade_resume_document(data),
                            expected_sha256=stored.working_sha256)  # fmt: skip
    printed: list[str] = []

    def spy(html: str) -> tuple[bytes, str]:
        printed.append(html)
        return printer(1)(html)

    sha = store.get_document(stored.id).working_sha256
    with pytest.raises(review.ReviewRequired):
        export_revision(conn, stored.id, "PDF", expected_sha256=sha, pdf=spy)
    assert printed == []


# --------------------------------------------------- proposal and approval


def test_the_proposal_names_each_line_and_why_and_changes_nothing(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    before = counts(conn, doc_id)
    plan = budget.propose(conn, doc_id, expected_sha256=before[2], measure=budget.estimate_pages)
    assert plan["status"] == "CUT" and plan["pages"] > 2 >= plan["pages_after"]
    assert plan["cuts"] and all(c["text"] and c["step"] for c in plan["cuts"])
    bullets = [c for c in plan["cuts"] if c["ref"].startswith("bullet/")]
    assert bullets and all(c["where"]["employer"].startswith("Invented Company") for c in bullets)
    assert counts(conn, doc_id) == before  # a proposal is never applied


def test_an_accepted_proposal_hides_exactly_that_keeps_the_copy_before_and_the_master(
    tmp_path: Path,
) -> None:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    store = ResumeStore(conn)
    master = store.current_master()
    assert master is not None
    revisions, _, sha = counts(conn, doc_id)
    plan = budget.propose(conn, doc_id, expected_sha256=sha, measure=budget.estimate_pages)
    refs = [c["ref"] for c in plan["cuts"]]
    budget.apply_cuts(conn, doc_id, refs, expected_sha256=sha, proposal=plan["proposal"])
    after = store.get_document(doc_id)
    # The copy as it was is the newest revision before the change: restorable.
    history = store.list_revisions(doc_id)
    assert len(history) == revisions + 1 and history[-1].content_sha256 == sha
    assert history[-1].reason == "MANUAL_CHECKPOINT"
    hidden = {f"bullet/{b.id}" for e in after.working.experience for b in e.bullets if b.hidden}
    hidden |= {f"skills/{g.id}" for g in after.working.skills if g.hidden}
    hidden |= {f"projects/{p.id}" for p in after.working.projects if p.hidden}
    hidden |= {f"certifications/{c.id}" for c in after.working.certifications if c.hidden}
    assert set(refs) <= hidden
    before_ids = {b.id for e in history[-1].content.experience for b in e.bullets}
    assert before_ids == {b.id for e in after.working.experience for b in e.bullets}  # no deletion
    assert all(any(not b.hidden for b in e.bullets) for e in after.working.experience)
    # Hiding is the only change: no word, number, claim or link of any line moves.
    was, now = history[-1].content.model_dump(mode="json"), after.working.model_dump(mode="json")

    def unhidden(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: unhidden(v) for k, v in node.items() if k != "hidden"}
        return [unhidden(v) for v in node] if isinstance(node, list) else node

    assert unhidden(was) == unhidden(now)
    assert store.get_document(master.id).working_sha256 == master.working_sha256
    # The export measures again; the approval is no pass for a long file.
    with pytest.raises(PageLimitExceeded):
        export_revision(conn, doc_id, "PDF", expected_sha256=after.working_sha256,
                        pdf=printer(3))  # fmt: skip
    made = export_revision(conn, doc_id, "PDF", expected_sha256=after.working_sha256,
                           pdf=printer(2))  # fmt: skip
    assert store.get_revision(made.revision_id).content_sha256 == after.working_sha256


def test_a_proposal_not_accepted_changes_nothing_and_the_gate_stays(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    before = counts(conn, doc_id)
    budget.propose(conn, doc_id, expected_sha256=before[2], measure=budget.estimate_pages)
    assert counts(conn, doc_id) == before
    with pytest.raises(PageLimitExceeded):
        export_revision(conn, doc_id, "PDF", expected_sha256=before[2], pdf=printer(4))


def test_an_approval_of_an_older_copy_is_refused(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    store = ResumeStore(conn)
    sha = store.get_document(doc_id).working_sha256
    plan = budget.propose(conn, doc_id, expected_sha256=sha, measure=budget.estimate_pages)
    data = store.get_document(doc_id).working.model_dump(mode="json")
    data["title"] = "Edited after the proposal"
    store.save_working_copy(doc_id, upgrade_resume_document(data), expected_sha256=sha)
    before = counts(conn, doc_id)
    refs = [c["ref"] for c in plan["cuts"]]
    with pytest.raises(StaleDocument):
        budget.apply_cuts(conn, doc_id, refs, expected_sha256=sha, proposal=plan["proposal"])
    # Nor with the new copy's sha: the seal is of the copy proposed.
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, refs, expected_sha256=before[2],
                          proposal=plan["proposal"])  # fmt: skip
    assert counts(conn, doc_id) == before


@pytest.mark.parametrize("bad", [[], ["bullet/not-a-line"], ["bullet/x", "bullet/x"]])
def test_a_cut_the_spec_does_not_allow_now_is_refused(tmp_path: Path, bad: list[str]) -> None:
    """Even sealed (as no proposal would be), the plan is checked again."""
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    before = counts(conn, doc_id)
    sealed = budget._seal(conn, doc_id, before[2], bad)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, bad, expected_sha256=before[2], proposal=sealed)
    assert counts(conn, doc_id) == before


# ------------------------------------------- only the proposal accepted, whole


def _proposed(tmp_path: Path) -> tuple[sqlite3.Connection, str, dict[str, Any], list[str]]:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    plan = budget.propose(conn, doc_id, expected_sha256=sha, measure=budget.estimate_pages)
    assert plan["status"] == "CUT" and plan["proposal"]
    return conn, doc_id, plan, [c["ref"] for c in plan["cuts"]]


def _other_allowed(conn: sqlite3.Connection, doc_id: str, refs: list[str]) -> str:
    """A cut the spec allows on this document that the proposal did not make."""
    stored = ResumeStore(conn).get_document(doc_id)
    plan = budget._plan(stored.working, budget._job_supports(conn, stored), date.today().year)
    return next(ref for _, ref in plan if ref not in refs)


@pytest.mark.parametrize("change", ["one_more", "one_fewer", "repeated", "reordered", "other"])
def test_only_the_exact_list_proposed_is_accepted(tmp_path: Path, change: str) -> None:
    conn, doc_id, plan, refs = _proposed(tmp_path)
    assert len(refs) >= 2
    sent = {
        "one_more": lambda: [*refs, _other_allowed(conn, doc_id, refs)],
        "one_fewer": lambda: refs[:-1],
        "repeated": lambda: [*refs, refs[0]],
        "reordered": lambda: [refs[1], refs[0], *refs[2:]],
        "other": lambda: [_other_allowed(conn, doc_id, refs)],
    }[change]()
    before = counts(conn, doc_id)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, sent, expected_sha256=before[2], proposal=plan["proposal"])
    assert counts(conn, doc_id) == before
    # The proposal itself still applies, whole.
    budget.apply_cuts(conn, doc_id, refs, expected_sha256=before[2], proposal=plan["proposal"])


@pytest.mark.parametrize("seal", ["", "0" * 64, "not a seal"])
def test_no_seal_or_a_made_up_one_is_refused(tmp_path: Path, seal: str) -> None:
    conn, doc_id, _, refs = _proposed(tmp_path)
    before = counts(conn, doc_id)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, refs, expected_sha256=before[2], proposal=seal)
    assert counts(conn, doc_id) == before


def test_a_new_revision_since_the_proposal_refuses_it(tmp_path: Path) -> None:
    """Same words, a newer revision (a milestone kept meanwhile): propose again."""
    conn, doc_id, plan, refs = _proposed(tmp_path)
    ResumeStore(conn).checkpoint_revision(doc_id, "MANUAL_CHECKPOINT")
    before = counts(conn, doc_id)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, refs, expected_sha256=before[2], proposal=plan["proposal"])
    assert counts(conn, doc_id) == before


def test_a_proposal_does_not_apply_to_another_document(tmp_path: Path) -> None:
    """Two versions made from the same Master share line ids: the seal names one."""
    conn, doc_id, plan, refs = _proposed(tmp_path)
    stored, _ = tailor(conn, ad={"title": "Customer Success Engineer", "text": AD})
    acknowledge_saved(conn, stored.id)
    second = lengthened(conn, stored.id)
    before = counts(conn, second)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, second, refs, expected_sha256=before[2], proposal=plan["proposal"])
    assert counts(conn, second) == before


def test_a_proposal_does_not_apply_in_another_profile(tmp_path: Path) -> None:
    """A copy of the profile holds the same document, sha and revisions: still refused."""
    conn, doc_id, plan, refs = _proposed(tmp_path)
    copy = tmp_path / "twin" / "personal.db"
    copy.parent.mkdir()
    raw = sqlite3.connect(copy)
    conn.backup(raw)
    raw.close()
    twin = connect(copy)
    before = counts(twin, doc_id)
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(twin, doc_id, refs, expected_sha256=before[2], proposal=plan["proposal"])
    assert counts(twin, doc_id) == before
    twin.close()


def test_a_proposal_that_cannot_fit_offers_nothing_to_accept(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = tailored(conn)
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    plan = budget.propose(conn, doc_id, expected_sha256=sha, measure=lambda doc: 9)
    assert plan["status"] == "CONFLICT" and plan["proposal"] is None


def test_what_proves_a_required_ask_is_never_cut_and_the_conflict_is_said(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = tailored(conn)
    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    plan = budget.propose(conn, doc_id, expected_sha256=sha, measure=lambda doc: 9)
    assert plan["status"] == "CONFLICT" and plan["pages_after"] == 9
    assert plan["protected"] and all(p["text"] for p in plan["protected"])
    protected = {p["ref"] for p in plan["protected"]}
    assert not protected & {c["ref"] for c in plan["cuts"]}
    before = counts(conn, doc_id)
    # Sealed as if proposed: the protected lines are still refused.
    sealed = budget._seal(conn, doc_id, sha, sorted(protected))
    with pytest.raises(ResumeStoreError):
        budget.apply_cuts(conn, doc_id, sorted(protected), expected_sha256=sha, proposal=sealed)
    assert counts(conn, doc_id) == before


def test_the_measure_is_asked_a_bounded_number_of_times(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    asked: list[int] = []

    def stubborn(doc: Any) -> int:
        asked.append(1)
        return 5

    sha = ResumeStore(conn).get_document(doc_id).working_sha256
    budget.propose(conn, doc_id, expected_sha256=sha, measure=stubborn)
    assert len(asked) <= budget.MAX_ROUNDS


def test_only_a_version_for_a_job_has_a_fit(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    tailored(conn)
    master = ResumeStore(conn).current_master()
    assert master is not None
    with pytest.raises(ResumeStoreError):
        budget.propose(conn, master.id, expected_sha256=master.working_sha256,
                       measure=budget.estimate_pages)  # fmt: skip


def test_a_changed_statement_after_approval_waits_for_the_person_again(tmp_path: Path) -> None:
    """The existing review: an acknowledgement is for the words as saved."""
    conn = profile(tmp_path)
    doc_id = tailored(conn)
    store = ResumeStore(conn)
    stored = store.get_document(doc_id)
    data = stored.working.model_dump(mode="json")
    data["experience"][0]["bullets"][0]["text"] = "Led onboarding for every customer we had."
    store.save_working_copy(doc_id, upgrade_resume_document(data),
                            expected_sha256=stored.working_sha256)  # fmt: skip
    acknowledge_saved(conn, doc_id)
    data["experience"][0]["bullets"][0]["text"] = "Led onboarding for every customer worldwide."
    sha = store.get_document(doc_id).working_sha256
    store.save_working_copy(doc_id, upgrade_resume_document(data), expected_sha256=sha)
    sha = store.get_document(doc_id).working_sha256
    with pytest.raises(review.ReviewRequired):
        export_revision(conn, doc_id, "PDF", expected_sha256=sha, pdf=printer(1))


def test_one_profiles_version_is_not_found_from_another(tmp_path: Path) -> None:
    first = profile(tmp_path / "a")
    doc_id = lengthened(first, tailored(first))
    sha = ResumeStore(first).get_document(doc_id).working_sha256
    other = profile(tmp_path / "b")
    avery(other)
    with pytest.raises(NotFound):
        budget.propose(other, doc_id, expected_sha256=sha, measure=budget.estimate_pages)
    with pytest.raises(NotFound):
        budget.apply_cuts(other, doc_id, ["bullet/x"], expected_sha256=sha, proposal="x")


# ------------------------------------------------------ the routes, real PDF


def _api(tmp_path: Path) -> tuple[JobsApi, str]:
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    return api, doc_id


def call(api: JobsApi, method: str, path: str, body: dict | None = None) -> Any:
    return api.handle_api(method, f"/api/resume{path}", {}, body or {})


def test_over_two_pages_then_the_fit_then_the_file_with_the_real_pdf(tmp_path: Path) -> None:
    """Export refused in words, the proposal measured on the real PDF, the
    person's yes, and a file that prints within two pages."""
    from career_agent.web.server import ApiError

    api, doc_id = _api(tmp_path)
    sha = call(api, "GET", f"/documents/{doc_id}")["sha256"]
    with pytest.raises(ApiError) as refused:
        call(api, "POST", f"/documents/{doc_id}/exports", {"format": "PDF", "expected_sha256": sha})
    error = refused.value
    assert error.status == 409 and error.code == "over_page_limit"
    assert error.data["limit"] == 2 and error.data["pages"] > 2
    plan = call(api, "POST", f"/documents/{doc_id}/fit", {"expected_sha256": sha})
    assert plan["status"] == "CUT" and plan["pages_after"] <= 2
    assert call(api, "GET", f"/documents/{doc_id}")["sha256"] == sha  # still unchanged
    accepted = {"refs": [c["ref"] for c in plan["cuts"]], "expected_sha256": sha,
                "proposal": plan["proposal"]}  # fmt: skip
    # One line short of the proposal, then one more: refused, nothing changed.
    history = call(api, "GET", f"/documents/{doc_id}/history")
    for refs in (accepted["refs"][:-1], [*accepted["refs"], accepted["refs"][0]]):
        with pytest.raises(ApiError) as refused:
            call(api, "POST", f"/documents/{doc_id}/fit/cuts", {**accepted, "refs": refs})
        assert refused.value.status == 409
    assert call(api, "GET", f"/documents/{doc_id}/history") == history
    assert call(api, "GET", f"/documents/{doc_id}")["sha256"] == sha
    applied = call(api, "POST", f"/documents/{doc_id}/fit/cuts", accepted)
    made = call(api, "POST", f"/documents/{doc_id}/exports",
                {"format": "PDF", "expected_sha256": applied["sha256"]})  # fmt: skip
    assert made["page_count"] <= 2
    # The same approval twice: the copy it was given for is gone.
    with pytest.raises(ApiError) as again:
        call(api, "POST", f"/documents/{doc_id}/fit/cuts", accepted)
    assert again.value.status == 409


def test_a_failure_after_the_checkpoint_leaves_no_revision_and_no_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The copy kept in the history and the hidden lines are one unit: when
    saving the fitted copy fails, the history has no extra revision."""
    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    before = counts(conn, doc_id)
    plan = budget.propose(conn, doc_id, expected_sha256=before[2], measure=budget.estimate_pages)

    def broken(*args: Any, **kwargs: Any) -> str:
        raise RuntimeError("the disk said no")

    monkeypatch.setattr(ResumeStore, "save_working_copy", broken)
    with pytest.raises(RuntimeError):
        budget.apply_cuts(conn, doc_id, [c["ref"] for c in plan["cuts"]],
                          expected_sha256=before[2], proposal=plan["proposal"])  # fmt: skip
    monkeypatch.undo()
    assert counts(conn, doc_id) == before


def test_a_file_made_before_the_gate_stays_downloadable(tmp_path: Path) -> None:
    """A version for a job exported at three pages before this rule: its file
    and its history stay; a new file waits for the fit."""
    from career_agent.resume_doc.export import export_dir
    from career_agent.web.server import ApiError

    conn = profile(tmp_path)
    doc_id = lengthened(conn, tailored(conn))
    store = ResumeStore(conn)
    old_pdf, _ = printer(3)("")
    relative = f"{doc_id}/old.pdf"
    (export_dir(conn) / doc_id).mkdir(parents=True, exist_ok=True)
    (export_dir(conn) / relative).write_bytes(old_pdf)
    revision = store.checkpoint_revision(doc_id, "EXPORTED")
    old = store.record_export(doc_id, revision.id, format="PDF", template="classic",
                              file_path=relative, file_sha256=hashlib.sha256(old_pdf).hexdigest(),
                              engine="fake", page_count=3)  # fmt: skip
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    sha = store.get_document(doc_id).working_sha256
    conn.close()
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    with pytest.raises(ApiError) as refused:
        call(
            api, "POST", f"/documents/{doc_id}/exports", {"format": "JSON", "expected_sha256": sha}
        )
    assert refused.value.code == "over_page_limit"
    got = api.handle_api("GET", f"/api/resume/exports/{old.id}/file", {}, {})
    assert got.body == old_pdf
    listed = call(api, "GET", f"/documents/{doc_id}/exports")
    assert [e["id"] for e in listed] == [old.id]


@pytest.mark.parametrize(
    "route,body",
    [
        ("fit", {}),
        ("fit", {"expected_sha256": 1}),
        ("fit", {"expected_sha256": "x", "refs": []}),
        ("fit/cuts", {}),
        ("fit/cuts", {"expected_sha256": "x", "proposal": "p"}),
        ("fit/cuts", {"refs": ["bullet/x"], "expected_sha256": "x"}),  # no proposal
        ("fit/cuts", {"refs": ["bullet/x"], "expected_sha256": "x", "proposal": 1}),
        ("fit/cuts", {"refs": ["bullet/x"], "expected_sha256": "x", "proposal": "p" * 129}),
        ("fit/cuts", {"refs": "bullet/x", "expected_sha256": "x", "proposal": "p"}),
        ("fit/cuts", {"refs": [], "expected_sha256": "x", "proposal": "p"}),
        ("fit/cuts", {"refs": [7], "expected_sha256": "x", "proposal": "p"}),
        ("fit/cuts", {"refs": ["b" * 201], "expected_sha256": "x", "proposal": "p"}),
        ("fit/cuts", {"refs": ["bullet/x"] * 501, "expected_sha256": "x", "proposal": "p"}),
        (
            "fit/cuts",
            {"refs": ["bullet/x"], "expected_sha256": "x", "proposal": "p", "document_id": "y"},
        ),
    ],
)
def test_the_routes_check_what_they_are_sent(tmp_path: Path, route: str, body: dict) -> None:
    from career_agent.web.server import ApiError

    api, doc_id = _api(tmp_path)
    before = call(api, "GET", f"/documents/{doc_id}/history")
    with pytest.raises(ApiError) as refused:
        call(api, "POST", f"/documents/{doc_id}/{route}", body)
    assert refused.value.status == 400
    assert call(api, "GET", f"/documents/{doc_id}/history") == before


def test_the_routes_refuse_a_master_and_an_unknown_document(tmp_path: Path) -> None:
    from career_agent.web.server import ApiError

    conn = profile(tmp_path)
    tailored(conn)
    master = ResumeStore(conn).current_master()
    assert master is not None
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    with pytest.raises(ApiError) as refused:
        call(api, "POST", f"/documents/{master.id}/fit", {"expected_sha256": master.working_sha256})
    assert refused.value.status == 400
    with pytest.raises(ApiError) as missing:
        call(api, "POST", "/documents/01ARZ3NDEKTSV4RRFFQ69G5FAV/fit/cuts",
             {"refs": ["bullet/x"], "expected_sha256": "x", "proposal": "p"})  # fmt: skip
    assert missing.value.status == 404


# ------------------------------------------------ skills nothing places


def test_the_master_keeps_a_skill_nothing_places_on_the_page_and_asks(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    mixed(conn)
    master, _ = get_or_create_master(conn)
    got = groups(master.working)
    assert got["Other skills"] == (False, ["Terraform"])  # kept, apart from a job's
    assert "Terraform" not in got["Skills"][1]
    assert "Terraform" in visible_labels(master.working)
    found = analyze.run(conn, master.id)["findings"]
    asked = [f for f in found if f["kind"] == "SKILL_ORIGIN_UNKNOWN"]
    assert [f["text"] for f in asked] == ["Terraform"]
    assert not review.pending(conn, master.working) or all(
        i.kind != review.OUTSIDE_A_JOB for i in review.pending(conn, master.working)
    )


def test_an_older_master_with_the_skill_in_skills_is_kept_and_asked_about(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    mixed(conn)
    master, _ = get_or_create_master(conn)
    data = master.working.model_dump(mode="json")
    chips = [i for g in data["skills"] for i in g["items"] if i["label"] in ("SQL", "Terraform")]
    data["skills"] = [{"id": data["skills"][0]["id"], "name": "Skills", "hidden": False,
                       "items": chips}]  # fmt: skip
    store = ResumeStore(conn)
    store.save_working_copy(master.id, upgrade_resume_document(data),
                            expected_sha256=master.working_sha256)  # fmt: skip
    sha = store.get_document(master.id).working_sha256
    found = analyze.run(conn, master.id)["findings"]
    assert [f["text"] for f in found if f["kind"] == "SKILL_ORIGIN_UNKNOWN"] == ["Terraform"]
    assert store.get_document(master.id).working_sha256 == sha  # asked, never moved
    # A version for a job made from it leaves the skill out and says so.
    ad = "Platform Engineer\n\nRequirements\n- Terraform\n- SQL reporting\n"
    stored, _ = tailor(conn, ad={"title": "Platform Engineer", "text": ad})
    assert "Terraform" not in visible_labels(stored.working)
    assert "SQL" in visible_labels(stored.working)


def test_a_skill_nothing_places_is_never_suggested_for_a_job(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    mixed(conn)
    get_or_create_master(conn)
    ad = "Platform Engineer\n\nRequirements\n- Terraform\n- Kubernetes experience\n"
    stored, _ = tailor(conn, ad={"title": "Platform Engineer", "text": ad})
    labels = [s["action"]["label"] for s in explain(conn, stored)["suggestions"]
              if s["action"] and s["action"]["type"] == "add_skill"]  # fmt: skip
    assert "Terraform" not in labels
