# Synthetic CV fixtures

Every person, company, address and figure in these files is invented. They exist
to prove how the CV reader handles real layouts: Markdown headings and
emphasis, one company with several promotions, Portuguese headings with
accents, overlapping roles, missing dates, duplicate bullets and malformed
Markdown.

Date ranges in real CVs use en and em dashes. This repository's own text may
not contain those characters, so the files write `{EN}` and `{EM}` instead, and
`tests/support_cv.py` substitutes the real characters when a test loads them.
