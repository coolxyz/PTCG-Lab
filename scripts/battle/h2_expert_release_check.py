"""Short local HTTP release check; H2 on both sides, no A1 strength sampling.

Run against an isolated database/server. Records every response including cold
creation and recovery reads. Gates are fixed here before measuring.
"""
import argparse
import json
import math
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
import httpx
from packages.battle.h2 import decide, VERSION
from apps.api.expert_policies import registry

BUDGET = {"actionP95Ms": 500, "actionMaxMs": 2000, "createMaxMs": 3000}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8767")
    parser.add_argument("--output", default="var/h2-app/release-http-v1.json")
    args = parser.parse_args()
    samples, games = [], []
    with httpx.Client(base_url=args.url, timeout=30) as client:
        def call(method, path, body=None, kind="other"):
            start = time.perf_counter()
            response = client.request(method, "/api/battle" + path, json=body)
            samples.append({"kind": kind, "path": path, "milliseconds": (time.perf_counter()-start)*1000, "status": response.status_code})
            response.raise_for_status()
            return response.json()

        session = call("POST", "/session", {})
        assert len(session["presets"]) == 5
        for preset in session["presets"]:
            rev = call("POST", f"/presets/{preset['id']}/revision", {})
            assert call("POST", f"/presets/{preset['id']}/revision", {})["id"] == rev["id"]
            v = call("POST", "/matches", {"requestId": str(uuid.uuid4()), "revisionId": rev["id"], "opponent": preset["id"]}, "create")
            assert v["aiVersion"] == VERSION
            assert v["aiStrategy"]["id"] == (preset["id"] if preset["id"] in registry.POLICIES else "original-h2")
            mid = v["matchId"]
            for step in range(2500):
                assert v["observation"]["opponent"]["hand"] is None
                if v["done"]:
                    break
                if v["decision"]:
                    command, detail = decide(v)
                    assert not detail["fallback"]
                    command["commandId"] = "client-" + str(uuid.uuid4())
                    v = call("POST", f"/matches/{mid}/commands", command, "action")["view"]
                else:
                    v = call("POST", f"/matches/{mid}/advance", kind="action")
                if step == 20:
                    restored = call("GET", f"/matches/{mid}", kind="read")
                    assert restored["stateVersion"] == v["stateVersion"]
            assert v["done"] and v["status"] == "finished"
            assert v["winner"] in ("PLAYER1", "PLAYER2") or v["result"].get("outcome") == "draw"
            after=-1
            while after<v['stateVersion']:
                replay=call("GET", f"/matches/{mid}/replay?after={after}")
                assert '\"fallback\": true' not in json.dumps(replay)
                assert '\"seed\"' not in json.dumps(replay)
                for frame in replay['frames']:
                    assert frame['view']['observation']['opponent']['hand'] is None
                    assert not {'plan','candidates','hand','reason'} & set(frame['event'])
                assert replay['nextAfter']>after
                after=replay['nextAfter']
            games.append({"preset": preset["id"], "matchId": mid, "steps": v["stateVersion"], "winner": v["winner"], "result": v["result"]})
            print(preset["id"], "completed", v["stateVersion"], flush=True)
    action = sorted(s["milliseconds"] for s in samples if s["kind"] == "action")
    measured = {"actionP95Ms": action[math.ceil(.95*len(action))-1], "actionMaxMs": max(action), "createMaxMs": max(s["milliseconds"] for s in samples if s["kind"] == "create")}
    passed = all(measured[k] <= BUDGET[k] for k in BUDGET)
    report = {"bundleHash": registry.bundle_hash(), "scope": "five complete product games: original H2 player vs routed opponent; automated play", "budget": BUDGET, "measured": measured, "passed": passed, "games": games, "samples": samples}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps({"passed": passed, **measured}))
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
