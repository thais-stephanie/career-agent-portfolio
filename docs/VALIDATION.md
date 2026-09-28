# Release validation: v0.2.0-beta.2

Validated on Windows 11 (x64, 16 GB RAM) with uv-managed Python 3.12, Chrome
for the browser tests, and the included Resume Tailor frontend. No live AI
provider was called, no job source was contacted and no private data was used
in any test below. The release's validation.json names the commit.

The test gate ran on commit `aaecaef` in a fresh worktree, like a public
checkout, and its logs record that commit. The release commit differs from it
only in Markdown documentation, including the test counts and badges in the
three READMEs; no code, test, script or configuration file differs. There is no continuous integration. The gate runs on one computer,
one suite at a time: the unit, integration and browser suites and the Tailor
frontend exhausted memory when an earlier gate ran them together.

## Measured test inventory

| Suite | Passed | Skipped |
|---|---:|---:|
| Career Agent unit | 7,018 | 6 |
| Career Agent integration | 1,656 | 1 |
| Career Agent browser | 371 | 0 |
| Resume Tailor Python | 22 | 0 |
| Resume Tailor frontend, seven test files | 38 | 0 |
| **Total** | **9,105** | **7** |

No test failed on the gated commit. The v0.2.0-beta.1 gate counted 7,001 unit
and 370 browser tests.

The seven skips:

- Five need private material that the public edition does not ship: three
  programme ledgers (`test_budget_ledger_concurrency.py`,
  `test_glm_recheck_authorization.py` twice), one corpus
  (`test_glm_recheck_authorization.py`) and the golden corpus
  (`test_prompts.py`).
- `test_protected_paths.py` checks the maintainer's operational data folder
  and skips in a fresh worktree, which has none.
- One integration persona (`test_onboarding_personas.py`) uses words that
  legitimately overlap the vocabulary an owner-leak assertion looks for; its
  other onboarding tests run.

None is counted as passing. Four corpus and experimental test modules are
omitted together with their private dependencies: test_golden, test_screen,
test_match_benchmark and test_match_context.

## Static checks

Ruff and the Ruff formatter pass for Career Agent (603 files) and Resume
Tailor (72 files). Mypy passes on 256 Career Agent source files. The frontend
checker passes on 39 modules and two pages, the localisation check on 35
modules, and the punctuation check on 740 project-authored files.
`uv lock --check` passes at the root and in `companion/resume-tailor`. `npm ci`
installs the Tailor frontend from its lock file, `npm audit` reports zero
vulnerabilities, and the Tailor build reproduces the committed bundle except
for line endings. Exact commands are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Update from v0.2.0-beta.1

The published v0.2.0-beta.1 ZIP was installed in a new folder whose path
contains spaces, with no uv or Python on PATH and uv told to use only Pythons
it installs itself, so it downloaded Python 3.12 into the test folder. It was
started once. Synthetic data was then added with v0.2.0-beta.1's own code:
three invented postings, a Search Fit configuration with invented phrases, one
stored semantic finding marked partial, one status, one note and one confirmed
statement. Its rescore wrote those scores under schema 11. That folder had no
`.env` file to copy.

Following [INSTALL.md](INSTALL.md#updating-to-a-new-version), the `data`
folder and `config\*.local.yaml` were copied into the extracted
v0.2.0-beta.2 folder, and its launcher was started.

- The list showed the schema 11 scores, and the notice above it read "3 scores
  were calculated by an earlier version of Career Agent and may be out of
  date" with a **Recalculate search fit** button.
- Pressing the button rescored the three postings from their stored readings
  (`jobs_replayed` 3, none read again, 0 errors). The first check, half a
  second later, found no older score and the notice gone.
- The posting whose only work evidence was the partial finding went from 47 to
  42, because its tools were held at half. The other two did not change.
- The status, the note, the confirmed statement, the stored finding, the
  postings and the configuration files were identical before and after.
- During the recalculation the Career Agent (`python.exe`) and `uv.exe`
  processes had no connection to anything but 127.0.0.1. Two connections to
  GitHub belonged to the launcher's `powershell.exe`, left from downloading uv
  at setup.
- After a restart, no score from an earlier version remained.

## Installation checks

- **Windows ZIP, cold.** The release ZIP was extracted into a new folder whose
  path contains spaces. uv and Python were not on PATH, uv's cache and Python
  folders pointed into the test folder, and uv was told to use only Pythons it
  installs itself, so nothing was preinstalled. `Start-Demo.cmd` downloaded uv
  (checksum verified), Python 3.12 and the locked libraries and was ready in
  38 seconds.
- **Demo.** Both addresses (127.0.0.1:8765 and :8766) returned 200. The demo
  held 21 synthetic postings.
- **Personal mode.** The first start created `data/profiles.json`, the first
  profile's database and the shared catalogue, with zero jobs, and was ready
  in 4.3 seconds. A second launcher on the same ports exited with the
  port-conflict message.
- **Ctrl+C and restart.** Ctrl+C sent to the launcher's console stopped the
  Career Agent and uv processes and freed both ports, for the demo and for
  personal mode. A restart was ready in 2.8 seconds, and after the last
  Ctrl+C no process from the install folder remained.
- **Network.** While the demo and personal mode ran, the Career Agent
  processes had no TCP connection to anything but 127.0.0.1.
- **Containment.** `~/.resume-tailor`, the user's uv folder and the user's
  uv-managed Pythons were not modified.
- **Disk.** 361 MB in the Career Agent folder, plus 303 MB of Python and
  download cache that uv keeps outside it.

Not repeated for this release, and last checked for v0.2.0-beta.1: the macOS
and Linux commands run from the source archive, the demo and personal mode
side by side, the update from v0.1.0-alpha.2, and the Windows execution-policy
refusal of the `.ps1` file. Not checked at all: double-clicking the launcher
in File Explorer (the tests started the same `.cmd` files from a process),
automatic browser opening, macOS, Linux, Windows 10, Windows on ARM and live
hosted AI providers.

## Public audit

gitleaks 8.30.1 found no leaks in the extracted Windows ZIP (13.65 MB) or in
the 98 non-merge commits in the history of the gated commit `aaecaef`. A
deterministic scan of the ZIP found no databases, `.env`, `*.local.yaml`,
`profiles.json`, backups, runtime folders or key-shaped strings. The only
owner-identity matches are the two negative privacy tests that assert those
strings never appear, unchanged since v0.2.0-beta.1. None of the maintainer's
local search phrases or role anchors appears in any file changed since
v0.2.0-beta.1. No image changed. See [PUBLIC_AUDIT.md](PUBLIC_AUDIT.md).
