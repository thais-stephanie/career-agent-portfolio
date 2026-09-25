"""Semantic interpretation of a posting against the person's Search Intent.

AI INTERPRETS. DETERMINISTIC CODE VALIDATES AND SCORES.

A provider reads one posting and says which of the person's intent items it
supports, quoting the posting. Nothing a provider returns is believed until
`gate.publish` has checked it: the shape, that every intent id was one we sent,
and that every quote is text the posting really contains. Only what survives
reaches `match.score`, which does all of the arithmetic. No provider ever
produces a number that becomes Search Fit.
"""
