"""The privacy boundary is verified, not assumed.

.gitignore only ignores files git is not ALREADY tracking. If a real profile
were ever committed once, adding the pattern afterwards would not remove it.
So these tests ask git what it actually tracks.
"""

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

REQUIRED_IGNORE_PATTERNS = [
    "config/*.local.yaml",
    ".env",
    "data/",
]


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _git_available() -> bool:
    try:
        _git("rev-parse", "--git-dir")
    except (OSError, subprocess.CalledProcessError):
        return False
    return True


def test_gitignore_declares_every_required_pattern() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    lines = {line.strip() for line in gitignore.splitlines()}
    missing = [p for p in REQUIRED_IGNORE_PATTERNS if p not in lines]
    assert not missing, f".gitignore is missing required patterns: {missing}"


@pytest.mark.skipif(not _git_available(), reason="not a git working tree")
def test_no_private_file_is_tracked_by_git() -> None:
    """The real assertion: whatever .gitignore says, what does git actually
    have staged or committed?"""
    tracked = _git("ls-files").splitlines()
    offenders = [
        path
        for path in tracked
        if path.endswith(".local.yaml")
        or path == ".env"
        or path.startswith("data/")
        or path.startswith("out/")
    ]
    assert not offenders, (
        f"private files are tracked by git: {offenders}. "
        "Remove them with `git rm --cached <path>` before committing anything else."
    )


@pytest.mark.skipif(not _git_available(), reason="not a git working tree")
def test_example_config_is_not_accidentally_ignored() -> None:
    """The committed example files are the schema documentation. If the ignore
    pattern were widened to config/*.yaml by mistake, they would vanish."""
    ignored = _git("status", "--short", "--ignored").splitlines()
    ignored_paths = [line[3:].strip() for line in ignored if line.startswith("!!")]
    for path in ignored_paths:
        assert not path.endswith(".example.yaml"), (
            f"{path} is being ignored, but example config must stay committed."
        )


# --- the candidate must not reach M2 ----------------------------------------

#: Phrases that only appear if someone has written a JUDGEMENT ABOUT THE
#: CANDIDATE into an M2 artifact. Deliberately narrow: it looks for preference
#: *assignments*, not for topic words.
#:
#: M2 legitimately says "presales_solution_consulting" everywhere, because that
#: is a fact about a posting. It must never say "the candidate dislikes
#: presales", because that is a judgement about a person and it belongs to a
#: later layer. The line is between describing work and rating it.
CANDIDATE_PREFERENCE_MARKERS = (
    "candidate prefers",
    "candidate dislikes",
    "candidate wants",
    "candidate likes",
    "candidate avoids",
    "candidate strongly",
    "desirable for the candidate",
    "not a target technology",
    "dream compan",
    "desired software",
    "profile.local",
)

#: `HUMAN_REVIEW` is a label SOURCE -- who reviewed an assertion -- and is
#: stripped before scanning. Recording that a human reviewed a label is
#: provenance about the label; it says nothing about the posting, it never
#: reaches a model, and every assertion would be less trustworthy without it.
REVIEW_SOURCE_TOKEN = "human_review"

#: Everything a model is shown, plus the document it produces, plus the labels
#: the benchmark scores against. If a judgement leaked, it leaked into one of
#: these.
M2_SURFACES = (
    "src/career_agent/llm/prompts",
    "src/career_agent/llm/transport.py",
    "src/career_agent/llm/assemble.py",
    "src/career_agent/domain/fingerprint.py",
)


def test_no_candidate_preference_reaches_any_m2_surface() -> None:
    """M2 is candidate-independent, and this is what makes that checkable.

    The same posting must produce the same `JobFingerprint` for every reader.
    A posting that says Salesforce is REQUIRED records REQUIRED whether or not
    the reader would enjoy the job; a role that is primarily presales is
    described as primarily presales, and whether that is desirable is a
    question a later layer asks against career intent.

    The risk is not hypothetical any more. The candidate's preferences are now
    written down in this repository: `docs/product/career-intent-m4.md` records
    that presales work is AVOID and that Salesforce should not be central, and
    the easiest possible mistake is to let one of those judgements drift into a
    prompt, a label, or the durable document. This test fails loudly if it does.

    It deliberately does not scan `docs/`. That is where the preferences are
    *supposed* to live.
    """
    offences: list[str] = []

    for surface in M2_SURFACES:
        path = REPO_ROOT / surface
        files = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate_file in files:
            if not candidate_file.is_file():
                continue
            if candidate_file.suffix not in {".py", ".md", ".yaml", ".txt", ".json"}:
                continue
            # raw.txt is the employer's words, archived verbatim. A posting that
            # happens to contain one of these strings is a fact about the
            # posting, and rewriting it would be the real violation.
            if candidate_file.name in {"raw.txt", "payload.json"}:
                continue
            text = candidate_file.read_text(encoding="utf-8", errors="ignore").lower()
            text = text.replace(REVIEW_SOURCE_TOKEN, "")
            for marker in CANDIDATE_PREFERENCE_MARKERS:
                if marker in text:
                    offences.append(f"{candidate_file.relative_to(REPO_ROOT)} contains {marker!r}")

    assert offences == [], "candidate preference has leaked into an M2 surface:\n  " + "\n  ".join(
        offences
    )


def test_the_extractor_is_never_handed_a_candidate() -> None:
    """Structural, not a convention: there is nowhere to put one.

    `extract_job` takes a `JobSource`: a job id, a content hash, description
    text and a provider. No profile, no preferences, no residence. The
    candidate-independence claim is not something the pipeline remembers to
    honour; it is a function signature with no parameter for the thing.
    """
    from career_agent.pipeline.extract import JobSource

    fields = set(JobSource.__dataclass_fields__)

    # Everything it carries is about the POSTING: which job, what text, which
    # provider, and the provider's own metadata. Nothing about a person.
    assert fields <= {
        "job_id",
        "content_hash",
        "description_text",
        "provider",
        "payload",
        "payload_hash",
        "observations",
        "field_map_digest",
    }, fields
    for forbidden in ("profile", "candidate", "preference", "intent", "residence", "resume"):
        assert not any(forbidden in name for name in fields), f"JobSource carries {forbidden!r}"
