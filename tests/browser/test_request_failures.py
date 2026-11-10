# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""How the page's one request function tells a failure from an answer.

A read can fail before its headers or while its body is read: Stop, a time
limit or a page left abort it; a connection cut off breaks it. None of these
is an empty answer, and none of them may be drawn as "nothing here". Each case
replaces `fetch` inside the page for one call only; nothing leaves the page.
"""

from __future__ import annotations

from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.test_loading_recovery import (  # noqa: F401 - fixtures
    _uninstall,
    intercept,
    part,
    tailor,
    tailor_server,
    text,
    wait_part,
)

#: Runs `getCareer({signal})` once with `fetch` replaced by `STUB` (a function
#: of `(options, controller)`), and says how it ended.
CASE = """(async () => {
  const api = await import('/js/api.js');
  const real = window.fetch;
  const controller = new AbortController();
  const stub = %s;
  window.fetch = (input, options) => stub(options, controller);
  try {
    const value = await api.getCareer({ signal: controller.signal });
    return { ok: true, value };
  } catch (error) {
    return { ok: false, kind: error.kind, status: error.status, expected: Boolean(error.expected) };
  } finally {
    window.fetch = real;
  }
})()"""

#: Headers at once; a body that sends a first piece and then waits.
BODY = """new ReadableStream({ start(c) {
  c.enqueue(new TextEncoder().encode('{"confirmed": ['));
  %s
} })"""


def case(page: Chrome, stub: str) -> dict[str, Any]:
    return dict(page.evaluate(CASE % stub))


def opened(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/#home")
    page.wait_for_app()


def test_an_abort_before_the_headers_is_an_abort(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    opened(page, tailor_server["url"])
    said = case(
        page,
        """(options, controller) => new Promise((resolve, reject) => {
          const stop = () => reject(new DOMException('Aborted', 'AbortError'));
          options.signal.addEventListener('abort', stop);
          setTimeout(() => controller.abort(), 20);
        })""",
    )
    assert said == {"ok": False, "kind": "aborted", "status": 0, "expected": True}


def test_an_abort_while_the_body_is_read_is_an_abort_not_an_empty_answer(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    opened(page, tailor_server["url"])
    body = BODY % (
        "controller.signal.addEventListener('abort', "
        "() => c.error(new DOMException('Aborted', 'AbortError')));"
    )
    said = case(
        page,
        f"""(options, controller) => {{
          setTimeout(() => controller.abort(), 50);
          return Promise.resolve(new Response({body}, {{ status: 200 }}));
        }}""",
    )
    assert said == {"ok": False, "kind": "aborted", "status": 0, "expected": True}


def test_a_body_cut_off_without_an_abort_is_a_failure(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    opened(page, tailor_server["url"])
    body = BODY % "setTimeout(() => c.error(new TypeError('network error')), 30);"
    said = case(page, f"() => Promise.resolve(new Response({body}, {{ status: 200 }}))")
    assert said["ok"] is False and said["kind"] == "network" and said["expected"] is False


def test_server_errors_keep_their_status(page: Chrome, tailor_server: dict[str, Any]) -> None:
    opened(page, tailor_server["url"])
    for status in (500, 503):
        said = case(
            page,
            "() => Promise.resolve(new Response(JSON.stringify({ error: 'synthetic' }), "
            f"{{ status: {status} }}))",
        )
        assert said == {"ok": False, "kind": "http", "status": status, "expected": False}
    # A server error whose body is cut off is still that server error.
    body = BODY % "setTimeout(() => c.error(new TypeError('network error')), 30);"
    said = case(page, f"() => Promise.resolve(new Response({body}, {{ status: 503 }}))")
    assert said == {"ok": False, "kind": "http", "status": 503, "expected": False}


def test_an_empty_body_is_still_an_empty_answer(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    opened(page, tailor_server["url"])
    said = case(page, "() => Promise.resolve(new Response('', { status: 200 }))")
    assert said == {"ok": True, "value": None}


def test_a_confirmed_list_whose_body_never_ends_is_a_timeout_never_none_confirmed(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    """Resume Tailor's evidence part: the headers come, the body never ends,
    the part's time limit aborts it. Said as a read that did not answer in
    time, never as "Nothing is confirmed yet"."""
    intercept(page, [{"match": "/api/career", "mode": "stall"}])
    tailor(page, tailor_server["url"])
    wait_part(page, "evidence", "error", timeout=40)
    said = str(part(page, "evidence", "innerText"))
    assert "did not answer in time" in said
    assert "Nothing is confirmed yet" not in text(page)
    assert page.evaluate("window.__lrAborted.some((u) => u.includes('/api/career'))")
