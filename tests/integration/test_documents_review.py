"""Documents and the guided import review, reconciled against the profile.

Every import -- a CV read or a document package -- is reviewed the same way:
experience by experience, each one compared with the canonical profile
(`career_experience`), each statement answered on its own and saved at once.
All data is invented and every database is temporary.
"""

from __future__ import annotations

import base64
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.integration.test_career_evidence_v2 import a_package, stage_package
from tests.support import committed_config_dir
from tests.support_cv import EM_DASH, load_cv

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[tuple[JobsApi, Path]]:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "documents-test")
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True), db


def call(api: JobsApi, method: str, path: str, body: dict | None = None) -> dict:
    return api.handle_api(method, path, {}, body or {})


def upload(api: JobsApi, text: str, name: str) -> str:
    raw = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return call(api, "POST", "/api/cv/import", {"filename": name, "content_base64": raw})[
        "import_id"
    ]


def review(api: JobsApi, import_id: str, kind: str = "cv") -> dict:
    return call(api, "GET", f"/api/documents/{kind}/{import_id}")


def entry(model: dict, company: str, role: str | None = None) -> dict:
    return next(
        e
        for e in model["experiences"]
        if e["company"] == company and (role is None or e["role"] == role)
    )


def answer(api: JobsApi, import_id: str, key: str, verb: str, **extra) -> dict:
    return call(
        api, "POST", f"/api/documents/cv/{import_id}/answer", {"key": key, "answer": verb, **extra}
    )


def place(api: JobsApi, import_id: str, key: str, choice: str, **extra) -> dict:
    return call(
        api,
        "POST",
        f"/api/documents/cv/{import_id}/place",
        {"entry": key, "choice": choice, **extra},
    )


# =========================================================================


