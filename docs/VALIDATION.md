# Release validation: v0.2.0-beta.3

Validated on Windows 11 (x64) with uv-managed Python 3.12 and Chrome for the
browser tests. No live AI provider was called: AI drafting and review were
tested with a fake provider. No job source was contacted by any test below,
and no private data is in any test, screenshot or archive.

- **Gated code commit:** `a1a2669b4f6fde9cdffbde216a022723ddc97fb4`. Every
  test and static gate below ran on this exact commit, one suite at a time,
  after the last code change.
- **Release candidate:** built from the commit that adds this file, which
  changes only Markdown files after the gated commit. The archives' `BUILD_ID`
  and `release.json` name that commit.

There is no continuous integration. The gate runs on the maintainer's computer.

## Tests

| Suite | Passed | Skipped | Failed | Time |
|---|---:|---:|---:|---:|
| Unit and integration | 10,560 | 6 | 0 | 29 min 29 s |
| Browser (headless Chrome) | 447 | 0 | 0 | 15 min 8 s |

Skipped tests are not counted as passing. All six need files the public
edition does not carry:

- `test_onboarding_personas.py:127`: this persona's own words overlap the
  maintainer's field, so the check does not apply;
- `test_budget_ledger_concurrency.py:301`, `test_glm_recheck_authorization.py:82`
  and `:97`: no model-evaluation ledger in a public working tree;
- `test_glm_recheck_authorization.py:346`: no job corpus in a public working
  tree;
- `test_prompts.py:228`: the private golden corpus is excluded from the public
  edition.

The Resume Tailor engine suite and its frontend tests are gone with the
engine. The move of its files is proved instead by a frozen synthetic
workspace that engine wrote (`tests/fixtures/legacy_resume_helper`), with the
rows the move wrote from it while it still used the engine's own readers: the
move must write the same rows.

Static gates, all clean on the gated commit: ruff, the formatter check, mypy
(279 source files), the punctuation gate, the frontend check (49 modules,
2 pages: parse, resolve, safety, hygiene), SQL portability, provider
neutrality, design tokens, the contrast report (100 pairs, 0 below their
floor), web boundaries and `uv lock --check`.

## Update from v0.2.0-beta.2

The published `Career-Agent-v0.2.0-beta.2-Windows.zip` (sha256 matching its
release page) was installed in a folder whose path contains spaces. Beta 2's
own code then made a synthetic user state through its running app:

- two local profiles;
- role anchors;
- four imported jobs and their scores;
- a saved job, an applied job and a note;
- confirmed evidence;
- a Resume Tailor workspace: a resume source with three accepted details, a
  base resume, two tailored versions for the same job, an edited draft and an
  exported Word file.

The Beta 3 Windows ZIP built from the gated commit was then extracted into a
new folder and the update followed [INSTALL.md](INSTALL.md) exactly: `data`
and `config\*.local.yaml` copied (that state had no `.env` and no backups).
Results:

- The database moved from schema 45 to 49. Profiles, anchors, jobs, the
  applied status and its date, the saved job, the note and the evidence were
  all as Beta 2 left them. Scores stayed current: same schema, same values,
  no recalculation.
- Nothing moved by itself. Resumes found the old resumes (2 base resumes,
  2 versions, 1 draft, 1 download).
- The move made a verified backup first, then created 2 imported resumes and
  the job's V1 and V2. The edited draft became V1's working copy. A second
  move copied nothing.
- The Beta 2 download stayed behind as unmatched, because both versions carry
  the same file name, and the result said so. This test is what found that
  Beta 2 names downloads differently from later builds; the fix and its test
  are in this release.
- The old folder and the old workspace files were byte-for-byte unchanged.
- One port only, and nothing listened on the next one. Quit freed the port, a
  restart found everything moved, and the second profile kept its own
  anchors with no move offered. No contact detail or resume line appeared in
  the console.

## Fresh Windows ZIP

The Beta 3 Windows ZIP built from the gated commit was extracted into a path
with spaces, in a cold environment: no uv on the PATH, an empty uv cache and
managed Python only.

- **First start, demo:** 53.0 s. The launcher downloaded uv and checked its
  checksum, then installed Python 3.12 and the locked libraries.
- The demo had 21 jobs and Resumes opened. Only 127.0.0.1 on the chosen port
  listened, `/rt/api` answered 404, and Quit left no process and a free port.
