<p align="center">
  <a href="README.md"><em>English</em></a> |
  <a href="README.pt-BR.md"><em>Português</em></a> |
  <a href="README.es.md"><em>Español</em></a>
</p>

<h1 align="center">✧ <strong>Career</strong> <em>Agent</em> ✧</h1>

<p align="center">
  Find jobs across <strong>26 integrated sources</strong>, understand why they match your search, and tailor your resume using experience you can back up.
</p>

<p align="center">
  <img
    src="docs/assets/readme/hero.png"
    alt="Career Agent illustration: Find jobs with Search Fit percentages beside a Resumes step from Master to a job version, Analyze and PDF export"
    width="100%"
  />
</p>

<p align="center">
  <em>Product illustration with invented examples, not a screenshot or measured outcomes. Actual demo screenshots appear below.</em>
</p>

<p align="center">
  <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.2">
    <img
      src="https://img.shields.io/badge/status-Beta_2-fff08a"
      alt="Beta 2"
    />
  </a>
  <a href="#resumes">
    <img
      src="https://img.shields.io/badge/Resumes-Workspace_V2-d8c8ff"
      alt="Resumes: Resume Workspace V2"
    />
  </a>
  <a href="docs/VALIDATION.md">
    <img
      src="https://img.shields.io/badge/Beta_2_tests-9%2C105_passed-a7ebcf"
      alt="9,105 tests passed"
    />
  </a>
  <a href="docs/INSTALL.md#which-computers-it-runs-on">
    <img
      src="https://img.shields.io/badge/Windows_11-tested-bbd6ff"
      alt="Windows 11 tested"
    />
  </a>
</p>

<p align="center">
  <img
    src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"
    alt="Python 3.12"
  />
  <img
    src="https://img.shields.io/badge/JavaScript-ES%20Modules-F7DF1E?logo=javascript&logoColor=111"
    alt="JavaScript ES Modules"
  />
  <img
    src="https://img.shields.io/badge/uv-package%20management-DE5FE9"
    alt="uv"
  />
  <img
    src="https://img.shields.io/badge/local--first-architecture-A7EBCF"
    alt="Local-first"
  />
  <a href="LICENSE">
    <img
      src="https://img.shields.io/badge/license-MIT-blue"
      alt="MIT license"
    />
  </a>
</p>

<p align="center">
  <strong>
    <a href="docs/INSTALL.md">Install guide</a>
    &nbsp;·&nbsp;
    <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.2">Download for Windows</a>
    &nbsp;·&nbsp;
    <a href="#screenshots">See the app</a>
  </strong>
</p>

Career Agent is a job-search workspace that runs on your own computer. It
collects public postings from job boards and employer hiring systems, checks
whether each employer can hire you where you live, and gives every posting a
**Search Fit** score from 0 to 100 that quotes the posting to explain itself.
**Resumes** keeps your resumes and makes a version for one posting from
experience you have confirmed. Your data is kept in files
inside the Career Agent folder; nothing is uploaded to a Career Agent server,
because there is none.

> [!NOTE]
>
> The latest release is **v0.2.0-beta.2**, for people who run it on their
> own computer; it shipped the previous resume tool, Resume Tailor Beta. This
> page describes the code since then, where **Resumes** replaced it. Search Fit describes how a posting
> matches your search preferences. It does not estimate your chances of being
> hired.

## Install

Choose one path. The [install guide](docs/INSTALL.md) has every step, written
for people who have never used a terminal.

