"""Reading a CV, on this machine, without believing it.

Two halves that must not merge. `extract` turns a file into characters and
decides nothing; `propose` says what those characters appear to claim and
confirms nothing. A fact becomes a `VerifiedClaim` only when a person accepts
a proposal, and `propose.to_claim` is the single place `verified=True` is set.
"""
