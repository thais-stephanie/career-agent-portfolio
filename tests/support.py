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
