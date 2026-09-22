"""Fixtures for the browser pass: one seeded database, one server, one Chrome.

The database is built in a temporary directory from `evaluation/demo/`, through
the same `seed_demo` and the same production matcher the CLI uses.
`data/demo.db` is deliberately NOT reused -- these tests mutate application
status, and a suite that edits a file a person also opens by hand is a suite
that eventually explains a mystery. `data/m1d2/career.db` is never opened.

Chrome is optional. When it is absent every browser test skips with a reason,
and `test_the_fixtures_are_wired` still runs against the server, so a skipped
suite is visibly a skipped suite rather than a silently empty one.
"""

from __future__ import annotations

import collections
import contextlib
import os
import shutil
import socket
import struct
import threading
import zlib
from collections.abc import Callable, Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome, find_chrome

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import load_demo_postings, seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
EVIDENCE_DIR = REPO_ROOT / "docs" / "evidence" / "browser"

#: The corpus is twenty-one postings. Stated here rather than counted from the
#: YAML at assert time, so a corpus that silently shrinks fails a test instead
#: of quietly agreeing with itself.
#:
#: Nineteen until migration 0027, when two were added for the people the rest
#: of the corpus left out: demo-020, which is explicitly open to somebody with
#: no track record, and demo-021, which is administration rather than systems
#: work and is therefore the career changer's case. A capability with no
#: example in that file cannot be demonstrated or proved.
DEMO_POSTING_COUNT = 21

#: ...but only SEVENTEEN cards, because demo-001, demo-018 and demo-019 are one
#: role published once per regional board and Cards groups duplicates by
#: default. Two constants rather than one arithmetic expression: the difference
#: between them is the feature, and a test that quietly computed `19 - 2` would
#: still pass on a build where grouping had stopped working and the corpus had
#: lost two rows.
#:
#: Which number a test wants is decided by the VIEW it is in. Cards groups;
#: Table does not; `?group_duplicates=0` turns it off anywhere.
#:
#: 19 rather than 17 since demo-020 and demo-021: each is at its own invented
#: company, so neither collapses into an existing group.
DEMO_GROUPED_COUNT = 19

# -- and what a person actually SEES, which V3 made a different number ----
#
# Three of the nineteen state a requirement that rules this candidate out, and
# the discovery views hide those by default. So there are now two pairs of
# numbers and the distinction is load-bearing:
#
#   DEMO_POSTING_COUNT / DEMO_GROUPED_COUNT   what the corpus HOLDS
#   DEMO_VISIBLE_* / DEMO_VISIBLE_GROUPED_*   what the default view SHOWS
#
# A test that waits on the corpus number while looking at a default view waits
# forever, which is exactly how this was discovered. Which pair a test wants is
# decided by what it is asserting: the health endpoint counts the database, the
# rendered list counts what a person is being shown.

#: Verified as ruling this candidate out, and hidden unless asked for.
DEMO_HIDDEN_COUNT = 3

#: And the three the person's own SEARCH set aside as a different kind of work.
#:
#: A SECOND default narrowing, added in V1, with its own control and its own
#: disclosure. It hides `screening_state == BLOCKED`: none of the signals the
#: search requires fired anywhere in the posting. The two never overlap in this
#: corpus -- an employer ruling you out and your search ruling the work out are
#: different postings here -- which is why the visible count subtracts both.
#:
#: FOUR since demo-021, and that posting is why the disjointness above had to be
#: checked rather than assumed. It is an Administrative Assistant role open
#: worldwide: the geography gate PASSES, nothing an employer wrote rules this
#: candidate out, and the only thing setting it aside is a systems search whose
#: required signals do not fire on administration. An earlier draft named the
#: United States and the European Union instead, which left it UNRESOLVED too
#: and would have put one posting inside two narrowings.
DEMO_OFF_TARGET_COUNT = 4

#: And the three that never said where the employer may hire.
#:
#: A THIRD default narrowing, added by the V1.5 eligibility correction, with
#: its own control and its own disclosure: "Include jobs that never said where
#: they hire". It hides `UNRESOLVED`, and it is what took the owner's
#: recommended list from five figures to hundreds.
#:
#: **It is not a verdict, and the help text says so**: nobody can tell whether
#: she could take these, which is a question the posting left open rather than
#: a no. Absence is still never permission -- this narrowing says the opposite
#: of "eligible", not the same thing.
#:
#: THIS FILE WENT RED WHEN THAT SHIPPED, and nothing said so until the next
#: session ran the browser suite: six tests waited thirty seconds each for a
#: number that had changed underneath them. The three narrowings are disjoint
#: in this corpus, which is why the visible count subtracts all three.
DEMO_UNRESOLVED_COUNT = 3

