"""A confirmed CV detail stays under the career experience its job belongs to.

WHAT HAPPENED, ON THE OWNER'S OWN MACHINE (2026-09-25)
------------------------------------------------------
Her experiences already existed. The guided review matched each job in her CV
to one of them ("Already in your profile"), but never recorded the match, so
the 56 lines she confirmed there were linked to no experience and Career
Profile -> Experience showed none of them. These tests pin the placement rule
(`storage/cv_placement.py`) for new answers and the repair for old ones.
"""

from __future__ import annotations

from tests.integration.test_documents_review import (
    answer,
    call,
    entry,
    place,
    review,
    upload,
    workspace,  # noqa: F401 -- the fixture
)
from tests.support_cv import load_cv

from career_agent.storage.cv_placement import BY_ENTRY, BY_JOB, apply_repair, plan_repair
from career_agent.storage.db import connect
from career_agent.storage.workspace_repo import candidate_id_of

__all__ = ["workspace"]

COMPANY, ROLE = "Fabrikam Cloud", "Senior Solutions Consultant"


def change(api, command: dict) -> dict:
    """A career change the way the interface makes one: preview, then apply."""
    preview = call(api, "POST", "/api/career/preview", command)
    body = {"command": command, "preview_hash": preview["preview_hash"]}
    return call(api, "POST", "/api/career/changes", body)


def experience(api, **metadata) -> str:
    fields = {"company": COMPANY, "title": ROLE, **metadata}
    return change(api, {"action": "create", "keys": [], "metadata": fields})["experience_id"]


def keys_under(api, experience_id: str) -> set[str]:
    career = call(api, "GET", "/api/career")
    return set(next(e for e in career["experiences"] if e["id"] == experience_id)["keys"])


def highlights_under(api, experience_id: str) -> set[str]:
    career = call(api, "GET", "/api/career")
    chosen = next(e for e in career["experiences"] if e["id"] == experience_id)
    return {h["claim_key"] for h in chosen["highlights"]}


# =========================================================================
# A. new answers
# =========================================================================


def test_a_line_confirmed_under_a_matched_job_lands_in_that_experience(workspace) -> None:
    api, _db = workspace
    target = experience(api)
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    assert job["state"] != "NEW" and job["match"]["experience_id"] == target
    assert not job["placed"], "nothing was placed yet: the review only matched"
    first, second = job["items"][0], job["items"][1]
    answer(api, import_id, first["key"], "CONFIRM")
    answer(api, import_id, second["key"], "EDIT", text=second["text"] + " (in my words)")
    shown = highlights_under(api, target)
    assert {first["key"], second["key"]} <= shown, shown
    # The rest of the job went with it, still waiting, never confirmed.
    assert {i["key"] for i in job["items"]} <= keys_under(api, target)
    assert entry(review(api, import_id), COMPANY, ROLE)["placed"]


def test_a_job_added_to_the_profile_keeps_what_is_confirmed_later(workspace) -> None:
    api, _db = workspace
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    created = place(api, import_id, job["key"], "new")["experience_id"]
    later = job["items"][2]
    answer(api, import_id, later["key"], "CONFIRM")
    assert later["key"] in highlights_under(api, created)


def test_an_ambiguous_job_is_not_placed_by_guesswork(workspace) -> None:
    api, _db = workspace
    first = experience(api)
    second = experience(api, title=ROLE)  # the same employer AND role, twice
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    item = job["items"][0]
    answer(api, import_id, item["key"], "CONFIRM")
    assert item["key"] not in keys_under(api, first) | keys_under(api, second)


def test_a_line_already_placed_elsewhere_is_never_moved(workspace) -> None:
    api, _db = workspace
    target = experience(api)
    elsewhere = experience(api, company="Northwind Traders", title="Analyst")
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    moved = job["items"][0]["key"]
    change(api, {"action": "move", "keys": [moved], "experience_id": elsewhere})
    answer(api, import_id, job["items"][1]["key"], "CONFIRM")
    assert moved in keys_under(api, elsewhere) and moved not in keys_under(api, target)


# =========================================================================
# B. the repair of what was already confirmed
# =========================================================================


def _confirm_the_old_way(api, import_id: str, key: str) -> None:
    """What the review did before the fix: the answer, with no placement."""
    api.decide_proposal(
        import_id=import_id, query={}, body={"claim_key": key, "decision": "ACCEPTED"}
    )


def test_the_dry_run_names_exactly_the_missing_links_and_the_repair_is_idempotent(
    workspace,
) -> None:
    api, db = workspace
    target = experience(api)
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    orphan = job["items"][0]["key"]
    _confirm_the_old_way(api, import_id, orphan)
    assert orphan not in keys_under(api, target)
    conn = connect(db)
    try:
        candidate = candidate_id_of(conn)
        (plan,) = [p for p in plan_repair(conn, candidate) if p.company == COMPANY]
        assert plan.proposed == [orphan] and plan.experience_id == target and plan.basis == BY_JOB
        assert apply_repair(conn, candidate, [plan]) == 1
        again = [p for p in plan_repair(conn, candidate) if p.company == COMPANY]
        assert again[0].proposed == [] and again[0].linked_here == 1
    finally:
        conn.close()
    assert orphan in highlights_under(api, target)
    # One recorded, undoable move.
    history = call(api, "GET", "/api/career/history")["items"]
    assert any(event["action"] == "move" for event in history)


def test_the_repair_follows_the_entry_before_the_names(workspace) -> None:
    api, db = workspace
    renamed = experience(api, title="Solutions Lead")  # names no longer agree
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    placed_key, orphan = job["items"][0]["key"], job["items"][1]["key"]
    _confirm_the_old_way(api, import_id, placed_key)
    _confirm_the_old_way(api, import_id, orphan)
    change(api, {"action": "move", "keys": [placed_key], "experience_id": renamed})
    conn = connect(db)
    try:
        (plan,) = [p for p in plan_repair(conn, candidate_id_of(conn)) if p.company == COMPANY]
    finally:
        conn.close()
    assert plan.proposed == [orphan] and plan.experience_id == renamed and plan.basis == BY_ENTRY


def test_an_ambiguous_mapping_is_reported_and_left_untouched(workspace) -> None:
    api, db = workspace
    experience(api)
    experience(api)  # two experiences with this employer and role
    import_id = upload(api, load_cv("markdown_complex.md"), "riley.md")
    job = entry(review(api, import_id), COMPANY, ROLE)
    _confirm_the_old_way(api, import_id, job["items"][0]["key"])
    conn = connect(db)
    try:
        candidate = candidate_id_of(conn)
        (plan,) = [p for p in plan_repair(conn, candidate) if p.company == COMPANY]
        assert plan.proposed == [] and plan.missing and plan.ambiguous
        assert apply_repair(conn, candidate, [plan]) == 0
    finally:
        conn.close()
