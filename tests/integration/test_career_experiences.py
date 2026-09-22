"""Organization changes preserve the evidence ledger and require reviewed scope."""

import pytest

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.storage.career_repo import CareerError, CareerRepo
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate


@pytest.fixture
def career(tmp_path):
    conn = connect(tmp_path / "career.db")
    migrate(conn)
    with transaction(conn):
        candidate = ensure_candidate(conn)
        for n in range(320):
            ClaimRepo(conn).add(
                candidate,
                VerifiedClaim(
                    claim_key=f"item-{n}",
                    claim_type=ClaimType.EMPLOYMENT,
                    text=f"Owned service {n} and trained colleagues.",
                    employer=["Teem", "Teem LLC", "Company B", "Company C", "Company D"][n % 5],
                    period_start="2019-01" if n < 160 else "2021-01",
                    period_end="2020-12" if n < 160 else "2023-12",
                    source=ClaimSource.RESUME,
                    verified=True,
                    evidence_ref=f"CV page {n // 40 + 1}",
                ),
            )
    yield CareerRepo(conn, candidate)
    conn.close()


def apply(repo, command, **kwargs):
    preview = repo.preview(command)
    with transaction(repo.conn):
        return repo.apply(command, preview["preview_hash"], **kwargs)


def ledger(repo):
    return [tuple(r) for r in repo.conn.execute("SELECT * FROM verified_claim ORDER BY id")]


def create(repo, keys, **metadata):
    return apply(repo, {"action": "create", "keys": keys, "metadata": metadata})


def test_large_profile_starts_as_reviewable_proposals(career):
    before = ledger(career)
    view = career.overview()
    assert view["experiences"] == []
    assert view["unassigned"] == 320
    assert len(view["proposals"]) == 10
    assert all(p["needs_role"] for p in view["proposals"])
    assert view["aliases"] == [
        {
            "first": "Teem",
            "second": "Teem LLC",
            "decision": None,
            "reason": "name_variant",
        }
    ]
    page = career.page(experience="inbox", query="OWNED", limit=25)
    assert len(page["items"]) == 25
    assert len(page["keys"]) == 320
    group = create(career, page["keys"], company="Candidate-approved organization", title="Role")
    assert career.overview()["experiences"][0]["count"] == 320
    assert career.page(experience=group["experience_id"], limit=10)["total"] == 320
    assert ledger(career) == before


def test_lookup_finds_reviewed_experience_metadata_without_rewriting_claims(career):
    created = create(career, ["item-0"], company="Cooperação", title="Community coordinator")
    assert career.page(query="COOPERACAO")["keys"] == ["item-0"]
    assert career.page(query="community COORDINATOR")["keys"] == ["item-0"]
    apply(
        career,
        {
            "action": "edit",
            "experience_id": created["experience_id"],
            "metadata": {"title": "Training coordinator"},
        },
    )
    assert career.page(query="training coordinator")["keys"] == ["item-0"]
    assert "item-0" in career.page(query="Teem")["keys"]


def test_multiple_roles_merge_split_and_sequential_undo(career):
    before = ledger(career)
    first = create(
        career,
        ["item-0", "item-5"],
        company="Teem",
        title="Role 1",
        period_start="2019-01",
        period_end="2020-12",
    )
    second = create(
        career,
        ["item-160"],
        company="Teem",
        title="Role 2",
        period_start="2021-01",
        period_end="2023-12",
    )
    assert len(career.overview()["experiences"]) == 2
    split = apply(
        career,
        {
            "action": "split",
            "keys": ["item-5"],
            "metadata": {"company": "Teem", "title": "Project"},
        },
    )
    merged = apply(
        career,
        {
            "action": "merge_experiences",
            "source_id": split["experience_id"],
            "experience_id": first["experience_id"],
        },
    )
    assert len(career.overview()["experiences"]) == 2
    for event in (merged, split, second, first):
        apply(career, {"action": "undo", "event_id": event["event_id"]})
    assert career.overview()["experiences"] == []
    assert career.overview()["unassigned"] == 320
    assert ledger(career) == before