- **I use Claude Code or Codex:** paste the
  [ready-made install message](docs/INSTALL.md#path-a-install-with-claude-code-or-codex)
  into it. It installs Career Agent, runs the demo and tells you how to start
  it again.
- **I want to install it myself:**
  - **Windows:** download `Career-Agent-v0.2.0-beta.2-Windows.zip` from the
    [release page](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.2.0-beta.2),
    unblock it (right-click, **Properties**, **Unblock**), right-click it and
    choose **Extract All**, then double-click
    **Start-Demo.cmd** in the extracted folder. You do not need to install
    Python first; the launcher downloads uv, which installs Python 3.12 and the
    locked libraries. [Step by step](docs/INSTALL.md#path-b-on-windows-download-and-double-click).
  - **macOS or Linux:** install uv, extract the source archive and run two
    commands. [Step by step](docs/INSTALL.md#path-b-on-macos-or-linux-use-the-terminal).
    These platforms were not tested for this release.

The demo opens with 21 invented jobs and an invented candidate, Alex Morgan,
in storage separate from your own. Stop it with **Ctrl+C** in its window,
then double-click **Start-Career-Agent.cmd** to set up your own search. The guide also covers
[opening it again](docs/INSTALL.md#how-to-open-career-agent-next-time),
[backups](docs/INSTALL.md#backups), [updating](docs/INSTALL.md#updating-to-a-new-version),
[troubleshooting](docs/INSTALL.md#troubleshooting) and
[uninstalling](docs/INSTALL.md#uninstalling).

## Screenshots

All three are captures of the demo, with synthetic jobs and an invented candidate.

**Find jobs**

![Find jobs in the demo: cards with a Search Fit percentage, the employer, place, level, pay and source of each synthetic posting](docs/assets/readme/find-jobs.png)

**Resume editor**

![The Resumes editor: contact fields beside the Check list and a live preview of the one-page resume](docs/assets/readme/resume-editor.png)

**Analyze for a job**

![Analyze for a job: counts and named findings for each ask, gaps, and an eligibility note kept apart, never a score](docs/assets/readme/analyze.png)

![Workflow illustration: find jobs, Search Fit, why it fits, Resumes and Applications](docs/assets/readme/how-it-works.png)

*Workflow illustration, not a screenshot.*

## What it does

| Area | What you get |
|---|---|
| Discover | Postings from 26 integrated sources, deduplicated into one list, plus manual import of any posting. Each posting keeps its source and collection history. Cards, Table and Board views; Table exports the current GOOD and STRONG results as a CSV file. |
| Eligibility | Hiring restrictions (country, work permit, clearance) are checked separately from preferences. "Remote" is never read as worldwide; a posting that does not say where it hires stays unresolved. |
| Search Fit | A 0 to 100 score with a band (STRONG, GOOD, MODERATE, WEAK), reasons quoted from the posting and the facts the posting left out. **Posting completeness** is shown beside it and never changes it. |
| Career Evidence | Career Agent reads your CV on your computer and proposes statements. Each one becomes part of your Career Profile only when you confirm it. |
| Applications | Statuses (Found, Interested, Applied, Interviewing, Offer, Rejected and others), notes and history for the postings you act on. |
| Resumes | A Master resume, PDF and Word import, a version for each job, an editor with a live preview, PDF, Word and JSON export, tailoring from confirmed experience, and Analyze: named findings, never a score. |

### What is stable, optional, experimental or missing

| Feature | Status |
|---|---|
| Collection from 26 sources, eligibility, Search Fit, Career Evidence, applications | Part of this Beta |
| Local profiles (several people on one installation) | Part of this Beta |
| Shared local job catalogue | Part of this Beta |
| Resumes (Resume Workspace V2) | Beta |
| AI drafting and AI review in Resumes | Optional, only when you press their buttons |
| Semantic matching (DeepSeek, Claude Code or Codex) | Optional, off until you start a run |
| Local model reading (Ollama, `qwen3:4b`) | Optional, runs only on this computer |
| LinkedIn through JobSpy | Experimental, off by default, per profile |
| Hosted or multi-user service, accounts, logins | Not implemented |
| Automatic applications | Not implemented |
| Cloud sync between computers | Not implemented |

## How Search Fit works

You describe the work you want in your own words. Career Agent compares that
description with the posting's own text: responsibilities, tools, level,
contract, work model and pay. Every point it gives is tied to a quoted
sentence from the posting. The title alone earns nothing.

- A posting that does not state a level is scored as mid-level (Pleno).
- An unstated salary or contract earns a middle value, between a mismatch and
  a match.
- **Role anchors** are the job titles you have in mind. They steer which
  searches Career Agent runs (for example on Himalayas and LinkedIn); they do
  not add points to a posting.
- **Semantic matching** is optional. An AI provider reads selected postings
  and says which parts of your search each one supports, with quotes. Career
  Agent accepts only quotes it can find in the posting, and its own
  arithmetic does the scoring. Details: [SEMANTIC_MATCHING.md](docs/SEMANTIC_MATCHING.md).

Search Fit never reads your confirmed evidence, and Resumes shows no match
score at all.

## Job sources

Career Agent has **26 integrated job sources** across global, U.S., European,
LATAM and Brazilian markets.

| Coverage | Integrated sources |
|---|---|
| ATS & employer systems | Greenhouse, Lever, Ashby, Workday, Workable, Teamtailor, Rippling, Recruitee, Comeet, Gupy |
| Remote & global boards | We Work Remotely, Remote OK, Himalayas, Jobicy, 4 Day Week, Remotive, Working Nomads, Dynamite Jobs, Arbeitnow |
| Networks & aggregators | a16z Speedrun, Jobgether, Jooble |
| LATAM & Brazil | Get on Board, Recruiterflow, Avlis Talent, Programathor |

Some sources provide complete structured inventories. Others expose recent-job
windows, bounded feeds or metadata-only results, so Career Agent records each
source's coverage instead of treating every connector as equivalent.
**Find jobs** refreshes the sources that are due (not refreshed in the last
day) first, then scores the new and changed jobs. **Settings & Sources** shows
a source health table: when each source last succeeded, which ones are due or
stale (three days old), and two buttons: **Refresh due sources** (the same as
Find jobs) and **Refresh all available sources** (recently refreshed ones too).
Neither wakes a paused source or asks a site that refused before its wait is
over. Nothing is collected until you press a button.

[Source permissions and coverage notes](docs/SOURCES.md)

### LinkedIn

LinkedIn restricts automated collection, so its source row stays marked as
forbidden. A person can choose an **experimental** exception for their own
profile: **LinkedIn via JobSpy**, off by default, turned on only after a
warning in Settings & Sources. It runs a bounded number of searches (24 by
default) from the role anchors and work phrases, never signs in, and stores no
LinkedIn password, cookie or session. When LinkedIn refuses, the source stops
and waits a day instead of retrying. You can also paste any LinkedIn posting
into manual import.

## Local profiles and the shared catalogue

One installation can hold several **local profiles**, for example you and a
family member. Each profile has its own settings, role anchors, CV, confirmed
evidence, Search Fit scores, applications, notes and resumes.
One profile's data never appears in or influences another's.

Public job data (postings, descriptions, employers, boards and the search
index) is stored once, in `data/shared/catalogue.db`, and every profile reads
it. Which profile's search found a posting stays in that profile.

Profiles are **not accounts**. There is no login or password, and anyone who
can use the same operating-system account can read every profile's files.
Details: [MULTI_PROFILE.md](docs/MULTI_PROFILE.md).

## Resumes

Open **Resumes** from the side bar, or from a job's details. Start with a
**Master** resume made from your confirmed Career Profile, import one from a
PDF or Word (.docx) file and review what was read, or start a blank one.
Every resume has a history you can restore, and **My resumes** groups the
versions made for each job.

A version for a job is built from your confirmed experience, without AI:
Resumes reads the job's asks, finds what you have confirmed for each one and
shows what it could not find. **Analyze** checks a resume on its own or
against a job and names what it found (a missing contact, an unsupported
number, an ask your resume does not show); it never gives a score. PDF, Word
and JSON export are checked before you download them. AI drafting and an
independent AI review are optional: each runs only when you press it, and
says first which provider receives what.

A sentence Resumes or an AI writes never becomes confirmed evidence, and a
line that cites evidence you have not confirmed cannot be exported. Details:
[RESUME_WORKSPACE.md](docs/RESUME_WORKSPACE.md).

The previous resume tool, Resume Tailor, is retired. If you used it, Resumes
offers to move its resumes, after a verified backup; nothing moves until you
choose to, and its files stay on your computer.

## Privacy

Your settings, postings, scores, notes, applications, CV text and evidence
are stored in the `data` and `config` folders inside the Career Agent folder.
Career Agent keeps the text it extracts from a CV, not the uploaded file.
Resumes keeps what you saved from an imported resume, not the file. No telemetry is
implemented, and the application does not encrypt its files.

These actions send data off the computer, each only when you take it:

| When | What leaves |
|---|---|
| First installation | Downloads of uv, Python and the locked libraries. |
| Collecting jobs | Requests to job boards and employer sites, with search phrases and places. Never your CV or profile. |
| LinkedIn via JobSpy (if you turned it on) | Short search phrases and places, sent to LinkedIn. |
| Semantic matching (if you start a run) | Your search phrases and the selected postings' text, to the provider you chose. |
| AI drafting or review in Resumes (when you press it) | The job's title and the asks it quotes, and the resume lines and confirmed statements relevant to them, to the provider you set up. Never your contact details. |
| Opening an employer link | Your browser visits that website. |

The local model reading (Ollama) stays on the computer: Career Agent refuses
an Ollama address that is not local. The full inventory is in
[PRIVACY.md](docs/PRIVACY.md).

## Validation

The v0.2.0-beta.2 release gate passed **9,105 tests** with 7 skipped and 0
failed, on Windows 11 with Python 3.12:

| Suite | Passed |
|---|---:|
| Career Agent unit | 7,018 |
| Career Agent integration | 1,656 |
| Career Agent browser | 371 |
| Resume Tailor Python | 22 |
| Resume Tailor frontend | 38 |

Ruff, the formatter check, mypy and the frontend checks were clean. The
release ZIP was installed in a new folder whose path contains spaces, with no
uv or Python available beforehand, and the demo, personal mode, a port
conflict, restart and shutdown were checked, as was an update from
v0.2.0-beta.1 with synthetic data. There is no continuous
integration: the gate runs on the maintainer's Windows computer.
[Release validation](docs/VALIDATION.md) lists the skips and the install
checks.

## Known limitations

- Windows 11 is the only platform tested for this release. macOS and Linux
  instructions exist but were not run.
- Local profiles separate data inside the app. They are not a security
  boundary.
- Search Fit: a manual review of recent results found GOOD postings supported
  mainly by generic work phrases. Scoring was not changed, because a change
  needs an evidence-quality benchmark first.
- LinkedIn collection is experimental. LinkedIn may refuse or rate-limit it,
  and a refused source waits a day.
- Source availability changes, and some sources return only recent windows or
  metadata. A posting that does not say where it hires stays unresolved.
- Career Agent's interface is in English and Brazilian Portuguese.
- The local model reading takes minutes on a laptop processor.
- PDF export needs Microsoft Edge or Chrome on the computer.
- No live hosted AI provider was called during release testing.
- Some validation datasets used during development are private and are not
  shipped; their tests are excluded, not counted as passing.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development commands and the
release gate. [Architecture](docs/ARCHITECTURE.md),
[source permissions](docs/SOURCES.md) and
[public audit](docs/PUBLIC_AUDIT.md) explain the engineering boundaries.

## License and third-party notices

Career Agent is [MIT](LICENSE). The reader that moves resumes from the retired Resume Tailor adapts its Apache-2.0 code ([license](licenses/resume-tailor-Apache-2.0.txt), [notice](licenses/resume-tailor-NOTICE.txt)). Bundled fonts retain their OFL licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component boundaries, dependencies and attribution.

Credit to [Career-Ops](https://github.com/career-ops-hq/career-ops) for protocol patterns that informed adapter work. Presentation references: [ECC](https://github.com/affaan-m/ECC), [Open Code Review](https://github.com/alibaba/open-code-review), [Ponytail](https://github.com/DietrichGebert/ponytail), [Colibri](https://github.com/JustVugg/colibri). The notices distinguish adapted material, protocol knowledge, inspiration and dependencies. No affiliation is implied.
