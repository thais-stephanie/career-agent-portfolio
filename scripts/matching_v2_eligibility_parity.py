"""Eligibility v2 against readers-12, gate by gate, on thousands of real postings.

    uv run python scripts/matching_v2_eligibility_parity.py sample \
        --catalogue CATALOGUE --personal PERSONAL_DB --config-version 9 --out COPY.db
    uv run python scripts/matching_v2_eligibility_parity.py compare \
        --copy COPY.db --config-dir DIR --out REPORT.json

SAMPLE opens both databases READ-ONLY and writes a NEW bounded copy
(`matching_v2_sample.write_copy`) of open postings drawn per stratum: every
provider, and every eligibility situation the catalogue's own full-text index
can name (sponsorship, its absence, authorization, citizenship, clearance,
export control, office attendance, hybrid, worldwide, Latin America, Brazil,
the United States, Europe, exclusions, country lists, residence), plus
postings that name none of them (silence) and the Mattermost and ElevenLabs
postings. Each posting keeps the strata it was drawn for.

COMPARE decides every posting three ways and compares whole `GateOutcome`s
(gate, result, reason, code, quote, offsets, conflict):

* STORED: the owner's readers-12 answer, as production wrote it;
* LEGACY: this build reading the posting per profile (`match_job`), which is
  what production does and must equal STORED;
* V2: the profile deciding from the posting's shared eligibility facts after
  a round trip through their stored JSON form (`features.eligibility_to_dict`
  and `shared_from_facts`), the path Phase C would read.

The owner's configuration is compared on all three and on the score; a set of
synthetic profiles (several countries, authorization and sponsorship
situations, contractor answers) LEGACY against V2. Writes REPORT.json (ids,
gates, codes; no employer text) and prints counts. Opens no socket and calls
no model.
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.matching_v2_sample import write_copy  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import (  # noqa: E402
    Blocker,
    SearchConfig,
    load_search_config,
)
from career_agent.config.setup import Answers, apply_answers  # noqa: E402
from career_agent.match.engine import match_job, read_shared  # noqa: E402
from career_agent.match.gates import negation_key  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.storage.mvp_repo import deserialise_match  # noqa: E402

#: Eligibility situations, as full-text queries over the advert.
STRATA = {
    "sponsorship": "sponsor OR sponsorship OR visa OR visto",
    "no_sponsorship": '"not sponsor" OR "unable to sponsor" OR "no sponsorship" OR'
    ' "cannot sponsor" OR "without sponsorship" OR "not able to sponsor"',
    "authorization": '"authorized to work" OR "authorised to work" OR "work authorization" OR'
    ' "right to work" OR "eligible to work" OR "legally authorized"',
    "citizenship": "citizen OR citizenship OR cidadania OR cidadao",
    "clearance": "clearance",
    "export_control": '"export control" OR "export controls" OR itar',
    "office": '"in office" OR "in-office" OR "days a week" OR "on-site" OR onsite OR presencial',
    "hybrid": "hybrid OR hibrido",
    "worldwide": 'worldwide OR "anywhere in the world" OR globally OR "work from anywhere"',
    "latam": 'latam OR "latin america" OR "america latina"',
    "brazil": "brazil OR brasil",
    "united_states": '"united states" OR "us only" OR usa OR "u.s."',
    "europe": 'europe OR emea OR "european union"',
    "exclusion": "except OR excluding OR exceto OR excluindo",
    "country_list": '"open to candidates" OR "based in" OR "residing in" OR "located in"',
    "residence": "reside OR residence OR residency OR resident OR residir",
}
CASE_COMPANIES = ("mattermost", "elevenlabs")


def _fts(conn: sqlite3.Connection, query: str) -> set[str]:
    rows = conn.execute(
        "SELECT job_id FROM job_search WHERE job_search MATCH ?", (f"description : ({query})",)
    )
    return {str(r[0]) for r in rows}


def sample(a: argparse.Namespace) -> None:
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; give a new file")
    rng = random.Random(a.seed)
    cat = sqlite3.connect(f"file:{a.catalogue.as_posix()}?mode=ro", uri=True)
    open_ids = {
        str(r[0]): r[1]
        for r in cat.execute(
            "SELECT j.id, j.provider FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
            " WHERE j.closed_at IS NULL AND length(r.description_text) > 200 ORDER BY j.id"
        )
    }
    strata: dict[str, list[str]] = {}
    named: set[str] = set()
    for name, query in STRATA.items():
        hits = sorted(_fts(cat, query) & open_ids.keys())
        named |= set(hits)
        strata[name] = hits
    strata["silence"] = sorted(open_ids.keys() - named)
    # Per provider over EVERY open posting, textless ones included: a lead
    # with a declared hiring scope and no advert (Jobgether) is scored.
    by_provider: dict[str, list[str]] = defaultdict(list)
    for job_id, provider in cat.execute(
        "SELECT id, provider FROM job WHERE closed_at IS NULL AND content_hash IS NOT NULL"
        " ORDER BY id"
    ):
        by_provider[str(provider)].append(str(job_id))
    for provider, ids in by_provider.items():
        strata[f"provider:{provider}"] = ids
    for kind, like in (("remote", "%remote%"), ("hybrid", "%hybrid%"), ("onsite", "%on%site%")):
        strata[f"location:{kind}"] = sorted(
            str(r[0])
            for r in cat.execute(
                "SELECT id FROM job WHERE closed_at IS NULL AND lower(location_raw) LIKE ?",
                (like,),
            )
            if str(r[0]) in open_ids
        )
    cases = [
        str(r[0])
        for r in cat.execute(
            "SELECT j.id FROM job j JOIN company c ON c.id = j.company_id"
            f" WHERE lower(c.name) IN ({','.join('?' * len(CASE_COMPANIES))})"
            " AND j.closed_at IS NULL ORDER BY j.id",
            CASE_COMPANIES,
        )
    ]
    cat.close()
    membership: dict[str, list[str]] = defaultdict(list)
    for name, ids in strata.items():
        cap = a.per_provider if name.startswith("provider:") else a.per_stratum
        for job_id in rng.sample(ids, min(cap, len(ids))):
            membership[job_id].append(name)
    for job_id in cases:
        membership[job_id].append("case:mattermost_elevenlabs")
    picked = sorted(membership)
    out = write_copy(a.catalogue, a.personal, a.config_version, a.out, picked)
    out.execute("CREATE TABLE stratum (job_id TEXT, name TEXT)")
    out.executemany(
        "INSERT INTO stratum VALUES (?, ?)", [(j, n) for j, ns in membership.items() for n in ns]
    )
    out.commit()
    print(f"picked {len(picked)} postings")
    for name in sorted(strata):
        drawn = sum(name in ns for ns in membership.values())
        print(f"  {name:28s} {drawn:5d} drawn of {len(strata[name])}")
    print("stored answers:", out.execute("SELECT count(*) FROM job_match").fetchone()[0])
    out.close()


def wizard(country: str, **eligibility: object) -> SearchConfig:
    starter = yaml.safe_load((ROOT / "config" / "search.starter.yaml").read_text("utf-8"))
    mapping, _ = apply_answers(
        starter, Answers(role_examples=("customer support",), residence_country=country)
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "search.local.yaml").write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        config, _ = load_search_config(Path(tmp))
    if eligibility:
        config = config.model_copy(
            update={"eligibility": config.eligibility.model_copy(update=eligibility)}
        )
    return config


BLOCKERS = [
    Blocker(id="clearance", label="Security clearance", gate="clearance",
            patterns=["security clearance", "active clearance", "ts/sci", "secret clearance"],
            negation_sensitive=True),
    Blocker(id="itar", label="Export control", gate="clearance",
            patterns=["itar", "export control"], negation_sensitive=True),
    Blocker(id="license", label="Licence required", gate="credential",
            patterns=["license required", "certification required", "must be licensed"],
            negation_sensitive=True),
    Blocker(id="travel", label="Heavy travel", gate="travel",
            patterns=["75% travel", "extensive travel"], negation_sensitive=True),
    Blocker(id="driving", label="Driving licence", gate="requirement",
            patterns=["driver's license", "valid driving licence"], negation_sensitive=True),
]  # fmt: skip


def synthetic() -> dict[str, SearchConfig]:
    return {
        # residence only: nothing declared about authorization
        "br_residence_only": wizard("BR"),
        "br_authorized_br": wizard("BR", authorized_countries=["BR"]),
        "br_authorized_br_us": wizard("BR", authorized_countries=["BR", "US"]),
        "br_needs_us_sponsorship": wizard(
            "BR", authorized_countries=["BR"], sponsorship_needed=["US"]
        ),
        "br_not_authorized_us": wizard("BR", not_authorized_countries=["US"]),
        "br_contractor": wizard("BR", contractor=True, contractor_abroad=True),
        "br_not_contractor": wizard("BR", contractor=False, contractor_abroad=False),
        "us_authorized_no_sponsorship": wizard(
            "US", authorized_countries=["US"], sponsorship_needed=[]
        ),
        "co": wizard("CO"),
        "mx": wizard("MX"),
        "ar": wizard("AR"),
        "pt": wizard("PT"),
        "de_authorized_eu": wizard("DE", authorized_countries=["DE", "FR", "ES", "PT"]),
        "in_needs_us_uk": wizard("IN", sponsorship_needed=["US", "GB"]),
        # A person's own blockers: the only way the clearance, credential and
        # requirement gates FAIL. Hers, never stored with the posting.
        "br_blockers": wizard("BR", blockers=BLOCKERS),
        "us_blockers": wizard("US", authorized_countries=["US"], blockers=BLOCKERS),
    }


def _gates(result) -> list[dict]:
    return [asdict(g) for g in result.gates]


def compare(a: argparse.Namespace) -> None:
    owner, _ = load_search_config(a.config_dir)
    profiles = {"owner": owner, **synthetic()}
    if a.only:
        profiles = {k: v for k, v in profiles.items() if k in a.only or k == "owner"}
    conn = sqlite3.connect(f"file:{a.copy.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    membership: dict[str, list[str]] = defaultdict(list)
    for job_id, name in conn.execute("SELECT job_id, name FROM stratum"):
        membership[str(job_id)].append(str(name))
    started = time.perf_counter()
    n = 0
    tally: dict[str, Counter] = defaultdict(Counter)
    per_stratum: dict[str, Counter] = defaultdict(Counter)
    per_gate: dict[str, Counter] = defaultdict(Counter)
    divergences: list[dict] = []
    statuses: Counter = Counter()
    gate_results: Counter = Counter()
    for job, content_hash, _provider, stored_json in postings(conn, a.limit):
        n += 1
        job_id = str(job.job_id)
        stored = deserialise_match(stored_json)
        legacy_owner = match_job(owner, job, computed_at="parity")
        shared_by_key: dict[tuple, tuple] = {}
        for name, config in profiles.items():
            headings = (
                tuple(config.prominence.primary_headings),
                tuple(config.prominence.secondary_headings),
            )
            key = (headings, negation_key(config.negation))
            if key not in shared_by_key:
                legacy_shared = read_shared(job, list(headings[0]), list(headings[1]))
                f = feat.extract(job, content_hash, headings, negation=config.negation)
                stored_form = json.loads(json.dumps(f.payloads))  # as written, as read back
                again = feat.JobFeatures(job_id, content_hash, stored_form, f.stamps)
                shared_by_key[key] = (legacy_shared, feat.shared_from_facts(job, again))
            legacy_shared, v2_shared = shared_by_key[key]
            legacy = (
                legacy_owner
                if name == "owner"
                else match_job(config, job, computed_at="parity", shared=legacy_shared)
            )
            v2 = match_job(config, job, computed_at="parity", shared=v2_shared)
            pairs = [("legacy_v2", legacy, v2)]
            if name == "owner":
                pairs += [("stored_v2", stored, v2), ("stored_legacy", stored, legacy)]
                statuses[str(v2.eligibility_status)] += 1
                for g in v2.gates:
                    gate_results[(g.gate, str(g.result))] += 1
            for pair, left, right in pairs:
                t = tally[f"{name}:{pair}"]
                t["n"] += 1
                same_status = left.eligibility_status == right.eligibility_status
                same_gates = left.gates == right.gates
                same_score = left.match_score == right.match_score
                t["status"] += same_status
                t["gates"] += same_gates
                t["score"] += same_score
                for lg, rg in zip(left.gates, right.gates, strict=False):
                    pg = per_gate[f"{pair}:{lg.gate}"]
                    pg["n"] += 1
                    pg["result"] += lg.result == rg.result
                    pg["reason"] += (lg.reason, lg.code) == (rg.reason, rg.code)
                    pg["evidence"] += (lg.quote, lg.char_start, lg.char_end, lg.conflict) == (
                        rg.quote,
                        rg.char_start,
                        rg.char_end,
                        rg.conflict,
                    )
                if pair in ("legacy_v2", "stored_v2"):
                    for s in membership[job_id]:
                        per_stratum[s]["n"] += 1
                        per_stratum[s]["identical"] += same_status and same_gates
                if not (same_status and same_gates and same_score):
                    divergences.append(
                        {
                            "job_id": job_id,
                            "profile": name,
                            "pair": pair,
                            "strata": membership[job_id],
                            "status": [str(left.eligibility_status), str(right.eligibility_status)],
                            "score": [left.match_score, right.match_score],
                            "gates": [
                                [lg["gate"], lg["result"], lg["code"], rg["result"], rg["code"]]
                                for lg, rg in zip(_gates(left), _gates(right), strict=False)
                                if lg != rg
                            ],
                        }
                    )
        if n % 500 == 0:
            print(f"  {n} postings, {time.perf_counter() - started:.0f} s", flush=True)
    elapsed = time.perf_counter() - started
    report = {
        "postings": n,
        "profiles": list(profiles),
        "tally": {k: dict(v) for k, v in tally.items()},
        "per_gate": {k: dict(v) for k, v in per_gate.items()},
        "per_stratum": {k: dict(v) for k, v in per_stratum.items()},
        "owner_status": dict(statuses),
        "owner_gate_results": {f"{g}:{r}": c for (g, r), c in gate_results.items()},
        "divergences": divergences,
    }
    a.out.write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    print(f"postings: {n}, profiles: {len(profiles)}, {elapsed:.0f} s")
    for key, t in sorted(tally.items()):
        print(
            f"  {key:44s} status {t['status']}/{t['n']}  gates {t['gates']}/{t['n']}"
            f"  score {t['score']}/{t['n']}"
        )
    print(f"owner eligibility (v2): {dict(statuses)}")
    print(f"divergences: {len(divergences)}")
    for d in divergences[:20]:
        print(f"  {d['job_id']} {d['profile']} {d['pair']} {d['status']} {d['gates'][:2]}")
    print(f"report: {a.out}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--catalogue", type=Path, required=True)
    s.add_argument("--personal", type=Path, required=True)
    s.add_argument("--config-version", type=int, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--per-stratum", type=int, default=250)
    s.add_argument("--per-provider", type=int, default=120)
    s.add_argument("--seed", type=int, default=20261014)
    c = sub.add_parser("compare")
    c.add_argument("--copy", type=Path, required=True)
    c.add_argument("--config-dir", type=Path, required=True)
    c.add_argument("--out", type=Path, required=True)
    c.add_argument("--limit", type=int)
    c.add_argument("--only", nargs="*", default=[], help="these profiles (and the owner)")
    a = p.parse_args()
    sample(a) if a.cmd == "sample" else compare(a)


if __name__ == "__main__":
    main()
