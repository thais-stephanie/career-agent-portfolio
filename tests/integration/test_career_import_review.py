"""CV/LinkedIn fragments remain atomic and ambiguous identity needs a decision."""

import pytest

from career_agent.cv.propose import read_cv
from career_agent.intake import parse_package
from career_agent.intake.store import import_package
from career_agent.storage.career_repo import CareerError, CareerRepo
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of, ensure_candidate


def package(names=("Teem", "Teem LLC"), *, conflicting=False):
    return parse_package(
        {
            "schema_version": "1.0",
            "generator": {"kind": "SELF", "name": "Synthetic candidate"},
            "sources": [
                {"ref": "cv", "kind": "RESUME", "title": "CV.txt"},
                {"ref": "li", "kind": "LINKEDIN", "title": "LinkedIn.txt"},
            ],
            "claims": [
                {
                    "type": "EMPLOYMENT",
                    "text": f"Organized weekly handovers for team {i}.",
                    "source_ref": "cv" if i == 0 else "li",
                    "employer": name,
                    "role_title": "Service coordinator",
                    "period": {
                        "start": {"original": "Jan 2020", "normalized": "2020-01"},
                        "end": {
                            "original": "Dec 2022",
                            "normalized": "2022-12" if not conflicting or i == 0 else "2023-01",
                        },
                    },
                    "evidence": {"quote": f"{name}: Service coordinator, Jan 2020 to Dec 2022"},
                }
                for i, name in enumerate(names)
            ],
        }
    )


@pytest.fixture
def staged(tmp_path):
    conn = connect(tmp_path / "imports.db")
    migrate(conn)
    import_package(conn, package())
    with transaction(conn):
        candidate = ensure_candidate(conn)
    yield CareerRepo(conn, candidate)
    conn.close()


def apply(repo, command, **kwargs):
    preview = repo.preview(command)
    with transaction(repo.conn):
        return repo.apply(command, preview["preview_hash"], **kwargs)


def test_duplicate_import_proposes_alias_and_keeps_original_sources(staged):
    before = [tuple(r) for r in staged.conn.execute("SELECT * FROM intake_claim ORDER BY id")]
    view = staged.overview()
    assert len(view["proposals"]) == 2
    assert view["experiences"] == []
    assert view["aliases"][0]["decision"] is None
    assert all(p["title"] == "Service coordinator" for p in view["proposals"])
    keys = staged.page()["keys"]
    apply(staged, {"action": "merge_companies", "first": "Teem", "second": "Teem LLC"})
    assert before == [
        tuple(r) for r in staged.conn.execute("SELECT * FROM intake_claim ORDER BY id")
    ]
    apply(
        staged,
        {
            "action": "create",
            "keys": keys,
            "metadata": {
                "company": "Teem LLC",
                "title": "Service coordinator",
                "period_start": "2020-01",
                "period_end": "2022-12",
            },
        },
    )
    apply(staged, {"action": "confirm", "keys": keys}, reviewed=True)
    claims = ClaimRepo(staged.conn).current(staged.candidate_id)
    assert len(claims) == 2 and all(c.verified for c in claims)
    assert {c.employer for c in claims} == {"Teem", "Teem LLC"}
    assert len({c.claim_key for c in claims}) == 2
    assert staged.overview()["experiences"][0]["count"] == 2
    assert all(item["source_records"] for item in staged.items())


def test_cv_file_and_original_quote_survive_bulk_confirmation(staged):
    text = "Projects\nBuilt a shared handover checklist for colleagues."
    proposals = read_cv(text).proposals
    assert proposals
    with transaction(staged.conn):
        CvReviewRepo(staged.conn).stage(
            staged.candidate_id,
            source_name="Graduate CV.txt",
            kind="txt",
            pages=None,
            characters=len(text),
            text=text,
            proposals=proposals,
        )
    key = proposals[0].claim_key
    before = next(item for item in staged.items() if item["claim_key"] == key)
    assert before["state"] == "PENDING"
    assert before["source_records"][0]["documents"][0]["title"] == "Graduate CV.txt"
    assert before["source_records"][0]["evidence"]["quote"] in text
    apply(staged, {"action": "confirm", "keys": [key]}, reviewed=True)
    after = next(item for item in staged.items() if item["claim_key"] == key)
    assert after["state"] == "CONFIRMED"
    assert after["source_records"] == before["source_records"]


def test_similar_but_distinct_entities_can_stay_separate(tmp_path):
    conn = connect(tmp_path / "separate.db")
    migrate(conn)
    import_package(conn, package(("Community Care", "Community Care Academy")))
    with transaction(conn):
        repo = CareerRepo(conn, ensure_candidate(conn))
    apply(
        repo,
        {"action": "keep_separate", "first": "Community Care", "second": "Community Care Academy"},
    )
    assert repo.overview()["aliases"][0]["decision"] == "SEPARATE"
    assert repo.overview()["experiences"] == []
    assert ClaimRepo(conn).current(repo.candidate_id) == []
    conn.close()


def test_disputed_dates_cannot_be_bypassed_by_bulk_confirmation(tmp_path):
    conn = connect(tmp_path / "conflict.db")
    migrate(conn)
    import_package(conn, package(conflicting=True))
    repo = CareerRepo(conn, candidate_id_of(conn))
    assert any(item["conflict"] for item in repo.items())
    with pytest.raises(CareerError, match="date disagreement"):
        repo.preview({"action": "confirm", "keys": repo.page()["keys"]})
    assert conn.execute("SELECT count(*) FROM verified_claim").fetchone()[0] == 0
    conn.close()


def test_failure_after_first_review_rolls_back_every_revision(staged, monkeypatch):
    from career_agent.intake import store

    original = store.confirm
    calls = 0

    def fail_second(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("Synthetic second-item failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "confirm", fail_second)
    with pytest.raises(RuntimeError, match="second-item"):
        apply(staged, {"action": "confirm", "keys": staged.page()["keys"]}, reviewed=True)
    assert staged.conn.execute("SELECT count(*) FROM verified_claim").fetchone()[0] == 0
    assert staged.conn.execute("SELECT count(*) FROM career_history_event").fetchone()[0] == 0
    assert all(not r["verified"] for r in staged.items())
