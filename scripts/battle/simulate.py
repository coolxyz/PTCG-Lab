"""Reproducible two-deck A1 stability matrix. No claim of competitive strength."""

import argparse
from concurrent.futures import ProcessPoolExecutor
from collections import Counter
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def batch(rows):
    from packages.battle.service import runtime
    from packages.battle.agent import predict
    from packages.collection.domain import RAW, CARDS

    rt = runtime()
    from packages.rules.agents import DTOAgent

    decks = [
        [
            f"{e['quantity']} {CARDS[e['printingId']]['engineLine']}"
            for e in t["entries"]
        ]
        for t in RAW["templates"]
    ]
    result = []
    for index in rows:
        pair = index % 4
        seed = 200000 + index // 4
        g = rt.Adapter(seed, decks[pair // 2], decks[pair % 2])
        row = {"index": index, "seed": seed, "pair": pair, "status": "truncated"}
        baseline = DTOAgent(seed + 40000, "development")
        start = time.perf_counter()
        maxms = 0
        try:
            for step in range(1500):
                if g.done:
                    row.update(
                        status="finished",
                        winner=getattr(g.info.get("winner"), "name", None),
                    )
                    break
                actor = g.actor
                v = rt.view(g, actor)
                t = time.perf_counter()
                # Half use A1 mirror; half paired baseline seating.
                use_baseline = index % 8 >= 4 and actor == (
                    rt.PlayerId.PLAYER1 if index % 16 >= 8 else rt.PlayerId.PLAYER2
                )
                cmd = baseline.predict(v) if use_baseline else predict(v)[0]
                maxms = max(maxms, (time.perf_counter() - t) * 1000)
                g.submit(actor, cmd)
            row.update(
                steps=g.version,
                seconds=round(time.perf_counter() - start, 4),
                maxDecisionMs=round(maxms, 3),
            )
        except Exception as e:
            row.update(status="error", error=repr(e), steps=g.version)
        result.append(row)
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--games", type=int, default=10000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--output", type=Path, default=ROOT / "artifacts/battle/simulation.json")
    args = p.parse_args()
    start = time.monotonic()
    results = []
    with ProcessPoolExecutor(args.workers) as pool:
        for rows in pool.map(
            batch,
            [
                list(range(i, min(i + 100, args.games)))
                for i in range(0, args.games, 100)
            ],
        ):
            results.extend(rows)
            print(
                f"{len(results)}/{args.games} {dict(Counter(r['status'] for r in results))}",
                flush=True,
            )
    from packages.battle.service import runtime
    from packages.battle.agent import VERSION

    report = {
        "scope": "Two reviewed compositions; A1 mirrors and development-baseline seating. Engineering stability, not competitive certification.",
        "engineVersion": runtime().ENGINE_VERSION,
        "aiVersion": VERSION,
        "games": args.games,
        "workers": args.workers,
        "seconds": round(time.monotonic() - start, 2),
        "statuses": dict(Counter(r["status"] for r in results)),
        "results": results,
    }
    args.output.parent.mkdir(exist_ok=True, parents=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(any(r["status"] != "finished" for r in results))
