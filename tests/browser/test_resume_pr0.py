"""Resume Workspace V2, PR 0, on screen: the Resume helper stops lying.

The test server runs no engine, so the page's `/rt/api` answers are synthetic
(the engine's side is proven in tests/integration/test_resume_pr0.py): a job
with two versions, a version whose ad could not be read, no name yet, and an
export refused because of an edited line.
"""

from __future__ import annotations

import json

from tests.browser.chrome import Chrome
from tests.browser.conftest import DESKTOP

AD = "Engenheiro de GenIA - Sênior\nRequisitos:\n• Experiência com Python.\n• Vivência com n8n."
RUNS = [
    {
        "id": run_id,
        "role": "Engenheiro de GenIA - Sênior",
        "company": "Exemplo Digital",
        "status": "Considering",
        "date": date,
        "match": None,
        "state": "done",
        "career_job_id": "job-synthetic-1",
    }
    for run_id, date in (
        ("20260102T000000-bbbbbb", "2026-01-02T10:00:00Z"),
        ("20260101T000000-aaaaaa", "2026-01-01T10:00:00Z"),
    )
]
VIEW = {
    "job": {
        "role": "Engenheiro de GenIA - Sênior",
        "company": "Exemplo Digital",
        "ad": AD,
        "source": {"kind": "career_agent", "career_job_id": "job-synthetic-1"},
        "required": [],
        "preferred": [],
        "conditions": [],
    },
    "match": {"strong": 0, "related": 0, "gaps": [], "rows": []},
    "resume": {},
    "pages": {},
    "checks": [],
    "options": {"evidence_only": True, "two_pages": True},
    "assembled_without_ai": True,
}
DRAFT = {
    "resume": {
        "headline": "Automation Engineer",
        "summary": [],
        "skills": [],
        "certifications": [],
        "experience": [
            {
                "company": "Northwind Ops",
                "title": "Automation Engineer",
                "start": "2023-01",
                "end": None,
                "position_id": "p1",
                "bullets": [
                    {
                        "id": "b001",
                        "text": "Built integrations.",
                        "hidden": False,
                        "edited": True,
                        "supported": False,
                    }
                ],
            }
        ],
    },
}
BLOCKED = {
    "detail": {
        "code": "edits_not_supported",
        "message": "",
        "params": {
            "lines": [
                {
                    "id": "b001",
                    "company": "Northwind Ops",
                    "text": "Led a team of 40 engineers.",
                    "why": ["A bigger role than your sources show"],
                },
            ]
        },
    }
}

ENGINE = f"""(() => {{
  const answers = {{
    workspace: {{ mode: 'profile', profile: {{ id: 'p', label: 'My profile' }}, candidate_id: 'c1',
      candidate_name: '', statuses: [] }},
    runs: {json.dumps(RUNS)}, view: {json.dumps(VIEW)}, draft: {json.dumps(DRAFT)},
    blocked: {json.dumps(BLOCKED)},
  }};
  const reply = (body, status = 200) => Promise.resolve(new Response(JSON.stringify(body),
    {{ status, headers: {{ 'Content-Type': 'application/json' }} }}));
  const real = window.fetch;
  window.fetch = (url, opts) => {{
    const path = String(url);
    if (!path.startsWith('/rt/api')) return real(url, opts);
    if (path.endsWith('/workspace')) return reply(answers.workspace);
    if (path.endsWith('/applications')) return reply(answers.runs);
    if (path.endsWith('/draft')) return reply(answers.draft);
    if (path.includes('/export/')) return reply(answers.blocked, 409);
    if (/\\/applications\\/[^/]+$/.test(path)) {{
      return reply({{ status: {{ status: 'done' }}, view: answers.view }});
    }}
    if (path.endsWith('/resumes')) return reply([]);
    return reply({{}});
  }};
}})()"""


def _open_resumes(page: Chrome, server: str) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(server)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')", message="rail")
    page.evaluate(ENGINE)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.getElementById('rh-tab-resumes') !== null", message="the tabs")
    page.evaluate("document.getElementById('rh-tab-resumes').click()")
    page.wait_for("document.querySelector('.rh-made') !== null", message="My resumes")


