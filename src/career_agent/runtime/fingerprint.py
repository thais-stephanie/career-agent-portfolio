"""Which process is answering, and is it the one the terminal described.

WHY THIS EXISTS
---------------
On 2026-09-08 the owner started Career Agent, read a banner saying
configuration v4 and 19,469 scores, opened the browser, and was served 404s on
routes that exist, 500s on routes that work, and a Jobs list reporting nothing
scored. Everything the terminal printed was true. It was true about a process
the browser was not talking to: a copy started the previous day still owned the
port, and Windows let the new one bind alongside it without a word.

`web.server.ExclusiveHTTPServer` makes that impossible now. This module is the
other half, and it is the half that survives the next surprise: it lets the
person compare what the terminal says with what the browser is actually
reaching, without knowing anything about sockets.

WHAT IS DELIBERATELY NOT IN IT
------------------------------
No absolute paths, no configuration values, no candidate facts, no
environment. `docs/PRIVACY.md` promises what leaves this machine and the health
payload is rendered on a page that gets screenshotted; an absolute path on
Windows carries the account holder's own name. The database is identified by
FILE NAME, which is the part that distinguishes `career.db` from `demo.db`, and
the configuration by digest PREFIX, which is enough to compare two runtimes and
useless for anything else.
"""

from __future__ import annotations

import os
import re
import subprocess
import time
from functools import lru_cache
from pathlib import Path

#: When this interpreter started serving, as a whole second in UTC. Captured at
#: import rather than per request: it is an identity, not a clock reading, and
#: two requests to the same server must agree about it.
STARTED_AT = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@lru_cache(maxsize=1)
def revision() -> str:
    """The git revision this is running from, or `unknown`.

    Short, and best effort. It is asked once per process and never on a request
    path. A source checkout answers; an installed copy without git does not,
    and `unknown` is the honest word for that rather than a fabricated version
    number.
    """
    root = Path(__file__).resolve().parents[3]
    # git archive substitutes this tracked marker. A plain working copy leaves
    # the marker untouched; never mistake it for a revision.
    marker = root / "BUILD_ID"
    archived = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    if re.fullmatch(r"[0-9a-f]{40}", archived):
        return archived[:12]
    try:
        result = subprocess.run(  # noqa: S603  -- fixed argv, no shell, no input
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    revision_text = result.stdout.strip()
    return revision_text if result.returncode == 0 and revision_text else "unknown"


def runtime_fingerprint() -> dict[str, object]:
    """Enough to tell two running copies apart, and nothing more.

    `pid` and `started_at` together answer "is this the server I just
    started?". `revision` answers "is it running the code I just wrote?" --
    which is the question a stale process makes urgent, because an old process
    serves old routes and a missing route looks like a bug in the new ones.
    """
    return {
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "revision": revision(),
    }
