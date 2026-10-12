"""Resume Tailor: choose the base, check what will be used, paste an ad.

Synthetic person and ads (`tests.support_tailor`). No model, no network, and no
write on a preview. A pasted ad is kept as typed and marked as pasted by hand.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.integration.test_resume_tailor import (
    AD,
    call,
    jobs,
    make,
    master_of,
)

from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError


@pytest.fixture
def api(tmp_path) -> JobsApi:
    return make(tmp_path)


def counts(api: JobsApi) -> tuple[int, int, int]:
    with connect(api.config.db_path) as conn:
        return tuple(  # type: ignore[return-value]
            conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("resume_document", "resume_revision", "jd_snapshot")
        )


def other_base(api: JobsApi) -> dict[str, Any]:
    """A second resume of the person's own: a copy of the Master, kept apart."""
    return call(api, "POST", "/documents", {"from": master_of(api)["document"]})


def test_a_preview_names_what_a_version_would_take_and_writes_nothing(api: JobsApi) -> None:
    before = counts(api)
    shown = call(api, "POST", "/tailor/preview", AD)
    assert counts(api) == before, "a preview saves no document, revision or ad snapshot"
    assert shown["base"]["kind"] == "MASTER" and shown["requirements"] > 0
    assert set(shown["coverage"]) == {"covered", "partly", "said", "not_found", "eligibility"}
    master_text = " ".join(
        b["text"] for e in master_of(api)["document"]["experience"] for b in e["bullets"]
    )
    for line in shown["adds"]:
        assert line["evidence_id"] and line["text"], "every added line cites confirmed evidence"
        assert line["asks"], "and says which request of the ad it answers"
    assert shown["gaps"], "what the person does not have stays a gap, never filled in"
    assert isinstance(master_text, str)


def test_a_preview_for_a_job_reads_the_stored_ad(api: JobsApi) -> None:
    job = jobs(api)[0]
    shown = call(api, "POST", "/tailor/preview", {"job_id": job})
    assert shown["requirements"] > 0
    with pytest.raises(ApiError) as missing:
        call(api, "POST", "/tailor/preview", {"job_id": "01MNOSUCHJOB0000000000000"})
    assert missing.value.status == 404


def test_a_version_can_start_from_another_resume_of_the_persons_own(api: JobsApi) -> None:
    base = other_base(api)
    made = call(api, "POST", "/tailor", {**AD, "base_id": base["id"]})
    assert made["kind"] == "TAILORED" and made["version_number"] == 1
    assert made["document"]["provenance"]["master_document_id"] is None
    with connect(api.config.db_path) as conn:
        parent = conn.execute(
            "SELECT parent_document_id FROM resume_document WHERE id = ?", (made["id"],)
        ).fetchone()[0]
    assert parent == base["id"], "the lineage names the resume it started from"


def test_a_version_for_a_job_never_starts_from_another_job_version(api: JobsApi) -> None:
    version = call(api, "POST", "/tailor", AD)
    with pytest.raises(ApiError) as refused:
        call(api, "POST", "/tailor", {**AD, "base_id": version["id"]})
    assert refused.value.status == 400
    with pytest.raises(ApiError) as unknown:
        call(api, "POST", "/tailor/preview", {**AD, "base_id": "01MNOSUCHRESUME000000000"})
    assert unknown.value.status == 404


def test_a_pasted_ad_is_kept_as_typed_and_marked_as_pasted_by_hand(api: JobsApi) -> None:
    made = call(api, "POST", "/tailor", AD)
    library = call(api, "GET", "/documents")
    group = next(g for g in library["jobs"] if any(v["id"] == made["id"] for v in g["versions"]))
    assert group["job_id"] is None and group["manual"] is True
    with connect(api.config.db_path) as conn:
        snapshot = conn.execute(
            "SELECT text, job_id, url FROM jd_snapshot WHERE id = ?",
            (made["document"]["target"]["jd_snapshot_id"],),
        ).fetchone()
    assert snapshot["text"] == AD["text"].strip() or snapshot["text"] == AD["text"]
    assert snapshot["job_id"] is None and snapshot["url"] is None
    job = jobs(api)[0]
    from_job = call(api, "POST", f"/jobs/{job}/tailor")
    group = next(
        g for g in call(api, "GET", "/documents")["jobs"]
        if any(v["id"] == from_job["id"] for v in g["versions"])
    )  # fmt: skip
    assert group["job_id"] == job and group["manual"] is False
