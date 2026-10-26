"""Opt-in local online window. Existing product services are never discovered or stopped."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import socket
import sys
from threading import Thread
import time
import webbrowser
import uvicorn
from ptcg_online_client.transport import Server
from .bridge import Bridge, BridgeError
from .host import Host, create_host_app
from .resources import Resources
from .reopen import ReopenServer, reopen


def open_window(host, *, no_browser):
    if host.product_mode:
        print("PTCG 本地 APP：" + host.origin + "/", flush=True)
        print(
            "本机授权码（五分钟内有效，仅使用一次）：" + host.activation_code(),
            flush=True,
        )
        print(
            "在网络大厅输入此码；同一浏览器关闭重开无需再次输入。终端输入 r 可重新授权。",
            flush=True,
        )
        if not no_browser:
            webbrowser.open(host.origin + "/#online")
        return None
    path = host.launch()
    print("Local online window: " + str(path), flush=True)
    print("首次授权 HTML 仅可使用一次，且须在两分钟内打开。", flush=True)
    print("关闭窗口后，在同一浏览器重新打开：" + host.origin + "/", flush=True)
    print("若授权已失效或更换浏览器，在此终端输入 r 并回车生成新 HTML。", flush=True)
    if not no_browser:
        webbrowser.open(path.resolve().as_uri())
    return path


def console_windows(host, runner, *, no_browser, stream):
    """Only the local terminal may mint another one-use authorization file."""
    try:
        for line in stream:
            if runner.should_exit:
                return
            if line.strip().lower() not in {"r", "reopen"}:
                print("输入 r 并回车重新打开窗口；Ctrl-C 关闭本机后台。", flush=True)
                continue
            try:
                open_window(host, no_browser=no_browser)
            except BridgeError as error:
                print("无法生成启动入口：" + error.code, flush=True)
    except (EOFError, OSError):
        # Detached launchers have no interactive input; the server remains alive.
        return


def main(*, local_factory=None, local_build=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8765 if local_factory else 0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--reopen",
        action="store_true",
        help="Reauthorize an existing product instance through private native IPC",
    )
    args = parser.parse_args()
    if (
        not local_factory and args.port in (8765, 8770, 8443)
    ) or not 0 <= args.port <= 65535:
        parser.error("Choose an isolated local port (0 selects a free port)")
    if args.config.stat().st_size > 65536:
        parser.error("Configuration exceeds limit")
    raw_config = args.config.read_bytes()
    config_hash = sha256(raw_config).hexdigest()
    if args.reopen:
        if not local_factory:
            parser.error("--reopen requires the integrated product host")
        result = reopen(args.state_dir, config_hash)
        print("PTCG 本地 APP：" + result["origin"] + "/", flush=True)
        print("本机授权码（五分钟内有效，仅使用一次）：" + result["code"], flush=True)
        if not args.no_browser:
            webbrowser.open(result["origin"] + "/#online")
        return
    config = json.loads(raw_config)
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
    if local_build is not None:
        # Different builds may legitimately share identical immutable assets.
        # Refuse ambiguous names rather than silently serving the wrong bundle.
        for file in (args.build / "assets").glob("*"):
            other = Path(local_build) / "assets" / file.name
            if other.is_file() and other.read_bytes() != file.read_bytes():
                parser.error("Product/online static asset name conflict")
    sock = socket.socket()
    sock.bind(("127.0.0.1", args.port))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    owner = Bridge(profiles, state / "credentials")
    host = Host(owner, origin, args.build, resources, state / "launch")
    control = None
    try:
        if local_factory:
            control = ReopenServer(state, host, config_hash)
            from .product import create_product_app

            app = create_product_app(local_factory(), host)
        else:
            app = create_host_app(host)
    except BaseException:
        if control:
            control.close()
        host.close()
        sock.close()
        raise
    runner = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            access_log=False,
            proxy_headers=False,
            log_level="warning",
            ws="none",
        )
    )

    def ready_window():
        for _ in range(200):
            if runner.started:
                open_window(host, no_browser=args.no_browser)
                if local_factory:
                    print("Product: " + origin + "/", flush=True)
                    print("Online: " + origin + "/#online", flush=True)
                if sys.stdin.isatty():
                    console_windows(
                        host, runner, no_browser=args.no_browser, stream=sys.stdin
                    )
                return
            time.sleep(0.1)

    helper = Thread(target=ready_window, daemon=True)
    helper.start()
    try:
        runner.run(sockets=[sock])
    except KeyboardInterrupt:
        pass
    finally:
        if control:
            control.close()
        host.close()
        sock.close()


if __name__ == "__main__":
    main()
