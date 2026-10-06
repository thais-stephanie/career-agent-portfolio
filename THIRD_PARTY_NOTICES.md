# Third-party notices
Reviewed 2026-09-22 for the integrated public edition.

## Component licenses and adapted material
Career Agent code is MIT, under the root LICENSE. Resume Tailor (Apache-2.0, upstream eb22205d06de2975191fb06961b4b0677d4238d8) was reused until v0.2.0-beta.2 and has been removed from the source tree. `src/career_agent/resume_doc/legacy_format.py` adapts its workspace models, draft rules and export file names to read the files it left; that module carries a modification notice, and Resume Tailor's LICENSE and NOTICE are kept in licenses/resume-tailor-Apache-2.0.txt and licenses/resume-tailor-NOTICE.txt. The root MIT license does not relicense that adapted code.

Career-Ops public offers protocol patterns informed the Recruitee adapter (providers/recruitee.mjs, reviewed 2026-09-10). Its MIT notice remains in licenses/career-ops-MIT.txt. The earlier inspection of Get on Board and Torre at fe06051 informed endpoints, parameter shapes and pagination behavior; it did not copy their JavaScript implementation. Protocol knowledge, adapted material and product inspiration are separate categories.

## Presentation references
The README organization was informed by career-ops-hq/career-ops, alibaba/open-code-review, affaan-m/ECC, DietrichGebert/ponytail and JustVugg/colibri. No text, code, screenshots, logos or visual assets were copied from those references for this edition.

## Dependencies
Python packages are installed by uv from uv.lock. Career Agent uses pydantic, PyYAML, typer, python-ulid, python-dotenv, httpx, tenacity, google-genai, openai, pypdf, python-docx and python-jobspy (MIT). python-jobspy brings pandas and numpy (BSD-3-Clause), beautifulsoup4, soupsieve, markdownify, six and pytz (MIT), python-dateutil (BSD/Apache-2.0), regex and tzdata (Apache-2.0), and tls-client (MIT), which ships prebuilt native libraries from the bogdanfinn/tls-client project. python-jobspy is used only by the experimental LinkedIn source, which is off by default; its LinkedIn scraper does not use tls-client. These packages retain their own licenses and distribution metadata. The provider SDKs are not a claim that hosted AI is required.

Career Agent's frontend uses browser ES modules without a framework, and the distribution contains no compiled third-party frontend bundle. uv is a build-time and install-time tool.

uv is downloaded at installation time from its official release, with its published SHA256 checked. Python is then managed by uv. They are not bundled into the source repository or relicensed here.

## Fonts and assets
Career Agent ships Lexend, Young Serif, Newsreader and JetBrains Mono under SIL OFL 1.1. Their full notices are in src/career_agent/web/static/fonts/licences. Names and copyright contacts in required license texts are retained intentionally.

README screenshots are captured from synthetic demo data. The hero and workflow banners are product illustrations adapted from supplied designs using the built-in image generation tool, with corrected product wording and geometric sparkle marks. Their fictional counts are not measured outcomes; they are not screenshots of the interface. See docs/assets/readme/ILLUSTRATIONS.md for their edit specifications. No personal CV, real application history or owner screenshot is used. Career Agent's pixel icons were replaced with original geometric bitmap glyphs under the root MIT license; supplied JOI3 art is not redistributed.

## Test data and names
The demo jobs and candidate are invented, and the legacy workspace fixture is synthetic. Adapter fixtures include public protocol response samples for regression testing; no private production corpus or golden research dataset is distributed. Technology and provider names are used to identify interfaces, not to claim affiliation or endorsement.
