"""Start both local applications with one runtime and isolated demo storage."""

from __future__ import annotations

import argparse
import os
import shutil
import socket
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
    if not 1024 <= args.port <= 65534:
        parser.error("Choose a port between 1024 and 65534; Tailor uses the next port.")
    os.chdir(ROOT)
    mode = "demo" if args.demo else "personal"
    # Never discover another installation's personal files.
    os.environ["RESUME_TAILOR_HOME"] = str(ROOT / "data" / f"tailor-{mode}")
    os.environ["RESUME_TAILOR_DATA"] = str(ROOT / "data" / f"tailor-{mode}" / "runtime")
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
        tailor_home = ROOT / profile.tailor_home
        os.environ["RESUME_TAILOR_HOME"] = str(tailor_home)
        os.environ["RESUME_TAILOR_DATA"] = str(tailor_home / "runtime")
    os.environ.setdefault("LLM_PROVIDER", "none")
    if args.demo:
        os.environ["LLM_PROVIDER"] = "none"
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
    if args.demo:
        from resume_tailor.workspace import WorkspaceStore
        from resume_tailor.workspace.demo import create_demo_candidate

        store = WorkspaceStore()
        if not store.list_candidates():
            create_demo_candidate(store)
    if args.check:
        print(f"Setup complete: {mode}. Both applications are installed. No AI was called.")
        return 0

    import uvicorn
    from resume_tailor.api.app import create_app

    from career_agent.web.api import JobsApi
    from career_agent.web.profiles import ProfileHost, SwitchableApp
    from career_agent.web.server import ServerConfig, build_server

    # The Tailor app is reached through a switch, so a profile change can
    # rebuild it on that profile's own workspace.
    tailor_app = SwitchableApp(create_app())
    host = (
        ProfileHost(
            ROOT,
            port=args.port,
            tailor=tailor_app,
            tailor_factory=lambda home: create_app(home=home),
        )
        if profile is not None
        else None
    )

    # Bind both ports before opening a browser. Never open an unrelated service.
    career = None
    tailor_socket = socket.socket()
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            tailor_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        tailor_socket.bind(("127.0.0.1", args.port + 1))
        tailor_socket.listen(128)
        from career_agent.runtime.profiles import ProfileError

        try:
            api = (
                host.open(profile)
                if host is not None and profile is not None
                else JobsApi(ServerConfig(db_path=db, config_dir=config, port=args.port))
            )
        except ProfileError as exc:
            tailor_socket.close()
            print(f"Career Agent could not start: {exc}")
            return 2
        career = build_server(api)
        if host is not None and profile is not None:
            host.server = career
            host.serve(profile, api)
    except OSError:
        tailor_socket.close()
        if career:
            career.server_close()
        print(
            f"Career Agent could not start: ports {args.port} and {args.port + 1} are already "
            "in use.\n"
            "It is probably already running in another launcher window. Use that window's "
            f"page (http://127.0.0.1:{args.port}/), or close that window and start again.\n"
            "Nothing was changed; your data is safe.\n"
            "To run a second copy side by side, open PowerShell in this folder and run:\n"
            "  .\\Start-Career-Agent.ps1 -Port 8875"
        )
        return 2
    worker = threading.Thread(target=career.serve_forever, daemon=True)
    worker.start()
    server = uvicorn.Server(
        uvicorn.Config(tailor_app, host="127.0.0.1", port=args.port + 1, log_level="warning")
    )

    def open_when_ready():
        import time

        for _ in range(100):
            if server.started:
                webbrowser.open(f"http://127.0.0.1:{args.port}/")
                return
            if server.should_exit:
                return
            time.sleep(0.1)

    if not args.no_open:
        threading.Thread(target=open_when_ready, daemon=True).start()
    if profile is not None:
        print(f"Local profile: {profile.label}")
    print(
        f"Career Agent is running: http://127.0.0.1:{args.port}/\n"
        f"Resume Tailor Beta: http://127.0.0.1:{args.port + 1}/\n"
        "If your browser did not open, copy the first address into it.\n"
        "Keep this window open while you use the apps. Press Ctrl+C here to stop both."
    )
    try:
        server.run(sockets=[tailor_socket])
    except KeyboardInterrupt:
        print(
            "Both apps stopped. Everything you saved stays in this folder. "
            "Double-click the launcher to open them again."
        )
    finally:
        career.shutdown()
        career.server_close()
        if host is not None:
            host.close()
        tailor_socket.close()
        worker.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
