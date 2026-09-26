# Release validation: v0.2.0-beta.1

Validated on Windows 11 (x64, 16 GB RAM) with uv-managed Python 3.12, Chrome
for the browser tests, and the included Resume Tailor frontend. No live AI
provider was called, no job source was contacted and no private data was used.
The results below belong to the release commit; the release's validation.json
names it.

There is no continuous integration. The gate runs on one computer, one suite
at a time: the unit, integration and browser suites and the Tailor frontend
exhausted memory when an earlier gate ran them together.

## Measured test inventory

| Suite | Passed | Skipped |
|---|---:|---:|
| Career Agent unit | 7,001 | 6 |
| Career Agent integration | 1,656 | 1 |
| Career Agent browser (60 acceptance + 310 others) | 370 | 0 |
| Resume Tailor Python | 22 | 0 |
| Resume Tailor frontend, seven test files | 38 | 0 |
| **Total** | **9,087** | **7** |

No test failed on the release commit. An earlier run on the release branch
found one problem, a 125-column line in the Portuguese interface text that the
frontend checker rejects (reported by two unit tests); the line was wrapped and
every suite above was run again.

The seven skips:

- Five need private material that the public edition does not ship: three
  programme ledgers (`test_budget_ledger_concurrency.py`,
  `test_glm_recheck_authorization.py` twice), one corpus
  (`test_glm_recheck_authorization.py`) and the golden corpus
  (`test_prompts.py`).
- `test_protected_paths.py` checks the maintainer's operational data folder
  and skips in a fresh clone, which has none. The release gate ran in a fresh
  worktree, like a public checkout; on the maintainer's own checkout this test
  runs and passes (7,002 unit tests).
- One integration persona (`test_onboarding_personas.py`) uses words that
  legitimately overlap the vocabulary an owner-leak assertion looks for; its
  other onboarding tests run.

None is counted as passing. Four corpus and experimental test modules are
omitted together with their private dependencies: test_golden, test_screen,
test_match_benchmark and test_match_context. They are not presented as
validated public benchmarks.

## Static checks

Ruff and the Ruff formatter pass for Career Agent (603 files) and Resume
Tailor (72 files). Mypy passes on 256 Career Agent source files. The frontend
checker passes on 39 modules and two pages. The punctuation check passes on 740
project-authored files. `uv lock --check` passes at the root and in
`companion/resume-tailor`. `npm ci` installs the Tailor frontend from its lock
file, `npm audit` reports zero vulnerabilities, and the Tailor build reproduces
the committed bundle byte for byte. Exact commands are in
[CONTRIBUTING.md](../CONTRIBUTING.md).

## Installation checks

- **Windows ZIP, cold.** The release ZIP was extracted into a new folder whose
  path contains spaces. uv and Python were removed from PATH and uv's cache and
  Python folders pointed into the test folder, so nothing was preinstalled.
  `Start-Demo.cmd` downloaded uv (checksum verified), Python 3.12 and the
  locked libraries and was ready in 38.8 seconds.
- **Demo.** Both addresses (127.0.0.1:8765 and :8766) returned 200. The demo
  held 21 synthetic postings and the synthetic candidate Alex Morgan.
- **Personal mode.** The first start created `data/profiles.json`, the first
  profile's database and the shared catalogue, with zero jobs. A second
  launcher on the same ports exited with the port-conflict message. Ctrl+C
  closed both ports with exit code 0 and no leftover process; a restart was
  ready in 1.6 seconds.
- **Network.** While the demo and personal mode ran, the app processes had no
  TCP connection to anything but 127.0.0.1.
- **Containment.** Nothing was written outside the install folder and the
  test's own uv folders; `~/.resume-tailor` was not touched.
- **Side by side.** Personal mode on 8765 and the demo on 8875 ran together.
- **Terminal path.** The macOS and Linux commands in
  [INSTALL.md](INSTALL.md) were run literally on Windows from the source
  archive: extract, `uv sync --locked --no-dev --python 3.12`, the demo, and
  both backup commands. This checks the commands, not those platforms.
- **Update from v0.1.0-alpha.2.** The published alpha.2 ZIP was installed and
  a synthetic posting imported. Its `data` and `config\*.local.yaml` were
  copied into the new version's folder; the new launcher migrated the database
  and kept the posting, and `career-agent integrity` reported nothing broken.
- **Execution policy.** Windows refused `.\Start-Career-Agent.ps1` from a
  ZIP that was downloaded and not unblocked, under the RemoteSigned policy, and ran
  `.\Start-Career-Agent.cmd` with the same options. The docs and the launcher's
  advice use the `.cmd` file.
- **Disk.** About 360 MB in the Career Agent folder, plus about 300 MB of
  Python and download cache that uv keeps outside it.

Not checked for this release: double-clicking the launcher in File Explorer
(the tests started the same `.cmd` files from a process), automatic browser
opening (runs used `--no-open`), macOS, Linux, Windows 10, Windows on ARM,
LibreOffice PDF export, and live hosted AI providers.

## Public audit

gitleaks 8.30.1 found no leaks in the extracted Windows ZIP or in the release
branch history. A deterministic scan of the ZIP found no databases, `.env`,
`*.local.yaml`, `profiles.json`, backups, owner paths, work-email domains or
key-shaped strings, and no text or EXIF chunks in any PNG. None of the owner's
local search phrases appears in any file this release changed. The four
screenshots were captured from demo mode and an empty first run in a temporary
install. See [PUBLIC_AUDIT.md](PUBLIC_AUDIT.md).
