"""A change to the language table re-reads only the postings it can move.

Every sentence of the synthetic corpus becomes a posting. The table then
gains two entries (a word and a fixed expression). `invalidation.diff` names
the postings whose stored work facts contain an affected word; every other
posting, recomputed under the new table anyway, must come out IDENTICAL,
which is what makes re-stamping it without reading it sound (ADR-0037).
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from tests.unit.test_matching_v2_language import CORPUS

from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2.invalidation import affected_jobs, diff, restamp
from career_agent.matching_v2.language import DATA, load_table
from career_agent.storage.db import connect, migrate, transaction


def _changed_table(tmp_path: Path):
    raw = yaml.safe_load(DATA.read_text(encoding="utf-8"))
    raw["version"] = 2
    raw["exact_equivalent"].append({"en": ["warehouse"], "pt": ["armazem"], "es": ["almacen"]})
    raw["fixed_expression"].append({"en": ["cash flow"], "pt": ["fluxo de caixa"]})
    path = tmp_path / "language_data.yaml"
    path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    return load_table(path)


def test_only_postings_with_an_affected_word_are_read_again(tmp_path: Path) -> None:
    conn = connect(tmp_path / "catalogue.db")
    migrate(conn)
    # Selection is the subject here, not the posting rows: no `job` table rows.
    conn.execute("PRAGMA foreign_keys = OFF")
    old = load_table()
    new = _changed_table(tmp_path)
    cases = ev.load_corpus(CORPUS)
    extra = [
        "Operar empilhadeira no armazém central.",
        "Projetar o fluxo de caixa trimestral.",
        "Own the cash flow forecast.",
    ]
    texts = [c.text for c in cases] + extra
    jobs = []
    with transaction(conn):
        for i, text in enumerate(texts):
            job_id = f"job-{i:03d}"
            job = JobFacts(title="Role", description=f"Responsibilities\n{text}\n", job_id=job_id)
            f = feat.extract(job, f"h{i}", only={"work_facts"}, table=old)
            conn.execute(
                "INSERT INTO job_features (job_id, component, content_hash, stamp, payload_json,"
                " computed_at) VALUES (?, 'work_facts', ?, 'old', ?, '2026-10-14T00:00:00Z')",
                (job_id, f"h{i}", json.dumps(f.payloads["work_facts"])),
            )
            jobs.append(job)
    change = diff(old, new)
    moved, unmoved = affected_jobs(conn, change)
    # The three postings with a new word, and a few whose stored words share a
    # root with an affected one: conservative, never short.
    assert {"job-117", "job-118", "job-119"} <= set(moved)
    assert len(moved) <= 6 and len(unmoved) >= len(texts) - 6
    # Soundness: every posting the diff calls unaffected comes out identical.
    stored = dict(
        conn.execute("SELECT job_id, payload_json FROM job_features WHERE component='work_facts'")
    )
    for job in jobs:
        again = feat.extract(job, "x", only={"work_facts"}, table=new).payloads["work_facts"]
        same = json.loads(json.dumps(again)) == json.loads(stored[job.job_id])
        if job.job_id in unmoved:
            assert same, f"{job.job_id} was called unaffected and moved"
        if job.job_id in {"job-117", "job-118", "job-119"}:
            assert not same, f"{job.job_id} has a new word and did not move"
    with transaction(conn):
        assert restamp(conn, unmoved, "work_facts", {j: "new" for j in unmoved}) == len(unmoved)
    assert conn.execute("SELECT COUNT(*) FROM job_features WHERE stamp = 'old'").fetchone()[
        0
    ] == len(moved)
    conn.close()
