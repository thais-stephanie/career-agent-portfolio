"""Local candidate workspaces: one isolated directory per candidate under
``~/.resume-tailor`` (see ``store.py``). "Workspace" is an internal architecture
term; the product UI says "Candidate"."""

from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceError, WorkspaceStore

__all__ = ["CandidateWorkspace", "WorkspaceError", "WorkspaceStore"]
