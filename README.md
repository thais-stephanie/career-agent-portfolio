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
    alt="Career Agent"
    width="100%"
  />
</p>

<p align="center">
  <em>Product illustration with invented examples, not a screenshot or measured outcomes. Actual demo screenshots appear below.</em>
</p>

<p align="center">
  <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2">
    <img
      src="https://img.shields.io/badge/status-Alpha_2-fff08a"
      alt="Alpha 2"
    />
  </a>
  <a href="#resume-tailor-beta">
    <img
      src="https://img.shields.io/badge/Resume_Tailor-Beta-d8c8ff"
      alt="Resume Tailor Beta"
    />
  </a>
  <a href="https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md">
    <img
      src="https://img.shields.io/badge/Alpha_2_tests-7%2C232_passed-a7ebcf"
      alt="7,232 tests passed"
    />
  </a>
  <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2">
    <img
      src="https://img.shields.io/badge/Alpha_2-Windows_validated-bbd6ff"
      alt="Windows validated"
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
    src="https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white"
    alt="FastAPI"
  />
  <img
    src="https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=111"
    alt="React 18"
  />
  <img
    src="https://img.shields.io/badge/TypeScript-5-3178C6?logo=typescript&logoColor=white"
    alt="TypeScript 5"
  />
  <img
    src="https://img.shields.io/badge/Vite-6-646CFF?logo=vite&logoColor=white"
    alt="Vite 6"
  />
</p>

<p align="center">
  <img
    src="https://img.shields.io/badge/pytest-tested-0A9EDC?logo=pytest&logoColor=white"
    alt="pytest"
  />
  <img
    src="https://img.shields.io/badge/uv-package%20management-DE5FE9"
    alt="uv"
  />
  <img
    src="https://img.shields.io/badge/local--first-architecture-A7EBCF"
    alt="Local-first"
  />
  <img
    src="https://img.shields.io/badge/26-job%20sources-FFD966"
    alt="26 integrated job sources"
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
    <a href="https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2">Download for Windows</a>
    &nbsp;·&nbsp;
    <a href="#demo">Try the demo</a>
    &nbsp;·&nbsp;
    <a href="#visual-tour">See the app</a>
  </strong>
</p>

> [!NOTE]
>
> Career Agent is currently **Alpha v0.1.0-alpha.2**, with **Resume Tailor Beta** included.
> The application is functional and under active development. Search Fit explains how a role matches your search preferences. It does not estimate your chances of being hired.

## Easiest installation: Windows

