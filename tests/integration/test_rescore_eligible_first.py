"""The readers-12 recalculation re-reads the stored-eligible postings first.

A score written by an older reader keeps a posting in the default search and
the deep selection on the strength of its stored "eligible". The pass now
takes those postings first, so one whose new reading finds a conflict or a
restriction leaves both selections as soon as its page is written, not at
the end of the whole pass. Same targets, same rules, nothing read twice, and
a stopped pass resumes with only what is left. Synthetic adverts.
"""

from __future__ import annotations

import pytest
from tests.integration.test_eligibility_old_scores import (
    app,  # noqa: F401
    as_readers_11,
    deep_ids,
    default_ids,
)

from career_agent.pipeline import rescore as rescore_module
from career_agent.storage.db import connect, transaction


def every_score_old(app) -> None:  # noqa: F811
    """A reader update: every stored score was read by an older reader, and
    the three shapes were stored as eligible (what readers-11 wrote)."""
    conn = connect(app.config.db_path)
    try:
        with transaction(conn):
            conn.execute("UPDATE job_match SET input_digest = 'readers-11-synthetic'")
            # The owner's case: no collector marks waiting, only a reader that
            # moved. A MARKED posting is retried after a stopped pass by design
            # (ADR-0026), whatever group it was in.
            conn.execute("DELETE FROM job_dirty")
    finally:
        conn.close()
    as_readers_11(app, *(app.ids[k] for k in ("security", "conflict", "elsewhere")))


def plan_now(app):  # noqa: F811
    conn = connect(app.config.db_path)
    try:
        return rescore_module.plan(conn, app.search_config())
    finally:
        conn.close()


def stored_eligible(app) -> set[str]:  # noqa: F811
    conn = connect(app.config.db_path)
    try:
        rows = conn.execute(
            "SELECT job_id FROM job_match"
            " WHERE eligibility_status IN ('VERIFIED_ELIGIBLE', 'LIKELY_ELIGIBLE')"
        ).fetchall()
    finally:
        conn.close()
    return {str(r[0]) for r in rows}


def test_the_plan_takes_the_stored_eligible_first_and_keeps_the_same_set(app) -> None:  # noqa: F811
    every_score_old(app)
    chosen = plan_now(app)
    eligible = stored_eligible(app)
    first, rest = chosen.targets[: chosen.priority], chosen.targets[chosen.priority :]
    assert chosen.priority > 0
    assert set(first) == eligible & set(chosen.targets)
    assert not set(rest) & eligible
    assert first == sorted(first) and rest == sorted(rest)
    assert len(chosen.targets) == len(set(chosen.targets)), "nothing is planned twice"
    for key in ("security", "conflict", "elsewhere"):
        assert app.ids[key] in first


def test_a_stale_eligible_leaves_both_selections_as_soon_as_it_is_rewritten(
    app,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every_score_old(app)
    watched = [app.ids[k] for k in ("security", "conflict", "elsewhere")]
    assert all(j in default_ids(app) and j in deep_ids(app) for j in watched)
    monkeypatch.setattr(rescore_module, "_PAGE", 2)
    seen_at: dict[str, int] = {}

    def progress(stats) -> None:
        if not stats.jobs_scored or stats.jobs_scored >= stats.jobs_targeted:
            return
        conn = connect(app.config.db_path)
        try:
            rewritten = {
                str(r[0])
                for r in conn.execute(
                    "SELECT job_id FROM job_match WHERE input_digest != 'readers-11-synthetic'"
                )
            }
        finally:
            conn.close()
        shown, deep = default_ids(app), deep_ids(app)
        for job_id in watched:
            if job_id in rewritten:
                assert job_id not in shown and job_id not in deep
                seen_at.setdefault(job_id, stats.jobs_scored)

    conn = connect(app.config.db_path)
    try:
        stats = rescore_module.rescore(conn, app.search_config(), progress=progress)
    finally:
        conn.close()
    assert stats.jobs_priority > 0 and stats.jobs_scored == stats.jobs_targeted
    assert set(seen_at) == set(watched), "they left before the pass ended"
    # Within the first group, not merely before the end: by id they come last.
    assert max(seen_at.values()) <= stats.jobs_priority + 1  # a page of two


def test_a_pass_stopped_after_the_first_group_resumes_with_only_the_rest(
    app,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    every_score_old(app)
    before = plan_now(app)
    first = set(before.targets[: before.priority])
    monkeypatch.setattr(rescore_module, "_PAGE", 1)

    class Stop(Exception):
        pass

    def stop_after_first_group(stats) -> None:
        if stats.jobs_scored >= stats.jobs_priority > 0:
            raise Stop

    conn = connect(app.config.db_path)
    try:
        with pytest.raises(Stop):
            rescore_module.rescore(conn, app.search_config(), progress=stop_after_first_group)
    finally:
        conn.close()
    # The first group's new scores are saved and already count.
    for key in ("security", "conflict", "elsewhere"):
        assert app.ids[key] not in default_ids(app)
    after = plan_now(app)
    assert after.priority == 0, "the eligible-first group is done"
    assert not set(after.targets) & first, "nothing of the first group is read again"
    assert set(after.targets) == set(before.targets) - first
    conn = connect(app.config.db_path)
    try:
        resumed = rescore_module.rescore(conn, app.search_config())
    finally:
        conn.close()
    assert resumed.jobs_scored == len(before.targets) - len(first)
    assert plan_now(app).targets == []


def test_the_run_account_carries_the_first_group(app) -> None:  # noqa: F811
    every_score_old(app)
    app.handle_api("POST", "/api/rescore", {}, {})
    app.rescore.join(60)
    state = app.handle_api("GET", "/api/rescore", {}, {})["state"]
    assert state["phase"] == "DONE"
    assert 0 < state["priority"] <= state["total"]
