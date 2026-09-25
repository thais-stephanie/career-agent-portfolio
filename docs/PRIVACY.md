# Privacy model
Reviewed for the integrated public edition, 2026-09-22. This describes implemented paths, not a promise that every feature is offline.

## Local storage
The shared launcher keeps personal Career Agent data in data/personal.db (SQLite): postings, provider payloads, search scores, tracking, notes, review state and evidence. User settings live in config/*.local.yaml; atomic settings saves can leave a .backup file. Career Agent reads uploaded PDF/DOCX bytes in memory, extracts text and persists proposals/evidence and provenance, not the original CV file. Extracted personal text is still personal data.

Resume Tailor uses data/tailor-personal under the shared launcher. It stores candidate profiles, base resumes, original uploaded sources, extracted suggestions, reviewed evidence, conflicts, drafts, applications, generated text and export artifacts. Original sources are retained in each candidate's sources/files directory. Optional provider caches are scoped to that mode's runtime/cache. Deleting a source is not automatically deleting confirmed evidence; the source removal workflow explains affected evidence.

Demo uses data/demo.db, data/demo-config and data/tailor-demo. It never reads the personal workspace or automatically imports a CV. It forces the AI provider to none. Starting the companion's standalone CLI separately can instead use its normal home (~/.resume-tailor); use the root launcher for the distributed experience.

Local profiles (docs/MULTI_PROFILE.md) keep each person's database, private settings and Resume Tailor workspace in separate files, so one person's CV, evidence, scores and applications never appear in another's profile. They are not accounts and not a security boundary: anyone using the same operating-system account can read every profile's files. A profile's backup holds only that profile.

Browser local storage holds presentation preferences such as language, filters and selected candidate. There is no application telemetry or analytics SDK in either product. Data is not encrypted by the application. Operating-system accounts, disk encryption and backups control access to local files.

## Network paths
| Action | What can leave the computer |
|---|---|
| First installation | Requests to GitHub for uv and Python distributions, and package indexes for locked Python dependencies. These services see normal connection metadata. |
| Browsing existing local data / deterministic tailoring | Requests only to the two local servers; no AI provider is needed. Fonts and frontend assets are bundled. |
| Collecting or discovering jobs | Requests to configured job boards and employer sites, including query parameters and ordinary connection metadata. Career Agent does not attach your CV or confirmed evidence to collection. Source permissions and request limits still apply. |
| Targeted searches and employer boards | A Himalayas refresh also runs a bounded number of searches (24 by default): each sends one search phrase, taken from your role anchors or your work phrases, and one country code from your eligible markets. Nothing else from your search settings, and never your CV, profile or evidence. Employer board discovery asks Ashby, Greenhouse and Lever whether a board exists under a name derived from an employer already seen on an aggregator; it sends that guessed board name only. |
| LinkedIn via JobSpy (experimental, off by default) | Only after the person switches it on for their own profile in Settings & Sources, having read that LinkedIn restricts automated collection. Each run sends LinkedIn's public guest job search a bounded number of short searches (one search phrase from role anchors or work phrases, and one place each; up to three result pages each) and then reads a limited number of public posting pages, from this computer's own connection, with the browser-like headers python-jobspy sends. It never signs in and never asks for or stores a LinkedIn password, cookie or session. Nothing about the CV, profile or evidence is sent. |
| Opening an employer link | Your browser visits the external website. Its privacy policy, cookies and browser settings apply. |
| Career Agent semantic matching (optional) | Only when you start a semantic run: your search intent phrases (the work, tools and other signals you want) and the title and text of each selected posting go to the provider you chose or Auto chose: DeepSeek with your API key, or your own signed-in Claude Code or Codex using your subscription. Never your Career Profile, evidence, CV files, application history, other postings or the database. Each run is capped; a metered run shows an estimate and stops at its budget. The DeepSeek key lives only in the root `.env`, never in the database or an API response. Demo mode never uses it. See SEMANTIC_MATCHING.md. |
| Career Agent Ollama action | Posting text goes to the explicitly selected local Ollama service, normally port 11434. Its client refuses non-loopback endpoints. Model downloads, if needed, are separate operations. |
| Resume Tailor optional AI | Provider configuration and a requested AI operation can send job descriptions and selected evidence/context to OpenAI-compatible, Anthropic or Ollama endpoints. A configured Ollama URL can be remote; do not assume the name means local. Default provider is none. An existing API key alone does not activate a provider in this edition. |
| Advanced Career Agent extraction CLI | Explicit execution flags permit sending selected posting text to configured hosted providers. This is not part of normal browsing or the release tests. |

The release tests make no live AI call. The semantic matching benchmark did, once, on the maintainer's machine (SEMANTIC_MATCHING.md). Do not use the phrase 'nothing leaves your computer' to describe all configurations.

## Handoff and provenance
Copy job description writes only the displayed posting text to the operating-system clipboard, after a click. Clipboard history, cloud clipboard and other applications may retain it. The app does not clear your clipboard. Opening Resume Tailor transfers no profile or evidence and adds no job text to a URL. You paste the description yourself. Scores and evidence stores remain separate. Generated text is never automatically confirmed as Career Evidence.

## Exports and backups
Markdown and DOCX are generated locally. PDF and actual page counts use a locally installed Word or LibreOffice renderer when available, with temporary local files; without a renderer the application reports unavailability. The application does not use a cloud PDF conversion service. Operating-system or office-suite sync settings are outside the app's control.

Tailor candidate backup is a ZIP and can include original source documents, evidence and applications. Career Agent backup includes its database and private configuration, excluding credential files. Manual copies of data/ and config local files are also private. Export and backup files are not encrypted by the application. Never attach them to a public issue without reviewing their content.

## Local HTTP protections
Default addresses: Career Agent http://127.0.0.1:8765 and Tailor http://127.0.0.1:8766. A custom main port uses the next port for Tailor. The launcher binds both before opening a browser and refuses conflicts. Career Agent enforces loopback, Host/Origin checks and JSON mutations. Tailor rejects non-local Host, mismatching Origin and cross-site requests; form uploads require same-origin browser headers. No permissive CORS policy is enabled. These services are for a trusted local user, not an authenticated multi-user or internet-facing deployment. A malicious local process can access them.
