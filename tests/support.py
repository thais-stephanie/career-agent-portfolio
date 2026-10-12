"""One rule, importable at module scope: a test reads only committed config.

**A test may not read `config/*.local.yaml`.** That file is the owner's real
search: gitignored, machine specific, and free to say anything. A test that
reads it passes or fails on what is in one person's file, so a green suite
proves nothing on anybody else's machine and a red one is unreproducible.

The rule was written down in `tests/unit/test_declared_scope.py` and obeyed
there and nowhere else. It cost a real hour of this session:
`tests/browser/conftest.py` deleted the local overrides from the directory it
SERVED and seeded its database from the directory it did not, so the corpus was
scored under the private file while the server read the committed example. For
as long as the two carried the same `config_version` the mismatch was
invisible. The moment the private one was bumped, one test's list went empty
and nothing in the failure said why.

This is a FUNCTION rather than a fixture because half the call sites are
module-level -- `CONFIG, _ = load_search_config(CONFIG_DIR)` runs at import,
before any fixture exists. The copy is made once per process and reused.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The real one. Named so that a call site reaching for it is visible in a
#: review rather than looking like every other path expression.
REAL_CONFIG_DIR = REPO_ROOT / "config"


@lru_cache(maxsize=1)
def committed_config_dir() -> Path:
    """A copy of `config/` holding only what is committed.

    Local overrides go, and so do the sidecars `candidate_writer` leaves beside
    them: a `search.local.yaml.backup` is still the private search, one rename
    away from being loaded by something that globbed too loosely.

    Cached for the life of the process. The files are read many times and
    written never, so one copy is correct and copying per test would make a
    suite of three thousand tests do three thousand directory copies.
    """
    copy = Path(tempfile.mkdtemp(prefix="career-committed-config")) / "config"
    shutil.copytree(REAL_CONFIG_DIR, copy)
    for stray in [*copy.glob("*.local.yaml"), *copy.glob("*.local.yaml.*")]:
        stray.unlink()
    # These fixtures explicitly model the committed worked-example search.
    # Production fallback is neutral; fixture intent must not rely on an
    # example silently becoming a new user's profile.
    shutil.copy2(copy / "search.worked-example.yaml", copy / "search.local.yaml")
    return copy


#: The audit timestamp, which a second computation is SUPPOSED to change.
#:
#: It appears twice -- as a `job_match` column and again inside `result_json`
#: -- and both copies have to be set aside before two scores can be compared.
#: Two tests need that, and two spellings of it is how one of them ends up
#: flaky: `test_interest_is_not_fit` compared the raw blob and passed only
#: while both rescores landed in the same second.
SCORE_AUDIT_FIELDS: frozenset[str] = frozenset({"computed_at"})


def comparable_score(name: str, value: object) -> object:
    """One `job_match` value, with only the audit timestamp inside it removed.

    `result_json` is compared rather than skipped: the blob holds every
    component, every quote and every confidence item, which is the substance
    of what a reader is shown. Excluding it would leave an assertion looking
    thorough and checking almost nothing.
    """
    if name != "result_json" or not isinstance(value, str):
        return value
    payload = json.loads(value)
    return json.dumps(
        {key: item for key, item in payload.items() if key not in SCORE_AUDIT_FIELDS},
        sort_keys=True,
    )


def career_profile(tmp_path: Path, pid: str, label: str) -> tuple[object, object]:
    """A synthetic local profile, served as the launcher serves one: its own
    database (seeded with the demo postings) and configuration, and its app.
    Returns `(Profile, JobsApi)`."""
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.demo_seed import seed_demo
    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.runtime.profiles import Profile
    from career_agent.storage.db import connect, migrate, transaction
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig

    base = tmp_path / pid
    config = base / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = base / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, label)
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml")
    finally:
        conn.close()
    profile = Profile(
        id=pid,
        label=label,
        created_at="2026-09-26T00:00:00Z",
        db=str(db),
        config_dir=str(config),
        tailor_home=str(base / "tailor"),
    )
    return profile, JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True)


def settle(runner: object, *, within: float = 30.0) -> dict | None:
    """Wait until the retrieval run has ENDED, and say where it is stuck if not.

    The tests used to `join(10)` and read the state afterwards. `join` returns
    when its time is up whether or not the run ended, so a run that was still
    working was read as a finished one. This waits for the condition itself,
    with `within` only as the safety limit, and fails naming the step and the
    thread's own stack instead of letting an assertion about something else
    explain it.
    """
    import sys
    import time
    import traceback

    deadline = time.monotonic() + within
    while runner.running and time.monotonic() < deadline:  # type: ignore[attr-defined]
        time.sleep(0.02)
    if not runner.running:  # type: ignore[attr-defined]
        runner.join(5)  # type: ignore[attr-defined]  # only its closing steps are left
    thread = runner._thread  # type: ignore[attr-defined]
    if thread is not None and thread.is_alive():
        frame = sys._current_frames().get(thread.ident)
        where = "".join(traceback.format_stack(frame)[-6:]) if frame else "no stack"
        snapshot = runner.snapshot()  # type: ignore[attr-defined]
        step = snapshot.get("current") if snapshot else None
        raise AssertionError(f"the run did not end within {within}s; step={step!r}; {where}")
    return runner.snapshot()  # type: ignore[attr-defined,no-any-return]
