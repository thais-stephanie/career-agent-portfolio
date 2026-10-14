"""Matching Architecture v2 against the current reader: work repeated, measured.

    uv run python scripts/matching_v2_benchmark.py                 # synthetic
    uv run python scripts/matching_v2_benchmark.py --db COPY --limit 300

Synthetic by default: adverts assembled from the language corpus's invented
sentences. `--db` reads a bounded sample of open postings from a COPY of a
catalogue (read-only; never the live file while it is being written). Ten
profiles are built by the setup wizard from invented answers in different
professions. Prints timings and how many artefacts each kind of change makes
stale under each model. Calls no model and opens no socket.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.config.setup import Answers, apply_answers  # noqa: E402
from career_agent.match.engine import JobFacts, match_job  # noqa: E402
from career_agent.matching_v2 import components as comp  # noqa: E402
from career_agent.matching_v2 import evaluation as ev  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402

CORPUS = ROOT / "tests" / "fixtures" / "matching_v2" / "language_corpus.yaml"

PROFILES = [
    Answers(role_examples=("workflow automation", "systems integration"), skills=("Zapier",),
            residence_country="BR", target_amount=8000, currency="USD", period="MONTH"),
    Answers(role_examples=("assistência ao paciente", "administração de medicamentos"),
            residence_country="BR"),
    Answers(role_examples=("lesson plans", "student support"), residence_country="PT"),
    Answers(role_examples=("lead generation", "vendas consultivas"), residence_country="MX",
            target_amount=70000, currency="USD", period="YEAR"),
    Answers(role_examples=("accounts payable", "budget analysis"), residence_country="AR"),
    Answers(role_examples=("recruitment", "payroll"), residence_country="CO"),
    Answers(role_examples=("user interface design", "user research"), skills=("Figma",),
            residence_country="BR"),
    Answers(role_examples=("customer service", "support tickets"), residence_country="CL"),
    Answers(role_examples=("data analysis", "data migration"), skills=("SQL",),
            residence_country="US"),
    Answers(role_examples=("inventory management", "supplier negotiation"),
            residence_country="ES"),
]  # fmt: skip
HEADINGS = ["Responsibilities", "Requirements", "Responsabilidades", "Requisitos", "About us",
            "Benefits", "Sobre nós", "Benefícios"]  # fmt: skip


def wizard(answers: Answers):
    starter = yaml.safe_load((ROOT / "config" / "search.starter.yaml").read_text(encoding="utf-8"))
    mapping, _ = apply_answers(starter, answers)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "search.local.yaml").write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        config, _ = load_search_config(Path(tmp))
    return config


def synthetic_jobs(n: int, seed: int = 7) -> list[tuple[JobFacts, str]]:
    rng = random.Random(seed)
    sentences = [c.text for c in ev.load_corpus(CORPUS)]
    out = []
    for i in range(n):
        parts = []
        for _ in range(4):
            parts.append(rng.choice(HEADINGS))
            parts += [f"- {rng.choice(sentences)}" for _ in range(rng.randint(2, 5))]
        body = "\n".join(parts)
        out.append((JobFacts(title="Role", description=body, job_id=f"syn-{i}",
                             location_raw=rng.choice(["Brazil", "Remote", "Lisbon, Portugal"])),
                    f"syn-{i}"))  # fmt: skip
    return out


def sample_jobs(db: Path, limit: int, seed: int = 7) -> list[tuple[JobFacts, str]]:
    conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT j.id, j.title, r.description_text, j.location_raw, j.content_hash"
        " FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.closed_at IS NULL AND length(r.description_text) > 400"
        " ORDER BY j.id LIMIT ?",
        (limit * 20,),
    ).fetchall()
    conn.close()
    rng = random.Random(seed)
    picked = rng.sample(rows, min(limit, len(rows)))
    return [(JobFacts(title=t, description=d, job_id=i, location_raw=loc), h)
            for i, t, d, loc, h in picked]  # fmt: skip


def timed(fn) -> tuple[float, object]:
    start = time.perf_counter()
    value = fn()
    return time.perf_counter() - start, value


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--db", type=Path)
    args.add_argument("--limit", type=int, default=200)
    a = args.parse_args()
    jobs = sample_jobs(a.db, a.limit) if a.db else synthetic_jobs(a.limit)
    profiles = [wizard(x) for x in PROFILES]
    headings = (tuple(profiles[0].prominence.primary_headings),
                tuple(profiles[0].prominence.secondary_headings))  # fmt: skip
    print(f"postings: {len(jobs)} ({'copy of a catalogue' if a.db else 'synthetic'}),"
          f" profiles: {len(profiles)}")  # fmt: skip

    # The current reader: every profile reads every advert from the beginning.
    current: dict[int, float] = {}
    for n in (1, 2, 10):
        t, _ = timed(lambda n=n: [match_job(p, j, computed_at="b") for j, _ in jobs
                                  for p in profiles[:n]])  # fmt: skip
        current[n] = t
    # v2: the shared facts once per posting, then each profile over them.
    t_extract, shared = timed(lambda: [feat.extract(j, h, headings) for j, h in jobs])
    v2: dict[int, float] = {}
    for n in (1, 2, 10):
        t, _ = timed(lambda n=n: [pm.match(p, j, f) for (j, _), f in zip(jobs, shared, strict=True)
                                  for p in profiles[:n]])  # fmt: skip
        v2[n] = t + t_extract
    per = statistics.mean
    print("\nms per posting        current    v2 (facts once + profiles)")
    for n in (1, 2, 10):
        print(f"  {n:>2} profile(s)     {1000 * current[n] / len(jobs):8.1f}   "
              f"{1000 * v2[n] / len(jobs):8.1f}")  # fmt: skip
    print(f"  of which shared facts (once): {1000 * t_extract / len(jobs):.1f} ms")
    sizes = [len(str(f.payloads)) for f in shared]
    print(f"  stored facts per posting: ~{per(sizes) / 1024:.1f} KB (JSON, all components)")

    # Writing them: the additive table, in a scratch database, never the source.
    from career_agent.matching_v2.store import FeatureStore
    from career_agent.storage.db import connect, migrate, transaction

    with tempfile.TemporaryDirectory() as tmp:
        scratch = connect(Path(tmp) / "scratch.db")
        migrate(scratch)
        scratch.execute("PRAGMA foreign_keys = OFF")  # postings are not copied
        store = FeatureStore(scratch)

        def fill():
            with transaction(scratch):  # one transaction, as a recalculation page is
                return [store.ensure(j, h, headings) for j, h in jobs]

        t_write, _ = timed(fill)
        t_again, _ = timed(lambda: [store.ensure(j, h, headings) for j, h in jobs])
        size = sum(len(r[0]) for r in scratch.execute("SELECT payload_json FROM job_features"))
        scratch.close()
    print(f"  filling job_features: {1000 * t_write / len(jobs):.1f} ms per posting,"
          f" {size / len(jobs) / 1024:.1f} KB per posting; a second pass (all current):"
          f" {1000 * t_again / len(jobs):.2f} ms per posting")  # fmt: skip
    print(f"  extrapolated to 171,319 postings: {t_write / len(jobs) * 171319 / 60:.0f} min,"
          f" {size / len(jobs) * 171319 / 2**30:.2f} GB")  # fmt: skip

    # What each change makes stale, for P profiles and N postings.
    n_jobs, n_prof = len(jobs), len(profiles)
    jc, pc = len(comp.JOB_COMPONENTS), len(comp.PROFILE_COMPONENTS)
    print(f"\nartefacts made stale ({n_prof} profiles x {n_jobs} postings)")
    print("  change                         current: advert re-reads    v2: re-reads / rows")
    scenarios = [
        ("a reader / language rule", n_prof * n_jobs, "language"),
        ("one profile's salary", 0, "profile.salary"),
        ("one profile's work phrases", n_jobs, "profile.lexicon"),
        ("one posting's text", n_prof, "job.text"),
    ]
    for name, cur, change in scenarios:
        stale = comp.affected({change})
        job_rows = len(stale & set(comp.JOB_COMPONENTS))
        prof_rows = len(stale & set(comp.PROFILE_COMPONENTS))
        if change == "job.text":
            reads, rows = 1, job_rows + n_prof * prof_rows
        elif change.startswith("profile."):
            reads, rows = 0, n_jobs * prof_rows
        else:
            reads, rows = n_jobs, n_jobs * job_rows + n_jobs * n_prof * prof_rows
        print(f"  {name:<30} {cur:>8}                    {reads:>6} / {rows}")
    print(f"  (components: {jc} shared, {pc} per profile; a re-read is the expensive half)")


if __name__ == "__main__":
    main()
