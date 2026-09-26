# Public distribution audit

Scope: the explicit staged public tree and its newly created history. Development repositories stay private. No old Git objects, branches, tags or commit authors are imported.

| Category | Status | Treatment |
|---|---|---|
| Development history, research branches and sealed holdouts | PASS | Excluded entirely. Fresh repository initialized from selected files. |
| .env, local settings, backups, databases, CVs, exports and production data | PASS | No files from these private roots copied. Runtime directories are excluded from the distribution inventory. |
| Golden datasets, evaluation outputs, agent outputs, research and operational documents | PASS | Excluded; only synthetic demo inputs and product regression fixtures are retained. |
| Personal names and absolute paths | PASS | Fixture candidate names replaced with synthetic identities. Public repository ownership links and negative privacy-test markers are intentional. Generic example paths are not machine paths. |
| Secrets and credentials | PASS | Gitleaks 8.30.1 reports zero leaks on the selected tree; deterministic scans report zero forbidden paths. Signed asset URLs, request tokens and opaque pagination tokens removed from protocol fixtures. Final result recorded in VALIDATION.md. |
| Protocol response fixtures | PASS | Public vendor responses retained for connector regression tests, separate from the synthetic demo. They are not the private corpus or personal applications. Remaining vendor HR contacts are public posting text, not candidate contacts; signed URLs and request tokens were removed. |
| Assets | PASS | README captures show synthetic jobs or empty first-run state and Alex Morgan. Decorative icons replaced with original geometric glyphs. Fonts and compiled dependency license notices retained. |
| Licenses | PASS | Root MIT, vendored Tailor Apache-2.0, OFL fonts, React MIT and Career-Ops notice retain explicit boundaries. |
| Commit metadata | NOTE | The alpha.2 history used the GitHub noreply address and imported no old parents. Commits merged after alpha.2 carry the maintainer's configured Git identities in author, committer and co-author fields. These are the project's own contributors, not candidate data; the history is published and was not rewritten. |
| Tags and release assets | PASS | alpha.1 is untouched in development. alpha.2 and v0.2.0-beta.1, with their assets, are created only after the integrated gate passes on the commit that is tagged. |

## Exclusion policy
The private preparation manifest records each excluded path locally. Public categories omit unnecessary personal filenames. The export admits tracked reviewed files only; .gitignore is a convenience, not proof. No private Git history will be pushed. An explicit source commit and BUILD_ID identify the release.

Corpus-bound tests for private golden screening and experimental match benchmarks are excluded with those datasets. Product, provenance and security tests remain. No excluded benchmark is presented as passing. See VALIDATION.md for the actual new counts.

## Observed exclusions
The private inventory contained data files, operational outputs, backups, env
files and a local configuration. None is admitted into the release. The public
export also excludes .claude material, agent transcripts, research plans,
sealed holdouts, golden datasets, evaluation outputs and the internal
company_sourcing list. Four private-data test modules leave with those inputs;
the empty public company-candidate template has its own regression check.

Only the new main history and the new annotated release tag are published.
Existing development repository visibility remains PRIVATE. Original private
filenames and raw scan/test logs are intentionally not attached to the release.
Required copyright identities, GitHub ownership links, generic paths,
example.test emails and negative privacy-test markers were reviewed and retained.

## Distribution check
The packaging script accepts a clean commit, uses git archive, verifies BUILD_ID
and rejects runtime roots and private file types inside the ZIP. A final smoke
installation uses that ZIP in a fresh path containing spaces. The release
manifest and validation.json bind the audit and test outcomes to the exact
commit and archive hashes. The older alpha.1 tag stays in development at its
original commit; it is neither moved nor imported into this new history.
