"""Browser acceptance tests.

A package rather than a bare directory because `tests/integration/conftest.py`
already resolves fixtures through `tests.*` imports, and pytest's rootdir-based
module naming needs the same anchor here.
"""
