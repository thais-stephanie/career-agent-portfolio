"""How v2's work grows with the number of profiles, part by part.

    uv run python scripts/matching_v2_scale.py --sample SAMPLE.db --limit 300

On a bounded copy (`matching_v2_sample.py`) and 1, 2, 10 and 50 wizard-built
profiles in different professions and countries, measures in ms per posting:

* shared facts, read once per posting (v2);
* per profile: work matching (phrases against shared tokens), eligibility
  (the gates deciding from shared facts) and the rest of the score;
* the current reader, `match_job` per profile;
* what one profile's salary change and one posting's new text cost.

Opens the copy read-only, writes nothing, calls no model, opens no socket.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.matching_v2_evaluate import wizard  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.setup import Answers  # noqa: E402
from career_agent.match.engine import match_job  # noqa: E402
from career_agent.match.gates import evaluate_gates  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402

ROLES = [
    ("workflow automation", "systems integration"), ("assistência ao paciente", "prontuário"),
    ("lesson plans", "student support"), ("lead generation", "vendas consultivas"),
    ("accounts payable", "budget analysis"), ("recruitment", "payroll"),
    ("user interface design", "user research"), ("atendimento ao cliente", "suporte técnico"),
    ("data analysis", "data migration"), ("inventory management", "supplier negotiation"),
    ("project management", "process improvement"), ("social media content", "campaign management"),
    ("gestão de projetos", "melhoria de processos"), ("customer onboarding", "implementation"),
    ("sales training", "report generation"), ("contract review", "legal research"),
    ("quality assurance", "test automation"), ("network security", "incident response"),
    ("financial reporting", "tax compliance"), ("event planning", "vendor management"),
    ("content writing", "seo"), ("machine learning", "model deployment"),
    ("clinical research", "patient recruitment"), ("logistics coordination", "fleet management"),
    ("customer success", "account management"),
]  # fmt: skip
COUNTRIES = ["BR", "MX", "AR", "CO", "PT", "ES", "US", "CL", "PE", "UY"]


def profiles(n: int):
    combos = list(product(ROLES, COUNTRIES))
    step = max(1, len(combos) // n)
    return [
        wizard(Answers(role_examples=roles, residence_country=country))
        for roles, country in combos[::step][:n]
    ]


def ms(seconds: float, n: int) -> str:
    return f"{1000 * seconds / n:7.2f}"


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--limit", type=int, default=300)
    a = p.parse_args()
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    jobs = [(j, h) for j, h, _p, _s in postings(conn, a.limit)]
    n = len(jobs)
    every = profiles(50)
    machinery = every[0]
    headings = (
        tuple(machinery.prominence.primary_headings),
        tuple(machinery.prominence.secondary_headings),
    )
    for job, h in jobs[:10]:  # warm the caches of both readers
        match_job(machinery, job, computed_at="w")
        pm.match(machinery, job, feat.extract(job, h, headings, negation=machinery.negation))
    print(f"postings: {n}; ms per posting (all profiles together)")
    print(" profiles | shared facts | v2 work | v2 eligibility | v2 rest | v2 total | current")
    for count in (1, 2, 10, 50):
        group = every[:count]
        t = time.perf_counter()
        facts = [feat.extract(j, h, headings, negation=machinery.negation) for j, h in jobs]
        t_shared = time.perf_counter() - t
        for (job, _h), f in zip(jobs, facts, strict=True):  # tokens: shared work too
            feat.work_units(job, f)
        t_shared = time.perf_counter() - t
        t_work = t_elig = t_total = 0.0
        for config in group:
            for (job, _h), f in zip(jobs, facts, strict=True):
                s = time.perf_counter()
                pm.observe_from_features(config, f, job)
                t_work += time.perf_counter() - s
                sh = f.shared
                assert sh is not None
                s = time.perf_counter()
                evaluate_gates(
                    config, {}, sh.title_field, sh.description_field,
                    declared_scope=job.declared_hiring_scope, workplace_type=job.workplace_type,
                    place=sh.place, location_raw=job.location_raw,
                    employment=sh.employment_stated, facts=sh.eligibility,
                )  # fmt: skip
                t_elig += time.perf_counter() - s
                s = time.perf_counter()
                pm.match(config, job, f)
                t_total += time.perf_counter() - s
        t = time.perf_counter()
        for config in group:
            for job, _h in jobs:
                match_job(config, job, computed_at="b")
        t_current = time.perf_counter() - t
        rest = max(0.0, t_total - t_work - t_elig)
        print(
            f" {count:8d} | {ms(t_shared, n)}      | {ms(t_work, n)} | {ms(t_elig, n)}        |"
            f" {ms(rest, n)} | {ms(t_shared + t_total, n)}  | {ms(t_current, n)}"
        )
    # One profile changes its salary: v2 recomputes that profile only, from
    # stored facts (no advert read); the current reader re-reads every advert
    # for that profile (or replays its stored readings, ADR-0028).
    stored = [feat.extract(j, h, headings, negation=machinery.negation) for j, h in jobs]
    for f in stored:
        f.shared, f.units = None, None  # as loaded from the store
    s = time.perf_counter()
    for (job, _h), f in zip(jobs, stored, strict=True):
        pm.match(every[0], job, f)
    t_v2 = time.perf_counter() - s
    s = time.perf_counter()
    for job, _h in jobs:
        match_job(every[0], job, computed_at="b")
    t_cur = time.perf_counter() - s
    print(f"one profile's preference changes, {n} postings: v2 {ms(t_v2, n)} ms/posting"
          f" (from stored facts), current {ms(t_cur, n)} ms/posting")  # fmt: skip
    # One posting's text changes, 50 profiles: v2 reads it once.
    job, h = jobs[0]
    s = time.perf_counter()
    f = feat.extract(job, h + "x", headings, negation=machinery.negation)
    for config in every:
        pm.match(config, job, f)
    t_v2 = time.perf_counter() - s
    s = time.perf_counter()
    for config in every:
        match_job(config, job, computed_at="b")
    t_cur = time.perf_counter() - s
    print(f"one posting's text changes, 50 profiles: v2 {1000 * t_v2:.1f} ms,"
          f" current {1000 * t_cur:.1f} ms")  # fmt: skip


if __name__ == "__main__":
    main()
