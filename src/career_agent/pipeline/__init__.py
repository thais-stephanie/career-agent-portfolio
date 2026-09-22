"""Stage orchestration.

Each stage reads rows in one state and writes the next, so every stage is
resumable and independently runnable.
"""