def test_a_new_user_sees_every_experience_as_new(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    listed = call(api, "GET", "/api/documents")["documents"]
    assert [(d["kind"], d["name"], d["experiences"]) for d in listed] == [("cv", "riley.md", 5)]
    model = review(api, import_id)
    assert {e["state"] for e in model["experiences"]} == {"NEW"}
    assert model["summary"]["experiences"] == 5
    assert model["summary"]["new_experiences"] == 5
    assert model["sections"]["skills"] and model["sections"]["certifications"]
    assert model["sections"]["education"]
    # Chronological, newest first: the role still held leads.
    assert model["experiences"][0]["role"] == "Senior Solutions Consultant"


def test_adding_an_experience_confirms_nothing_and_highlights_need_review(workspace) -> None:
    api, db = workspace
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    teem = entry(review(api, import_id), "Teem")
    placed = place(api, import_id, teem["key"], "new")
    assert placed["event_id"] and placed["experience_id"]
    career = call(api, "GET", "/api/career")
    (experience,) = career["experiences"]
    assert (experience["company"], experience["title"]) == ("Teem", "Business Operations / RevOps")
    # Year-only dates stay years: nothing invented.
    assert (experience["period_start"], experience["period_end"]) == ("2025", "2026")
    assert experience["highlights"] == [] and experience["waiting"] == 4
    teem = entry(placed, "Teem")
    assert teem["state"] == "EXISTING_WITH_NEW_DETAILS" and teem["counts"]["new"] == 4
    conn = connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM verified_claim").fetchone()[0] == 0
    finally:
        conn.close()

    first = teem["items"][0]
    after = answer(api, import_id, first["key"], "CONFIRM")
    teem = entry(after, "Teem")
    assert teem["counts"]["confirmed"] == 1 and teem["counts"]["new"] == 3
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    assert [h["text"] for h in experience["highlights"]] == [first["text"]]
    assert experience["highlights"][0]["origin"] == "document"


def test_an_unchanged_experience_says_so_on_a_second_read(workspace) -> None:
    api, _db = workspace
    first = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    initech = entry(review(api, first), "Initech")
    place(api, first, initech["key"], "new")
    for item in initech["items"]:
        answer(api, first, item["key"], "CONFIRM")
    second = upload(api, load_cv("plain_promotions.txt"), "jordan-again.txt")
    again = entry(review(api, second), "Initech")
    assert again["state"] == "EXISTING_UNCHANGED"
    assert again["match"]["company"] == "Initech"
    assert all(i["already"] for i in again["items"])


def test_a_date_conflict_offers_both_and_changes_only_what_is_chosen(workspace) -> None:
    api, _db = workspace
    first = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    manager = entry(review(api, first), "Globex Logistics", "Operations Manager")
    place(api, first, manager["key"], "new")
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    changed = load_cv("plain_promotions.txt").replace("Mar 2018", "Jun 2018", 1)
    second = upload(api, changed, "jordan-v2.txt")
    conflict = entry(review(api, second), "Globex Logistics", "Operations Manager")
    assert conflict["state"] == "DATE_CONFLICT"
    assert conflict["match"]["period_start"] == "2018-03"
    assert conflict["period"]["start"] == "2018-06"
    # Keeping the profile's dates changes nothing about the experience.
    kept = place(api, second, conflict["key"], "existing", dates="profile")
    (still,) = call(api, "GET", "/api/career")["experiences"]
    assert still["period_start"] == "2018-03"
    assert entry(kept, "Globex Logistics", "Operations Manager")["state"] == "DATE_CONFLICT"
    # Taking the document's dates is an edit, with history.
    took = place(api, second, conflict["key"], "existing", dates="document")
    (now,) = call(api, "GET", "/api/career")["experiences"]
    assert now["period_start"] == "2018-06" and now["id"] == experience["id"]
    assert entry(took, "Globex Logistics", "Operations Manager")["state"] != "DATE_CONFLICT"


def _one_job(dates: str) -> str:
    return (
        "Jordan Example\njordan@example.invalid | +00 0000 0000\n\nPROFESSIONAL EXPERIENCE\n\n"
        f"Initech, Operations Analyst (contract), {dates}\n"
        "- Mapped the returns process for the finance team.\n"
        "- Built the first shared KPI dashboard in Excel.\n\nSKILLS\nLean, Six Sigma, Excel, SQL\n"
    )


def test_the_documents_dates_change_only_what_the_document_states(workspace) -> None:
    """A second CV that gives only a start must not erase the profile's end."""
    api, _db = workspace
    first = upload(api, _one_job("Jun 2015 - Jan 2016"), "a.txt")
    place(api, first, review(api, first)["experiences"][0]["key"], "new")
    second = upload(api, _one_job("Mar 2015"), "b.txt")
    conflict = review(api, second)["experiences"][0]
    assert conflict["state"] == "DATE_CONFLICT"
    place(api, second, conflict["key"], "existing", dates="document")
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    assert experience["period_start"] == "2015-03"
    assert experience["period_end"] == "2016-01", "a date the document never stated was erased"


def test_a_bad_correction_is_a_readable_refusal_not_a_server_fault(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, _one_job("Jun 2015 - Jan 2016"), "a.txt")
    key = review(api, import_id)["experiences"][0]["key"]
    for fields in (
        {"period_start": "2020-13"},
        {"period_start": "2020-05", "period_end": "2019-01"},
        {"current_role": "false"},
    ):
        with pytest.raises(ApiError) as caught:
            place(api, import_id, key, "new", fields=fields)
        assert caught.value.status == 400, (fields, caught.value.status)
    assert call(api, "GET", "/api/career")["experiences"] == []


def test_uncertain_structure_asks_for_help(workspace) -> None:
    api, _db = workspace
    text = (
        f"EXPERIENCE\nAcme {EM_DASH} Globex, 2019 - 2020\n- Built a reporting pipeline.\n"
        "- Mentored two analysts.\n\nSKILLS\nSQL, Python, Excel\n"
        + "Filler line so the document is long enough to be read.\n"
        * 4
    )
    import_id = upload(api, text, "unclear.txt")
    (uncertain,) = review(api, import_id)["experiences"]
    assert uncertain["state"] == "STRUCTURE_UNCERTAIN"
    assert review(api, import_id)["summary"]["need_help"] == 1


def test_one_statement_at_a_time_and_the_review_resumes(workspace) -> None:
    api, db = workspace
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    contoso = entry(review(api, import_id), "Contoso Health")
    keys = [i["key"] for i in contoso["items"]]
    answer(api, import_id, keys[0], "CONFIRM")
    answer(api, import_id, keys[1], "REJECT")
    unsure = answer(api, import_id, keys[2], "UNSURE")
    states = [i["state"] for i in entry(unsure, "Contoso Health")["items"]]
    # "Not sure yet" leaves a CV statement waiting: still unanswered.
    assert states == ["confirmed", "rejected", "waiting"]
    edited = answer(api, import_id, keys[2], "EDIT", text="Owned the data migration in Python.")
    assert (
        entry(edited, "Contoso Health")["items"][2]["text"] == "Owned the data migration in Python."
    )
    # A fresh read of the same document: every answer is where it was left.
    again = entry(review(api, import_id), "Contoso Health")
    assert [i["state"] for i in again["items"]] == ["confirmed", "rejected", "confirmed"]
    # There is no way to confirm several at once.
    with pytest.raises(ApiError):
        call(
            api, "POST", f"/api/documents/cv/{import_id}/answer", {"key": keys, "answer": "CONFIRM"}
        )
    conn = connect(db)
    try:
        assert conn.execute("SELECT COUNT(*) FROM verified_claim").fetchone()[0] == 2
    finally:
        conn.close()


def test_dont_import_rejects_only_what_is_unanswered(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    northwind = entry(review(api, import_id), "Northwind Retail")
    answer(api, import_id, northwind["items"][0]["key"], "CONFIRM")
    skipped = call(api, "POST", f"/api/documents/cv/{import_id}/skip", {"entry": northwind["key"]})
    states = [i["state"] for i in entry(skipped, "Northwind Retail")["items"]]
    assert states == ["confirmed", "rejected"]


def test_an_archived_document_is_read_only_until_restored(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    call(api, "POST", f"/api/cv/imports/{import_id}/archive")
    model = review(api, import_id)
    assert model["status"] == "archived" and not model["editable"]
    with pytest.raises(ApiError):
        place(api, import_id, model["experiences"][0]["key"], "new")
    listed = call(api, "GET", "/api/documents")["documents"]
    assert listed[0]["status"] == "archived"


def test_a_package_is_reviewed_the_same_way(workspace) -> None:
    api, db = workspace
    package_id = stage_package(db, 3)
    listed = call(api, "GET", "/api/documents")["documents"]
    assert [(d["kind"], d["experiences"], d["waiting"]) for d in listed] == [("package", 3, 9)]
    model = review(api, package_id, "package")
    assert {e["state"] for e in model["experiences"]} == {"NEW"}
    first = model["experiences"][0]
    placed = call(
        api,
        "POST",
        f"/api/documents/package/{package_id}/place",
        {"entry": first["key"], "choice": "new"},
    )
    key = entry(placed, first["company"])["items"][0]["key"]
    unsure = call(
        api, "POST", f"/api/documents/package/{package_id}/answer", {"key": key, "answer": "UNSURE"}
    )
    assert entry(unsure, first["company"])["items"][0]["state"] == "unsure"
    confirmed = call(
        api,
        "POST",
        f"/api/documents/package/{package_id}/answer",
        {"key": key, "answer": "CONFIRM"},
    )
    assert entry(confirmed, first["company"])["items"][0]["state"] == "confirmed"
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    assert len(experience["highlights"]) == 1, "confirming took the highlight off the profile"


def test_removing_an_experience_is_undoable_and_destroys_nothing(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, load_cv("plain_promotions.txt"), "jordan.txt")
    initech = entry(review(api, import_id), "Initech")
    place(api, import_id, initech["key"], "new")
    answer(api, import_id, initech["items"][0]["key"], "CONFIRM")
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    command = {"action": "remove_experience", "experience_id": experience["id"]}
    preview = call(api, "POST", "/api/career/preview", command)
    removed = call(
        api,
        "POST",
        "/api/career/changes",
        {"command": command, "preview_hash": preview["preview_hash"]},
    )
    career = call(api, "GET", "/api/career")
    assert career["experiences"] == []
    assert call(api, "GET", "/api/evidence")["confirmed"] == 1
    undo = {"action": "undo", "event_id": removed["event_id"]}
    preview = call(api, "POST", "/api/career/preview", undo)
    call(
        api,
        "POST",
        "/api/career/changes",
        {"command": undo, "preview_hash": preview["preview_hash"]},
    )
    (back,) = call(api, "GET", "/api/career")["experiences"]
    assert back["id"] == experience["id"] and len(back["highlights"]) == 1


def test_an_experience_can_carry_a_description_and_year_only_dates(workspace) -> None:
    api, _db = workspace
    command = {
        "action": "create",
        "keys": [],
        "metadata": {
            "company": "Initrode",
            "title": "Analyst",
            "period_start": "2015",
            "period_end": "2017",
            "description": "Reporting for the ops team.",
        },
    }
    preview = call(api, "POST", "/api/career/preview", command)
    call(
        api,
        "POST",
        "/api/career/changes",
        {"command": command, "preview_hash": preview["preview_hash"]},
    )
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    assert experience["description"] == "Reporting for the ops team."
    assert (experience["period_start"], experience["period_end"]) == ("2015", "2017")
    bad = {**command, "metadata": {**command["metadata"], "period_start": "2018"}}
    with pytest.raises(ApiError):
        call(api, "POST", "/api/career/preview", bad)


def test_a_manual_statement_is_not_a_quote(workspace) -> None:
    api, _db = workspace
    command = {
        "action": "create",
        "keys": [],
        "metadata": {"company": "Initrode", "title": "Analyst"},
    }
    preview = call(api, "POST", "/api/career/preview", command)
    created = call(
        api,
        "POST",
        "/api/career/changes",
        {"command": command, "preview_hash": preview["preview_hash"]},
    )
    call(
        api,
        "POST",
        "/api/evidence",
        {
            "claim_type": "ACHIEVEMENT",
            "text": "Cut the monthly close from ten days to six.",
            "experience_id": created["experience_id"],
        },
    )
    (experience,) = call(api, "GET", "/api/career")["experiences"]
    assert [(h["text"], h["origin"]) for h in experience["highlights"]] == [
        ("Cut the monthly close from ten days to six.", "self")
    ]
    assert a_package  # the helper is shared; keep the import honest
