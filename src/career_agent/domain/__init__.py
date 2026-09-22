"""Pure domain layer.

Nothing in this package may import sqlite3, httpx, anthropic, or any module from
providers/, storage/, llm/ or pipeline/. A test enforces this, and it is what
lets the same code survive the eventual move to PostgreSQL and a web frontend.
"""
