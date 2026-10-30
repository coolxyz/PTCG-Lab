"""Exercise a relocated payload with a new private profile and no LAN traffic."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import os
import queue
import subprocess
import tempfile
from threading import Thread

import httpx


def probe(payload, *, all_strategies=False):
    payload = Path(payload).resolve()
    metadata = json.loads((payload / "manifest.json").read_text())
    target = metadata["platform"]
    if target not in {"macos-arm64", "windows-x64"} or (target == "windows-x64") != (
        os.name == "nt"
    ):
        raise ValueError("Probe must execute on the payload's native platform")
    interpreter = payload / (
        "python/python.exe" if target == "windows-x64" else "python/bin/python3"
    )
    checks = {}
    before = {
        p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in payload.rglob("*")
        if p.is_file()
    }
    with tempfile.TemporaryDirectory(prefix="ptcg-native-proof-") as temp:
        home = Path(temp).resolve()
        state = home / "profile"
        children = []

        def launch():
            child = subprocess.Popen(
                [
                    str(interpreter),
                    "-I",
                    "-B",
                    "-X",
                    "utf8",
                    str(payload / "bootstrap.py"),
                ],
                cwd=home,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                env={
                    **{
                        k: v
                        for k in [
                            "SystemRoot",
                            "WINDIR",
                            "USERPROFILE",
                            "LOCALAPPDATA",
                            "APPDATA",
                            "TEMP",
                            "TMP",
                        ]
                        if (v := os.environ.get(k)) is not None
                    },
                    "HOME": str(home),
                },
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
            children.append(child)
            nonce = "a" * 64
            child.stdin.write(
                json.dumps(
                    {
                        "schema": 1,
                        "type": "start",
                        "nonce": nonce,
                        "stateDir": str(state),
                        "config": {"servers": {}},
                    }
                )
                + "\n"
            )
            child.stdin.flush()
            replies = queue.Queue(maxsize=1)

            def answer():
                try:
                    replies.put(child.stdout.readline(16385))
                except Exception:
                    replies.put("")

            Thread(target=answer, daemon=True).start()
            try:
                raw = replies.get(timeout=35)
            except queue.Empty:
                raise RuntimeError("PROBE_START_TIMEOUT")
            if len(raw) > 16384 or not raw.endswith("\n"):
                raise RuntimeError("PROBE_START_FAILED")
            result = json.loads(raw)
            if (
                result.get("type") != "ready"
                or result.get("pid") != child.pid
                or result.get("nonce") != nonce
            ):
                raise RuntimeError("PROBE_START_FAILED")
            return child, result

        def stop(child):
            child.stdin.close()
            if child.wait(timeout=10) != 0:
                raise RuntimeError("PROBE_SHUTDOWN_FAILED")

        try:
            first, ready = launch()
            origin = ready["origin"]
            with httpx.Client(
                base_url=origin, trust_env=False, headers={"Origin": origin}, timeout=30
            ) as web:
                checks["productGUI"] = web.get("/").status_code == 200
                checks["onlineGUI"] = web.get("/online").status_code == 200
                web.cookies.set(
                    "ptcg-local", ready["cookie"], domain="127.0.0.1", path="/"
                )
                auth = web.post(
                    "/session",
                    json={"windowId": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
                )
                checks["sameOriginSession"] = auth.status_code == 200
                catalog = web.get("/api/cards").json()
                # The catalog endpoint is a list in the existing product contract.
                cards = (
                    catalog if isinstance(catalog, list) else catalog.get("cards", [])
                )
                if not cards:
                    raise RuntimeError("PROBE_CATALOG_EMPTY")
                saved = web.post(
                    "/api/decks",
                    json={
                        "name": "P8 isolated recovery proof",
                        "entries": [
                            {"printingId": cards[0]["printingId"], "quantity": 1}
                        ],
                    },
                )
                if saved.status_code != 201:
                    raise RuntimeError("PROBE_DECK_FAILED")
                deck = saved.json()["id"]
                practice = web.post("/api/battle/session")
                checks["practiceSession"] = practice.status_code == 200
                if all_strategies:
                    presets = practice.json()["presets"]
                    if len(presets) != 5:
                        raise RuntimeError("PROBE_POLICY_SET_INCOMPLETE")
                    for preset in presets:
                        revision = web.post(
                            "/api/battle/presets/" + preset["id"] + "/revision", json={}
                        )
                        if revision.status_code != 200:
                            raise RuntimeError("PROBE_POLICY_REVISION_FAILED")
                        created = web.post(
                            "/api/battle/matches",
                            json={
                                "requestId": "p8b-" + preset["id"],
                                "revisionId": revision.json()["id"],
                                "opponent": preset["id"],
                                "aiLevel": "H2",
                            },
                        )
                        if (
                            created.status_code != 201
                            or created.json().get("aiStrategy", {}).get("id")
                            != preset["id"]
                        ):
                            raise RuntimeError("PROBE_POLICY_ROUTE_FAILED")
                    checks["fiveH2Policies"] = True
            stop(first)
            second, reopened = launch()
            with httpx.Client(base_url=reopened["origin"], trust_env=False) as web:
                checks["persistedDeck"] = (
                    web.get("/api/decks/" + deck).status_code == 200
                )
            checks["freshLocalSession"] = reopened["cookie"] != ready["cookie"]
            stop(second)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()
                if not child.stdin.closed:
                    child.stdin.close()
    after = {
        p.relative_to(payload).as_posix(): sha256(p.read_bytes()).hexdigest()
        for p in payload.rglob("*")
        if p.is_file()
    }
    checks["immutablePayload"] = before == after
    if not all(checks.values()):
        raise RuntimeError(
            "PROBE_ACCEPTANCE_FAILED " + json.dumps(checks, sort_keys=True)
        )
    return {
        "schema": 1,
        "stage": "P8-B-payload" if target == "windows-x64" else "P8-A",
        "checks": checks,
        "windowsAccepted": False,
        "keychainAccepted": False,
        "releaseReady": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--all-strategies", action="store_true")
    args = parser.parse_args()
    print(json.dumps(probe(args.payload, all_strategies=args.all_strategies), indent=2))
