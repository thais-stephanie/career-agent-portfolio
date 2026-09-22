# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Public test fixtures: everything is built from the synthetic demo candidate
(resume_tailor.workspace.demo). No private data, no network, no real renderer."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("RESUME_TAILOR_PAGE_RENDERER", "none")
os.environ.setdefault("RESUME_TAILOR_HOME", tempfile.mkdtemp(prefix="resume-tailor-public-tests-"))

import pytest
from resume_tailor.core.models import TailorOptions, TailorRequest
from resume_tailor.workspace import WorkspaceStore
from resume_tailor.workspace.demo import create_demo_candidate


@pytest.fixture(scope="session")
def store(tmp_path_factory) -> WorkspaceStore:
    return WorkspaceStore(tmp_path_factory.mktemp("home"))


@pytest.fixture(scope="session")
def demo_ws(store):
    return create_demo_candidate(store)


@pytest.fixture(scope="session")
def index(demo_ws):
    return demo_ws.load_index()


def request(jd_text: str, resume_id: str = "demo_systems_engineer", **opts) -> TailorRequest:
    return TailorRequest(
        jd_text=jd_text,
        resume_id=resume_id,
        target_profile=None,
        options=TailorOptions(use_llm=False, **opts),
    )


@pytest.fixture(scope="session")
def demo_run(demo_ws, index):
    from resume_tailor.core.pipeline import TailorService
    from resume_tailor.providers.llm.vendors import NoneProvider
    from resume_tailor.workspace.demo import DEMO_JD

    svc = TailorService(index, demo_ws.load_resumes(), demo_ws.load_profiles(), NoneProvider())
    return svc.run(request(DEMO_JD), "20260101T000000-abcdef")
