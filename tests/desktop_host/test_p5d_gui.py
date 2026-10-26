# ruff: noqa: F811
"""Opt-in full GUI match with real native process death and wire-level recovery."""

from contextlib import ExitStack
from hashlib import sha256
import json
import os
from pathlib import Path
import queue
import socket
import subprocess
import sys
from threading import Thread
import time
from urllib.request import urlopen
import pytest
from ptcg_online_server.repository import Repository
from ptcg_online_server.rules import RuleStatus
from tests.online_v2.conftest import settings  # noqa: F401
from tests.online_v2.test_real_lobby import real  # noqa: F401
from tests.online_v2.test_real_matches import certificate
from tests.online_v2.test_process import server
from tests.desktop_host.test_resources import PNG


class Device:
    def __init__(self, root, config):
        root.mkdir(mode=0o700)
        self.root, self.child = root, None
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.origin = f"http://127.0.0.1:{port}"
        self.config = root / "config.json"
        self.config.write_text(
            json.dumps(
                {**config, "state": str(root), "port": port, "origin": self.origin}
            )
        )
        self.config.chmod(0o600)

    def start(self):
        (self.root / "ready.json").unlink(missing_ok=True)
        self.log = (self.root / "process.log").open("a")
        self.child = subprocess.Popen(
            [sys.executable, "-m", "tests.desktop_host.fault_host"],
            env={**os.environ, "PTCG_TEST_HOST_CONFIG": str(self.config)},
            stdout=self.log,
            stderr=self.log,
        )
        for _ in range(150):
            assert self.child.poll() is None, "Isolated native host did not start"
            try:
                with urlopen(self.origin, timeout=0.3) as r:
                    assert r.status == 200
                return json.loads((self.root / "ready.json").read_text())
            except OSError:
                time.sleep(0.05)
        raise AssertionError("Isolated native host startup timed out")

    def stop(self):
        if self.child:
            if self.child.poll() is None:
                self.child.kill()
            self.child.wait(timeout=10)
            self.log.close()
            self.child = None


def test_gui_natural_match_native_restart_faults_and_offline_images(real, tmp_path):
    browser, build = os.environ.get("PTCG_P5D_BROWSER"), os.environ.get(
        "PTCG_P5D_BUILD"
    )
    if not browser or not build:
        pytest.skip("Explicit P5-D browser and isolated GUI build required")
    settings, inputs = real
    repo = Repository(settings)
    repo.migrate()
    tls = certificate(tmp_path)
    output = Path(os.environ["PTCG_V2_REPORT_DIRECTORY"])
    source = tmp_path / "images"
    source.mkdir()
    (source / "files").mkdir()
    key = sha256(PNG).hexdigest()
    (source / "files" / key).write_bytes(PNG)
    bundle = Path(inputs["directory"])
    components = json.loads((bundle / "bundle.json").read_text())["components"]
    catalog = json.loads(
        (
            bundle / next(c["path"] for c in components if c["role"] == "catalog")
        ).read_text()
    )
    ids = {e["printingId"] for e in inputs["deck1"] + inputs["deck2"]}
    cards = {}
    for card in catalog["cards"]:
        if card["printingId"] in ids:
            name = card["engineLine"].rsplit(" ", 2)[0]
            cards[card["engineId"]] = dict(
                engineId=card["engineId"],
                englishName=name,
                cnName=name,
                image={"sha256": key, "url": None},
            )
    manifest = source / "manifest.json"
    manifest.write_text(
        json.dumps(
            dict(
                catalogVersion=RuleStatus(settings).catalog_version,
                assetVersion="p5d-synthetic-placeholder",
                cards=list(cards.values()),
            )
        )
    )
    manifests = [[str(manifest), sha256(manifest.read_bytes()).hexdigest()]]
    with server(settings, tmp_path, tls=tls) as origin, ExitStack() as stack:
        devices = [
            Device(
                tmp_path / f"device-{i}",
                dict(
                    server={"origin": origin, "ca_file": str(tls[0])},
                    manifests=manifests,
                    build=build,
                ),
            )
            for i in range(2)
        ]
        for device in devices:
            stack.callback(device.stop)
        config = tmp_path / "browser.json"
        config.write_text(
            json.dumps(
                dict(
                    devices=[d.start() for d in devices],
                    decks=[inputs["deck1"], inputs["deck2"]],
                    output=str(output),
                )
            )
        )
        config.chmod(0o600)
        with (output / "gui.log").open("w") as log:
            child = subprocess.Popen(
                ["node", "tests/desktop_host/p5d-gui.mjs", str(config)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=log,
                text=True,
                env={**os.environ, "PTCG_P5C_BROWSER": browser},
            )
            lines = queue.Queue()

            def read():
                for line in child.stdout:
                    lines.put(line)
                lines.put(None)

            Thread(target=read, daemon=True).start()
            deadline = time.monotonic() + 600
            try:
                while True:
                    assert (
                        time.monotonic() < deadline
                    ), "GUI acceptance deadline exceeded"
                    try:
                        line = lines.get(timeout=1)
                    except queue.Empty:
                        continue
                    if line is None:
                        break
                    if not line.startswith("P5D:"):
                        log.write(line)
                        log.flush()
                        continue
                    event = json.loads(line[4:])
                    device = devices[event.get("device", 0)]
                    action = event["action"]
                    reply = {}
                    if action in ("drop-command", "drop-stream"):
                        (device.root / action).touch()
                    elif action == "restart":
                        assert (device.root / "dropped-command").exists()
                        device.stop()
                        reply = device.start()
                    elif action == "offline-images":
                        assert all(
                            (d.root / "resources" / key).exists() for d in devices
                        )
                        (source / "files" / key).unlink()
                    elif action == "evidence":
                        assert any(
                            (d.root / "dropped-stream").exists() for d in devices
                        )
                        with repo.db() as db:
                            count = db.execute(
                                "SELECT count(*) AS n FROM ptcg_v2.match_receipts"
                            ).fetchone()["n"]
                        assert (
                            count == event["commands"]
                        ), "Recovered command must commit exactly once"
                        reply = {"receipts": count}
                    else:
                        raise AssertionError("Unknown private test checkpoint")
                    child.stdin.write(json.dumps(reply) + "\n")
                    child.stdin.flush()
                assert (
                    child.wait(timeout=5) == 0
                ), "GUI acceptance failed; see private gui.log"
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait(timeout=5)
                config.unlink(missing_ok=True)
