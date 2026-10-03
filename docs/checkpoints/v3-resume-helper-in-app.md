# The Resume helper, inside Career Agent (V3, 2026-10)

## What changed

The Resume helper (formerly Resume Tailor Beta) is a page of Career Agent:
the sidebar item and step 3 of "Before you apply" open it in the same window,
with the same shell, profile and language. There is no new tab, no second
window and no iframe.

- **Engine unchanged.** The tailoring engine (`companion/resume-tailor`) still
  runs beside Career Agent, started by the launcher on the next port. The page
  never talks to that port: it asks `/rt/api/...` on Career Agent's origin and
  `web/server.py` forwards the request.
- **Proxy guards.**
  - It checks the browser as for Career Agent's own API (Host, Origin, JSON,
    and a same-origin page for uploads).
  - It refuses `Sec-Fetch-Site: cross-site`.
  - It forwards only the workspace, candidate and bridge routes.
  - It speaks only to a program that answers as the engine.
- **No second interface.** Under the launcher the engine serves no interface
  of its own: its `/` answers 404 and its old screens are not mounted.
- **One data model.**
  - The jobs are Career Agent's.
  - The experience is the Career Profile, copied verbatim into the engine
    before every resume (the existing bridge import). If that copy fails, no
    resume is made.
  - The profile is the active local profile.
  - A resume made for a job shows, and moves, that job's own status in Career
    Agent.

## Kept from the old interface

- Base resume upload, default, copy and remove.
- Make a resume from a saved job or a pasted ad.
- The requirement check ("What the job asks").
- The engine's checks ("Make it better").
- Line edit and hide, with the evidence check.
- "Undo all my changes".
- Word and PDF export.
- The list of resumes made.
- The Career Profile edit and remove, through the Career Profile itself.

## Deferred (DEFER, not removed; the engine routes still exist)

| Old interface feature | Why deferred |
|---|---|
| Sources upload and review, conflicts, decisions | The Career Profile and Documents in Career Agent are the one place experience is reviewed now. |
| Base resume rename; headline, summary, skills and certificates editing; reorder; lock | V3 edits lines on the sheet only. |
| Step-by-step undo and redo | V3 offers "Undo all my changes". |
| Markdown and HTML export | V3 offers Word and PDF. |
| Candidate backup and import, select and archive | One candidate per local profile; backups are Career Agent's. |
| Application note, role and company edits | The application lives in Career Agent. |

Experience accepted earlier through the old interface's Sources screens is
still in the engine's list and still confirmed by the person, which is what
the page's wording promises ("experience you confirmed").
