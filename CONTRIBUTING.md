# Contributing
Open Start, type PowerShell and open Windows PowerShell. On macOS open Applications → Utilities → Terminal; on Linux open Terminal. Install uv, change to the extracted source folder with `cd "folder path"`, then:

```powershell
uv sync --locked --python 3.12
uv run python -m pytest tests/unit tests/integration
uv run python -m pytest tests/browser
uv run python -m pytest companion/resume-tailor/tests
uv run ruff check src tests scripts
uv run ruff check --config companion/resume-tailor/pyproject.toml companion/resume-tailor/src companion/resume-tailor/tests
uv run mypy src
node scripts/frontend_check.mjs
cd companion/resume-tailor/frontend
npm ci
npm test
npm run build
```

The release gate runs these on one Windows 11 computer with 16 GB of RAM, one
suite at a time: running the unit, integration and browser suites or the
Tailor frontend build at the same time exhausted memory. There is no
continuous integration. Before a release also run `uv lock --check` at the
root and in `companion/resume-tailor`.

Python tests use synthetic or public protocol fixtures, never local configuration or a production database. Browser tests need Chrome or Edge; skipped tests must be reported as skipped. Use Node 24 for frontend development; users of the Windows ZIP do not need Node. Rebuild Tailor's bundled interface after frontend edits and preserve its font licenses and Apache modification notices.

Do not commit personal CVs, .env, local settings, databases, exports, caches or backups. The release gate audits the actual tracked tree and distribution, not only .gitignore. Preserve deterministic scoring, source permission checks and evidence provenance. Keep Search Fit separate from Tailor Match. No live model calls are necessary for this test gate.

See docs/VALIDATION.md for release results and docs/ARCHITECTURE.md for the integration decision.

After the release gate passes and the reviewed tree is committed, run
`uv run python scripts/package_release.py`. It archives the commit, verifies
the expanded BUILD_ID and checks the archive inventory, then writes the two
archives, SHA256SUMS.txt and release.json into out/release. Do not package a
working folder containing user data.
