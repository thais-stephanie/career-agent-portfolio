"""Asking a host before reading it, and the difference between three answers.

WHY THIS EXISTS
---------------
Every source this product reads had its permission checked BY HAND: somebody
opened the vendor's `robots.txt` and its terms page, read them, and quoted them
into `config/source_catalogue.yaml` with a date. That is a better answer than a
parser for eighteen vendors, because a person can read a terms page and a
parser cannot.

It is no answer at all for the **two hundred and eighty company websites**
career-site discovery reads. ADR-0018's fourth and fifth limits are what this
implements: an explicit restriction obeyed when found, and a first-party
statement about one host never read as covering another.

THE ASSERTION THAT MATTERS is the boundary between SILENCE and UNREADABLE.
ADR-0018 says silence is permission, so a host with no `robots.txt` may be
read. A host whose `robots.txt` answered 500 has not been silent: there may be
a statement we failed to read, and reading the site anyway would be assuming
the answer we prefer.

NOTHING HERE OPENS A SOCKET. The policy's per-origin cache is populated
directly, so what is under test is the decision rather than the network.
"""

from __future__ import annotations

import urllib.error
from urllib.robotparser import RobotFileParser

import pytest

from career_agent.net.robots import RobotsDecision, RobotsPolicy, origin_of

AGENT = "career-agent/test"


def _policy_with(origin: str, lines: list[str]) -> RobotsPolicy:
    policy = RobotsPolicy(agent=AGENT)
    parser = RobotFileParser()
    parser.parse(lines)
    policy._cache[origin] = parser  # noqa: SLF001 - the cache is the seam
    return policy


# =========================================================================
# 1. SCOPE
# =========================================================================


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://acme.invalid/careers", "https://acme.invalid"),
        ("https://jobs.acme.invalid/x/y?z=1", "https://jobs.acme.invalid"),
        ("http://acme.invalid", "http://acme.invalid"),
    ],
)
def test_a_policy_covers_one_origin_and_not_a_family_of_them(url: str, expected: str) -> None:
    """The fifth limit, in one function.

    A subdomain is a different host and gets its own file. Reading one host's
    statement as covering another is the mistake this product refuses about
    vendors and must refuse about companies too.
    """
    assert origin_of(url) == expected


# =========================================================================
# 2. THE THREE ANSWERS
# =========================================================================


def test_a_rule_that_matches_refuses() -> None:
    policy = _policy_with("https://acme.invalid", ["User-agent: *", "Disallow: /jobs"])
    assert policy.decide("https://acme.invalid/jobs") is RobotsDecision.DISALLOWED
    assert policy.may_fetch("https://acme.invalid/jobs") is False


def test_a_path_the_file_says_nothing_about_is_allowed() -> None:
    """Silence is permission -- ADR-0018 in as many words -- and a file that
    forbids one path has said nothing about the others."""
    policy = _policy_with("https://acme.invalid", ["User-agent: *", "Disallow: /jobs"])
    assert policy.decide("https://acme.invalid/careers") is RobotsDecision.ALLOWED


def test_a_disallow_aimed_at_somebody_else_is_not_aimed_at_us() -> None:
    policy = _policy_with(
        "https://acme.invalid",
        ["User-agent: SomeOtherBot", "Disallow: /", "User-agent: *", "Allow: /"],
    )
    assert policy.decide("https://acme.invalid/careers") is RobotsDecision.ALLOWED


def test_a_blanket_disallow_refuses_everything() -> None:
    policy = _policy_with("https://acme.invalid", ["User-agent: *", "Disallow: /"])
    assert policy.decide("https://acme.invalid/careers") is RobotsDecision.DISALLOWED
    assert policy.decide("https://acme.invalid/") is RobotsDecision.DISALLOWED


# =========================================================================
# 3. SILENCE IS NOT UNREADABLE
# =========================================================================


class _Fails:
    """A stand-in for `urlopen` that raises whatever it was given."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self, *args: object, **kwargs: object) -> None:
        raise self.error


@pytest.mark.parametrize("status", [404, 410])
def test_a_host_with_no_robots_file_has_not_objected(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """ADR-0018: for collection, silence is permission. A 404 is silence."""
    monkeypatch.setattr(
        "urllib.request.urlopen",
        _Fails(urllib.error.HTTPError("u", status, "gone", {}, None)),  # type: ignore[arg-type]
    )
    policy = RobotsPolicy(agent=AGENT)
    assert policy.decide("https://acme.invalid/careers") is RobotsDecision.ALLOWED


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("u", 500, "boom", {}, None),  # type: ignore[arg-type]
        urllib.error.HTTPError("u", 403, "no", {}, None),  # type: ignore[arg-type]
        urllib.error.URLError("timed out"),
        OSError("connection reset"),
    ],
)
def test_a_statement_we_failed_to_read_is_not_silence(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """The assertion this file exists for.

    A 500, a 403 or a timeout means there MAY be a statement we did not read.
    Reading the site anyway would be assuming the answer we prefer, which is
    the same move as reading a posting's silence about geography as permission.
    """
    monkeypatch.setattr("urllib.request.urlopen", _Fails(error))
    policy = RobotsPolicy(agent=AGENT)
    assert policy.decide("https://acme.invalid/careers") is RobotsDecision.UNREADABLE
    assert policy.may_fetch("https://acme.invalid/careers") is False


def test_only_allowed_may_fetch() -> None:
    """Three values and one of them is a yes. Written as an assertion because
    a caller reading `decision is not DISALLOWED` would fetch on UNREADABLE."""
    assert RobotsDecision.ALLOWED.may_fetch is True
    assert RobotsDecision.DISALLOWED.may_fetch is False
    assert RobotsDecision.UNREADABLE.may_fetch is False


# =========================================================================
# 4. ONE REQUEST PER HOST
# =========================================================================


def test_a_hosts_file_is_read_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """A scan tries up to five paths on each host. Fetching `robots.txt` five
    times would be the impoliteness this class exists to prevent."""
    calls: list[str] = []

    def fake(request: object, timeout: float | None = None) -> None:
        del timeout
        calls.append(getattr(request, "full_url", str(request)))
        raise urllib.error.HTTPError("u", 404, "gone", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr("urllib.request.urlopen", fake)
    policy = RobotsPolicy(agent=AGENT)
    for path in ("/careers", "/jobs", "/about/careers", "/"):
        policy.decide(f"https://acme.invalid{path}")
    assert len(calls) == 1, calls
