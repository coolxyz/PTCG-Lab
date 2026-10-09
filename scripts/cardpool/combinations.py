"""Mechanism-stratified A1 regression for the published GHIJ subset."""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
import argparse
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


@lru_cache(maxsize=1)
def decks():
    from packages.battle import runtime  # noqa: F401
    from packages.collection.domain import CARDS, normalize, validation
    from packages.simulation.registry import EFFECTS
    from packages.rules.plain import SPECS, TRAINER_SPECS, SPECIAL_ENERGY_SPECS
    from scripts.simulation.combinations import variants

    by_effect = {
        c["engineId"]: c for c in CARDS.values() if c["effectStatus"] == "verified"
    }
    names = {}
    for effect, c in by_effect.items():
        if c["category"] == "宝可梦":
            names.setdefault(EFFECTS[effect]["name"], []).append(effect)
    result, unavailable_chains = variants(), []
    energy = {
        "GRASS": "CN:basic:GRA",
        "FIRE": "CN:basic:FIR",
        "WATER": "CN:basic:WAT",
        "LIGHTNING": "CN:basic:LIG",
        "PSYCHIC": "CN:basic:PSY",
        "FIGHTING": "CN:basic:FIG",
        "DARK": "CN:basic:DAR",
        "METAL": "CN:basic:MET",
    }
    specs = {s["effectKey"]: s for s in SPECS}
    trainers = [
        "PAF-084",
        "PAF-091",
        "PAF-087",
        "OBF-186",
        "SFA-061",
        "PAR-163",
        "PAL-265",
        "TEF-159",
    ]
    for spec in SPECS:
        chain = [spec["effectKey"]]
        cursor = spec
        while cursor["stage"] != "BASIC":
            matches = names.get(cursor["evolvesFrom"][0], [])
            if not matches:
                unavailable_chains.append(
                    {
                        "effectKey": spec["effectKey"],
                        "missingPredecessor": cursor["evolvesFrom"][0],
                    }
                )
                break
            parent = matches[0]
            chain.append(parent)
            cursor = specs.get(parent, {"stage": "BASIC"})
        else:
            counts = Counter({by_effect[k]["printingId"]: 4 for k in chain})
            anchor = by_effect["P01-005"]
            if anchor["nameLimitKey"] not in {CARDS[p]["nameLimitKey"] for p in counts}:
                counts[anchor["printingId"]] = 4
            types = sorted(
                {t for a in spec["attacks"] for t in a["cost"] if t != "COLORLESS"}
            ) or ["METAL"]
            for i in range(24):
                counts[energy[types[i % len(types)]]] += 1
            for key in trainers:
                amount = min(4, 60 - counts.total())
                if amount:
                    counts[by_effect[key]["printingId"]] += amount
            entries = normalize(
                [{"printingId": p, "quantity": q} for p, q in counts.items()]
            )
            assert validation(entries)["playable"], (
                spec["effectKey"],
                validation(entries),
            )
            result.append({"name": spec["effectKey"], "entries": entries})
    for spec in TRAINER_SPECS + SPECIAL_ENERGY_SPECS:
        selected = by_effect[spec["effectKey"]]
        counts = Counter(
            {
                by_effect["P01-005"]["printingId"]: 4,
                by_effect["P01-006"]["printingId"]: 4,
                selected["printingId"]: 1 if selected.get("aceSpec") else 4,
            }
        )
        for key in trainers:
            c = by_effect[key]
            if c["nameLimitKey"] not in {CARDS[pid]["nameLimitKey"] for pid in counts}:
                counts[c["printingId"]] = min(4, 36 - counts.total())
            if counts.total() >= 36:
                break
        counts["CN:basic:MET"] = 60 - counts.total()
        entries = normalize(
            [{"printingId": pid, "quantity": n} for pid, n in counts.items() if n]
        )
        assert validation(entries)["playable"], (spec["name"], validation(entries))
        result.append({"name": spec["effectKey"], "entries": entries})
    # Each newly available energy also appears in a legal colorless-attack deck.
    for pid in energy.values():
        entries = [
            {"printingId": by_effect["P01-005"]["printingId"], "quantity": 4},
            {"printingId": by_effect["P01-006"]["printingId"], "quantity": 4},
            {"printingId": pid, "quantity": 52},
        ]
        assert validation(entries)["playable"]
        result.append({"name": pid, "entries": entries})
    return result, unavailable_chains


def batch(indices):
    from packages.battle.runtime import Adapter, view
    from packages.battle.decision import decide
    from packages.battle.service import MatchService
    from packages.rules.invariants import check_conservation

    variants, _ = decks()
    rows = []
    for index in indices:
        a, b = (
            (index * 97 + index // len(variants)) % len(variants),
            (index * 17 + index // len(variants) + 3) % len(variants),
        )
        row = {
            "index": index,
            "seed": 700000 + index,
            "decks": [a, b],
            "status": "truncated",
            "fallbacks": 0,
        }
        actions = Counter()
        try:
            game = Adapter(
                row["seed"],
                MatchService._lines(variants[a]["entries"]),
                MatchService._lines(variants[b]["entries"]),
            )
            for step in range(2000):
                if game.done:
                    row.update(status="finished", winner=str(game.info.get("winner")))
                    break
                dto = view(game, game.actor)
                cmd, fallback = decide(dto)
                row["fallbacks"] += int(fallback)
                if dto["decision"]["kind"] != "selection":
                    option = next(
                        o
                        for o in dto["decision"]["options"]
                        if o["id"] == cmd["choice"]["optionId"]
                    )
                    if option.get("sourceId"):
                        actions[option["sourceId"] + ":" + option["actionType"]] += 1
                game.submit(game.actor, cmd)
                check_conservation(game.env.gamestate)
            row["steps"] = game.version
        except Exception as exc:
            row.update(status="error", error=repr(exc))
        row["actions"] = dict(actions)
        rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", default="artifacts/cardpool/ghij-combinations.json")
    args = parser.parse_args()
    start, results = time.monotonic(), []
    frozen, gaps = decks()
    with ProcessPoolExecutor(args.workers) as pool:
        for rows in pool.map(
            batch,
            [list(range(i, min(i + 50, args.games))) for i in range(0, args.games, 50)],
        ):
            results.extend(rows)
            print(len(results), dict(Counter(r["status"] for r in results)), flush=True)
    from packages.simulation.registry import VERSION
    from packages.battle.runtime import ENGINE_VERSION
    from packages.battle.agent import VERSION as AI_VERSION
    from packages.collection.domain import CATALOG_VERSION
    from packages.cardpool.scope import VERSION as SCOPE_VERSION

    output = {
        "scope": "Published GHIJ subset; does not certify unsupported cards",
        "releaseVersion": VERSION,
        "engineVersion": ENGINE_VERSION,
        "catalogVersion": CATALOG_VERSION,
        "scopeVersion": SCOPE_VERSION,
        "aiVersion": AI_VERSION,
        "games": args.games,
        "seconds": time.monotonic() - start,
        "decks": frozen,
        "unavailableEvolutionChains": gaps,
        "statuses": dict(Counter(r["status"] for r in results)),
        "fallbacks": sum(r["fallbacks"] for r in results),
        "actionCounts": dict(sum((Counter(r["actions"]) for r in results), Counter())),
        "results": results,
    }
    Path(args.output).write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    raise SystemExit(any(r["status"] != "finished" for r in results))