def test_my_resumes_lists_a_jobs_versions_and_offers_another(page: Chrome, server: str) -> None:
    _open_resumes(page, server)
    text = page.evaluate("document.querySelector('#page-resume').innerText")
    assert "2 versions" in text
    assert "Make another version" in text
    page.evaluate(
        "[...document.querySelectorAll('#page-resume button')]"
        ".find((b) => b.textContent === 'See versions').click()"
    )
    page.wait_for("document.querySelectorAll('.rh-made--version').length === 2", message="V1, V2")


def test_a_reopened_version_shows_its_ad_its_target_and_says_how_it_was_made(
    page: Chrome, server: str
) -> None:
    _open_resumes(page, server)
    page.evaluate(
        "[...document.querySelectorAll('#page-resume button')]"
        ".find((b) => b.textContent === 'Open latest').click()"
    )
    page.wait_for("document.querySelector('.rh-result') !== null", message="the result")
    head = page.evaluate("document.querySelector('.rh-result__head').innerText")
    assert "Engenheiro de GenIA - Sênior" in head and "Exemplo Digital" in head
    assert "Version 2 of 2" in head
    assert "without AI" in head
    # No fake score: the ad could not be read, and the page says so.
    assert page.evaluate("document.querySelector('.rh-fit__pct')") is None
    assert "couldn’t read the requirements" in page.evaluate(
        "document.querySelector('.rh-unreadable').innerText"
    )
    # The Job ad tab is the ad itself.
    page.evaluate("document.querySelector('[data-k=\"restab-ad\"]').click()")
    page.wait_for("document.querySelector('.rh-adtext__body') !== null", message="the ad")
    assert page.evaluate("document.querySelector('.rh-adtext__body').textContent") == AD


def test_no_name_and_a_refused_export_are_said_plainly(page: Chrome, server: str) -> None:
    _open_resumes(page, server)
    page.evaluate(
        "[...document.querySelectorAll('#page-resume button')]"
        ".find((b) => b.textContent === 'Open latest').click()"
    )
    page.wait_for("document.querySelector('.rh-paper') !== null", message="the paper")
    # Never "You": the paper asks for the name instead.
    assert page.evaluate("document.querySelector('.rh-paper .rh-nameform') !== null")
    assert "You" not in page.evaluate("document.querySelector('.rh-paper').innerText").split()
    page.evaluate("document.getElementById('rh-download').click()")
    page.wait_for("document.querySelector('[data-format=\"docx\"]') !== null", message="menu")
    page.evaluate("document.querySelector('[data-format=\"docx\"]').click()")
    page.wait_for("document.querySelector('.rh-blocked') !== null", message="why not")
    blocked = page.evaluate("document.querySelector('.rh-blocked').innerText")
    assert "Led a team of 40 engineers." in blocked
    assert "A bigger role than your sources show" in blocked


def test_a_tab_chosen_while_another_loads_is_the_one_that_stays(page: Chrome, server: str) -> None:
    """Start is still loading when My resumes is chosen: Start finishing
    later must not paint over the tab the person chose."""
    page.set_viewport(*DESKTOP)
    page.navigate(server)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')", message="rail")
    page.evaluate(ENGINE)
    # The first list of versions (Start's) answers last.
    page.evaluate(
        "(() => { const f = window.fetch; let n = 0; window.fetch = (u, o) =>"
        " String(u).endsWith('/applications')"
        " ? new Promise((r) => setTimeout(r, n++ === 0 ? 800 : 50)).then(() => f(u, o))"
        " : f(u, o); })()"
    )
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.getElementById('rh-tab-resumes') !== null", message="the tabs")
    page.evaluate("document.getElementById('rh-tab-resumes').click()")
    page.wait_for("document.querySelector('.rh-made') !== null", message="My resumes")
    page.evaluate("new Promise((r) => setTimeout(r, 1200))")
    assert page.evaluate("document.querySelector('.rh-made') !== null"), "Start painted over it"
    assert (
        page.evaluate("document.getElementById('rh-tab-resumes').getAttribute('aria-selected')")
        == "true"
    )
