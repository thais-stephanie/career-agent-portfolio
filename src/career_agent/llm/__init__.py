"""The LLM boundary.

Everything a vendor knows about lives under this package. `domain/` imports
nothing from here, and nothing outside `llm/anthropic.py` (or a sibling adapter)
imports a vendor SDK. A purity test enforces both.
"""
