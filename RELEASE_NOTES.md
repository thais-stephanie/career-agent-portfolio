# v0.1.0-alpha.2
Career Agent Alpha with Resume Tailor Beta. New public history from a curated tree; no development or research history is imported.

- One Python 3.12 installation, one launcher and an included Tailor frontend.
- Resume Tailor Beta opens from the sidebar and from job details, with explicit description copying.
- Separate evidence stores, separate scores, no automatic evidence confirmation or synchronization.
- English, Brazilian Portuguese and Spanish getting-started guides, synthetic screenshots, privacy inventory and license boundaries.
- Default Tailor provider none, local request boundary and isolated personal/demo directories.

The earlier annotated v0.1.0-alpha.1 remains unchanged in the development repository and points to b16a273772d5f8666fbc15eeba37dd99dab4912c. It had no GitHub Release at preparation time. The old 7,246-test checkpoint is historical and is not the result of this build. See docs/VALIDATION.md for this edition's measured gate.

## Limits
Alpha remains a local single-user application. Source availability and language coverage vary. Resume Tailor is functional Beta, with user review required. PDF and rendered page counts require local Word or LibreOffice. No shared profile synchronization exists. Windows is the release-tested platform; first installation needs internet.

## Build identity
The source archive and Windows archive are generated from the validated tag. BUILD_ID expands to that exact commit using git export-subst. SHA256SUMS.txt is supplied with release assets; the release body names the validated commit. The repository stores the substitution marker to avoid a self-referential commit hash.
