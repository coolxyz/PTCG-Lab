# ruff: noqa: E402
"""100 coexisting authenticated matches exercised through the HTTP ASGI gateway.
This measures local gateway + DB + serialized AI, not TCP/network throughput.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from concurrent.futures import ThreadPoolExecutor
import json
import statistics
import tempfile
import time
from fastapi.testclient import TestClient
from apps.api.main import create_app
from packages.collection.domain import RAW

if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        app = create_app(Path(tmp) / "load.sqlite")
        s = app.state.store
        t = RAW["templates"][0]
        d = s.create_deck("load", t["entries"])
        r = s.freeze(d["id"], d["version"])
        clients = []
        matches = []
        errors = []
        for i in range(100):
            c = TestClient(app)
            c.post("/api/battle/session", json={})
            clients.append(c)
            response = c.post(
                "/api/battle/matches",
                json={
                    "requestId": f"load-{i:05d}",
                    "revisionId": r["id"],
                    "opponent": "dragapult" if i % 2 else "gholdengo",
                },
            )
            if response.status_code != 201:
                raise RuntimeError(response.text)
            matches.append(response.json()["matchId"])

        def step(i):
            start = time.perf_counter()
            c = clients[i]
            id = matches[i]
            response = c.post("/api/battle/matches/" + id + "/advance")
            if response.status_code != 200:
                return {"error": response.text}
            view = response.json()
            check = c.get("/api/battle/matches/" + id).json()
            assert view["stateVersion"] == check["stateVersion"]
            assert view["observation"]["opponent"]["hand"] is None
            return {
                "ms": (time.perf_counter() - start) * 1000,
                "seq": view["stateVersion"],
            }

        start = time.perf_counter()
        with ThreadPoolExecutor(max_workers=100) as pool:
            rows = list(pool.map(step, range(100)))
        lat = sorted(r["ms"] for r in rows if "ms" in r)
        report = {
            "scope": "100 simultaneous ASGI HTTP requests / 100 active sessions; localhost single-process gateway, SQLite and AI serialization included; no TCP or remote host.",
            "matches": 100,
            "concurrentRequests": 100,
            "errors": [r for r in rows if "error" in r],
            "wallSeconds": time.perf_counter() - start,
            "p50Ms": statistics.median(lat),
            "p95Ms": lat[int(len(lat) * 0.95) - 1],
            "maxMs": max(lat),
            "databaseBytes": (Path(tmp) / "load.sqlite").stat().st_size,
        }
        out = ROOT / "artifacts/battle/load.json"
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        for c in clients:
            c.close()
        sys.exit(bool(report["errors"]))
