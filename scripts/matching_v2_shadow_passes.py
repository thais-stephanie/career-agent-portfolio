"""Two complete shadow passes over one bounded copy, then eight changes.

    uv run python scripts/matching_v2_shadow_passes.py --sample S.db \
        --config-dir DIR --work WORKDIR

Builds, in WORKDIR (a new directory), a catalogue holding the copy's
postings at the current schema (`job_features`, the full-text index) and one
profile database per profile (the owner's configuration and nine
wizard-built professions in other countries). Then:

1. PASS 1 fills the shared facts and every profile's answers;
2. PASS 2 runs again, postings and profiles in the reverse order: it must
   read no advert, compute nothing and return the same answers;
3. a FRESH catalogue and fresh profiles, postings shuffled: the same answers
   again (no dependence on order or on what was stored before);
4. each profile ALONE in a fresh database: its answers equal the ones it got
   beside nine others (nothing crosses between profiles), and no profile
   database holds a shared fact nor the catalogue a profile row;
5. eight changes, each from a copy of the state after pass 2: what became
   stale, what stayed valid, which postings and profiles were selected, and
   how long recomputing took.

Prints counts and timings only; writes only inside WORKDIR (delete it after).
Opens the sample read-only, opens no socket, calls no model.
"""

from __future__ import annotations

import argparse
import random
import shutil
import sqlite3
import sys
import time
from dataclasses import replace
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.matching_v2_evaluate import PROFESSIONS, wizard  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import SearchConfig, load_search_config  # noqa: E402
from career_agent.config.setup import Answers  # noqa: E402
from career_agent.matching_v2 import components as comp  # noqa: E402
from career_agent.matching_v2 import invalidation as inv  # noqa: E402
from career_agent.matching_v2.language import DATA, load_table, tokens  # noqa: E402
from career_agent.matching_v2.profile_match import profile_inputs  # noqa: E402
from career_agent.matching_v2.store import FeatureStore, MatchStore  # noqa: E402
from career_agent.storage.db import connect, migrate, transaction  # noqa: E402
from career_agent.storage.search_index import rebuild  # noqa: E402

COPIED = ("company", "job_raw", "job", "job_provider_payload", "job_discovery_source")


def build_catalogue(sample: Path, path: Path) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    conn.execute("PRAGMA foreign_keys = OFF")  # boards are not part of the copy
    # Attached by path (the project's connection opens no URIs); only read.
    conn.execute("ATTACH DATABASE ? AS src", (str(sample),))
    with transaction(conn):
        for table in COPIED:
            mine = [r[1] for r in conn.execute(f"PRAGMA main.table_info({table})")]
            theirs = {r[1] for r in conn.execute(f"PRAGMA src.table_info({table})")}
            cols = ", ".join(c for c in mine if c in theirs)
            conn.execute(f"INSERT INTO main.{table} ({cols}) SELECT {cols} FROM src.{table}")
    conn.execute("DETACH DATABASE src")
    rebuild(conn)
    return conn


def profile_db(path: Path) -> sqlite3.Connection:
    conn = connect(path)
    migrate(conn)
    return conn


