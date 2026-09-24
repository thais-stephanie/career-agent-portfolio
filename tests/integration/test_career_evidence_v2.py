"""Career Evidence V2: a CV read as jobs, and imports that can be put down.

Every test here was written against a failure reproduced on `main` (c8e8b10)
with synthetic data, and fails on that code:

* a Markdown CV leaked its syntax into 17 of 34 suggestions, turned headings
  and date lines into claims, and dropped a whole "Additional experience"
  section;
* a long CV arrived as one flat queue with no company, role or dates;
* archiving an intake package left "145 statements waiting" on the first
  screen, and "discarding" a CV read hard-deleted it with the provenance of
  what had been confirmed from it;
* there was no way to remove an import and its unconfirmed suggestions.

All data is invented and every database is temporary.
"""

from __future__ import annotations

import base64
import shutil
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir
from tests.support_cv import EN_DASH, as_docx, as_pdf, load_cv, long_cv

from career_agent.intake import parse_package
from career_agent.intake.store import import_package
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import MIGRATIONS_DIR, connect, migrate, transaction
from career_agent.storage.review_counts import review_counts
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

#: Markdown syntax that must never reach a suggestion, a job or a heading.
MARKDOWN_TOKENS = ("**", "__", "##", "](", "`", "~~")


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[JobsApi, Path]]:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "v2-test")
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True), db


def call(api: JobsApi, method: str, path: str, body: dict | None = None) -> dict:
    return api.handle_api(method, path, {}, body or {})


def upload(api: JobsApi, data: bytes | str, name: str) -> dict:
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return call(
        api,
        "POST",
        "/api/cv/import",
        {"filename": name, "content_base64": base64.b64encode(raw).decode("ascii")},
    )


def proposals(review: dict) -> list[dict]:
    return [p for entry in review["entries"] for p in entry["proposals"]] + [
        p for group in review["sections"] for p in group["proposals"]
    ]


def rows(db: Path, sql: str, args: tuple = ()) -> list[tuple]:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(sql, args).fetchall()
    finally:
        conn.close()


def waiting_everywhere(api: JobsApi, db: Path) -> dict[str, int]:
    """Every surface that shows a "needs review" figure, side by side."""
    from career_agent.cli_local import _waiting_for_review

    steps = {s["key"]: s for s in call(api, "GET", "/api/firstrun")["steps"]}
    conn = connect(db)
    try:
        cli = _waiting_for_review(conn)
    finally:
        conn.close()
    return {
        "firstrun": steps["evidence"]["waiting"],
        "cv_imports": call(api, "GET", "/api/cv/imports")["counts"]["waiting"],
        "cli": cli,
    }


def a_package(scale: int = 40) -> dict:
    """An intake package of 3 * scale unanswered claims."""
    return {
        "schema_version": "1.0",
        "generator": {"kind": "SELF", "name": "Synthetic reader"},
        "sources": [{"ref": "cv", "kind": "RESUME", "title": "Invented CV.pdf"}],
        "claims": [
            {
                "type": "EMPLOYMENT",
                "text": f"Delivered invented workstream {n} for employer {employer}.",
                "source_ref": "cv",
                "employer": f"Invented Employer {employer}",
                "role_title": "Analyst",
                "period": {
                    "start": {
                        "original": f"Jan {2010 + employer}",
                        "normalized": f"{2010 + employer}-01",
                    },
                    "end": {
                        "original": f"Dec {2011 + employer}",
                        "normalized": f"{2011 + employer}-12",
                    },
                },
                "evidence": {"quote": f"Invented line {employer}-{n}"},
            }
            for employer in range(3)
            for n in range(scale)
        ],
    }


def stage_package(db: Path, scale: int = 40) -> str:
    conn = connect(db)
    try:
        return import_package(conn, parse_package(a_package(scale)), filename="invented.json")
    finally:
        conn.close()


# =========================================================================
# 1. MARKDOWN IS NORMALISED, STRUCTURE IS READ, PROVENANCE IS RAW
# =========================================================================


