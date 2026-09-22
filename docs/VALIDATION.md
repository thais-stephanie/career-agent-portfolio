# Integrated release validation

Validated on Windows with managed Python 3.12, Chrome, and the included Tailor
frontend. No live AI provider was called. The historical 7,246-test checkpoint
belongs to the earlier development build and is not this edition's result.

## Measured test inventory

| Suite | Passed | Skipped |
|---|---:|---:|
| Career Agent unit | 5,638 | 5 |
| Career Agent integration | 1,282 | 1 |
| Career Agent browser | 266 | 0 |
| Resume Tailor Python | 22 | 0 |
| Resume Tailor frontend, five test files | 24 | 0 |
| Unique passing tests | **7,232** | **6** |

These are consolidated results, not a sum that double-counts reruns. During
preparation, the full Career Agent run reported six failures: one private-list
expectation and five pagination failures caused by a sanitized fixture cursor
containing a forbidden period. The cursor was replaced with a valid synthetic
value; the public list test now requires an empty personal watchlist. All 89
tests in the affected files then passed. The release gate repeats the complete
Career Agent Python suite against the committed tree before tagging. The
release's validation.json records that final run and its exact commit.

The complete rerun finished with 6,920 passed, six skipped and zero failures.
Career Agent runtime, configuration and test inputs are unchanged by the final
companion license-notice and development-tool updates. Companion tests and its
build are rerun after those updates; the final archive is smoke-tested again.

Five skips concern deliberately absent private programme ledgers or golden
corpora. One integration persona intentionally overlaps a vocabulary checked
by an owner-leak assertion; its other onboarding tests still execute. None is
counted as passing. Four corpus/experimental test modules are omitted together
with their private dependencies: test_golden, test_screen, test_match_benchmark
and test_match_context. They are not presented as validated public benchmarks.

## Static checks

Ruff passes for Career Agent source, tests and scripts and for the vendored
Tailor source/tests. Mypy passes on 209 Career Agent source files. The frontend
checker passes on 29 modules and two pages. Tailor's TypeScript build and Vite
bundle pass. The npm audit reports zero vulnerabilities after updating Vitest
and its DOM assertions. The punctuation check passes, including the three READMEs.
Exact reproduction commands are in [CONTRIBUTING.md](../CONTRIBUTING.md).

## Installation and product checks

- Clean Windows directory with spaces, no uv or Python on PATH: uv bootstrap,
  managed Python, locked dependency installation and first database creation
  passed in 144.67 seconds. Two earlier bootstrap defects were corrected before
  this successful run. No administrator or manual Python installation was used.
- Both HTTP services returned 200. First personal use had zero jobs and no
  Tailor candidates. Demo created 21 synthetic jobs and Alex Morgan in separate
  storage; the personal job count remained zero.
- Port conflict produced a plain-language error. Ctrl+C closed both ports;
  restart reopened both services. A final smoke test repeats installation from
  the exact release ZIP, with its BUILD_ID and checksum checked.
- Real deterministic Tailor generation completed through the running HTTP
  server. Starting Word to verify pages can take tens of seconds. An interrupted
  generation should be run again after restart.
- Markdown and DOCX export passed. A real Word conversion produced a one-page
  PDF. The missing-renderer path returns an honest unavailable response while
  Word and Markdown remain usable. LibreOffice itself was not installed/tested.
- Regression tests cover a Salesforce gap, an edit claiming unsupported
  Salesforce experience, evidence-only export excluding that edit, unreviewed
  sources leaving confirmed evidence unchanged, backup roundtrip, candidate
  isolation, hostile Host/Origin and implicit AI activation prevention.

## Public audit and limits

Gitleaks 8.30.1 found no leaks in the selected distribution tree. Deterministic
inventory found no forbidden private paths. Final history and archives are
scanned again before publication. Screenshots were visually reviewed and their
PNG chunks contain no EXIF or text metadata. Static images were chosen for a
readable and repeatable tour; no GIF is required to run the product.

The release covers Windows, local single-user operation and deterministic
tailoring. It does not claim a live-provider benchmark, current availability of
every external source, cloud synchronization, a macOS/Linux smoke test, or
shared evidence between the modules. See [PUBLIC_AUDIT.md](PUBLIC_AUDIT.md).
