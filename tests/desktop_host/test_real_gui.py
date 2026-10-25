# ruff: noqa: F811
"""Opt-in GUI acceptance uses actual Chromium plus a private approved rule bundle."""

from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import socket
import subprocess
from threading import Thread
import time
import pytest
import uvicorn
from ptcg_desktop_api.bridge import Bridge
from ptcg_desktop_api.host import Host, create_host_app
from ptcg_desktop_api.resources import Resources
from ptcg_online_client.transport import Server
from ptcg_online_server.repository import Repository
from tests.online_v2.conftest import settings  # noqa: F401
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_process import server
from tests.online_v2.test_real_matches import certificate


@contextmanager
def local(profile, root, build):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    origin = f"http://127.0.0.1:{sock.getsockname()[1]}"
    owner = Bridge({"lan": profile}, root / "credentials")
    host = Host(owner, origin, build, Resources(root / "resources"), root / "launch")
    launch = host.launch()
    runner = uvicorn.Server(
        uvicorn.Config(
            create_host_app(host),
            access_log=False,
            proxy_headers=False,
            log_level="critical",
            ws="none",
        )
    )
    thread = Thread(target=runner.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        for _ in range(100):
            if runner.started:
                break
            assert thread.is_alive()
            time.sleep(0.02)
        assert runner.started
        yield {"origin": origin, "launch": str(launch)}
    finally:
        runner.should_exit = True
        thread.join(timeout=25)
        sock.close()
        assert not thread.is_alive()


def test_real_gui_invite_player2_reload_and_private_replay(real, tmp_path):
    browser = os.environ.get("PTCG_P5C_BROWSER")
    build = os.environ.get("PTCG_P5C_BUILD")
    if not browser or not build:
        pytest.skip("Explicit isolated GUI build and browser required")
    settings, inputs = real
    Repository(settings).migrate()
    tls = certificate(tmp_path)
    output = Path(os.environ["PTCG_V2_REPORT_DIRECTORY"])
    with server(settings, tmp_path, tls=tls) as origin, ExitStack() as stack:
        profile = Server(origin, ca_file=str(tls[0]))
        devices = [
            stack.enter_context(local(profile, tmp_path / f"device-{i}", build))
            for i in range(2)
        ]
        config = tmp_path / "browser-inputs.json"
        config.write_text(
            json.dumps(
                {
                    "devices": devices,
                    "decks": [inputs["deck1"], inputs["deck2"]],
                    "output": str(output),
                }
            )
        )
        config.chmod(0o600)
        try:
            subprocess.run(
                ["node", "tests/desktop_host/gui-real.mjs", str(config)],
                check=True,
                timeout=150,
            )
        finally:
            config.unlink(missing_ok=True)