def test_markdown_never_leaks_into_a_suggestion_or_a_job(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    texts = [p["text"] for p in proposals(review)] + [p["evidence"] for p in proposals(review)]
    for entry in review["entries"]:
        texts += [entry["company"] or "", entry["role_title"] or ""]
    leaked = [t for t in texts if any(token in t for token in MARKDOWN_TOKENS)]
    assert leaked == []
    assert not any(t.startswith(("#", "*", "- ", "+ ", ">")) for t in texts)


def test_headings_and_date_lines_are_structure_not_claims(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    texts = {p["text"] for p in proposals(review)}
    assert not any("Fabrikam Cloud" in t and "Consultant" in t and len(t) < 60 for t in texts)
    assert not any(t.startswith(("Jan 2020", "Mar 2022", "2025")) for t in texts)
    assert "Experience" not in texts and "Skills" not in texts
    # The fenced block and the horizontal rule are decoration.
    assert not any("fenced block" in t for t in texts)


def test_the_complex_cv_is_read_as_company_role_dates_evidence(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    jobs = [(e["company"], e["role_title"], e["period_text"]) for e in review["entries"]]
    # Newest first, from the dates, and a role still held before one that
    # ended: the current Fabrikam role, then Teem (2025 to 2026), which sits at
    # the END of the document.
    assert [j[0] for j in jobs[:2]] == ["Fabrikam Cloud", "Teem"]
    assert ("Teem", "Business Operations / RevOps") in [j[:2] for j in jobs]
    assert ("Fabrikam Cloud", "Senior Solutions Consultant") in [j[:2] for j in jobs]
    assert ("Fabrikam Cloud", "Solutions Consultant") in [j[:2] for j in jobs]
    assert ("Contoso Health", "Implementation Lead") in [j[:2] for j in jobs]
    assert ("Northwind Retail", "Implementation Analyst") in [j[:2] for j in jobs]
    teem = next(e for e in review["entries"] if e["company"] == "Teem")
    assert [p["text"] for p in teem["proposals"]][:1] == [
        "Built the lead-routing rules in HubSpot for four regions."
    ]
    summary = review["summary"]
    assert summary["experiences"] == 5
    assert summary["suggestions"] == len(proposals(review))
    assert summary["waiting"] == summary["suggestions"]


def test_raw_provenance_survives_normalisation(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    routing = next(p for p in proposals(review) if "lead-routing rules" in p["text"])
    assert (
        routing["source_text"]
        == "1. Built the `lead-routing` rules in HubSpot for __four__ regions."
    )
    assert isinstance(routing["source_line"], int)
    teem = next(e for e in review["entries"] if e["company"] == "Teem")
    assert [line["text"] for line in teem["source"]] == [
        "### Teem",
        "#### Business Operations / RevOps",
        f"2025{EN_DASH}2026",
    ]
    assert rows(db, "SELECT COUNT(*) FROM cv_proposal WHERE source_text IS NULL") == [(0,)]


@pytest.mark.parametrize(
    "fixture", ["markdown_complex.md", "plain_promotions.txt", "portuguese.txt"]
)
def test_every_format_yields_the_same_structure(workspace, fixture) -> None:
    api, _db = workspace
    text = load_cv(fixture)
    shapes = []
    for name, data in (
        ("cv.md" if fixture.endswith(".md") else "cv.txt", text),
        ("cv.docx", as_docx(text)),
        ("cv.pdf", as_pdf(text)),
    ):
        review = upload(api, data, name)
        shapes.append([(e["company"], e["role_title"]) for e in review["entries"]])
    assert shapes[0] == shapes[1] == shapes[2]
    assert shapes[0]


def test_one_company_several_promotions(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    globex = [e["role_title"] for e in review["entries"] if e["company"] == "Globex Logistics"]
    assert globex == ["Operations Director", "Operations Manager", "Operations Analyst"]


def test_missing_dates_duplicates_and_malformed_markdown(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("overlap_missing.md"), "casey.md")
    umbrella = next(e for e in review["entries"] if e["company"] == "Umbrella Corp")
    # Never invented: no role, no dates, and the review says so.
    assert umbrella["role_title"] is None and umbrella["period_text"] is None
    assert set(umbrella["unresolved"]) >= {"role", "dates"}
    # Undated sorts last.
    assert review["entries"][-1]["company"] == "Umbrella Corp"
    duplicates = [p for p in proposals(review) if p["duplicate_of"]]
    assert len(duplicates) == 1 and "duplicate" in duplicates[0]["attention"]
    assert review["summary"]["attention"] >= 3
    assert all("**" not in p["text"] for p in proposals(review))


def test_accented_portuguese_headings_are_read(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("portuguese.txt"), "maria.txt")
    assert [e["company"] for e in review["entries"]][:2] == ["Empresa Fictícia S.A."] * 2
    assert {g["group"] for g in review["sections"]} >= {"education", "skills"}


# =========================================================================
# 2. NOTHING IS CONFIRMED BY BEING READ, OR IN A BATCH
# =========================================================================


def test_importing_confirms_nothing(workspace) -> None:
    api, db = workspace
    upload(api, long_cv(10, 14), "long.txt")
    assert rows(db, "SELECT COUNT(*) FROM verified_claim") == [(0,)]


def test_there_is_no_batch_confirmation(workspace) -> None:
    api, db = workspace
    review = upload(api, long_cv(3, 4), "short.txt")
    keys = [p["claim_key"] for p in proposals(review)]
    with pytest.raises(ApiError):
        call(
            api,
            "POST",
            f"/api/cv/imports/{review['import_id']}/organize",
            {"action": "confirm", "keys": keys},
        )
    assert rows(db, "SELECT COUNT(*) FROM verified_claim") == [(0,)]


def test_a_confirmed_claim_carries_its_job(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    senior = next(e for e in review["entries"] if e["role_title"] == "Senior Solutions Consultant")
    key = senior["proposals"][0]["claim_key"]
    call(
        api,
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {"claim_key": key, "decision": "ACCEPTED"},
    )
    ((employer, start, end, evidence),) = rows(
        db, "SELECT employer, period_start, period_end, evidence_ref FROM verified_claim"
    )
    assert (employer, start, end) == ("Fabrikam Cloud", "2022-03", None)
    assert "**" not in evidence


# =========================================================================
# 3. ARCHIVE IS REVERSIBLE AND COUNTS NOWHERE; RESTORE BRINGS IT BACK
# =========================================================================


def test_one_definition_of_waiting_across_every_surface(workspace) -> None:
    api, db = workspace
    review = upload(api, long_cv(8, 13), "long.txt")
    stage_package(db, 40)
    counts = waiting_everywhere(api, db)
    expected = review["summary"]["waiting"] + 120
    assert counts == {"firstrun": expected, "cv_imports": expected, "cli": expected}


def test_archiving_a_package_empties_every_counter_and_restore_refills_it(workspace) -> None:
    """The reproduced bug: archived, and still "145 statements waiting"."""
    api, db = workspace
    package_id = stage_package(db, 48)
    assert waiting_everywhere(api, db)["firstrun"] == 144
    assert call(api, "GET", "/api/career")["unassigned"] == 144

    call(api, "POST", f"/api/intake/{package_id}/discard")
    assert set(waiting_everywhere(api, db).values()) == {0}
    assert call(api, "GET", "/api/career")["unassigned"] == 0
    # Kept whole: archiving deletes nothing.
    assert rows(db, "SELECT COUNT(*) FROM intake_claim") == [(144,)]
    documents = next(
        s for s in call(api, "GET", "/api/firstrun")["steps"] if s["key"] == "documents"
    )
    assert documents["done"] and documents["archived"] == 1

    call(api, "POST", f"/api/intake/{package_id}/restore")
    assert waiting_everywhere(api, db)["firstrun"] == 144
    assert call(api, "GET", "/api/career")["unassigned"] == 144


def test_archiving_a_cv_read_is_reversible(workspace) -> None:
    api, db = workspace
    review = upload(api, long_cv(10, 14), "long.txt")
    total = review["summary"]["suggestions"]
    assert total >= 140
    call(api, "POST", f"/api/cv/imports/{review['import_id']}/archive")
    assert set(waiting_everywhere(api, db).values()) == {0}
    assert call(api, "GET", "/api/career")["unassigned"] == 0
    assert rows(db, "SELECT COUNT(*) FROM cv_proposal") == [(total,)]
    listed = call(api, "GET", "/api/cv/imports")["imports"]
    assert [i["archived"] for i in listed] == [True]

    call(api, "POST", f"/api/cv/imports/{review['import_id']}/restore")
    assert waiting_everywhere(api, db)["firstrun"] == total
    assert call(api, "GET", "/api/career")["unassigned"] == total


def test_an_archived_import_cannot_be_answered(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    key = proposals(review)[0]["claim_key"]
    call(api, "POST", f"/api/cv/imports/{review['import_id']}/archive")
    with pytest.raises(ApiError) as refused:
        call(
            api,
            "POST",
            f"/api/cv/imports/{review['import_id']}/decide",
            {"claim_key": key, "decision": "ACCEPTED"},
        )
    assert refused.value.status == 409
    package_id = stage_package(db, 2)
    call(api, "POST", f"/api/intake/{package_id}/discard")
    first = rows(db, "SELECT claim_key FROM intake_claim LIMIT 1")[0][0]
    with pytest.raises(ApiError):
        call(
            api,
            "POST",
            f"/api/intake/{package_id}/answer",
            {"claim_key": first, "answer": "CONFIRM"},
        )
    assert rows(db, "SELECT COUNT(*) FROM verified_claim") == [(0,)]


# =========================================================================
# 4. DELETE IS PERMANENT, SAYS WHAT IT WILL DO, AND KEEPS CONFIRMED EVIDENCE
# =========================================================================


def test_the_wrong_cv_can_be_removed_with_all_its_suggestions(workspace) -> None:
    """ "I uploaded the wrong CV. Remove this import and all 142 unconfirmed
    suggestions." Zero confirmed: nothing of it stays."""
    api, db = workspace
    review = upload(api, long_cv(11, 13), "wrong.txt")
    import_id = review["import_id"]
    total = review["summary"]["suggestions"]
    assert total == 11 * 13 + 5

    plan = call(api, "POST", f"/api/cv/imports/{import_id}/delete")
    assert plan == {
        "deleted": False,
        "plan": {
            "pending": total,
            "rejected": 0,
            "removed": total,
            "confirmed_kept": 0,
            "entries": 11,
        },
    }
    assert rows(db, "SELECT COUNT(*) FROM cv_proposal") == [(total,)], "a plan changed something"

    done = call(api, "POST", f"/api/cv/imports/{import_id}/delete", {"confirm": True})
    assert done["deleted"] and done["imports"] == []
    for table in ("cv_import", "cv_proposal", "cv_entry"):
        assert rows(db, f"SELECT COUNT(*) FROM {table}") == [(0,)], table
    assert set(waiting_everywhere(api, db).values()) == {0}
    documents = next(
        s for s in call(api, "GET", "/api/firstrun")["steps"] if s["key"] == "documents"
    )
    assert not documents["done"]


def test_a_delete_keeps_confirmed_evidence_and_its_provenance(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    import_id = review["import_id"]
    teem = next(e for e in review["entries"] if e["company"] == "Teem")
    kept = teem["proposals"][0]
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": kept["claim_key"], "decision": "EDITED", "text": "Built lead routing."},
    )
    rejected = teem["proposals"][1]
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": rejected["claim_key"], "decision": "REJECTED"},
    )

    plan = call(api, "POST", f"/api/cv/imports/{import_id}/delete")["plan"]
    total = review["summary"]["suggestions"]
    assert plan["confirmed_kept"] == 1
    assert plan["rejected"] == 1
    assert plan["removed"] == total - 1

    call(api, "POST", f"/api/cv/imports/{import_id}/delete", {"confirm": True})
    assert call(api, "GET", "/api/cv/imports")["imports"] == []
    # The confirmed claim is untouched and still verified.
    (claim,) = rows(db, "SELECT text, verified, employer FROM verified_claim")
    assert claim == ("Built lead routing.", 1, "Teem")
    # The row it cites stays, raw line and all, with the job above it.
    assert rows(db, "SELECT claim_key, source_text FROM cv_proposal") == [
        (kept["claim_key"], kept["source_text"])
    ]
    assert rows(db, "SELECT company FROM cv_entry") == [("Teem",)]
    evidence = call(api, "GET", "/api/evidence")
    assert evidence["confirmed"] == 1
    with pytest.raises(ApiError):
        call(api, "GET", f"/api/cv/imports/{import_id}")


def test_deleting_a_package_both_ways(workspace) -> None:
    api, db = workspace
    empty = stage_package(db, 5)
    assert call(api, "POST", f"/api/intake/{empty}/delete")["plan"]["removed"] == 15
    call(api, "POST", f"/api/intake/{empty}/delete", {"confirm": True})
    assert rows(db, "SELECT COUNT(*) FROM intake_package") == [(0,)]
    assert rows(db, "SELECT COUNT(*) FROM intake_claim") == [(0,)]

    kept = stage_package(db, 6)
    first = rows(db, "SELECT claim_key FROM intake_claim ORDER BY claim_key LIMIT 1")[0][0]
    call(api, "POST", f"/api/intake/{kept}/answer", {"claim_key": first, "answer": "CONFIRM"})
    plan = call(api, "POST", f"/api/intake/{kept}/delete")["plan"]
    assert plan == {"waiting": 17, "rejected": 0, "removed": 17, "confirmed_kept": 1}
    call(api, "POST", f"/api/intake/{kept}/delete", {"confirm": True})
    assert rows(db, "SELECT claim_key, review_state FROM intake_claim") == [(first, "CONFIRMED")]
    assert call(api, "GET", "/api/intake")["packages"] == []
    assert rows(db, "SELECT COUNT(*) FROM verified_claim WHERE verified = 1") == [(1,)]
    assert set(waiting_everywhere(api, db).values()) == {0}
    # The same file can be imported again: the deleted one does not answer for it.
    again = stage_package(db, 6)
    assert again != kept


# =========================================================================
# 5. MOVE, MERGE, SPLIT, CREATE, EDIT, DELETE: PROVENANCE SURVIVES
# =========================================================================


def organize(api: JobsApi, import_id: str, body: dict) -> dict:
    return call(api, "POST", f"/api/cv/imports/{import_id}/organize", body)


def fingerprint(review: dict) -> dict[str, tuple]:
    return {
        p["claim_key"]: (p["text"], p["evidence"], p["source_text"], p["source_line"])
        for p in proposals(review)
    }


def test_move_merge_split_preserve_provenance(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    import_id = review["import_id"]
    before = fingerprint(review)
    by_role = {e["role_title"]: e for e in review["entries"]}
    senior, earlier = by_role["Senior Solutions Consultant"], by_role["Solutions Consultant"]

    moved = organize(
        api,
        import_id,
        {
            "action": "move",
            "keys": [senior["proposals"][0]["claim_key"]],
            "entry_key": earlier["entry_key"],
        },
    )
    assert fingerprint(moved) == before
    target = next(e for e in moved["entries"] if e["entry_key"] == earlier["entry_key"])
    assert senior["proposals"][0]["claim_key"] in [p["claim_key"] for p in target["proposals"]]

    merged = organize(
        api,
        import_id,
        {"action": "merge", "source": senior["entry_key"], "target": earlier["entry_key"]},
    )
    assert fingerprint(merged) == before
    survivor = next(e for e in merged["entries"] if e["entry_key"] == earlier["entry_key"])
    assert len(survivor["source"]) == len(senior["source"]) + len(earlier["source"])
    assert senior["entry_key"] not in [e["entry_key"] for e in merged["entries"]]

    keys = [p["claim_key"] for p in survivor["proposals"][:2]]
    split = organize(
        api,
        import_id,
        {
            "action": "split",
            "keys": keys,
            "fields": {
                "role_title": "Senior Solutions Consultant",
                "period_start": "2022-03",
                "current_role": True,
            },
        },
    )
    assert fingerprint(split) == before
    new = next(e for e in split["entries"] if e["entry_key"] == split["result"]["entry_key"])
    assert new["company"] == "Fabrikam Cloud" and new["current_role"]
    assert [p["claim_key"] for p in new["proposals"]] == keys
    # Chronology from the dates: the current role is first.
    assert split["entries"][0]["entry_key"] == new["entry_key"]


def test_correcting_a_job_and_creating_a_missing_one(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("overlap_missing.md"), "casey.md")
    import_id = review["import_id"]
    umbrella = next(e for e in review["entries"] if e["company"] == "Umbrella Corp")
    edited = organize(
        api,
        import_id,
        {
            "action": "edit_entry",
            "entry_key": umbrella["entry_key"],
            "fields": {
                "role_title": "Organiser",
                "period_start": "2018-01",
                "period_end": "2018-12",
            },
        },
    )
    fixed = next(e for e in edited["entries"] if e["entry_key"] == umbrella["entry_key"])
    assert fixed["unresolved"] == [] and fixed["edited"]
    # The source still says what the document said.
    assert [line["text"] for line in fixed["source"]] == ["### Umbrella Corp"]
    with pytest.raises(ApiError):
        organize(
            api,
            import_id,
            {
                "action": "edit_entry",
                "entry_key": umbrella["entry_key"],
                "fields": {"period_start": "2019-05", "period_end": "2018-01"},
            },
        )
    created = organize(
        api,
        import_id,
        {"action": "create_entry", "fields": {"company": "Initrode", "role_title": "Mentor"}},
    )
    assert any(e["company"] == "Initrode" for e in created["entries"])
    key = fixed["proposals"][0]["claim_key"]
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": key, "decision": "ACCEPTED"},
    )
    assert rows(db, "SELECT employer, period_start, period_end FROM verified_claim") == [
        ("Umbrella Corp", "2018-01", "2018-12")
    ]


def test_batch_reject_and_delete_never_touch_confirmed(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("overlap_missing.md"), "casey.md")
    import_id = review["import_id"]
    keys = [p["claim_key"] for p in proposals(review)]
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": keys[0], "decision": "ACCEPTED"},
    )
    rejected = organize(api, import_id, {"action": "reject", "keys": keys})
    assert rejected["result"]["rejected"] == len(keys) - 1
    with pytest.raises(ApiError):
        organize(api, import_id, {"action": "delete", "keys": keys})
    deleted = organize(api, import_id, {"action": "delete", "keys": keys, "confirm": True})
    assert deleted["result"]["deleted"] == len(keys) - 1
    assert [p["claim_key"] for p in proposals(deleted)] == [keys[0]]
    assert rows(db, "SELECT COUNT(*) FROM verified_claim WHERE verified = 1") == [(1,)]
    with pytest.raises(ApiError):
        call(
            api,
            "POST",
            f"/api/cv/imports/{import_id}/decide",
            {"claim_key": keys[0], "decision": "REJECTED"},
        )


def test_an_empty_job_can_be_deleted_and_a_full_one_cannot(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    import_id = review["import_id"]
    entry = review["entries"][0]
    with pytest.raises(ApiError):
        organize(api, import_id, {"action": "delete_entry", "entry_key": entry["entry_key"]})
    organize(
        api,
        import_id,
        {
            "action": "move",
            "keys": [p["claim_key"] for p in entry["proposals"]],
            "entry_key": review["entries"][1]["entry_key"],
        },
    )
    after = organize(api, import_id, {"action": "delete_entry", "entry_key": entry["entry_key"]})
    assert entry["entry_key"] not in [e["entry_key"] for e in after["entries"]]


def test_chronology_is_deterministic(workspace) -> None:
    api, _db = workspace
    text = load_cv("markdown_complex.md")
    orders = [
        [e["entry_key"] for e in upload(api, text, f"riley-{n}.md")["entries"]] for n in range(3)
    ]
    assert orders[0] == orders[1] == orders[2]
    review = upload(api, text, "riley.md")
    # A role still held first; then by when each ended, newest first.
    ends = [9999 if e["current_role"] else e["end_year"] for e in review["entries"]]
    assert ends == sorted(ends, reverse=True)


# =========================================================================
# 6. A LONG CV STAYS FAST, AND OPENS ON A SUMMARY
# =========================================================================


def test_a_long_import_is_fast_and_grouped(workspace) -> None:
    api, _db = workspace
    started = time.perf_counter()
    review = upload(api, long_cv(20, 14, markdown=True), "long.md")
    reading = time.perf_counter() - started
    assert review["summary"]["suggestions"] == 20 * 14 + 5
    assert review["summary"]["experiences"] == 20
    assert max(e["total"] for e in review["entries"]) == 14
    started = time.perf_counter()
    for _ in range(5):
        call(api, "GET", f"/api/cv/imports/{review['import_id']}")
        call(api, "GET", "/api/career")
    per_round = (time.perf_counter() - started) / 5
    assert reading < 5.0, f"importing took {reading:.2f}s"
    assert per_round < 2.0, f"a review round took {per_round:.2f}s"


# =========================================================================
# 7. AN EXISTING ALPHA WORKSPACE UPGRADES WITHOUT LOSING ANYTHING
# =========================================================================


def test_an_existing_workspace_upgrades_in_place(tmp_path: Path) -> None:
    """A database at migration 0038 with a flat CV read, answers and a
    confirmed claim: every row reads as it did, confirmed stays confirmed,
    and the flat suggestions are shown as not yet placed in a job."""
    from career_agent.cv.propose import Proposal, to_claim
    from career_agent.domain.enums import ClaimType
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import CvReviewRepo, ensure_candidate
    from career_agent.web.cv_api import cv_review_payload

    old = tmp_path / "migrations-0038"
    old.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) <= 38:
            shutil.copyfile(path, old / path.name)
    conn = connect(tmp_path / "alpha.db")
    migrate(conn, old)
    flat = [
        Proposal(
            claim_key=f"experience-{n:02d}-line",
            claim_type=ClaimType.EMPLOYMENT,
            text=f"Did invented thing {n}",
            section="experience",
            evidence=f"- Did invented thing {n}",
        )
        for n in range(1, 4)
    ]
    with transaction(conn):
        candidate = ensure_candidate(conn)
        now = "2026-01-01T00:00:00Z"
        conn.execute(
            "INSERT INTO cv_import (id, candidate_id, source_name, kind, pages, characters,"
            " text_sha256, status, created_at, updated_at)"
            " VALUES ('old', ?, 'old.txt', 'txt', 1, 300, 'x', 'OPEN', ?, ?)",
            (candidate, now, now),
        )
        for n, proposal in enumerate(flat, start=1):
            conn.execute(
                "INSERT INTO cv_proposal (id, import_id, claim_key, claim_type, section, text,"
                " evidence, has_measurement, decision, ordinal, created_at)"
                " VALUES (?, 'old', ?, 'EMPLOYMENT', 'experience', ?, ?, 0, 'PENDING', ?, ?)",
                (f"p{n}", proposal.claim_key, proposal.text, proposal.evidence, n, now),
            )
        claim_id = ClaimRepo(conn).supersede(candidate, to_claim(flat[0]))
        conn.execute(
            "UPDATE cv_proposal SET decision = 'ACCEPTED', decided_text = ?, claim_id = ?"
            " WHERE claim_key = ?",
            (flat[0].text, claim_id, flat[0].claim_key),
        )
    before = {
        table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
        for table in ("verified_claim", "cv_import", "cv_proposal")
    }
    # Everything main added after 0038 runs, 0039 first; later migrations
    # (0040 adds an experience description) must not disturb what 0039 kept.
    applied = [m.version for m in migrate(conn)]
    assert applied[0] == 39 and applied == sorted(applied)
    for table, old_rows in before.items():
        width = len(old_rows[0])
        assert [
            tuple(r)[:width] for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")
        ] == old_rows
    repo = CvReviewRepo(conn)
    record = repo.get_import(candidate, "old")
    review = cv_review_payload(record, repo.proposals("old"), repo.entries("old"))
    assert review["entries"] == []
    (unplaced,) = review["sections"]
    assert unplaced["group"] == "unplaced" and unplaced["total"] == 3
    assert unplaced["confirmed"] == 1 and unplaced["waiting"] == 2
    assert review_counts(conn, candidate).waiting == 2
    assert [c.verified for c in ClaimRepo(conn).current(candidate)] == [True]
    conn.close()


def test_the_migration_is_recorded_once(tmp_path: Path) -> None:
    conn = connect(tmp_path / "fresh.db")
    migrate(conn)
    assert migrate(conn) == []
    columns = {r[1] for r in conn.execute("PRAGMA table_info(cv_import)")}
    assert {"archived_at", "deleted_at"} <= columns
    conn.close()


# =========================================================================
# 8. THE CAREER WORKSPACE: SUGGESTED EXPERIENCES FROM THE CV'S JOBS, BY DATE
# =========================================================================


def test_the_career_workspace_suggests_the_cvs_jobs_in_date_order(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("markdown_complex.md"), "riley.md")
    northwind = next(e for e in review["entries"] if e["company"] == "Northwind Retail")
    key = northwind["proposals"][0]["claim_key"]
    call(
        api,
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {"claim_key": key, "decision": "ACCEPTED"},
    )
    career = call(api, "GET", "/api/career")
    suggested = [
        (p["company"], p["title"], p["period_start"] or p["period_label"])
        for p in career["proposals"]
    ]
    # Every job the CV names, newest first, with the role it gave; year-only
    # spans as written rather than as invented months.
    assert [s[0] for s in suggested] == [
        "Fabrikam Cloud",
        "Teem",
        "Fabrikam Cloud",
        "Contoso Health",
        "Northwind Retail",
    ]
    assert ("Northwind Retail", "Implementation Analyst", f"2015 {EN_DASH} 2017") in suggested
    confirmed_group = next(p for p in career["proposals"] if p["company"] == "Northwind Retail")
    assert key in confirmed_group["keys"]

    # Accepting two groups in the wrong order: the list still reads by date.
    for company in ("Northwind Retail", "Contoso Health"):
        group = next(
            p for p in call(api, "GET", "/api/career")["proposals"] if p["company"] == company
        )
        command = {
            "action": "create",
            "keys": group["keys"],
            "metadata": {
                "company": group["company"],
                "title": group["title"],
                "period_start": group["period_start"],
                "period_end": group["period_end"],
            },
        }
        preview = call(api, "POST", "/api/career/preview", command)
        call(
            api,
            "POST",
            "/api/career/changes",
            {"command": command, "preview_hash": preview["preview_hash"]},
        )
    experiences = [e["company"] for e in call(api, "GET", "/api/career")["experiences"]]
    assert experiences == ["Contoso Health", "Northwind Retail"]


# =========================================================================
# 9. FOUND BY ADVERSARIAL REVIEW, EACH REPRODUCED FIRST
# =========================================================================


def test_a_reversed_date_range_is_unresolved_not_a_crash(workspace) -> None:
    api, _db = workspace
    text = load_cv("plain_promotions.txt").replace("Mar 2018", "Mar 2021", 1)
    review = upload(api, text, "typo.txt")
    manager = next(e for e in review["entries"] if e["role_title"] == "Operations Manager")
    assert manager["period_start"] is None and manager["period_text"]
    assert review["summary"]["suggestions"] == 10


def test_long_and_hostile_lines_are_read_in_linear_time(workspace) -> None:
    api, _db = workspace
    from career_agent.cv.propose import read_cv

    hostile = "\n".join(
        [
            "EXPERIENCE",
            "**a " * 16000,
            "# a" + " " * 40000 + "b",
            "Acme, Consultant, 2020 - 2021",
            " " * 40000 + "x",
            *["Name Only" for _ in range(6000)],
            "- did things",
        ]
    )
    started = time.perf_counter()
    read = read_cv(hostile)
    elapsed = time.perf_counter() - started
    assert elapsed < 5.0, f"{elapsed:.1f}s"
    assert any(p.text == "did things" for p in read.proposals)


def test_a_deleted_package_cannot_be_restored_or_selected(workspace) -> None:
    api, db = workspace
    deleted = stage_package(db, 4)
    first = rows(db, "SELECT claim_key FROM intake_claim ORDER BY claim_key LIMIT 1")[0][0]
    call(api, "POST", f"/api/intake/{deleted}/answer", {"claim_key": first, "answer": "CONFIRM"})
    call(api, "POST", f"/api/intake/{deleted}/delete", {"confirm": True})
    conn = connect(db)
    try:
        live = import_package(
            conn,
            parse_package({**a_package(2), "generator": {"kind": "SELF", "name": "Other"}}),
            filename="live.json",
        )
    finally:
        conn.close()
    assert waiting_everywhere(api, db)["firstrun"] == 6
    for route in ("restore", "select"):
        with pytest.raises(ApiError) as refused:
            call(api, "POST", f"/api/intake/{deleted}/{route}")
        assert refused.value.status == 404, route
    assert call(api, "GET", "/api/intake")["active_package_id"] == live
    assert waiting_everywhere(api, db)["firstrun"] == 6
    assert all(
        item.get("package_id") != deleted
        for item in call(api, "GET", "/api/career/evidence", None).get("items", [])
    )


def test_job_dates_are_checked_across_requests(workspace) -> None:
    api, _db = workspace
    review = upload(api, load_cv("overlap_missing.md"), "casey.md")
    import_id = review["import_id"]
    umbrella = next(e for e in review["entries"] if e["company"] == "Umbrella Corp")
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/organize",
        {
            "action": "edit_entry",
            "entry_key": umbrella["entry_key"],
            "fields": {"period_start": "2022-01"},
        },
    )
    with pytest.raises(ApiError) as refused:
        call(
            api,
            "POST",
            f"/api/cv/imports/{import_id}/organize",
            {
                "action": "edit_entry",
                "entry_key": umbrella["entry_key"],
                "fields": {"period_end": "2020-01"},
            },
        )
    assert refused.value.status == 409
    acme = next(e for e in review["entries"] if e["company"] == "Acme Analytics")
    cleared = call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/organize",
        {
            "action": "edit_entry",
            "entry_key": acme["entry_key"],
            "fields": {"period_start": None, "period_end": None},
        },
    )
    after = next(e for e in cleared["entries"] if e["entry_key"] == acme["entry_key"])
    assert after["start_year"] is None and "dates" in after["unresolved"]
    assert cleared["entries"][-1]["entry_key"] in {acme["entry_key"], umbrella["entry_key"]}


# =========================================================================
# 10. NEEDS ORGANIZING: LIVE EVIDENCE WITH NO EXPERIENCE, AND NOTHING ELSE
# =========================================================================


def organizing(api: JobsApi) -> tuple[int, set[str]]:
    """The button's count and the inbox list's keys. They must agree."""
    count = call(api, "GET", "/api/career")["unassigned"]
    page = api.handle_api(
        "GET", "/api/career/evidence", {"experience": ["inbox"], "limit": ["100"]}, {}
    )
    assert page["total"] == count, (page["total"], count)
    return count, set(page["keys"])


def add_claim(db: Path, key: str, *, verified: bool, retire: bool = False) -> None:
    from career_agent.domain.claims import VerifiedClaim
    from career_agent.domain.enums import ClaimSource, ClaimType
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    conn = connect(db)
    try:
        with transaction(conn):
            candidate = ensure_candidate(conn)
            claim = VerifiedClaim(
                claim_key=key,
                claim_type=ClaimType.PROJECT,
                text=f"Invented statement {key}.",
                source=ClaimSource.SELF_ATTESTED,
                verified=verified,
            )
            ClaimRepo(conn).add(candidate, claim)
            if retire:
                ClaimRepo(conn).supersede(candidate, claim.next_revision(verified=False))
    finally:
        conn.close()


def test_needs_organizing_is_live_evidence_only(workspace) -> None:
    api, db = workspace
    review = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    import_id = review["import_id"]
    keys = [p["claim_key"] for p in proposals(review)]
    total = len(keys)

    # Unconfirmed live suggestions: all of them.
    count, listed = organizing(api)
    assert count == total and listed == set(keys)

    # Confirmed but unassigned: still needs an experience, so still listed,
    # now as confirmed evidence.
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": keys[0], "decision": "ACCEPTED"},
    )
    count, listed = organizing(api)
    assert count == total and keys[0] in listed

    # Rejected: answered, so not.
    call(
        api,
        "POST",
        f"/api/cv/imports/{import_id}/decide",
        {"claim_key": keys[1], "decision": "REJECTED"},
    )
    count, listed = organizing(api)
    assert count == total - 1 and keys[1] not in listed

    # Retired (withdrawn after confirming): not.
    call(api, "POST", f"/api/evidence/{keys[0]}/retire")
    count, listed = organizing(api)
    assert count == total - 2 and keys[0] not in listed

    # A draft claim (never confirmed) is live: listed, and waiting for review.
    add_claim(db, "draft-statement", verified=False)
    add_claim(db, "withdrawn-statement", verified=True, retire=True)
    count, listed = organizing(api)
    assert "draft-statement" in listed and "withdrawn-statement" not in listed
    assert count == total - 1
    evidence = call(api, "GET", "/api/evidence")
    states = {c["claim_key"]: c["state"] for c in evidence["claims"]}
    assert states["draft-statement"] == "DRAFT"
    assert states["withdrawn-statement"] == "RETIRED"
    assert (evidence["retired"], evidence["drafts"]) == (2, 1)
    waiting = waiting_everywhere(api, db)
    assert set(waiting.values()) == {total - 2 + 1}, waiting

    # Archived: none of the read's suggestions. Restore: they come back.
    call(api, "POST", f"/api/cv/imports/{import_id}/archive")
    count, listed = organizing(api)
    assert listed == {"draft-statement"}
    call(api, "POST", f"/api/cv/imports/{import_id}/restore")
    count, listed = organizing(api)
    assert count == total - 1

    # Deleted: the unconfirmed suggestions are gone, and the row kept only as
    # the provenance of the (retired) confirmed claim is not a suggestion.
    call(api, "POST", f"/api/cv/imports/{import_id}/delete", {"confirm": True})
    count, listed = organizing(api)
    assert listed == {"draft-statement"}
    assert rows(db, "SELECT COUNT(*) FROM cv_proposal") == [(1,)]


def test_a_deleted_packages_provenance_rows_are_not_suggestions(workspace) -> None:
    api, db = workspace
    package_id = stage_package(db, 3)
    first = rows(db, "SELECT claim_key FROM intake_claim ORDER BY claim_key LIMIT 1")[0][0]
    call(api, "POST", f"/api/intake/{package_id}/answer", {"claim_key": first, "answer": "CONFIRM"})
    assert organizing(api)[0] == 9
    call(api, "POST", f"/api/intake/{package_id}/delete", {"confirm": True})
    count, listed = organizing(api)
    # Only the confirmed claim itself: live evidence with no experience.
    assert count == 1
    (confirmed,) = rows(db, "SELECT claim_key FROM verified_claim WHERE verified = 1")
    assert listed == {confirmed[0]}
