"""Is the posting's page still up? Simulated HTTP and DNS only; nothing leaves.

Every answer that is not unmistakable is INCONCLUSIVE, never REMOVED; LinkedIn
is EXCLUDED, never asked; a private, local or metadata address is never asked,
also after a redirect.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from career_agent import availability as av
from career_agent.availability import Availability, api_url, check_one, due, public_destination

PUBLIC = "93.184.216.34"


def resolver(table: dict[str, str] | None = None):
    def resolve(host: str) -> list[str]:
        return [(table or {}).get(host, PUBLIC)]

    return resolve


def client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False)


def domains() -> av._Domains:
    return av._Domains(0.0, lambda s: None, lambda: 0.0)


def ask(
    handler,
    url="https://jobs.example.com/acme/123",
    title="Senior Integration Engineer",
    resolve=None,
):
    seen: list[str] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return handler(request)

    result = check_one(
        "job-1",
        url,
        title,
        client=client(recording),
        resolve=resolve or resolver(),
        domains=domains(),
    )
    return result, seen


@pytest.mark.parametrize("status", [404, 410])
def test_gone_is_removed(status) -> None:
    result, _ = ask(lambda r: httpx.Response(status, text="Not here"))
    assert result.state is Availability.REMOVED and result.http_status == status


def test_a_closed_page_that_answers_200_is_removed_by_its_own_words() -> None:
    page = "<h1>Senior Integration Engineer</h1><p>This job is no longer available.</p>"
    result, _ = ask(lambda r: httpx.Response(200, text=page))
    assert result.state is Availability.REMOVED
    assert "no longer available" in (result.evidence or "")


def test_the_page_still_showing_the_title_is_available() -> None:
    result, _ = ask(
        lambda r: httpx.Response(200, text="<h1>Senior Integration Engineer</h1> Apply")
    )
    assert result.state is Availability.AVAILABLE


def test_a_200_alone_confirms_nothing() -> None:
    result, _ = ask(lambda r: httpx.Response(200, text="<h1>Careers at Acme</h1> Join us"))
    assert result.state is Availability.INCONCLUSIVE


@pytest.mark.parametrize("status", [403, 429, 500, 502, 503])
def test_blocks_and_server_errors_are_inconclusive(status) -> None:
    result, _ = ask(lambda r: httpx.Response(status, text="no"))
    assert result.state is Availability.INCONCLUSIVE


def test_a_timeout_or_a_failed_connection_is_inconclusive() -> None:
    def timeout(request):
        raise httpx.ConnectTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    assert ask(timeout)[0].state is Availability.INCONCLUSIVE
    assert ask(refused)[0].state is Availability.INCONCLUSIVE


def test_a_bot_check_is_inconclusive() -> None:
    page = "Please verify you are human. Senior Integration Engineer"
    assert ask(lambda r: httpx.Response(200, text=page))[0].state is Availability.INCONCLUSIVE


def test_a_redirect_to_a_careers_home_is_not_a_confirmation() -> None:
    def handler(request):
        if request.url.path == "/acme/123":
            return httpx.Response(302, headers={"location": "/careers"})
        return httpx.Response(200, text="All our openings")

    result, seen = ask(handler)
    assert result.state is Availability.INCONCLUSIVE
    assert result.final_url.endswith("/careers") and len(seen) == 2


def test_too_many_redirects_stop() -> None:
    result, seen = ask(lambda r: httpx.Response(302, headers={"location": "/again"}))
    assert result.state is Availability.INCONCLUSIVE and len(seen) == av.MAX_REDIRECTS + 1


def test_linkedin_is_never_asked_and_never_called_removed() -> None:
    result, seen = ask(lambda r: httpx.Response(404), url="https://www.linkedin.com/jobs/view/1")
    assert result.state is Availability.EXCLUDED and seen == []

    def to_linkedin(request):
        return httpx.Response(301, headers={"location": "https://br.linkedin.com/jobs/view/1"})

    result, seen = ask(to_linkedin)
    assert result.state is Availability.EXCLUDED and len(seen) == 1


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.5", "192.168.1.2", "169.254.169.254", "::1"]
)
def test_a_private_local_or_metadata_address_is_never_asked(address) -> None:
    result, seen = ask(
        lambda r: httpx.Response(200), resolve=resolver({"jobs.example.com": address})
    )
    assert result.state is Availability.INCONCLUSIVE and seen == []


def test_a_redirect_to_a_private_address_is_refused() -> None:
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})

    result, seen = ask(handler)
    assert result.state is Availability.INCONCLUSIVE and len(seen) == 1


@pytest.mark.parametrize(
    "url",
    ["ftp://jobs.example.com/x", "file:///etc/passwd", "https://user:pass@jobs.example.com/x"],
)
def test_only_plain_public_http_is_asked(url) -> None:
    assert public_destination(url, resolver()) is not None


def test_retry_after_pauses_the_domain_for_the_rest_of_the_run() -> None:
    calls: list[str] = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(429, headers={"retry-after": "120"})

    shared = domains()
    http = client(handler)
    first = check_one(
        "a", "https://jobs.example.com/1", "x", client=http, resolve=resolver(), domains=shared
    )
    second = check_one(
        "b", "https://jobs.example.com/2", "x", client=http, resolve=resolver(), domains=shared
    )
    assert first.state is Availability.INCONCLUSIVE and second.state is Availability.INCONCLUSIVE
    assert len(calls) == 1, "the paused domain was asked again"


def test_the_ats_api_is_used_and_a_removed_lever_posting_reads_removed() -> None:
    url = "https://jobs.lever.co/jobgether/b7a4aea9-74ae-4cb9-a834-9370c7033a21"
    assert (
        api_url(url)
        == "https://api.lever.co/v0/postings/jobgether/b7a4aea9-74ae-4cb9-a834-9370c7033a21"
    )
    assert api_url("https://job-boards.greenhouse.io/mattermost/jobs/5379251008") == (
        "https://boards-api.greenhouse.io/v1/boards/mattermost/jobs/5379251008"
    )
    result, seen = ask(lambda r: httpx.Response(404, json={"ok": False}), url=url)
    assert result.state is Availability.REMOVED and result.method == "api"
    assert seen[0].startswith("https://api.lever.co/")


def test_a_large_page_is_read_only_up_to_the_cap() -> None:
    body = "<h1>Senior Integration Engineer</h1>" + "x" * (av.MAX_BYTES * 3)
    assert ask(lambda r: httpx.Response(200, text=body))[0].state is Availability.AVAILABLE


def test_each_answer_is_asked_again_after_its_own_interval() -> None:
    now = datetime(2026, 10, 13, tzinfo=UTC)
    stamp = lambda days: (now - timedelta(days=days)).isoformat().replace("+00:00", "Z")  # noqa: E731
    assert due(None, None, now)
    assert not due("AVAILABLE", stamp(1), now) and due("AVAILABLE", stamp(4), now)
    assert not due("REMOVED", stamp(7), now) and due("REMOVED", stamp(15), now)
    assert due("INCONCLUSIVE", stamp(2), now)
