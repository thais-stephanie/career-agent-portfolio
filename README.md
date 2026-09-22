[English](README.md) | [Português](README.pt-BR.md) | [Español](README.es.md)

# Career Agent
Find jobs, understand why they fit your search, and prepare evidence-backed resumes on your computer.

![Career Agent Discover with synthetic jobs](docs/assets/readme/discover.png)

## Project status
**Alpha v0.1.0-alpha.2**, with **Resume Tailor Beta** included. A working local application, still under active development. Search Fit explains your search preferences; it is not a hiring probability.

## Easiest installation: Windows
1. Open [Releases](https://github.com/thais-stephanie/career-agent-portfolio/releases).
2. Download `Career-Agent-v0.1.0-alpha.2-Windows.zip`.
3. Right-click the ZIP, choose **Extract All**, then open the extracted folder.
4. Double-click **Start-Career-Agent.cmd**. Keep its window open.
5. Wait for setup. Your browser opens automatically.

You do not need to install Python or Node. The launcher downloads uv if needed; uv installs Python 3.12 and the locked dependencies. The first setup needs internet. Use a writable folder, not Program Files. [First-run help](FIRST_RUN.md).

## Open it again
Double-click the same launcher in the same folder. Your data stays there. Press **Ctrl+C** in its window to stop both apps. Before moving to a new version, back up your data.

## What it does
- Discover jobs from supported sources or import a posting yourself.
- Check hiring eligibility separately from search preferences.
- Inspect Search Fit, with the reasons and missing information visible.
- Review your career evidence and track applications you choose to send.

## Resume Tailor Beta
Open **Resume Tailor Beta** in the sidebar. From a job, choose **Copy job description**, open Tailor and paste it into **Tailor resume**.

Create a candidate, add a base resume and sources, review evidence, analyze the job, inspect matches and gaps, generate, edit, validate and export. Markdown and Word exports work without AI. PDF needs Microsoft Word or LibreOffice installed locally; otherwise the app explains that PDF is unavailable.

The two evidence stores are separate. No profile or evidence is automatically synchronized. **Search Fit and Tailor Match answer different questions.** Generated text never becomes confirmed Career Evidence. Unsupported experience must not become a resume claim. See [honesty guarantees](docs/ARCHITECTURE.md).

## Visual tour
All images show synthetic demo data.

| Discover and understand | Prepare and review |
|---|---|
| ![Why this matches](docs/assets/readme/why.png) | ![Resume Tailor Beta](docs/assets/readme/tailor.png) |
| ![First run](docs/assets/readme/first-run.png) | Job analysis → evidence matching → strategy → generation → validation → editing → export |

## Privacy
Your settings, jobs, notes and evidence are stored locally. Tailor retains uploaded source documents; Career Agent extracts CV text without keeping the uploaded file. Optional AI providers can receive content when you configure and use them. Collection and employer links also use internet. No telemetry is implemented. Read the [privacy model](docs/PRIVACY.md), including backups and clipboard handling.

## What it deliberately does not do
It does not auto-apply, promise interviews, turn remote work into worldwide eligibility, hide evidence gaps, or treat model output as confirmed experience.

## Install from a terminal
**Windows: open Start, type PowerShell, and open Windows PowerShell.** On macOS, open Applications → Utilities → Terminal. On Linux, open your Terminal application.

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), download and extract the source ZIP, then type `cd` followed by the folder path in quotes. Run:

```powershell
uv sync --locked --python 3.12
uv run python scripts/launch.py
```

No manual Python installation is needed. Windows is the release-tested platform. To use different ports: `uv run python scripts/launch.py --port 8875` (Tailor uses 8876).

## Demo
Double-click **Start-Demo.cmd**, or run `uv run python scripts/launch.py --demo`. It creates invented jobs and Alex Morgan, a synthetic candidate, in separate demo storage. Stop personal mode before starting demo on the same ports. Demo never uses an AI provider.

## Development and tests
See [CONTRIBUTING.md](CONTRIBUTING.md) for exact commands and [release validation](docs/VALIDATION.md) for measured results. The Tailor frontend bundle is included; Node is needed only to rebuild it. [Architecture](docs/ARCHITECTURE.md), [source permissions](docs/SOURCES.md) and [public audit](docs/PUBLIC_AUDIT.md) explain the engineering boundaries.

## Limitations
Alpha: source availability changes, language readers are incomplete, and eligibility can remain unknown. Beta: evidence review remains your responsibility; the modules do not share profiles. PDF and actual page counts depend on a local renderer. This is a local single-user app with no remote access or cloud sync. The initial install is not offline.

## License and third-party notices
Career Agent is [MIT](LICENSE). Resume Tailor in `companion/resume-tailor` is [Apache-2.0](companion/resume-tailor/LICENSE). Bundled fonts retain their OFL licenses. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for component boundaries, dependencies and attribution.
