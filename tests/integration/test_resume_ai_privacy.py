"""What AI drafting and review send, and when nothing is sent (Beta 3 gate).

The FAKE provider records every prompt; no network, no key. Distinctive
markers are planted where a careless payload would pick them up: contact
details, another job, an application note, unrelated confirmed evidence and
another profile. None may reach a prompt. Ordinary editing, preview, export
and Analyze never call a provider at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.integration.test_resume_reviewer import draft, review, setup
from tests.integration.test_resume_tailor import make

from career_agent.clock import new_id
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.storage.db import transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate

MARKERS = {
    "email": "privacy-marker@example.invalid",
    "phone": "+1 555 0100 7777",
    "link": "linkedin.com/in/privacy-marker",
    "note": "PRIVATE-NOTE-MARKER",
    "unrelated": "UNRELATED-EVIDENCE-MARKER beekeeping",
    "other_profile": "OTHER-PROFILE-MARKER",
}
#: A demo job that is not the one being drafted for.
OTHER_JOB = "Marketing Technology Specialist"


def call(api: Any, method: str, path: str, body: dict | None = None) -> Any:
    return api.handle_api(method, f"/api/resume{path}", {}, body or {})


def plant(api: Any) -> None:
    master = call(api, "POST", "/master")["master"]
    identity = {
        "full_name": "Riley Synthetic",
        "email": MARKERS["email"],
        "phone": MARKERS["phone"],
        "links": [{"id": new_id(), "kind": "LINKEDIN", "url": "https://" + MARKERS["link"]}],
    }
    call(
        api,
        "PATCH",
        "/master/identity",
        {"identity": identity, "expected_sha256": master["sha256"]},
    )
    conn = api.connect()
    try:
        other = conn.execute("SELECT id FROM job WHERE title = ?", (OTHER_JOB,)).fetchone()[0]
        with transaction(conn):
            ClaimRepo(conn).add(
                ensure_candidate(conn),
                VerifiedClaim(
                    claim_key="privacy-unrelated",
                    claim_type=ClaimType.ACHIEVEMENT,
                    text=MARKERS["unrelated"],
                    source=ClaimSource.SELF_ATTESTED,
                    verified=True,
                ),
            )
    finally:
        conn.close()
    api.handle_api("PATCH", f"/api/jobs/{other}/status", {}, {"status": "APPLIED"})
    api.handle_api("PATCH", f"/api/jobs/{other}/notes", {}, {"notes": MARKERS["note"]})


def leaked(prompts: list[str]) -> list[str]:
    text = "\n".join(prompts).casefold()
    found = [k for k, v in MARKERS.items() if v.casefold() in text]
    found += [
        w
        for w in (OTHER_JOB, "match_score", "search fit", "eligibility_status")
        if w.casefold() in text
    ]
    return found


def test_drafting_and_review_send_only_what_the_job_needs(tmp_path: Path, monkeypatch) -> None:
    api, fake, job = setup(tmp_path, monkeypatch)
    other = make(tmp_path, "b")
    conn = other.connect()
    try:
        with transaction(conn):
            ClaimRepo(conn).add(
                ensure_candidate(conn),
                VerifiedClaim(
                    claim_key="privacy-other",
                    claim_type=ClaimType.ACHIEVEMENT,
                    text=MARKERS["other_profile"],
                    source=ClaimSource.SELF_ATTESTED,
                    verified=True,
                ),
            )
    finally:
        conn.close()
    plant(api)
    view = draft(api, job)
    assert fake.kinds == ["drafter"], "drafting alone is one call"
    review(api, view["id"])
    assert fake.kinds == ["drafter", "reviewer"], "with review, two calls and no retry"
    assert "Business Systems Engineer" in fake.calls[0], "the prompt does carry the job"
    assert leaked(fake.calls + fake.systems) == []


def test_editing_preview_export_and_analyze_call_no_provider(tmp_path: Path, monkeypatch) -> None:
    api, fake, job = setup(tmp_path, monkeypatch)
    plant(api)
    master = call(api, "GET", "/master")["master"]
    doc_id = master["id"]
    one = call(api, "GET", f"/documents/{doc_id}")
    saved = call(
        api,
        "PATCH",
        f"/documents/{doc_id}/working",
        {"document": {**one["document"], "title": "Edited"}, "expected_sha256": one["sha256"]},
    )
    call(api, "POST", "/render", {"document": one["document"]})
    call(
        api,
        "POST",
        f"/documents/{doc_id}/exports",
        {"format": "JSON", "expected_sha256": saved["sha256"]},
    )
    call(api, "POST", f"/documents/{doc_id}/analyze", {"expected_sha256": saved["sha256"]})
    call(
        api,
        "POST",
        f"/documents/{doc_id}/analyze",
        {"expected_sha256": saved["sha256"], "job": job},
    )
    call(api, "GET", f"/jobs/{job}")
    call(api, "POST", f"/jobs/{job}/tailor", {})  # deterministic Tailor: no model either
    assert fake.calls == [], "no provider call outside the AI buttons"
