"""Create public archives from an exact clean commit, never from working files."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def release_name(pep440: str) -> tuple[str, str]:
    """Map a PEP 440 pre-release version to its tag and stage: 0.2.0b1 -> v0.2.0-beta.1."""
    match = re.fullmatch(r"(\d+\.\d+\.\d+)(a|b|rc)(\d+)", pep440)
    if not match:
        raise SystemExit(f"{pep440} is not a pre-release version this recipe publishes.")
    base, stage, number = match.groups()
    label, status = {"a": ("alpha", "Alpha"), "b": ("beta", "Beta"), "rc": ("rc", "RC")}[stage]
    return f"v{base}-{label}.{number}", status


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=ROOT / "out/release")
    args = parser.parse_args()
    if git("status", "--porcelain"):
        raise SystemExit("Commit the reviewed changes before packaging.")
    commit = git("rev-parse", args.ref + "^{commit}")
    project = tomllib.loads(git("show", commit + ":pyproject.toml"))["project"]
    tailor = tomllib.loads(git("show", commit + ":companion/resume-tailor/pyproject.toml"))
    version, status = release_name(project["version"])
    args.output.mkdir(parents=True, exist_ok=True)
    windows = args.output / f"Career-Agent-{version}-Windows.zip"
    source = args.output / f"Career-Agent-{version}-source.tar.gz"
    # Windows "Extract All" already makes a folder named after the ZIP. A tarball
    # carries its own top folder, so unpacking it on macOS or Linux gives the
    # Career-Agent-<version> folder docs/INSTALL.md names.
    for path, fmt, prefix in (
        (windows, "zip", ""),
        (source, "tar.gz", f"Career-Agent-{version}/"),
    ):
        subprocess.run(
            ["git", "archive", "--format=" + fmt, "--prefix=" + prefix, "-o", str(path), commit],
            cwd=ROOT,
            check=True,
        )
    with tarfile.open(source) as archive:
        names = archive.getnames()
        assert all(n.startswith(f"Career-Agent-{version}/") for n in names), "tar prefix"
        member = archive.extractfile(f"Career-Agent-{version}/BUILD_ID")
        assert member is not None and member.read().decode().strip() == commit
    with zipfile.ZipFile(windows) as archive:
        assert archive.read("BUILD_ID").decode().strip() == commit
        for name in archive.namelist():
            parts = Path(name).parts
            assert not Path(name).name.startswith("out-"), name
            assert not any(
                p
                in {
                    ".git",
                    ".venv",
                    "node_modules",
                    ".tools",
                    "backups",
                    "data",
                    "out",
                    ".claude",
                    ".playwright-mcp",
                }
                for p in parts
            ), name
            assert not (
                ".local.yaml" in name
                or name.endswith((".db", ".sqlite", ".backup"))
                or Path(name).name in {".env", "profiles.json"}
            ), name
        migrations = [
            n for n in archive.namelist() if n.startswith("src/career_agent/storage/migrations/")
        ]
        assert any(n.endswith(".sql") for n in migrations), "migrations missing"
        for name in (
            "src/career_agent/web/static/index.html",
            "companion/resume-tailor/src/resume_tailor/ui/static_v2/index.html",
            "config/companies.yaml",
            "config/source_catalogue.yaml",
            "uv.lock",
            "FIRST_RUN.md",
            "docs/INSTALL.md",
            "Start-Career-Agent.cmd",
            "Start-Career-Agent.ps1",
            "Start-Demo.cmd",
            "README.md",
            "README.pt-BR.md",
            "README.es.md",
            "companion/resume-tailor/LICENSE",
        ):
            assert name in archive.namelist(), name
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (windows, source)}
    (args.output / "SHA256SUMS.txt").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()), encoding="ascii"
    )
    manifest = {
        "version": version,
        "commit": commit,
        "status": status,
        "resume_tailor": f"{tailor['project']['version']} (Beta)",
        "sha256": hashes,
    }
    (args.output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