1. Open [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2).
2. Download `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Right-click the ZIP, choose **Extract All**, then open the extracted folder.
4. Double-click **Start-Career-Agent.cmd**. Keep its window open.
5. Wait for setup. Your browser opens automatically.

You do not need to install Python or Node. The launcher downloads uv, a tool that manages Python and packages, if needed; uv installs Python 3.12 and the locked dependencies. The first setup needs internet. Use a writable folder, not Program Files. [First-run help](FIRST_RUN.md).

## Open it again
Double-click the same launcher in the same folder. Your data stays there. Press **Ctrl+C** in its window to stop both apps. Before moving to a new version, back up your data.

## What it does

| Step | What you get |
|---|---|
| Discover | 26 integrated sources across ATS networks, remote job boards and aggregators, plus manual posting import. Each posting keeps its source and collection provenance. |
| Check eligibility | Hiring restrictions are separate from preferences. “Remote” does not mean worldwide; missing facts stay unresolved. |
| Understand Search Fit | A transparent score comparing posting facts with your search preferences, with reasons and missing information visible. It is not a hiring probability. |
| Review Career Evidence | Extract suggestions from your CV, then confirm, edit or reject them. Importing text does not confirm experience. |
| Track | Keep the status and notes for applications you choose to send. |

## Job sources

Career Agent currently has **26 integrated job sources** across global, U.S., European, LATAM and Brazilian markets.

| Coverage | Integrated sources |
|---|---|
| ATS & employer systems | Greenhouse, Lever, Ashby, Workday, Workable, Teamtailor, Rippling, Recruitee, Comeet, Gupy |
| Remote & global boards | We Work Remotely, Remote OK, Himalayas, Jobicy, 4 Day Week, Remotive, Working Nomads, Dynamite Jobs, Arbeitnow |
| Networks & aggregators | a16z Speedrun, Jobgether, Jooble |
| LATAM & Brazil | Get on Board, Recruiterflow, Avlis Talent, Programathor |

Some sources provide complete structured inventories. Others expose recent-job windows, bounded feeds or metadata-only results, so Career Agent records source coverage instead of treating every connector as equivalent.

[See the source permissions and coverage notes →](docs/SOURCES.md)

### Coming next

**LinkedIn job ingestion is in development.**

The planned pipeline will bring discovered LinkedIn roles through the same normalization, deduplication, eligibility and Search Fit flow used by the existing sources.

LinkedIn ingestion is not included in Alpha 2. The final collection method will be qualified separately before release.

## Resume Tailor Beta
Open **Resume Tailor Beta** in the sidebar. From a job, choose **Copy job description**, open Tailor and paste it into **Tailor resume**.

Create a candidate, add a base resume and sources, review evidence, analyze the job, inspect matches and gaps, generate, edit, validate and export. Markdown and Word exports work without AI. PDF needs Microsoft Word or LibreOffice installed locally; otherwise the app explains that PDF is unavailable.

The two evidence stores are separate. No profile or evidence is automatically synchronized. **Search Fit and Tailor Match answer different questions.** Generated text never becomes confirmed Career Evidence. Unsupported experience must not become a resume claim. See [honesty guarantees](docs/ARCHITECTURE.md).

## Visual tour

![Workflow: discover, inspect Search Fit and reasons, prepare with Tailor Beta, track applications](docs/assets/readme/how-it-works.png)

*Workflow illustration. The screenshots below show the actual app with synthetic demo data.*

| Discover jobs | Understand “Why this matches” |
|---|---|
| ![Discover with synthetic jobs](docs/assets/readme/discover.png) | ![Search Fit reasons and missing facts](docs/assets/readme/why.png) |

| Prepare with Resume Tailor Beta | Start your workspace |
|---|---|
| ![Resume Tailor Beta](docs/assets/readme/tailor.png) | ![First-run setup](docs/assets/readme/first-run.png) |

## Privacy
Your settings, jobs, notes and evidence are stored locally. Tailor retains uploaded source documents; Career Agent extracts CV text without keeping the uploaded file. Optional AI providers can receive content when you configure and use them. Collection and employer links also use internet. No telemetry is implemented. Read the [privacy model](docs/PRIVACY.md), including backups and clipboard handling.

## What it deliberately does not do
It does not auto-apply, promise interviews, turn remote work into worldwide eligibility, hide evidence gaps, or treat model output as confirmed experience.

## Install from a terminal

<details>
<summary>Manual setup for developers</summary>

**Windows: open Start, type PowerShell, and open Windows PowerShell.** On macOS, open Applications → Utilities → Terminal. On Linux, open your Terminal application.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), download and extract the source ZIP from [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases/tag/v0.1.0-alpha.2), then type `cd` followed by the folder path in quotes. Run:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

No manual Python installation is needed. Windows is the release-tested platform. To use different ports: `uv run python scripts/launch.py --port 8875` (Tailor uses 8876).

</details>

## Demo
Double-click **Start-Demo.cmd**, or run `uv run python scripts/launch.py --demo`. It creates invented jobs and Alex Morgan, a synthetic candidate, in separate demo storage. Stop personal mode before starting demo on the same ports. Demo never uses an AI provider.

## Development and tests

![Python 3.12](https://img.shields.io/badge/Python-3.12-3776ab) ![JavaScript ES Modules](https://img.shields.io/badge/JavaScript-ES_Modules-f7df1e) ![FastAPI](https://img.shields.io/badge/FastAPI-009688) ![React 18](https://img.shields.io/badge/React-18-61dafb) ![TypeScript 5](https://img.shields.io/badge/TypeScript-5-3178c6) ![Vite 6](https://img.shields.io/badge/Vite-6-646cff)

The **v0.1.0-alpha.2 release** passed **7,232 tests**, with six skipped:

| Suite | Passed |
|---|---:|
| Career Agent unit | 5,638 |
| Career Agent integration | 1,282 |
| Career Agent browser | 266 |
| Resume Tailor Python | 22 |
| Resume Tailor frontend | 24 |

These results belong to the released build, not later README edits. [Release validation](https://github.com/thais-stephanie/career-agent-portfolio/blob/v0.1.0-alpha.2/docs/VALIDATION.md) covers skips, static checks, clean Windows installation and exports.

See [CONTRIBUTING.md](CONTRIBUTING.md) for development commands. The Tailor frontend bundle is included; Node is needed only to rebuild it. [Architecture](docs/ARCHITECTURE.md), [source permissions](docs/SOURCES.md) and [public audit](docs/PUBLIC_AUDIT.md) explain the engineering boundaries.

## Limitations
Alpha: source availability changes, language readers are incomplete, and eligibility can remain unknown. Beta: evidence review remains your responsibility; the modules do not share profiles. PDF and actual page counts depend on a local renderer. This is a local single-user app with no remote access or cloud sync. The initial install is not offline.

Windows is the release-tested platform. Word PDF conversion was tested; LibreOffice was not installed. No live hosted AI provider was tested.

## License and third-party notices
Career Agent is [MIT](LICENSE). Resume Tailor in `companion/resume-tailor` is [Apache-2.0](companion/resume-tailor/LICENSE). Bundled fonts retain their OFL licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component boundaries, dependencies and attribution.

Credit to [Career-Ops](https://github.com/career-ops-hq/career-ops) for protocol patterns that informed adapter work. Presentation references: [ECC](https://github.com/affaan-m/ECC), [Open Code Review](https://github.com/alibaba/open-code-review), [Ponytail](https://github.com/DietrichGebert/ponytail), [Colibri](https://github.com/JustVugg/colibri). The notices distinguish adapted material, protocol knowledge, inspiration and dependencies. No affiliation is implied.
