# Resource footprint

Measured on 2026-09-23 on the development machine (Windows 11, x86_64,
Python 3.12.13 managed by uv 0.11.x). Every figure below says what was measured
and how, so it can be repeated. Numbers from a different machine will differ;
the method is what matters.

## What belongs to Career Agent and what does not

| Item | Size | Belongs to |
|---|---:|---|
| Tracked source files (`git ls-files`, 872 files) | 16.0 MB | the project |
| Runtime environment, as the launcher installs it (`uv sync --locked --no-dev`) | 57.6 MB, 4,465 files | the project folder (`.venv`) |
| Development environment (adds pytest, ruff, mypy and stubs) | 142.7 MB, 8,473 files | contributors only |
| Python 3.12 interpreter managed by uv | 62.1 MB | shared by every uv project on the machine |
| uv download cache | 982.5 MB on this machine | shared by every uv project; not Career Agent's |
| Tailor frontend `node_modules` | 81.9 MB | contributors rebuilding the bundle only; the Windows ZIP ships the built bundle |
| `.mypy_cache`, `__pycache__` | 46.5 MB, 14.0 MB | development by-products, regenerated on demand |

The uv cache and the managed Python are **shared**: a second uv project on the
same machine reuses them, and deleting Career Agent's folder does not remove
them. They are reported separately so they are not counted as this project's
footprint.

The single largest item in the development environment is mypy
(about 32 MB with its compiled module), which the launcher never installs.

## Backend process

`career-agent serve` on the demo corpus (21 postings, worked-example
configuration), measured on the real interpreter process. uv's `python.exe` in
a virtual environment is a small launcher that starts the interpreter as a
child process; measuring the launcher reports about 5 MB, which is wrong.

| Measurement | Run 1 | Run 2 |
|---|---:|---:|
| Process start to first answered `/api/health` | 1.08 s | 1.07 s |
| Working set, idle | 59.4 MB | 59.2 MB |
| Private memory, idle | 45.8 MB | 45.6 MB |
| CPU time while idle for 10 s | 31 ms (tail of start-up) | 0 ms |
| Working set after loading Home and Discover | 60.2 MB | 60.3 MB |
| Working set after Settings, Profile, Evidence and Sources | 60.4 MB | 60.6 MB |

The browser's own memory is not included: it is the browser's, and it depends on
the browser far more than on this page.

Not measured here: the combined launcher (`scripts/launch.py`) runs Career Agent
and Resume Tailor Beta in one Python process. It changes into the project folder
and writes `data/`, so measuring it would have written into this checkout.
Measuring it on a copy of the release folder is left as a follow-up.

## Response times of the heavy screens

The four requests Settings and Career Profile make were dominated by parsing
YAML in pure Python (the 48 KB search configuration and the source catalogue).
They now parse through libyaml's safe loader (`career_agent/yaml_io.py`), which
builds the same objects and raises the same errors. Median of five direct
handler calls, demo corpus, worked-example configuration:

| Request | Before | After |
|---|---:|---:|
| `GET /api/profile` | 242 ms | 12 ms |
| `GET /api/preferences` | 270 ms | 7.5 ms |
| `GET /api/sources` | 274 ms | 10 ms |
| `GET /api/source-maintenance` | 261 ms | 21 ms |

Discover's own requests (`/api/jobs`, `/api/home`, `/api/health`) were already
under 35 ms on this corpus.

## How to reproduce

All measurements use temporary copies of `config/` and a temporary database;
none touches `data/` or a personal database.

1. **Disk.** Sum file sizes with `os.walk` for `.venv`, the path printed by
   `uv cache dir` and the path printed by `uv python dir` (note that uv lists
   each Python twice, once as a short-name link, so count one of them). For the
   runtime-only environment, set `UV_PROJECT_ENVIRONMENT` to an empty temporary
   folder and run `uv sync --locked --no-dev --python 3.12`.
2. **Backend.** Build a demo database with
   `python -m career_agent.cli seed-demo --db <tmp>/data/demo.db --config-dir <tmp-config>`,
   then, with the temporary folder as the working directory (`serve --demo`
   always opens `data/demo.db` relative to it), start
   `python -m career_agent.cli serve --demo --config-dir <tmp-config> --port <free> --no-open`.
   Poll `/api/health` for the start-up time. Read `WorkingSet64`,
   `PrivateMemorySize64` and `TotalProcessorTime` from `Get-Process` for the
   interpreter, which is the child of the virtual environment's `python.exe`.
3. **Requests.** Construct `JobsApi` over the same temporary database and time
   `handle_api("GET", path, {}, {})` five times after one warm-up call; report the
   median.
