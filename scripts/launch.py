"""Start Career Agent: one server, one process, isolated demo storage."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--profile",
        default=None,
        help="Start with this local profile (its name or id) and make it the active one.",
    )
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a port between 1024 and 65535.")
    os.chdir(ROOT)
    mode = "demo" if args.demo else "personal"
    profile = None
    if not args.demo and not args.check:
        # LOCAL PROFILES. The first start adopts the existing workspace as the
        # first profile, where it already is; nothing is moved or copied.
        from career_agent.runtime.profiles import ProfileError, ensure_registry, set_active

        try:
            registry = ensure_registry(ROOT)
        except ProfileError as exc:
            print(f"Career Agent could not start: {exc}")
            return 2
        if args.profile:
            wanted = [
                p
                for p in registry.profiles
                if p.id == args.profile or p.label.casefold() == args.profile.casefold()
            ]
            if not wanted:
                parser.error(f"No local profile is called {args.profile!r}.")
            try:
                set_active(ROOT, wanted[0].id)
            except ProfileError as exc:
                parser.error(str(exc))
            registry = ensure_registry(ROOT)
        profile = registry.current
    config = ROOT / ("data/demo-config" if args.demo else "config")
    if profile is not None:
        config = ROOT / profile.config_dir
    if args.demo and not config.exists():
        config.mkdir(parents=True)
        for name in (
            "places.yaml",
            "search.starter.yaml",
            "search.worked-example.yaml",
            "search.legacy-example.yaml",
            "source_catalogue.yaml",
            "sources.yaml",
            "companies.yaml",
            "profile.example.yaml",
        ):
            shutil.copyfile(ROOT / "config" / name, config / name)
        shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = ROOT / "data" / f"{mode}.db"
    if profile is not None:
        db = ROOT / profile.db
    if not db.exists():
        command = (
            ["seed-demo", "--db", str(db), "--config-dir", str(config)]
            if args.demo
            else ["init-personal", "--db", str(db), "--label", "My search"]
        )
        subprocess.run(
            [sys.executable, "-c", "from career_agent.cli import app; app()", *command], check=True
        )
        if not args.demo:
            # A fresh installation starts with the shared job catalogue: its
            # first profile holds only private state from the beginning.
            from career_agent.storage.catalogue import link_new_profile

            link_new_profile(db, create=True)
    # Bring an EXISTING database up to this build's schema, as `serve` does.
    # Without this, an updated installation opened through the launcher ran
    # new code on an old schema: migrations only ever reached a database
    # through CLI commands, and a feature's tables could simply be missing.
    # Migrations are additive and recorded, so this is a no-op when current.
    from career_agent.storage.db import connect, migrate

    connection = connect(db)
    try:
        migrate(connection)
        if not args.demo:
            # The employer boards the product ships with. Additive only: a
            # board already here is never touched. Without this, a personal
            # database never held an ATS board and every board family read
            # "Never run" for ever.
            from career_agent.config.registry import sync_registry_quietly

            sync_registry_quietly(connection, config)
    finally:
        connection.close()
    if args.check:
        print(f"Setup complete: {mode}. Career Agent is installed. No AI was called.")
        return 0

    from career_agent.runtime.profiles import ProfileError
    from career_agent.web import server as web_server
    from career_agent.web.api import JobsApi
    from career_agent.web.profiles import ProfileHost
    from career_agent.web.server import ServerConfig, build_server

    host = ProfileHost(ROOT, port=args.port) if profile is not None else None
    # Bind before opening a browser. Never open an unrelated service.
    try:
        api = (
            host.open(profile)
            if host is not None and profile is not None
            else JobsApi(ServerConfig(db_path=db, config_dir=config, port=args.port))
        )
    except ProfileError as exc:
        print(f"Career Agent could not start: {exc}")
        return 2
    try:
        career = build_server(api)
    except OSError:
        if host is not None:
            host.close()
        print(
            f"Career Agent could not start: port {args.port} is already in use.\n"
            "It is probably already running in another launcher window. Use that window's "
            f"page (http://127.0.0.1:{args.port}/), or close that window and start again.\n"
            "Nothing was changed; your data is safe.\n"
            + (
                "To open the demo or another profile side by side, open PowerShell in this "
                "folder and run:\n"
                "  .\\Start-Career-Agent.cmd -Port 8875 -Demo\n"
                '  .\\Start-Career-Agent.cmd -Port 8875 -ProfileName "Profile name"'
                if sys.platform == "win32"
                else "To open the demo or another profile side by side, run in this folder:\n"
                "  uv run --no-sync python scripts/launch.py --port 8875 --demo\n"
                '  uv run --no-sync python scripts/launch.py --port 8875 --profile "Profile name"'
            )
        )
        return 2
    if host is not None and profile is not None:
        host.server = career
        host.serve(profile, api)
    # "Quit Career Agent" and the desktop launcher end the same way Ctrl+C
    # does: serve_forever returns and the finally below closes everything.
    web_server.on_quit = lambda: threading.Thread(target=career.shutdown, daemon=True).start()
    if not args.no_open:
        threading.Thread(
            target=webbrowser.open, args=(f"http://127.0.0.1:{args.port}/",), daemon=True
        ).start()
    if profile is not None:
        print(f"Local profile: {profile.label}")
    print(
        f"Career Agent is running: http://127.0.0.1:{args.port}/\n"
        "If your browser did not open, copy this address into it.\n"
        "Keep this window open while you use Career Agent. Press Ctrl+C here to stop it."
    )
    try:
        career.serve_forever()
    except KeyboardInterrupt:
        print(
            "Career Agent stopped. Everything you saved stays in this folder. "
            "Double-click the launcher to open it again."
        )
    finally:
        career.server_close()
        if host is not None:
            host.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