- **Personal mode:** first start 6.1 s. One profile, a private database, the
  shared catalogue, zero jobs, Resumes usable and no move offered. A second
  start on the same profile was refused with "This profile is already open in
  another Career Agent window" and paused for the reader. A restart took
  3.0 s.
- The ZIP contained no `.venv`, `.tools`, `data`, `.env` or local
  configuration.

## Windows shortcut and window

Measured on an earlier candidate of this release whose launcher files are
identical to the gated commit, with the maintainer's own shortcuts backed up
and restored byte for byte afterwards.

- **Shortcuts:** the Desktop and Start menu shortcuts point at that folder,
  with the star icon (`career-agent.ico`).
- **Window:** a double-click on the shortcut's target opened Career Agent in
  5.1 s, in an Edge app window titled "Career Agent". The window uses its own
  data in `data\app-window`, never the person's Edge profile, and only
  127.0.0.1:8765 listened.
- **Closing:** closing the window stopped Career Agent in 6.6 s with no
  process left; reopening took 5.1 s.
- **Moved folder:** after moving the folder, the next start made the
  shortcuts again for the new path.
- **NOT TESTED:** the taskbar icon, hover preview and Alt+Tab were not
  checked by automation.

## Network

Measured on the Career Agent server process, in personal mode, on a private
copy of a real-size installation, through Home, Find jobs, Resumes, Editor,
export, tailoring without AI, Analyze and the Resume Tailor move. It listened
only on 127.0.0.1 and had only loopback connections. The launcher's first
setup downloads uv, Python and libraries; collecting jobs, semantic matching
and AI drafting or review send data only when started, as
[PRIVACY.md](PRIVACY.md) describes.

## Privacy contract for AI drafting and review

A fake provider recorded every prompt
(`tests/integration/test_resume_ai_privacy.py`). Markers planted in:

- contact details;
- another job;
- an application note;
- unrelated confirmed evidence;
- another profile;
- Search Fit and eligibility results.

None reached a prompt. Drafting made one call, and drafting with review made
two, with no retry. Editing, preview, export, tailoring without AI and Analyze
made no call.

## Dogfood on a private copy

A real installation was copied with SQLite's backup API (both databases
passed `integrity_check`) into a temporary folder. The original was left
unchanged, and the copy was deleted afterwards. Exercised:

- Home, Find jobs, My applications, My profile, Proof of my work, Resumes and
  Settings, with no console error;
- the Master, a Word import, the Editor and preview, PDF and Word export, a
  manual job version, tailoring without AI and Analyze (resume and job);
- the resume used for an application;
- the Resume Tailor move, then a second move that copied nothing;
- AI drafting and review with the fake provider: one and two calls, and no
  other job title in any prompt;
- profile backup and Quit.

## Performance (measured)

Dogfood times are from the private copy above, on a large job catalogue. The
first Find jobs load read a freshly copied database that was not yet in the
file cache.

| What | Time |
|---|---:|
| Demo first start, cold machine (downloads uv, Python, libraries) | 53.0 s |
| Personal first start after setup | 6.1 s |
| Restart | 3.0 s |
| Open from the shortcut / close the window | 5.1 s / 6.6 s |
| Start on the real-size copy | 4.1 s |
| Find jobs, first load / next loads | 27.9 s / 0.19 s |
| Home | 0.6 s |
| Resumes home | 0.014 s |
| Proof of my work | 0.07 s |
| Sources | 1.2 s |
| Manual job version | 0.06 s |
| Tailor for a job without AI | 0.58 s |
| Analyze, resume / against a job | 0.1 s / 0.07 s |
| Export PDF (4 pages) / Word | 13.9 s / 0.21 s |

## Public audit

- gitleaks 8.30.1 (official release, checksum verified) over the full git
  history and over the extracted release archives: no leaks.
- The maintainer's own identity strings appear in the archives only inside
  tests that assert their absence.
- The archives contain no `.env`, no local configuration, no `data` folder,
  no database and no virtual environment.
- The README images are the supplied illustrations and screenshots of the
  demo with synthetic data, with their metadata removed.

## Not tested

macOS and Linux; the taskbar icon, hover preview and Alt+Tab; a live hosted
AI provider; LibreOffice.
