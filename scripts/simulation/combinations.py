"""Deterministic legal variant generator and scoped combination stress test."""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import argparse
import json
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def variants():
    from packages.collection.domain import RAW, CARDS, normalize, validation

    result = []
    for template in RAW["templates"]:
        for seed in range(12):
            rng = random.Random(seed)
            entries = Counter(
                {e["printingId"]: e["quantity"] for e in template["entries"]}
            )
            candidates = [
                c
                for c in CARDS.values()
                if c["effectStatus"] == "verified"
                and c["category"] == "训练家"
                and not c["aceSpec"]
                # P3 information-set regression fixtures keep the completed P3
                # trainer pool. P4 adds its own legal deck for every new trainer.
                and not c.get('engineId', '').startswith('P4T-')
            ]
            for _ in range(4):
                removable = [
                    p
                    for p in entries
                    if entries[p]
                    and CARDS[p]["category"] == "训练家"
                    and not CARDS[p]["aceSpec"]
                ]
                if not removable:
                    break
                old = rng.choice(removable)
                new = rng.choice(candidates)["printingId"]
                if (
                    sum(
                        q
                        for p, q in entries.items()
                        if CARDS[p]["nameLimitKey"] == CARDS[new]["nameLimitKey"]
                    )
                    >= 4
                ):
                    continue
                entries[old] -= 1
                entries[new] += 1
            es = normalize(
                [{"printingId": p, "quantity": q} for p, q in entries.items() if q]
            )
            assert validation(es)["playable"]
            result.append({"name": f"{template['id']}-variant-{seed}", "entries": es})
    # Mixed evolution lines and independent energy types; no archetype template.
    # Bind the mixed recipe to current source-owned printing identities.
    # Earlier engine aliases can be replaced by reviewed complete-face effects.
    recipe = {
        'CN:CSVM2cC:004':4, 'CN:CSVM2cC:007':3,
        'CN:CSVM2bC:005':4, 'CN:CSVM2bC:006':3, 'CN:CSVM2bC:007':2,
        'CN:CSM2.1C:044':8, 'CN:CSM2.1C:038':4, 'CN:CSM2.1C:041':4,
    }
    for name in ('巢穴球','高级球','博士的研究','派帕','奇树','超级能量回收','夜间担架'):
        card=next(c for c in CARDS.values() if c['cnName']==name and c['effectStatus']=='verified' and c.get('mark') in ('G','H','I','J'))
        recipe[card['printingId']]=4
    es = normalize(
        [
            {"printingId": k, "quantity": v}
            for k, v in recipe.items()
        ]
    )
    assert validation(es)["playable"]
    result.append({"name": "mixed-evolution-energy", "entries": es})
    return result


def batch(indices):
    from packages.battle.runtime import Adapter, view
    from packages.battle.decision import decide
    from packages.battle.service import MatchService
    from packages.rules.invariants import check_conservation

    decks = variants()
    rows = []
    for index in indices:
        a = index % len(decks)
        b = (index * 7 + 3) % len(decks)
        game = Adapter(
            400000 + index,
            MatchService._lines(decks[a]["entries"]),
            MatchService._lines(decks[b]["entries"]),
        )
        row = {
            "index": index,
            "seed": 400000 + index,
            "decks": [a, b],
            "status": "truncated",
            "fallbacks": 0,
        }
        commands = []
        try:
            for step in range(2000):
                if game.done:
                    row.update(status="finished", winner=str(game.info.get("winner")))
                    break
                cmd, fallback = decide(view(game, game.actor))
                row["fallbacks"] += int(fallback)
                commands.append({"actor": game.actor.name, "command": cmd})
                game.submit(game.actor, cmd)
                check_conservation(game.env.gamestate)
            row["steps"] = game.version
        except Exception as e:
            row.update(status="error", error=repr(e), steps=game.version)
            from packages.simulation.failures import capture

            row["reproduction"] = capture(
                ROOT / "var/p33-failures" / f"combination-{index}.json",
                game.config, commands, e,
            )
        rows.append(row)
    return rows


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--games", type=int, default=10000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--output", default="artifacts/simulation/combinations.json")
    a = p.parse_args()
    start = time.monotonic()
    results = []
    with ProcessPoolExecutor(a.workers) as pool:
        for rows in pool.map(
            batch, [list(range(i, min(i + 50, a.games))) for i in range(0, a.games, 50)]
        ):
            results.extend(rows)
            print(len(results), dict(Counter(r["status"] for r in results)), flush=True)
    from packages.simulation.registry import VERSION
    from packages.battle.agent import VERSION as AI

    data = {
        "scope": "25 deterministic supported-pool variants; not exhaustive or competitive evaluation",
        "releaseVersion": VERSION,
        "aiVersion": AI,
        "games": a.games,
        "seconds": time.monotonic() - start,
        "statuses": dict(Counter(r["status"] for r in results)),
        "fallbacks": sum(r["fallbacks"] for r in results),
        "decks": variants(),
        "results": results,
    }
    Path(a.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.exit(any(r["status"] != "finished" for r in results))
