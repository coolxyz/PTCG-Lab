"""P8 parent-owned backend. JSON lines use inherited pipes, never TCP launch IPC.

The native shell owns stdin/stdout. EOF shuts down only this child. No credentials,
authorization HTML, activation codes or arbitrary exceptions are written to logs.
"""

import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
from threading import Event, Thread
import time

import uvicorn
from ptcg_online_client.transport import Server
from .bridge import Bridge
from .host import Host, create_host_app
from .resources import Resources
from .secure_storage import native_client

LIMIT = 16384


def frame(stream):
    line = stream.readline(LIMIT + 1)
    if not line or len(line) > LIMIT or not line.endswith("\n"):
        raise ValueError("NATIVE_INVALID_FRAME")
    return json.loads(line)


def request(value):
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "type", "nonce", "stateDir", "config"}
        or type(value["schema"]) is not int
        or value["schema"] != 1
        or value["type"] != "start"
        or not isinstance(value["nonce"], str)
        or not re.fullmatch(r"[a-f0-9]{64}", value["nonce"])
        or not isinstance(value["stateDir"], str)
        or not Path(value["stateDir"]).is_absolute()
        or not isinstance(value["config"], dict)
    ):
        raise ValueError("NATIVE_INVALID_START")
    config = value["config"]
    if set(config) - {"servers", "manifests"} or not isinstance(
        config.get("servers"), dict
    ):
        raise ValueError("NATIVE_INVALID_CONFIG")
    if (
        not isinstance(config.get("manifests", []), list)
        or len(config.get("manifests", [])) > 8
    ):
        raise ValueError("NATIVE_INVALID_CONFIG")
    return value


class StateLock:
    """macOS proof; Windows uses a separate native backend in P8-B."""

    def __init__(self, path):
        path = Path(path)
        # Reject links in every existing ancestor before creating anything.
        if any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError("NATIVE_UNSAFE_STATE")
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("NATIVE_UNSAFE_STATE")
        fd = os.open(
            path / "native.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise ValueError("NATIVE_UNSAFE_LOCK")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BaseException:
            os.close(fd)
            raise
        self.fd = fd

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def main(*, build, local_factory=None):
    protocol = sys.stdout
    # Application output must never corrupt the inherited protocol pipe.
    sys.stdout = sys.stderr
    lock = host = sock = None
    done = Event()

    def emit(value):
        protocol.write(json.dumps(value, separators=(",", ":")) + "\n")
        protocol.flush()

    try:
        start = request(frame(sys.stdin))
        state = Path(start["stateDir"])
        lock = StateLock(state)
        profiles = {
            key: Server(**value) for key, value in start["config"]["servers"].items()
        }
        if any(not s.origin.startswith("https://") for s in profiles.values()):
            raise ValueError("NATIVE_HTTPS_REQUIRED")
        resources = Resources(
            state / "resources",
            [(m["path"], m["sha256"]) for m in start["config"].get("manifests", [])],
        )
        if not (Path(build) / "online.html").is_file():
            raise ValueError("NATIVE_BUILD_MISSING")
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
        owner = Bridge(profiles, state / "credentials", allow_offline=True, client_factory=native_client)
        host = Host(owner, origin, build, resources, state / "launch")
        if local_factory:
            from .product import create_product_app

            app = create_product_app(local_factory(state), host)
        else:
            app = create_host_app(host)
        runner = uvicorn.Server(
            uvicorn.Config(
                app,
                access_log=False,
                proxy_headers=False,
                log_level="critical",
                ws="none",
            )
        )

        def control():
            # EOF, malformed commands and wrong nonces all fail closed.
            try:
                value = frame(sys.stdin)
                if value != {"schema": 1, "type": "shutdown", "nonce": start["nonce"]}:
                    return
            except Exception:
                pass
            finally:
                runner.should_exit = True

        def ready():
            until = time.monotonic() + 30
            while not done.wait(0.02):
                if runner.should_exit:
                    return
                if runner.started:
                    emit(
                        {
                            "schema": 1,
                            "type": "ready",
                            "nonce": start["nonce"],
                            "pid": os.getpid(),
                            "origin": origin,
                            "cookie": host.native_session(),
                        }
                    )
                    return
                if time.monotonic() >= until:
                    runner.should_exit = True
                    return

        Thread(target=control, daemon=True).start()
        Thread(target=ready, daemon=True).start()
        runner.run(sockets=[sock])
        return 0
    except Exception:
        emit({"schema": 1, "type": "error", "code": "NATIVE_START_FAILED"})
        return 1
    finally:
        done.set()
        if host:
            host.close()
        if sock:
            sock.close()
        if lock:
            lock.close()
        sys.stdout = protocol
