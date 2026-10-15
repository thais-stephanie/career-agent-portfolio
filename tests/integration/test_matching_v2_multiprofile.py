"""One posting, several people: shared facts once, private results each.

Four invented postings in two languages and four invented profiles built by
the setup wizard itself (`config.setup.apply_answers` over the neutral
starter): an automation specialist, a nurse who writes in Portuguese, a
teacher and a salesperson. Each profile has its own database, as local
profiles do. Nothing here is the owner's configuration (ADR-0037).
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.config.setup import Answers, apply_answers
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import evidence_policy as policy_mod
from career_agent.matching_v2.store import FeatureStore, MatchStore
from career_agent.pipeline.manual_import import import_posting
from career_agent.storage.db import connect, migrate, transaction

POSTINGS = {
    "automation": (
        "Automation Specialist",
        "Example Fintech (fictitious)",
        "Brazil",
        "About us\nWe are a payments company founded in 2015.\n\n"
        "Responsibilities\n- Lead workflow automation across finance and operations.\n"
        "- Own the integration of internal systems with partner platforms.\n\n"
        "Requirements\nExperience with Zapier or similar tools.\n\n"
        "Benefits\nHealth insurance and paid time off.",
    ),
    "teacher": (
        "Professor de Matemática",
        "Escola Exemplo (fictícia)",
        "Lisboa, Portugal",
        "Sobre nós\nSomos uma escola com 40 anos de história.\n\n"
        "Responsabilidades\n- Elaborar planos de aula para o ensino fundamental.\n"
        "- Oferecer suporte aos alunos durante as aulas.\n\n"
        "Requisitos\nLicenciatura em Matemática.",
    ),
    "nurse": (
        "Enfermeira Assistencial",
        "Hospital Exemplo (fictício)",
        "São Paulo, Brazil",
        "Responsabilidades\n- Prestar assistência ao paciente internado.\n"
        "- Realizar a administração de medicamentos conforme prescrição.\n\n"
        "Requisitos\nCOREN ativo.\n\nBenefícios\nPlano de saúde.",
    ),
    "sales": (
        "Account Executive",
        "Example SaaS (fictitious)",
        "Mexico City, Mexico",
        "Responsibilities\n- Generate qualified leads through outbound prospecting.\n"
        "- Lead consultative sales for mid-market accounts.\n\n"
        "Compensation\nUSD 60,000 to 80,000 per year.",
    ),
}

PROFILES = {
    "automation": Answers(
        role_examples=("workflow automation", "systems integration"),
        skills=("Zapier",),
        residence_country="BR",
        target_amount=8000,
        currency="USD",
        period="MONTH",
    ),
    "nurse": Answers(
        role_examples=("assistência ao paciente", "administração de medicamentos"),
        residence_country="BR",
    ),
    "teacher": Answers(role_examples=("lesson plans", "student support"), residence_country="PT"),
    "sales": Answers(
        role_examples=("lead generation", "vendas consultivas"),
        residence_country="MX",
        target_amount=70000,
        currency="USD",
        period="YEAR",
    ),
}


def wizard_profile(answers: Answers) -> SearchConfig:
    """A configuration exactly as the setup wizard would write it."""
    starter = yaml.safe_load(
        (committed_config_dir() / "search.starter.yaml").read_text(encoding="utf-8")
    )
    mapping, _ = apply_answers(starter, answers)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "search.local.yaml").write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        config, _ = load_search_config(Path(tmp))
    return config


@pytest.fixture
def world(tmp_path: Path):
    catalogue = connect(tmp_path / "catalogue.db")
    migrate(catalogue)
    base = wizard_profile(PROFILES["automation"])
    jobs: dict[str, tuple[JobFacts, str]] = {}
    for key, (title, company, location, description) in POSTINGS.items():
        job_id = import_posting(
            catalogue, base, title=title, company=company, description=description,
            location=location, config_id=base.config_id, config_version=base.config_version,
            now="2026-10-14T00:00:00Z",
        )  # fmt: skip
        content_hash = catalogue.execute(
            "SELECT content_hash FROM job WHERE id = ?", (job_id,)
        ).fetchone()[0]
        jobs[key] = (
            JobFacts(title=title, description=description, job_id=job_id, location_raw=location),
            content_hash,
        )
    profiles = {}
    for key, answers in PROFILES.items():
        conn = connect(tmp_path / f"profile-{key}.db")
        migrate(conn)
        profiles[key] = (wizard_profile(answers), conn)
    yield catalogue, jobs, profiles
    catalogue.close()
    for _, conn in profiles.values():
        conn.close()


def headings(config: SearchConfig) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return tuple(config.prominence.primary_headings), tuple(config.prominence.secondary_headings)


def run_all(catalogue, jobs, profiles, features: FeatureStore | None = None):
    features = features or FeatureStore(catalogue)
    stores = {k: MatchStore(conn) for k, (_, conn) in profiles.items()}
    out: dict[str, dict[str, dict]] = {k: {} for k in profiles}
    with transaction(catalogue):
        for job_key, (job, content_hash) in jobs.items():
            machinery = profiles["automation"][0]
            shared = features.ensure(job, content_hash, headings(machinery), machinery.negation)
            for p_key, (config, conn) in profiles.items():
                with transaction(conn):
                    out[p_key][job_key] = stores[p_key].ensure(config, job, content_hash, shared)
    return features, stores, out


def test_shared_facts_are_made_once_and_every_profile_gets_its_own_answer(world) -> None:
    catalogue, jobs, profiles = world
    features, _, out = run_all(catalogue, jobs, profiles)
    assert features.tally.reads == len(jobs), "one reading of each advert for four people"
    assert features.tally.computed == {c: len(jobs) for c in comp.JOB_COMPONENTS}
    # Each person's own posting is their best answer: the design is not
    # anybody's profile, and a nurse writing in Portuguese is read as well as
    # an automation specialist writing in English.
    for person in profiles:
        scores = {job: out[person][job]["match_score"] for job in jobs}
        assert max(scores, key=scores.get) == person, (person, scores)
    # A second pass over the same postings reads nothing again.
    again, _, _ = run_all(catalogue, jobs, profiles, features)
    assert again.tally.reads == len(jobs)


def test_no_preference_or_evidence_crosses_between_profiles(world) -> None:
    catalogue, jobs, profiles = world
    _, _, out = run_all(catalogue, jobs, profiles)
    for person, (config, conn) in profiles.items():
        own = set(config.lexicon)
        for job in jobs:
            for item in out[person][job]["evidence"]:
                assert item[0] in own, (person, item)
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert conn.execute("SELECT COUNT(*) FROM profile_job_match").fetchone()[0] == len(jobs)
        assert conn.execute("SELECT COUNT(*) FROM job_features").fetchone()[0] == 0, (
            "a profile database holds no shared facts"
        )
        assert "profile_job_match" in tables
    assert catalogue.execute("SELECT COUNT(*) FROM profile_job_match").fetchone()[0] == 0


def _stale_after(profiles, key, jobs, new_config):
    _, conn = profiles[key]
    store = MatchStore(conn)
    return {
        job_key: store.stale(new_config, job, content_hash)
        for job_key, (job, content_hash) in jobs.items()
    }


def test_a_salary_preference_never_reaches_shared_facts_or_work(world) -> None:
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    config, conn = profiles["automation"]
    answers = PROFILES["automation"]
    changed = wizard_profile(
        Answers(
            role_examples=answers.role_examples, skills=answers.skills,
            residence_country=answers.residence_country, target_amount=9000,
            currency=answers.currency, period=answers.period,
        )
    )  # fmt: skip
    for stale in _stale_after(profiles, "automation", jobs, changed).values():
        assert stale == {"compensation_match", "score"}
    # The other people are untouched, and the shared facts are all current.
    for other in ("nurse", "teacher", "sales"):
        c, _ = profiles[other]
        assert all(not s for s in _stale_after(profiles, other, jobs, c).values())
    profiles["automation"] = (changed, conn)
    features, stores, _ = run_all(catalogue, jobs, profiles)
    assert features.tally.reads == 0 and not features.tally.computed
    assert stores["automation"].tally.computed == {
        "compensation_match": len(jobs), "score": len(jobs),
    }  # fmt: skip
    for other in ("nurse", "teacher", "sales"):
        assert not stores[other].tally.computed


def test_work_preferences_never_reach_location_pay_or_eligibility(world) -> None:
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    config, _ = profiles["teacher"]
    answers = PROFILES["teacher"]
    changed = wizard_profile(
        Answers(
            role_examples=(*answers.role_examples, "classroom management"),
            residence_country=answers.residence_country,
        )
    )
    for stale in _stale_after(profiles, "teacher", jobs, changed).values():
        assert stale == {"work_match", "tools_match", "score"}


def test_new_text_invalidates_that_posting_only(world) -> None:
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    job, content_hash = jobs["teacher"]
    edited = JobFacts(
        title=job.title, description=job.description + "\n- Corrigir provas.",
        job_id=job.job_id, location_raw=job.location_raw,
    )  # fmt: skip
    jobs = {**jobs, "teacher": (edited, "edited-hash")}
    features, stores, _ = run_all(catalogue, jobs, profiles)
    assert features.tally.reads == 1
    assert features.tally.computed == {c: 1 for c in comp.JOB_COMPONENTS}
    # Everything a profile derives from that text; its evidence policy reads
    # no text and stays.
    for store in stores.values():
        assert store.tally.computed == {
            c: 1 for c in comp.PROFILE_COMPONENTS if c != "evidence_policy"
        }


def test_a_language_change_redoes_work_and_nothing_else(world, monkeypatch) -> None:
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    bumped = comp.REGISTRY["language"].version + "-next"
    monkeypatch.setitem(
        comp.REGISTRY, "language", comp.Component("language", bumped, "job", ("text_parser",))
    )
    features, stores, _ = run_all(catalogue, jobs, profiles)
    redone = {"language", "semantic_evidence_facts", "work_facts"}
    assert features.tally.computed == {c: len(jobs) for c in redone}
    assert set(features.tally.reused) == set(comp.JOB_COMPONENTS) - redone
    for store in stores.values():
        assert set(store.tally.computed) == {"work_match", "tools_match", "score"}


def test_a_location_change_leaves_the_words_alone(world) -> None:
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    job, content_hash = jobs["sales"]
    moved = JobFacts(
        title=job.title, description=job.description, job_id=job.job_id,
        location_raw="Remote, Latin America",
    )  # fmt: skip
    features, stores, _ = run_all(catalogue, {"sales": (moved, content_hash)}, profiles)
    assert set(features.tally.computed) == {
        "pay_facts",
        "location_facts",
        "engagement_facts",
        "eligibility_facts",
    }
    for store in stores.values():
        assert "work_match" not in store.tally.computed
        assert {"eligibility", "score"} <= set(store.tally.computed)


def test_profile_databases_are_separate_files(world, tmp_path) -> None:
    _, _, profiles = world
    paths = {conn.execute("PRAGMA database_list").fetchone()[2] for _, conn in profiles.values()}
    assert len(paths) == len(profiles)
    assert all(isinstance(c, sqlite3.Connection) for _, c in profiles.values())


def test_an_evidence_policy_change_rematches_and_never_rereads(world) -> None:
    """What a kind of evidence is worth is the profile's (2026-10-16): a new
    policy re-matches that person's postings and reads no advert again."""
    catalogue, jobs, profiles = world
    run_all(catalogue, jobs, profiles)
    config, conn = profiles["teacher"]
    policy = {**policy_mod.DEFAULT, "counted_at": 0.5}
    changed = config.model_copy(update={"evidence_policy": policy})
    for stale in _stale_after(profiles, "teacher", jobs, changed).values():
        assert stale == {"evidence_policy", "work_match", "tools_match", "score"}
    profiles["teacher"] = (changed, conn)
    features, stores, _ = run_all(catalogue, jobs, profiles)
    assert features.tally.reads == 0 and not features.tally.computed
    assert set(stores["teacher"].tally.computed) == {
        "evidence_policy",
        "work_match",
        "tools_match",
        "score",
    }
    for other in ("nurse", "automation", "sales"):
        assert not stores[other].tally.computed
