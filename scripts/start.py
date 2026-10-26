"""Cross-platform local service launcher; does not install or change user data."""

import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check dependencies and engine without starting a server",
    )
    parser.add_argument(
        "--online-config",
        type=Path,
        help="Enable the integrated product host with this native configuration",
    )
    parser.add_argument(
        "--online-state",
        type=Path,
        help="Private online identity/state directory; existing state is never discovered automatically",
    )
    parser.add_argument(
        "--online-build",
        type=Path,
        help="Directory containing the built online.html and assets",
    )
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not args.online_config and (
        args.online_state or args.online_build or args.no_browser
    ):
        parser.error("online options require --online-config")
    if args.online_config:
        args.online_config = args.online_config.resolve()
        args.online_state = (
            args.online_state or ROOT / "var/product-online"
        ).absolute()
        args.online_build = (
            args.online_build or ROOT / "apps/desktop-web/dist"
        ).resolve()
        if (
            not args.online_config.is_file()
            or not (args.online_build / "online.html").is_file()
        ):
            parser.error(
                "Provide an existing online config and a build containing online.html"
            )
    python = (
        ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    if not python.is_file():
        raise SystemExit(
            "Missing local environment. Run start.ps1 -Setup or bash start.sh --setup."
        )
    for name in (
        "apps/web/dist/index.html",
        "runtime/engine/ptcg",
        "artifacts/engine/overlay-hashes.json",
        "data/catalog/catalog.json",
    ):
        if not (ROOT / name).exists():
            raise SystemExit(
                "Missing release file: "
                + name
                + ". Restore the release, then run setup."
            )
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONPATH": str(ROOT)}
    if args.check:
        code = 'import fastapi,uvicorn; from packages.battle.service import runtime; print("Ready. Engine:", runtime().ENGINE_VERSION)'
        if args.online_config:
            code += '; from ptcg_desktop_api.product import create_product_app; print("Integrated host available")'
        command = [str(python), "-X", "utf8", "-c", code]
    elif args.online_config:
        command = [
            str(python),
            "-X",
            "utf8",
            "-m",
            "apps.api.product",
            "--config",
            str(args.online_config),
            "--state-dir",
            str(args.online_state),
            "--build",
            str(args.online_build),
            "--port",
            str(args.port),
        ]
        if args.no_browser:
            command.append("--no-browser")
    else:
        print(f"PTCG: http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
        command = [
            str(python),
            "-X",
            "utf8",
            "-m",
            "uvicorn",
            "apps.api.main:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(args.port),
        ]
    try:
        return subprocess.call(command, cwd=ROOT, env=env)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
