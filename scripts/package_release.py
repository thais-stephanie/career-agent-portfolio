"""Create public archives from an exact clean commit, never from working files."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--output", type=Path, default=ROOT / "out/release")
    args = parser.parse_args()
    if git("status", "--porcelain"):
        raise SystemExit("Commit the reviewed changes before packaging.")
    commit = git("rev-parse", args.ref + "^{commit}")
    project = tomllib.loads(git("show", commit + ":pyproject.toml"))["project"]
    if project["version"] != "0.1.0a2":
        raise SystemExit("This release recipe is for 0.1.0a2.")
    version = "v0.1.0-alpha.2"
    args.output.mkdir(parents=True, exist_ok=True)
    windows = args.output / f"Career-Agent-{version}-Windows.zip"
    source = args.output / f"Career-Agent-{version}-source.tar.gz"
    for path, fmt in ((windows, "zip"), (source, "tar.gz")):
        subprocess.run(
            ["git", "archive", "--format=" + fmt, "-o", str(path), commit], cwd=ROOT, check=True
        )
    with zipfile.ZipFile(windows) as archive:
        assert archive.read("BUILD_ID").decode().strip() == commit
        for name in archive.namelist():
            parts = Path(name).parts
            assert not Path(name).name.startswith("out-"), name
            assert not any(
                p
                in {".git", ".venv", "node_modules", ".tools", "backups", "data", "out", ".claude"}
                for p in parts
            ), name
            assert not (
                ".local.yaml" in name
                or name.endswith((".db", ".sqlite", ".backup"))
                or Path(name).name == ".env"
            ), name
        for name in (
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
        "status": "Alpha",
        "resume_tailor": "0.1.0b1 (Beta)",
        "sha256": hashes,
    }
    (args.output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