def test_aliases_are_explicit_reversible_and_do_not_merge_roles(career):
    before = ledger(career)
    create(career, ["item-0"], company="Teem", title="Role 1")
    create(career, ["item-1"], company="Teem LLC", title="Role 2")
    command = {"action": "merge_companies", "first": "Teem", "second": "Teem LLC"}
    assert career.preview(command)["count"] == 128
    result = apply(career, command)
    experiences = career.overview()["experiences"]
    assert len(experiences) == 2
    assert {e["company"] for e in experiences} == {"Teem LLC"}
    apply(career, {"action": "undo", "event_id": result["event_id"]})
    assert {e["company"] for e in career.overview()["experiences"]} == {"Teem", "Teem LLC"}
    apply(career, {**command, "action": "keep_separate"})
    assert career.overview()["aliases"][0]["decision"] == "SEPARATE"
    assert ledger(career) == before


def test_alias_undo_before_first_experience_does_not_revive_a_hidden_merge(career):
    merged = apply(career, {"action": "merge_companies", "first": "Teem", "second": "Teem LLC"})
    apply(career, {"action": "undo", "event_id": merged["event_id"]})
    create(career, ["item-0"], company="Teem", title="Coordinator")
    assert career.overview()["experiences"][0]["company"] == "Teem"


def test_older_organization_history_remains_accessible_and_reversible(career):
    first = create(career, ["item-0"], company="Teem", title="Coordinator")
    for _n in range(21):
        apply(career, {"action": "category", "keys": ["item-1"], "category": "PROJECT"})
    page = career.history()
    assert page["total"] == 22 and len(page["items"]) == 20
    older = career.history(offset=20)
    assert first["event_id"] in {event["id"] for event in older["items"]}
    apply(career, {"action": "undo", "event_id": first["event_id"]})
    assert career.overview()["experiences"] == []


def test_undo_organization_preserves_later_category_and_confirmation_reviews(career):
    created = create(career, ["item-0"], company="Teem", title="Coordinator")
    apply(career, {"action": "category", "keys": ["item-0"], "category": "PROJECT"})
    apply(career, {"action": "retire", "keys": ["item-0"]})
    before = ledger(career)
    preview = career.preview({"action": "undo", "event_id": created["event_id"]})
    assert preview["count"] == 1
    apply(career, {"action": "undo", "event_id": created["event_id"]})
    item = next(r for r in career.items() if r["claim_key"] == "item-0")
    assert item["category"] == "PROJECT" and item["state"] == "RETIRED"
    assert item["experience_id"] is None
    assert ledger(career) == before


def test_stale_preview_and_bulk_review_preserve_revisions(career):
    keys = ["item-0", "item-1"]
    command = {"action": "retire", "keys": keys}
    preview = career.preview(command)
    create(career, keys, company="Teem", title="Support")
    with pytest.raises(CareerError, match="changed"), transaction(career.conn):
        career.apply(command, preview["preview_hash"])
    apply(career, command)
    with pytest.raises(CareerError, match="explicitly confirm"):
        apply(career, {"action": "confirm", "keys": keys})
    apply(career, {"action": "confirm", "keys": keys}, reviewed=True)
    apply(career, {"action": "category", "keys": keys, "category": "PROJECT"})
    for key in keys:
        rows = career.conn.execute(
            "SELECT * FROM verified_claim WHERE claim_key = ? ORDER BY revision",
            (key,),
        ).fetchall()
        assert [r["revision"] for r in rows] == [1, 2, 3, 4]
        assert len({r["text"] for r in rows}) == 1
        assert len({r["evidence_ref"] for r in rows}) == 1
        assert rows[-1]["claim_type"] == "PROJECT"
    assert career.overview()["confirmed"] == 320


@pytest.mark.parametrize(
    "command",
    [
        {"action": []},
        {"action": "create", "metadata": []},
        {"action": "move", "keys": ["item-0"], "experience_id": []},
        {"action": "split", "keys": ["item-0"], "metadata": {"title": "Project"}},
        {"action": "create", "metadata": {"title": "Invalid", "period_start": "2024-99"}},
    ],
)
def test_invalid_commands_cannot_write(career, command):
    before = ledger(career)
    with pytest.raises(ValueError):
        apply(career, command)
    assert ledger(career) == before
    assert career.overview()["experiences"] == []
