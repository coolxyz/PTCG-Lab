"""Opt-in local online window. Existing product services are never discovered or stopped."""

import argparse
import json
from pathlib import Path
import socket
from threading import Thread
import time
import webbrowser
import uvicorn
from ptcg_online_client.transport import Server
from .bridge import Bridge
from .host import Host, create_host_app
from .resources import Resources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    if args.port in (8765, 8770, 8443) or not 0 <= args.port <= 65535:
        parser.error("Choose an isolated local port (0 selects a free port)")
    if args.config.stat().st_size > 65536:
        parser.error("Configuration exceeds limit")
    config = json.loads(args.config.read_text())
    if set(config) - {"servers", "manifests"} or not isinstance(
        config.get("servers"), dict
    ):
        parser.error("Invalid native configuration")
    if len(config.get("manifests", [])) > 8:
        parser.error("At most eight approved display manifests")
    profiles = {key: Server(**value) for key, value in config["servers"].items()}
    # Server() permits HTTP only with an explicit loopback test setting; CLI excludes it.
    if any(not s.origin.startswith("https://") for s in profiles.values()):
        parser.error("Configured remote services must use HTTPS")
    state = args.state_dir
    if state.is_symlink():
        parser.error("State root cannot be a symlink")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if state.stat().st_mode & 0o077:
        parser.error("State directory must be private (0700)")
    resources = Resources(
        state / "resources",
        [(m["path"], m["sha256"]) for m in config.get("manifests", [])],
    )
    if not (args.build / "online.html").is_file():
        parser.error("Build the online GUI first")
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    owner = Bridge(profiles, state / "credentials")
    host = Host(owner, origin, args.build, resources, state / "launch")
    path = host.launch()
    runner = uvicorn.Server(
        uvicorn.Config(
            create_host_app(host),
            host="127.0.0.1",
            access_log=False,
            proxy_headers=False,
            log_level="warning",
            ws="none",
        )
    )

    def open_window():
        for _ in range(200):
            if runner.started:
                print("Local online window: " + str(path), flush=True)
                if not args.no_browser:
                    webbrowser.open(path.resolve().as_uri())
                return
            time.sleep(0.1)

    helper = Thread(target=open_window, daemon=True)
    helper.start()
    try:
        runner.run(sockets=[sock])
    finally:
        host.close()
        sock.close()


if __name__ == "__main__":
    main()
