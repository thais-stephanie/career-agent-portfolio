"""A change re-reads only the postings it can move (ADR-0037).

Every sentence of the synthetic corpus becomes a posting in a real
catalogue, with its full-text index built. The language table then gains two
entries (a word and a fixed expression). `invalidation.affected_jobs` names
the postings that contain an affected word, through the index the catalogue
already has, and through a full scan when the index cannot be trusted; every
posting it calls unaffected must tokenise IDENTICALLY under the new table,
which is what makes re-stamping it without reading it sound. A person's new
phrase is selected the same way (`phrase_candidates`).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from tests.unit.test_matching_v2_language import CORPUS

from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2 import invalidation as inv
from career_agent.matching_v2.language import DATA, load_table, tokens
from career_agent.matching_v2.store import FeatureStore
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.search_index import rebuild

EXTRA = [
    "Operar empilhadeira no armazém central.",
    "Projetar o fluxo de caixa trimestral.",
    "Own the cash flow forecast.",
]


def _changed_table(tmp_path: Path):
    raw = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    raw["version"] = 2
    raw["exact_equivalent"].append({"en": ["warehouse"], "pt": ["armazem"], "es": ["almacen"]})
    raw["fixed_expression"].append({"en": ["cash flow"], "pt": ["fluxo de caixa"]})
    path = tmp_path / "language_data.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return load_table(path)


def _catalogue(tmp_path: Path):
    conn = connect(tmp_path / "catalogue.db")
    migrate(conn)
    # The subject is selection, not the board registry: no `source_board` rows.
    conn.execute("PRAGMA foreign_keys = OFF")
    texts = [c.text for c in ev.load_corpus(CORPUS)] + EXTRA
    jobs = {}
    stamp = "2026-10-14T00:00:00Z"
    with transaction(conn):
        conn.execute(
            "INSERT INTO company (id, slug, name, created_at, updated_at)"
            " VALUES ('c1', 'c1', 'Example Co (fictional)', ?, ?)",
            (stamp, stamp),
        )
        for i, text in enumerate(texts):
            job_id, h = f"job-{i:03d}", f"h{i:03d}"
            description = f"Responsibilities\n{text}\n"
            conn.execute(
                "INSERT INTO job_raw (content_hash, description_text, byte_length, created_at)"
                " VALUES (?, ?, ?, ?)",
                (h, description, len(description), stamp),
            )
            conn.execute(
                "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url,"
                " title, content_hash, first_seen_at, last_seen_at, collection_status,"
                " created_at, updated_at) VALUES (?, 'c1', 'b1', 'example', ?, ?, 'Role', ?,"
                " ?, ?, 'NORMALISED', ?, ?)",
                (job_id, job_id, f"https://example.test/{job_id}", h, stamp, stamp, stamp, stamp),
            )
            jobs[job_id] = (JobFacts(title="Role", description=description, job_id=job_id), h)
        store = FeatureStore(conn)
        for job, h in jobs.values():
            store.ensure(job, h, ((), ()))
        rebuild(conn)
    return conn, jobs


def test_a_language_change_selects_through_the_index_and_is_sound(tmp_path: Path) -> None:
    conn, jobs = _catalogue(tmp_path)
    old, new = load_table(), _changed_table(tmp_path)
    change = inv.diff(old, new)
    moved, unmoved, how = inv.affected_jobs(conn, change, old)
    assert how == "index"
    news = {f"job-{len(jobs) - 3 + i:03d}" for i in range(3)}
    assert news <= set(moved), "a posting with a new word is always selected"
    assert len(moved) <= 8, f"the index selects the few, not the corpus: {len(moved)}"
    # The full scan finds no posting the index missed.
    scanned = inv.scan(conn, change.touches, old)
    assert scanned <= set(moved)
    # Soundness: an unaffected posting tokenises identically under the new table.
    for job_id in unmoved:
        job, _ = jobs[job_id]
        before = [(t.root, t.start, t.relation) for t in tokens(job.description, old)]
        after = [(t.root, t.start, t.relation) for t in tokens(job.description, new)]
        assert before == after, f"{job_id} was called unaffected and moved"
    for job_id in news:
        job, _ = jobs[job_id]
        assert tokens(job.description, old) != tokens(job.description, new)
    conn.close()


def test_a_stale_index_falls_back_to_a_scan(tmp_path: Path) -> None:
    conn, jobs = _catalogue(tmp_path)
    with transaction(conn):
        conn.execute("UPDATE search_index_state SET job_count = job_count + 1")
    change = inv.diff(load_table(), _changed_table(tmp_path))
    moved, _, how = inv.affected_jobs(conn, change)
    assert how == "scan"
    assert {f"job-{len(jobs) - 3 + i:03d}" for i in range(3)} <= set(moved)
    conn.close()


def test_a_new_phrase_selects_only_postings_with_its_words(tmp_path: Path) -> None:
    conn, jobs = _catalogue(tmp_path)
    picked, how = inv.phrase_candidates(conn, ["supplier negotiation"])
    assert how == "index"
    # Every posting the phrase can match is among the candidates...
    from career_agent.matching_v2.language import concept_readings

    readings = [set(r) for r in concept_readings("supplier negotiation")]
    for job_id, (job, _) in jobs.items():
        roots = {t.root for t in tokens(job.description)}
        if any(r <= roots for r in readings):
            assert job_id in picked, job_id
    # ...and they are a small part of the corpus.
    assert 0 < len(picked) <= len(jobs) // 10, len(picked)
    conn.close()
