# Third-party notices
Reviewed 2026-09-22 for the integrated public edition.

## Component licenses and adapted material
Career Agent code is MIT, under the root LICENSE. The complete Resume Tailor implementation is reused under Apache-2.0 in companion/resume-tailor, pinned to upstream eb22205d06de2975191fb06961b4b0677d4238d8. Its LICENSE is preserved; NOTICE records the integration changes. The root MIT license does not relicense that component or its fonts. Apache attribution and modification notices remain with distributions.

Career-Ops public offers protocol patterns informed the Recruitee adapter (providers/recruitee.mjs, reviewed 2026-09-10). Its MIT notice remains in licenses/career-ops-MIT.txt. The earlier inspection of Get on Board and Torre at fe06051 informed endpoints, parameter shapes and pagination behavior; it did not copy their JavaScript implementation. Protocol knowledge, adapted material and product inspiration are separate categories.

## Presentation references
The README organization was informed by career-ops-hq/career-ops, alibaba/open-code-review, affaan-m/ECC, DietrichGebert/ponytail and JustVugg/colibri. No text, code, screenshots, logos or visual assets were copied from those references for this edition.

## Dependencies
Python packages are installed by uv from uv.lock. Career Agent uses pydantic, PyYAML, typer, python-ulid, python-dotenv, httpx, tenacity, google-genai, openai, pypdf and python-docx. Tailor additionally uses FastAPI, uvicorn and python-multipart. These packages retain their own licenses and distribution metadata. The provider SDKs are not a claim that hosted AI is required.

Tailor's compiled frontend contains React and React DOM (MIT); source, lockfile and licenses/react-MIT.txt and licenses/react-dom-MIT.txt are included. Vite, TypeScript, Vitest and Testing Library are build/test tools, not software the end user must install. Their licenses remain with the npm packages. Career Agent's own frontend uses browser ES modules without a framework. The bundled Tailor frontend makes a blanket 'no third-party frontend code' claim incorrect.

uv is downloaded at installation time from its official release, with its published SHA256 checked. Python is then managed by uv. They are not bundled into the source repository or relicensed here.

## Fonts and assets
Career Agent ships Fraunces, Outfit, JetBrains Mono and Press Start 2P under SIL OFL 1.1. Their full notices are in src/career_agent/web/static/fonts/licences. Tailor preserves frontend/public/fonts/OFL-LICENSES.txt and the same notice in its built static bundle. Names and copyright contacts in required license texts are retained intentionally.

Public README images are newly captured from synthetic demo data. No personal CV, real application history or owner screenshot is used. Career Agent and Tailor pixel icons were replaced with original geometric bitmap glyphs under the root MIT license; supplied JOI3 art is not redistributed.

## Test data and names
The demo jobs and Tailor candidate are invented. Adapter fixtures include public protocol response samples for regression testing; no private production corpus or golden research dataset is distributed. Technology and provider names are used to identify interfaces, not to claim affiliation or endorsement.