#: Postings on screen by default, ungrouped. Table, or `?group_duplicates=0`.
DEMO_VISIBLE_COUNT = (
    DEMO_POSTING_COUNT - DEMO_HIDDEN_COUNT - DEMO_OFF_TARGET_COUNT - DEMO_UNRESOLVED_COUNT
)

#: Roles on screen by default, grouped. Cards.
#:
#: 8 rather than 17: nine postings are set aside by the three defaults, and
#: each belongs to a different company, so hiding them removes nine whole
#: groups. Written as the subtraction rather than as `8` so a corpus change
#: that broke the assumption fails loudly here instead of quietly agreeing
#: with itself.
DEMO_VISIBLE_GROUPED_COUNT = (
    DEMO_GROUPED_COUNT - DEMO_HIDDEN_COUNT - DEMO_OFF_TARGET_COUNT - DEMO_UNRESOLVED_COUNT
)

DESKTOP = (1440, 960)
MOBILE = (390, 844)
#: A laptop. The width where the card grid drops from three columns to two,
#: which neither 1440 nor 390 exercises.
LAPTOP = (1280, 720)

#: Where every company name the interface can print lives in the DOM.
COMPANY_SELECTORS = ".card__company, .cell--company, .d-company"


def _free_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    return port


@pytest.fixture(scope="session")
def committed_config(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """`config/`, with every private override removed. The only one these
    tests may read.

    `config/*.local.yaml` is the owner's real search: gitignored, machine
    specific, and free to say anything. A suite that reads it passes or fails
    on what is in one person's file, which is the rule
    `tests/unit/test_declared_scope.py` states in so many words.

    It was already half-obeyed. `writable_config` deleted the local files from
    ITS copy -- and `demo_db` and `server` went on reading the real directory,
    so the database was SEEDED under whatever `search.local.yaml` said while
    `writable_server` served it under the committed example. For as long as
    the two files carried the same `config_version` that mismatch was
    invisible; the moment the private file was bumped to 3, the list went
    empty in one test and nowhere else.

    A `.backup` is removed too. `candidate_writer` leaves one beside every
    save, and a copy of the private search is still the private search.
    """
    copy = tmp_path_factory.mktemp("browser-committed") / "config"
    shutil.copytree(CONFIG_DIR, copy)
    for stray in [*copy.glob("*.local.yaml"), *copy.glob("*.local.yaml.*")]:
        stray.unlink()
    shutil.copyfile(copy / "search.worked-example.yaml", copy / "search.local.yaml")
    return copy


@pytest.fixture(scope="session")
def demo_db(tmp_path_factory: pytest.TempPathFactory, committed_config: Path) -> Path:
    """A freshly seeded demo database. Invented companies, `.invalid` URLs.

    Scored under `committed_config`, never under the real directory: the
    server fixtures below serve it under the same configuration, and a
    database seeded under one `config_version` and served under another
    returns nothing at all.
    """
    db_path = tmp_path_factory.mktemp("browser-demo") / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        # Stamp it, exactly as `seed-demo` does. Without this the fixture's
        # database declares no kind, /api/health reports "Unidentified
        # database", and every committed screenshot shows that instead of the
        # mode banner it is meant to be evidence of.
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(committed_config)
        stats = seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    assert stats["postings"] == DEMO_POSTING_COUNT
    return db_path


@pytest.fixture(scope="session")
def server(demo_db: Path, committed_config: Path) -> Iterator[str]:
    """The real `JobsApi` on a real socket: loopback, free port, own thread."""
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=demo_db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    # Chrome closes keep-alive sockets when it navigates, and Windows reports
    # that as ECONNRESET. `ThreadingHTTPServer` prints a traceback per event,
    # which buries the actual test output in noise about nothing.
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def writable_config(tmp_path_factory: pytest.TempPathFactory, committed_config: Path) -> Path:
    """A COPY of `config/`, for the one test that writes to it.

    The preference editor writes `search.local.yaml`, and that file overrides
    the committed example for every command afterwards -- `rescore`, `serve`,
    `doctor`, and the person's own next session. A browser test pointed at the
    real directory therefore leaves a phrase in the developer's search
    configuration and silently bumps `config_version` to 2, which detaches the
    whole corpus from its scores: every stored `job_match` row is under v1, so
    the list goes empty until someone re-scores.

    It happened while writing that test, and the file had to be deleted by
    hand. Isolating it is not tidiness; it is the difference between a test
    suite and a suite that edits your search.
    """
    copy = tmp_path_factory.mktemp("browser-config") / "config"
    shutil.copytree(committed_config, copy)
    return copy


@pytest.fixture(scope="session")
def writable_server(demo_db: Path, writable_config: Path) -> Iterator[str]:
    """A second server, bound to the copied configuration directory.

    Session scoped like the first, and deliberately a SEPARATE process-level
    app: the main `server` fixture caches its configuration, and a version bump
    underneath it would empty every other test's list.
    """
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=demo_db, config_dir=writable_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture
def pristine_server(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    """A server over a database nothing has touched yet.

    The session `demo_db` is shared and the suite MUTATES it -- statuses move,
    dates get recorded, the board fills up. Every one of those changes shows in
    a screenshot, so the committed evidence depended on which tests happened to
    run before the capture: running the whole suite and running the capture
    test alone produced six different frames.

    That is the same class of problem as the blank frames, one level up. A
    screenshot is evidence of a state, and evidence whose state is decided by
    test ordering documents nothing in particular. This fixture is seeded fresh
    per test, so a frame is a function of the code and the demo corpus and
    nothing else -- and `git diff` on `docs/evidence/` means the interface
    changed rather than that the suite ran in a different order.
    """
    db_path = tmp_path / "pristine.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(committed_config)
        stats = seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    assert stats["postings"] == DEMO_POSTING_COUNT

    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db_path, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture(scope="session")
def browser(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Chrome]:
    binary = find_chrome()
    if binary is None:
        pytest.skip(
            "Chrome was not found in Program Files, LOCALAPPDATA or PATH. The "
            "browser acceptance pass needs a real browser; nothing else in the "
            "suite depends on one."
        )
    profile = tmp_path_factory.mktemp("chrome-profile")
    with Chrome.launch(binary, profile) as chrome:
        yield chrome


@pytest.fixture
def page(browser: Chrome) -> Chrome:
    """The session browser, reset: desktop viewport, no media override, console
    log emptied, and NOTHING REMEMBERED FROM THE LAST TEST.

    The colour-scheme reset matters because Chrome is session scoped: a test
    that photographs the light theme would otherwise hand the next test a
    differently-themed page and a mysterious failure.

    `localStorage` is the same trap, one layer down, and it had been patched
    one key at a time. This product deliberately REMEMBERS view preferences
    there -- which narrowings are revealed, which language, which phrases are
    hidden -- so a test that reveals the ineligible postings, or types a word
    into a hide box, hands the next test a filtered list for reasons that test
    cannot see. Measured: two plain-language tests failed in a full run and
    passed alone, on a count that was short by exactly what somebody else had
    left behind.

    Cleared by PREFIX rather than by name. A named list is a list that goes
    stale the next time a preference learns to be remembered, and that is
    precisely how this arrived.

    It is best effort: a browser sitting on `about:blank` has no storage to
    clear, and there is nothing to leak from a page that never loaded.
    """
    browser.set_viewport(*DESKTOP)
    browser.set_color_scheme(None)
    browser.clear_console()
    with contextlib.suppress(RuntimeError):
        browser.evaluate(
            "(() => {"
            "  const keys = [];"
            "  for (let i = 0; i < localStorage.length; i += 1) {"
            "    const key = localStorage.key(i);"
            "    if (key && key.startsWith('careerAgent.')) keys.push(key);"
            "  }"
            "  for (const key of keys) localStorage.removeItem(key);"
            "  return keys.length;"
            "})()"
        )
    return browser


@pytest.fixture(scope="session")
def demo_company_names() -> frozenset[str]:
    postings, _ = load_demo_postings(DEMO_FILE)
    return frozenset(str(posting["company"]["name"]) for posting in postings)


@pytest.fixture(scope="session")
def evidence_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Where the captured frames go. A temporary directory, unless asked.

    **The committed screenshots are a DELIVERABLE, not test output.** Writing
    them on every run left the tracked tree dirty after every suite, and a
    different subset each time: measured 2026-09-08, eight files changed by
    5 to 41 pixels of antialiasing out of 1,382,400, plus a scrollbar column
    on the mobile frame. All of it invisible, none of it a UI change, and
    every one of them indistinguishable from a real regression in
    `git status`.

    That is worse than untidy. Evidence nobody can trust to have changed for a
    reason is evidence nobody reads, and a genuine visual regression would have
    arrived in exactly the same shape as the noise.

    Every assertion still runs on every run -- the pixel guard, the
    company-name check, the secret check. Only the DESTINATION moves. Refresh
    the committed set deliberately:

        CAREER_AGENT_REFRESH_EVIDENCE=1 uv run python -m pytest tests/browser

    and then look at what changed before committing it.
    """
    if os.environ.get("CAREER_AGENT_REFRESH_EVIDENCE") == "1":
        EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
        return EVIDENCE_DIR
    return tmp_path_factory.mktemp("browser-evidence")


@pytest.fixture(scope="session")
def env_secrets() -> frozenset[str]:
    """Credential values out of `.env`, held only to prove a PNG lacks them.

    Never printed, never logged, never asserted ON -- only asserted AGAINST.

    Only credential-shaped KEYS are collected, and that narrowing is the point
    rather than laziness: `.env` also holds `OLLAMA_BASE_URL`, which the drawer
    prints on purpose ("the local model at http://localhost:11434 has not been
    contacted yet"). A guard that called that a leak would be trained away
    within a week. Short values are dropped too -- a six-character secret would
    match half the page and turn a real check into noise.
    """
    env = REPO_ROOT / ".env"
    if not env.is_file():
        return frozenset()
    values = set()
    for line in env.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        if not any(word in name.upper() for word in ("KEY", "TOKEN", "SECRET", "PASSWORD")):
            continue
        cleaned = value.strip().strip("'\"")
        if len(cleaned) >= 8:
            values.add(cleaned)
    return frozenset(values)


@pytest.fixture
def capture(
    page: Chrome,
    evidence_dir: Path,
    demo_company_names: frozenset[str],
    env_secrets: frozenset[str],
) -> Callable[[str], Path]:
    """Save a screenshot, after proving there is nothing in it to regret.

    One redaction happens first, and it is worth naming. The header prints the
    absolute path of the database it opened -- useful provenance for the person
    running the server, and this machine's directory layout in a file meant to
    be committed, which on Windows sits under the account holder's own name.
    The evidence keeps the file name, which is the part that says anything.
    """

    def _capture(name: str) -> Path:
        # Nothing may still be moving. The drawer opens with a 160ms `slide-in`
        # that starts at `opacity: 0.6`, and the frames were being taken during
        # it -- so three committed screenshots showed the drawer SEMI
        # TRANSPARENT with the list bleeding through it, which is not a picture
        # of anything the product does. The pixel guard passes those happily:
        # a half-transparent drawer has plenty of colour.
        #
        # `getAnimations()` is the browser's own answer to "is anything still
        # animating", so this waits for the real thing rather than sleeping for
        # a duration that would have to be kept in step with the CSS.
        page.wait_for(
            "document.getAnimations().every((a) => a.playState !== 'running')",
            message=f"every animation to settle before capturing {name}",
        )
        page.evaluate(
            "(() => { const n = document.querySelector('.health__db');"
            " if (n) n.textContent = 'demo.db'; return true; })()"
        )
        text = str(page.evaluate("document.body.innerText"))
        companies = page.evaluate(
            f"Array.from(document.querySelectorAll({COMPANY_SELECTORS!r}))"
            ".map((n) => n.textContent.trim())"
        )
        _assert_nothing_to_regret(text, list(companies or []), demo_company_names, env_secrets)
        written = page.screenshot(evidence_dir / f"{name}.png")
        _assert_the_frame_shows_something(written)
        return written

    return _capture


def _assert_nothing_to_regret(
    text: str,
    companies_on_screen: list[str],
    corpus: frozenset[str],
    secrets: frozenset[str],
) -> None:
    """Three questions, asked of the pixels rather than of our intentions:
    is every employer invented, is any real path visible, did `.env` leak."""
    for company in companies_on_screen:
        assert company in corpus or company == "Company not stated", (
            f"{company!r} is not one of the invented demo companies"
        )

    lowered = text.lower()
    fragments = ["c:\\users", "c:/users", "appdata"]
    account = os.environ.get("USERNAME", "").strip().lower()
    if len(account) >= 3:
        fragments.append(account)
    for fragment in fragments:
        assert fragment not in lowered, f"the page shows a personal path fragment: {fragment!r}"

    for secret in secrets:
        assert secret not in text, "a value from .env reached the rendered page"


#: Sampled every fourth pixel, and only inside the region that holds RESULTS --
#: right of the desktop rail, below the header, legend and chip bands. Sampling
#: the whole frame would pass on the strength of the chrome around the part
#: that matters, which is exactly the failure this exists to catch.
_CONTENT_ORIGIN_WIDE = (300, 160)
_CONTENT_ORIGIN_NARROW = (10, 400)

#: A results region that is one flat colour is an empty pane. The thresholds
#: are far from both sides of the observed data rather than tuned to it: the
#: six blank frames scored 4-6 distinct colours at 98.0-98.9% dominance, and
#: the thinnest legitimate frame -- the empty state, which IS mostly background
#: by design -- scored 108 at 97.6%.
_MIN_DISTINCT_COLOURS = 12
_MAX_DOMINANT_SHARE = 0.985


def _assert_the_frame_shows_something(path: Path) -> None:
    """Look at the pixels that were written, not at the DOM they came from.

    A screenshot test can only be trusted if it fails when the picture is
    wrong, and this one could not. Thirteen frames were captured, committed and
    reported as evidence while six of them showed an empty results pane: the
    grid had placed `.results` in row 2, a thousand pixels below the fold. Every
    assertion around them held -- the rows were in the DOM, `wait_for_count`
    passed, the console was clean, the leak check passed -- because all of them
    asked the DOM and none of them asked the image.

    So this asks the image. Two statistics over the results region, both crude
    on purpose: how many distinct colours are in it, and how much of it is the
    single most common one. Anything a person would call a screenshot of a list
    clears both by a wide margin; a flat panel clears neither.

    It cannot tell a WRONG frame from a right one -- that is what the eye and
    the rest of the suite are for. It can tell an EMPTY one, which is the
    failure that actually shipped.
    """
    width, height, channels, rows = _decode_png(path)
    x0, y0 = _CONTENT_ORIGIN_WIDE if width > 900 else _CONTENT_ORIGIN_NARROW
    seen: collections.Counter[bytes] = collections.Counter()
    for y in range(y0, height, 4):
        row = rows[y]
        for x in range(x0, width, 4):
            seen[row[x * channels : x * channels + 3]] += 1
    total = sum(seen.values())
    assert total, f"{path.name}: the sampled region is empty; the origins are wrong"
    dominant = seen.most_common(1)[0][1] / total
    assert len(seen) >= _MIN_DISTINCT_COLOURS and dominant <= _MAX_DOMINANT_SHARE, (
        f"{path.name} shows an empty content region: {len(seen)} distinct "
        f"colours, {dominant:.1%} of it a single colour. The DOM said the rows "
        f"were there; the picture says they were not on screen."
    )


def _decode_png(path: Path) -> tuple[int, int, int, list[bytes]]:
    """Enough PNG to read back what we just wrote. Standard library only.

    The same reasoning as `chrome.py`: adding Pillow to inspect thirteen images
    would be a larger dependency decision than the thing it verifies. Chrome
    writes 8-bit RGB or RGBA, so the eight sub-cases of the spec this does not
    implement are eight Chrome does not emit -- and it asserts that rather than
    assuming it.
    """
    blob = path.read_bytes()
    position, compressed = 8, b""
    width = height = channels = 0
    while position < len(blob):
        length = struct.unpack(">I", blob[position : position + 4])[0]
        kind = blob[position + 4 : position + 8]
        data = blob[position + 8 : position + 8 + length]
        if kind == b"IHDR":
            width, height, depth, colour_type = struct.unpack(">IIBB", data[:10])
            assert depth == 8 and colour_type in (2, 6), (
                f"{path.name}: unexpected PNG form (depth {depth}, type {colour_type})"
            )
            channels = 3 if colour_type == 2 else 4
        elif kind == b"IDAT":
            compressed += data
        elif kind == b"IEND":
            break
        position += 12 + length
    raw = zlib.decompress(compressed)
    stride = width * channels
    rows: list[bytes] = []
    previous = bytearray(stride)
    cursor = 0
    for _ in range(height):
        filter_type = raw[cursor]
        line = bytearray(raw[cursor + 1 : cursor + 1 + stride])
        cursor += 1 + stride
        for x in range(stride):
            left = line[x - channels] if x >= channels else 0
            up = previous[x]
            corner = previous[x - channels] if x >= channels else 0
            if filter_type == 1:
                line[x] = (line[x] + left) & 0xFF
            elif filter_type == 2:
                line[x] = (line[x] + up) & 0xFF
            elif filter_type == 3:
                line[x] = (line[x] + (left + up) // 2) & 0xFF
            elif filter_type == 4:
                estimate = left + up - corner
                da, db, dc = (
                    abs(estimate - left),
                    abs(estimate - up),
                    abs(estimate - corner),
                )
                nearest = left if (da <= db and da <= dc) else (up if db <= dc else corner)
                line[x] = (line[x] + nearest) & 0xFF
        rows.append(bytes(line))
        previous = line
    return width, height, channels, rows
