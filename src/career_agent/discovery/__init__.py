"""Intent-aware retrieval: what to ask query-based sources for, and where.

Two lanes feed the corpus. The EXPLORATORY lane is every broad collector the
product already had (ATS boards, feeds), which finds relevant work whatever its
title. The TARGETED lane asks query-capable sources (Himalayas search,
LinkedIn through JobSpy) for the person's role anchors, their generated
role-family aliases and their stated work, in each market scope they would
accept. Both lanes land in one corpus, deduplicated, and eligibility and Search
Fit then treat every posting the same way.

Nothing here decides eligibility or fit. A query that said "Worldwide" proves
nothing about where a posting hires; the posting's own words do.
"""