def headings(config: SearchConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(config.prominence.primary_headings), tuple(config.prominence.secondary_headings)


def run(catalogue, jobs, profiles, machinery, order=None, profile_order=None):
    """One pass. Returns (answers per profile, feature tally, store tallies, s)."""
    features = FeatureStore(catalogue)
    stores = {k: MatchStore(conn) for k, (_, conn) in profiles.items()}
    out: dict[str, dict[str, dict]] = {k: {} for k in profiles}
    keys = order or list(jobs)
    names = profile_order or list(profiles)
    started = time.perf_counter()
    for i in range(0, len(keys), 200):
        with transaction(catalogue):
            for job_key in keys[i : i + 200]:
                job, h = jobs[job_key]
                shared = features.ensure(job, h, headings(machinery), machinery.negation)
                for name in names:
                    config, conn = profiles[name]
                    with transaction(conn):
                        answer = stores[name].ensure(config, job, h, shared)
                    out[name][job_key] = answer
    return (
        out,
        features.tally,
        {k: s.tally for k, s in stores.items()},
        (time.perf_counter() - started),
    )


def same(a: dict, b: dict) -> bool:
    return all(a[p] == b[p] for p in a)


def copy_state(work: Path, name: str, profiles_src: dict) -> tuple[sqlite3.Connection, dict]:
    """A copy of the catalogue and profile databases after pass 2."""
    target = work / name
    target.mkdir()
    cat = connect(target / "catalogue.db")
    src = connect(work / "base" / "catalogue.db")
    src.backup(cat)
    src.close()
    cat.execute("PRAGMA foreign_keys = OFF")
    out = {}
    for key, (config, conn) in profiles_src.items():
        dst = connect(target / f"{key}.db")
        conn.backup(dst)
        out[key] = (config, dst)
    return cat, out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    p.add_argument("--work", type=Path, required=True)
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    if a.work.exists():
        raise SystemExit(f"{a.work} exists; give a new directory")
    (a.work / "base").mkdir(parents=True)
    owner, _ = load_search_config(a.config_dir)
    answers = {k: v for k, v in list(PROFESSIONS.items())[:9]}
    configs = {"owner": owner, **{k: wizard(v) for k, v in answers.items()}}
    src = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    jobs = {str(j.job_id): (j, h) for j, h, _p, _s in postings(src, a.limit)}
    print(f"postings: {len(jobs)}; profiles: {len(configs)}")
    catalogue = build_catalogue(a.sample, a.work / "base" / "catalogue.db")
    profiles = {k: (c, profile_db(a.work / "base" / f"{k}.db")) for k, c in configs.items()}

    one, t1, s1, sec1 = run(catalogue, jobs, profiles, owner)
    print(f"\nPASS 1: {sec1:.1f} s; adverts read {t1.reads}; shared computed {dict(t1.computed)}")
    print(f"  profile components computed (owner): {dict(s1['owner'].computed)}")
    order = list(reversed(list(jobs)))
    two, t2, s2, sec2 = run(catalogue, jobs, profiles, owner, order, list(reversed(configs)))
    computed2 = sum(sum(t.computed.values()) for t in s2.values())
    print(f"PASS 2 (reverse order): {sec2:.1f} s; adverts read {t2.reads}; shared computed"
          f" {sum(t2.computed.values())}; profile components computed {computed2};"
          f" answers identical: {same(one, two)}")  # fmt: skip

    # A fresh world, postings shuffled.
    (a.work / "fresh").mkdir()
    fresh_cat = build_catalogue(a.sample, a.work / "fresh" / "catalogue.db")
    fresh = {k: (c, profile_db(a.work / "fresh" / f"{k}.db")) for k, c in configs.items()}
    shuffled = list(jobs)
    random.Random(7).shuffle(shuffled)
    three, _, _, sec3 = run(fresh_cat, jobs, fresh, owner, shuffled)
    print(f"FRESH, shuffled: {sec3:.1f} s; answers identical to pass 1: {same(one, three)}")
    shared_rows = {
        r[0]: r[1:]
        for r in catalogue.execute(
            "SELECT job_id, content_hash, inputs, sentences, facts_json FROM job_features"
        )
    }
    fresh_rows = {
        r[0]: r[1:]
        for r in fresh_cat.execute(
            "SELECT job_id, content_hash, inputs, sentences, facts_json FROM job_features"
        )
    }
    print(f"  shared facts byte-identical across the two worlds: {shared_rows == fresh_rows}")
    fresh_cat.close()
    for _, conn in fresh.values():
        conn.close()

    # Each profile alone.
    leaks = 0
    alone_same = 0
    for key, config in configs.items():
        (a.work / f"alone-{key}").mkdir()
        cat = build_catalogue(a.sample, a.work / f"alone-{key}" / "catalogue.db")
        solo = {key: (config, profile_db(a.work / f"alone-{key}" / "p.db"))}
        out, _, _, _ = run(cat, jobs, solo, owner)
        alone_same += out[key] == one[key]
        own = set(config.lexicon)
        leaks += sum(
            1 for ans in one[key].values() for item in ans["evidence"] if item[0] not in own
        )
        cat.close()
        solo[key][1].close()
    in_profiles = sum(
        conn.execute("SELECT count(*) FROM job_features").fetchone()[0]
        for _, conn in profiles.values()
    )
    in_catalogue = catalogue.execute("SELECT count(*) FROM profile_job_match").fetchone()[0]
    print(f"ALONE: {alone_same}/{len(configs)} profiles identical alone and together;"
          f" evidence of another profile's phrase: {leaks}; shared facts in profile databases:"
          f" {in_profiles}; profile rows in the catalogue: {in_catalogue}")  # fmt: skip
    catalogue.close()

    # Eight changes.
    print("\nCHANGES (from the state after pass 2):")
    who = "automation"
    target = PROFESSIONS[who]

    def with_answers(**kw) -> SearchConfig:
        fields = {
            "role_examples": target.role_examples,
            "residence_country": target.residence_country,
        }
        fields.update(kw)
        return wizard(Answers(**fields))

    def eligibility(config: SearchConfig, **kw) -> SearchConfig:
        return config.model_copy(update={"eligibility": config.eligibility.model_copy(update=kw)})

    scenarios = {
        "1 salary preference": ("profile", with_answers(target_amount=9000, currency="USD",
                                                        period="MONTH")),
        "2 work preference": ("profile", with_answers(
            role_examples=(*target.role_examples, "process mapping"))),
        "3 residence": ("profile", with_answers(residence_country="AR")),
        "4 authorization": ("profile", eligibility(configs[who], authorized_countries=["BR", "US"],
                                                   sponsorship_needed=["GB"])),
        "5 posting text": ("text", None),
        "6 language table": ("language", None),
        "7 eligibility rules version": ("version", ("eligibility_facts", "eligibility")),
        "8 work match version": ("version", ("work_match",)),
    }  # fmt: skip
    for title, (kind, payload) in scenarios.items():
        name = title.split()[0]
        cat, profs = copy_state(a.work, f"change-{name}", profiles)
        selected_jobs = list(jobs)
        expected: set[str] = set()
        note = ""
        saved = dict(comp.REGISTRY)
        try:
            if kind == "profile":
                old = profs[who][0]
                before, after = profile_inputs(old), profile_inputs(payload)
                expected = comp.affected({k for k, v in after.items() if v != before.get(k)})
                profs[who] = (payload, profs[who][1])
                if title.startswith("2"):
                    started = time.perf_counter()
                    cands, how = inv.phrase_candidates(cat, ["process mapping"])
                    took = 1000 * (time.perf_counter() - started)
                    note = (
                        f"can gain the phrase: {len(cands)} of {len(jobs)} ({how}, {took:.0f} ms)"
                    )
            elif kind == "text":
                key = next(iter(jobs))
                job, h = jobs[key]
                edited = replace(job, description=job.description + chr(10) + "New line.")
                selected_jobs = [key]
                jobs_changed = {**jobs, key: (edited, h + "-edited")}
                expected = comp.affected({"job.text"})
            elif kind == "language":
                raw = yaml.safe_load(DATA.read_text(encoding="utf-8"))
                raw["exact_equivalent"].append({"en": ["warehouse"], "pt": ["armazem"]})
                path = a.work / "language_data.yaml"
                path.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
                old_t, new_t = load_table(), load_table(path)
                change = inv.diff(old_t, new_t)
                started = time.perf_counter()
                moved, unmoved, how = inv.affected_jobs(cat, change, old_t)
                took = 1000 * (time.perf_counter() - started)
                check = random.Random(3).sample(unmoved, min(300, len(unmoved)))
                identical = sum(
                    [t.root for t in tokens(jobs[j][0].description, old_t)]
                    == [t.root for t in tokens(jobs[j][0].description, new_t)]
                    for j in check
                )
                note = (f"selected {len(moved)} of {len(jobs)} by the {how} in {took:.0f} ms;"
                        f" {identical}/{len(check)} unselected tokenise identically")  # fmt: skip
                expected = comp.affected({"language"})
                selected_jobs = moved
            else:
                for name_ in payload:
                    c = comp.REGISTRY[name_]
                    comp.REGISTRY[name_] = comp.Component(
                        c.name, c.version + "+", c.scope, c.depends_on, c.inputs
                    )
                expected = comp.affected(set(payload))
            if kind == "language":
                # The table is not part of `extract`'s signature; what a change
                # costs is the selection above and re-reading those postings.
                comp.REGISTRY["language"] = comp.Component(
                    "language", "1+", "job", ("text_parser",)
                )
            use = jobs_changed if kind == "text" else jobs
            sub = {k: use[k] for k in selected_jobs}
            _, ft, st, sec = run(cat, sub, profs, owner)
            profiles_hit = sorted(k for k, t in st.items() if t.computed)
            stale_seen = set(ft.computed) | {c for t in st.values() for c in t.computed}
            valid = sorted(set(comp.REGISTRY) - stale_seen)
            print(f"  {title}:")
            print(f"    stale (expected by the graph): {sorted(expected)}")
            print(f"    recomputed: shared {dict(ft.computed)}; adverts read {ft.reads};"
                  f" profiles recomputed {profiles_hit}")  # fmt: skip
            print(f"    still valid: {valid}")
            print(f"    postings selected {len(sub)}; {sec:.2f} s"
                  + (f"; {note}" if note else ""))  # fmt: skip
            unexpected = stale_seen - expected
            if unexpected:
                print(f"    STALE WITHOUT A DEPENDENCY: {sorted(unexpected)}")
        finally:
            comp.REGISTRY.clear()
            comp.REGISTRY.update(saved)
            cat.close()
            for _, conn in profs.values():
                conn.close()
    for _, conn in profiles.values():
        conn.close()
    shutil.rmtree(a.work, ignore_errors=True)
    print(f"\n{a.work} removed")


if __name__ == "__main__":
    main()
