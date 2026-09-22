"""The operational sample: 150 postings, chosen the same way every time.

This is evaluation infrastructure. It is **not** the production relevance
prefilter, and nothing here decides whether a job is worth the candidate's
attention -- it decides which postings the extractor is measured on.

WHY STRATIFIED RATHER THAN RANDOM
---------------------------------
A uniform random draw from this corpus would be about 70% greenhouse, mostly
long US postings from a handful of large employers, and would say almost
nothing about the shapes that break extraction. The strata below are the
dimensions along which extraction observably differs -- provider envelope,
description length, and how much the ATS record actually carries.

WHY A SEED AND NOT A TIMESTAMP
------------------------------
Two benchmark runs have to be comparable. A sample that changed between them
would turn every difference into an unanswerable question about whether the
model improved or the postings got easier.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

#: Changing this changes the sample. It is written down, committed, and
#: reported beside every number the sample produces.
SEED = "career-agent-m2-operational-sample-v1"

SAMPLE_SIZE = 150

#: Description length bands, in characters, with the share of the sample each
#: takes. Deliberately over-weights the tails: the median posting is the one
#: extraction handles best, and the short and long ends are where the
#: interesting failures live.
LENGTH_BANDS: tuple[tuple[str, int, int, float], ...] = (
    ("very_short", 0, 1_500, 0.10),
    ("short", 1_500, 3_500, 0.20),
    ("median", 3_500, 6_500, 0.30),
    ("long", 6_500, 9_500, 0.25),
    ("very_long", 9_500, 10**9, 0.15),
)


def _rank(seed: str, job_id: str) -> str:
    """A stable pseudo-random ordering key.

    A hash rather than `random.shuffle` because it needs no shared state and
    gives the same answer on any machine, in any Python version, forever.
    """
    return hashlib.sha256(f"{seed}:{job_id}".encode()).hexdigest()


@dataclass
class Stratum:
    name: str
    target: int
    chosen: list[str] = field(default_factory=list)
    available: int = 0

    @property
    def shortfall(self) -> int:
        return max(0, self.target - len(self.chosen))


@dataclass
class SampleManifest:
    """The sample, and everything needed to reproduce or audit it."""

    seed: str
    corpus_denominator: int
    job_ids: list[str]
    strata: list[Stratum]
    provider_distribution: dict[str, int]
    company_distribution: dict[str, int]
    metadata_richness: dict[str, int]
    work_model_language: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "sample_size": len(self.job_ids),
            "corpus_denominator": self.corpus_denominator,
            "strata": [
                {
                    "name": s.name,
                    "target": s.target,
                    "chosen": len(s.chosen),
                    "available": s.available,
                }
                for s in self.strata
            ],
            "provider_distribution": self.provider_distribution,
            "companies": len(self.company_distribution),
            "most_represented_company": max(self.company_distribution.values(), default=0),
            "metadata_richness": self.metadata_richness,
            "work_model_language": self.work_model_language,
            "job_ids": self.job_ids,
        }


def build_sample(
    conn: sqlite3.Connection, seed: str = SEED, size: int = SAMPLE_SIZE
) -> SampleManifest:
    """Draw the sample. Same corpus and same seed give the same 150 ids.

    Strata are filled in order and a shortfall in one is topped up from the
    global remainder, so a band the corpus cannot fill does not silently
    shrink the sample. The shortfall is reported rather than hidden.
    """
    rows = conn.execute(
        "SELECT j.id, j.provider, c.name AS company, LENGTH(r.description_text) AS chars,"
        "       r.description_text"
        " FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " JOIN company c ON c.id = j.company_id"
        " WHERE j.closed_at IS NULL AND TRIM(r.description_text) <> ''"
        " ORDER BY j.id"
    ).fetchall()

    denominator = len(rows)
    ordered = sorted(rows, key=lambda row: _rank(seed, row["id"]))

    strata = [Stratum(name, round(size * share)) for name, _, _, share in LENGTH_BANDS]
    bounds = {name: (low, high) for name, low, high, _ in LENGTH_BANDS}
    by_stratum: dict[str, Stratum] = {s.name: s for s in strata}

    #: At most this many from one employer, so the sample measures extraction
    #: rather than one company's house style.
    per_company = max(2, size // 25)
    company_count: dict[str, int] = {}
    taken: set[str] = set()

    for row in ordered:
        chars = row["chars"] or 0
        name = next(n for n, (low, high) in bounds.items() if low <= chars < high)
        stratum = by_stratum[name]
        stratum.available += 1
        if len(stratum.chosen) >= stratum.target:
            continue
        if company_count.get(row["company"], 0) >= per_company:
            continue
        stratum.chosen.append(row["id"])
        company_count[row["company"]] = company_count.get(row["company"], 0) + 1
        taken.add(row["id"])

    # Top up from the remainder, still in seeded order, when a band ran dry.
    shortfall = sum(s.shortfall for s in strata)
    if shortfall:
        for row in ordered:
            if shortfall <= 0:
                break
            if row["id"] in taken or company_count.get(row["company"], 0) >= per_company:
                continue
            strata[2].chosen.append(row["id"])  # the median band absorbs top-ups
            company_count[row["company"]] = company_count.get(row["company"], 0) + 1
            taken.add(row["id"])
            shortfall -= 1

    chosen_rows = [row for row in ordered if row["id"] in taken]
    job_ids = sorted(taken)

    providers: dict[str, int] = {}
    companies: dict[str, int] = {}
    richness = {"payload_absent": 0, "one_field": 0, "several_fields": 0}
    language = {"says_remote": 0, "says_hybrid_or_onsite": 0, "silent": 0}

    for row in chosen_rows:
        providers[row["provider"]] = providers.get(row["provider"], 0) + 1
        companies[row["company"]] = companies.get(row["company"], 0) + 1

        payload = conn.execute(
            "SELECT payload_json FROM job_provider_payload WHERE job_id = ?"
            " ORDER BY captured_at DESC, id DESC LIMIT 1",
            (row["id"],),
        ).fetchone()
        if payload is None:
            richness["payload_absent"] += 1
        else:
            fields = len(json.loads(payload["payload_json"]))
            richness["one_field" if fields <= 1 else "several_fields"] += 1

        text = (row["description_text"] or "").lower()
        if "remote" in text:
            language["says_remote"] += 1
        elif "hybrid" in text or "onsite" in text or "on-site" in text:
            language["says_hybrid_or_onsite"] += 1
        else:
            language["silent"] += 1

    return SampleManifest(
        seed=seed,
        corpus_denominator=denominator,
        job_ids=job_ids,
        strata=strata,
        provider_distribution=providers,
        company_distribution=companies,
        metadata_richness=richness,
        work_model_language=language,
    )


def golden_overlap(manifest: SampleManifest, golden_job_ids: Sequence[str]) -> list[str]:
    """Which golden postings the sample also drew.

    Reported rather than prevented. The golden set tunes the prompt; if the
    operational sample shared postings with it, a benchmark run would be
    partly measuring memorisation of cases the prompt was written against.
    """
    return sorted(set(manifest.job_ids) & set(golden_job_ids))
