"""A pasted posting is scored from the same facts a rescore would read.

Found 2026-10-16 in the clean-install smoke: `import_posting` built `JobFacts`
by hand and skipped `pipeline/facts.job_facts`, so a salary stated only in the
advert's text (`Salario: R$ 8.000 por mes`) was absent from the stored score
while the card showed it. The import settles its own dirty marks, so no
targeted rescore would ever have corrected it. Fictional posting.
"""

from __future__ import annotations

from pathlib import Path

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.manual_import import import_posting
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import MatchRepo

ADVERT = (
    "Analista de Operacoes (vaga ficticia). Trabalho remoto. Responsavel por processos "
    "operacionais, integracoes de sistemas e automacao de fluxos entre ferramentas. "
    "Salario: R$ 8.000 por mes."
)


def _row(conn, job_id, config):
    assert MatchRepo(conn).get(job_id, config.config_id, config.config_version) is not None
    return conn.execute(
        "SELECT match_score, salary_min, salary_max, salary_currency, salary_period"
        " FROM job_match WHERE job_id = ? AND config_version = ? ORDER BY id DESC LIMIT 1",
        (job_id, config.config_version),
    ).fetchone()


def test_a_pasted_salary_is_scored_and_a_forced_rescore_moves_nothing(tmp_path: Path) -> None:
    conn = connect(tmp_path / "import.db")
    migrate(conn)
    config, _ = load_search_config(committed_config_dir())
    job_id = import_posting(
        conn,
        config,
        title="Analista de Operacoes (ficticia)",
        company="Empresa Ficticia",
        description=ADVERT,
        config_id=config.config_id,
        config_version=config.config_version,
        now="2026-10-16T00:00:00Z",
    )
    imported = tuple(_row(conn, job_id, config))
    assert imported[1:] == (8000.0, 8000.0, "BRL", "MONTH")
    rescore(conn, config, force=True)
    assert tuple(_row(conn, job_id, config)) == imported
    conn.close()
