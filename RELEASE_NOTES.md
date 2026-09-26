# v0.2.0-beta.1

Career Agent Beta with Resume Tailor Beta (0.2.0b1), for people who run it on
their own computer. It replaces v0.1.0-alpha.2. To update, extract it into a
new folder and copy the old folder's `data`, `config\*.local.yaml` and both
`.env` files into it ([how](docs/INSTALL.md#updating-to-a-new-version)); the old
folder is not changed.

## New

- **Local profiles.** One installation holds several people. Each profile has
  its own settings, role anchors, CV, evidence, scores, applications and Resume
  Tailor workspace, and a profile menu in the side bar switches between them.
  Profiles are not accounts or a security boundary.
- **Shared job catalogue.** Public postings are stored once in
  `data/shared/catalogue.db` and read by every profile. Which profile's search
  found a posting stays private to that profile. `career-agent backup
  --catalogue --profile NAME` backs it up separately from a profile.
- **Role anchors.** Optional job titles you have in mind, with a role-family
  planner, steer targeted searches. They add no Search Fit points.
- **Semantic matching (optional).** DeepSeek, or your own signed-in Claude Code
  or Codex, reads selected postings and quotes which parts of your search they
  support. Career Agent checks every quote and does the scoring. DeepSeek runs
  show an estimate and stop at a budget.
- **LinkedIn via JobSpy (experimental).** Off by default and turned on per
  profile after a warning. Bounded searches from role anchors and work phrases,
  no sign-in, and a refusal ends the run and waits a day.
- **Local model reading.** `qwen3:4b` through Ollama reads one posting in the
  background, streamed and cancellable, with a six-minute limit. Only local
  Ollama addresses are accepted, and the reading never changes Search Fit.
- **Source health.** Settings & Sources shows each source's last success and
  whether it is due (a day old), stale (three days), refused or failing.
  **Refresh due sources** runs only the due ones.
- **GOOD + STRONG export.** Table view saves the current GOOD and STRONG
  results as a CSV file.
- **Coverage.** An ATS board registry, employer board discovery from
  aggregator postings, and Himalayas searches built from your search intent.
- **Install guide.** [docs/INSTALL.md](docs/INSTALL.md) covers installing with
  Claude Code or Codex, installing by hand on Windows, macOS and Linux, backups,
  updating, troubleshooting and uninstalling.

## Changed

- **Career Agent and Resume Tailor share the active profile.** Resume Tailor
  opens on the posting you came from, lists your tracked jobs and changes a
  status through Career Agent. Confirmed Career Profile statements move into
  it only when you ask. Base resumes can be uploaded as PDF, Word or Markdown.
- **"To apply" is merged into "Interested".**
- **Seniority.** A posting that does not state a level is scored as mid-level
  (Pleno), the same as a stated mid-level posting.
- **Posting completeness** (formerly Posting detail) is reported beside Search
  Fit and never changes it.
- **Discover and onboarding.** A search box in the toolbar, a simpler table,
  a new side bar and a ten-step setup that saves each answer as you go.
- **Career Profile and Evidence.** A CV is read as companies, roles and dates
  before any statement is proposed, and an import can be archived or deleted.
  Profile has one Experience view edited in place; Evidence shows projects,
  achievements and certifications as cards; Documents holds the imports.
  Skills can be entered several at a time and are read from more CV layouts.
- **Work model** preferences (remote, hybrid, on-site) now count in Search Fit
  and can narrow Discover.
- `career-agent cv-import --accept-all` was removed: each proposal is confirmed
  one at a time, as in the app.
- The launcher's port-conflict advice names `Start-Career-Agent.cmd`, because
  Windows blocks the `.ps1` file from a downloaded ZIP, and suggests the demo
  or another profile, since one profile cannot be open twice.
- The first-run privacy line says "Your answers stay on this computer" instead
  of "Everything stays on this computer", which was not true once collection or
  an optional provider is used.
- The source archive now extracts into a `Career-Agent-v0.2.0-beta.1` folder.

## Fixed

- Local HTTP server: a request refused before its body was read left that
  body on the open connection, where it was parsed as a second request that
  skipped the Host, Origin and JSON checks. Such a connection now closes, and
  ambiguous request lengths are refused.
- Eligibility: a posting whose stated region contains your country, but which
  you did not select as a target, is now unknown instead of "not eligible".
- Configuration files are parsed with libyaml's safe loader.
- A Search Fit recalculation that stopped because its process died says so and
  can continue.
- An installation opened through the launcher now migrates its database to the
  current schema before starting.
- Resume Tailor refuses upload files whose extension, type and content
  disagree, and duplicate base resumes are cleaned up.
- Resume Tailor's error messages are shown in English or Brazilian Portuguese.

## Known limitations

- Windows 11 is the only platform tested for this release.
- A manual review found GOOD Search Fit results supported mainly by generic
  work phrases. Scoring is unchanged until an evidence-quality benchmark
  exists.
- LinkedIn may refuse or rate-limit the experimental source.
- `career-agent backup` needs `--profile NAME` (also with `--catalogue`);
  without it, it looks for a database at an older default location and stops
  with "no database".
- Resume Tailor's interface is in English.
- PDF export needs Word or LibreOffice. No live hosted AI provider was called
  in release testing.

## Validation and build identity

See [docs/VALIDATION.md](docs/VALIDATION.md) for the test counts and the
install checks. The Windows ZIP and the source archive are made with
`git archive` from the tagged commit; `BUILD_ID` in each holds that commit, and
`SHA256SUMS.txt` is attached to the release.
