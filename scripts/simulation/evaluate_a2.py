"""Reproducible development games, deliberately separate from strength holdout.

Freeze a manifest before running. Full deck lists, code hashes and budgets are
stored with each result; errors and truncated games remain in the denominator.
The incumbent A1 is also the rollout policy, so this is NOT an independent
strong-baseline certification, regardless of the observed score.
"""

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def code_hashes():
    paths = sorted((ROOT / "packages").rglob("*.py")) + [Path(__file__).resolve()]
    return {
        p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in paths
    }


def freeze(path):
    from packages.collection.domain import RAW, validation
    from packages.battle.agent import VERSION as A1
    from packages.battle.runtime import ENGINE_VERSION
    from packages.simulation.a2 import VERSION, SearchBudget
    from packages.simulation.registry import VERSION as RELEASE

    decks = [{"id": t["id"], "entries": t["entries"]} for t in RAW["templates"]]
    assert all(validation(d["entries"])["playable"] for d in decks)
    plan = {
        "schema": "p34-development-manifest-v1",
        "purpose": "development-only",
        "agents": {"candidate": VERSION, "opponent": A1, "independentBaseline": False},
        "engineVersion": ENGINE_VERSION,
        "releaseVersion": RELEASE,
        "hardware": {
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "cpus": os.cpu_count(),
            "python": platform.python_version(),
        },
        "budget": asdict(SearchBudget()),
        "maxSteps": 2000,
        "p95SecondsTarget": 1.25,
        "decks": decks,
        "opponentPriorDecks": decks,
        "pairs": [
            {"seed": 510000 + i, "decks": [i % len(decks), (i + 1) % len(decks)]}
            for i in range(2)
        ],
        "scorePolicy": "win=1, draw=0.5, loss/error/truncation=0; two seats per seed, aggregate by pair",
        "limitations": [
            "Two known archetypes, not six",
            "No independent strong baseline",
            "Not a held-out dataset; do not tune on reserved 300000-series seeds",
        ],
        "sourceHashes": code_hashes(),
    }
    # Exclusive creation prevents accidentally overwriting the pre-run conditions.
    with path.open("x", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return plan


def play(plan, pairing, seat):
    from packages.battle.runtime import Adapter, PlayerId, view
    from packages.battle.decision import decide as baseline
    from packages.battle.service import MatchService
    from packages.simulation.information import InformationSet, observation
    from packages.simulation.a2 import SearchBudget, decide, legal
    from packages.rules.invariants import check_conservation

    decks = [MatchService._lines(plan["decks"][i]["entries"]) for i in pairing["decks"]]
    viewer = PlayerId[seat]
    game = Adapter(pairing["seed"], *decks)
    history = [observation(view(game, viewer))]
    choices, diagnostics = {}, []
    row = {
        "seed": pairing["seed"],
        "decks": pairing["decks"],
        "seat": seat,
        "status": "truncated",
        "score": 0,
        "illegalProposals": 0,
    }
    began = time.monotonic()
    try:
        for _ in range(plan["maxSteps"]):
            if game.done:
                break
            actor = game.actor
            dto = view(game, actor)
            if actor == viewer:
                info = InformationSet(
                    seat,
                    history,
                    choices,
                    decks[0 if seat == "PLAYER1" else 1],
                    [
                        MatchService._lines(d["entries"])
                        for d in plan["opponentPriorDecks"]
                    ],
                )
                result = decide(
                    info, SearchBudget(**plan["budget"]), sampling_seed=game.version
                )
                cmd = result["command"]
                diagnostics.append(
                    {
                        k: result[k]
                        for k in (
                            "status",
                            "stopReason",
                            "seconds",
                            "simulations",
                            "particles",
                        )
                    }
                )
                if not legal(dto, cmd):
                    row["illegalProposals"] += 1
                    cmd, _ = baseline(dto)
                choices[str(game.version)] = cmd["choice"]
            else:
                cmd, _ = baseline(dto)
            game.submit(actor, cmd)
            check_conservation(game.env.gamestate)
            history.append(observation(view(game, viewer)))
        if game.done:
            winner = game.info.get("winner")
            row.update(
                status="finished",
                winner=getattr(winner, "name", None),
                score=1 if winner == viewer else 0.5 if winner is None else 0,
            )
    except Exception as e:
        row.update(status="error", error=repr(e))
    row.update(
        steps=game.version, seconds=time.monotonic() - began, decisions=diagnostics
    )
    return row


def run(plan, output):
    if plan["sourceHashes"] != code_hashes():
        raise ValueError("SOURCE_CHANGED_AFTER_FREEZE: use a new development manifest")
    if output.exists():
        raise ValueError("RESULT_ALREADY_EXISTS")
    output.mkdir(parents=True)
    rows = []
    for pairing in plan["pairs"]:
        for seat in ("PLAYER1", "PLAYER2"):
            row = play(plan, pairing, seat)
            rows.append(row)
            (output / f"game-{len(rows):03d}.json").write_text(
                json.dumps(row, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            print(
                json.dumps({k: v for k, v in row.items() if k != "decisions"}),
                flush=True,
            )
    decisions = [d for r in rows for d in r["decisions"]]
    times = sorted(d["seconds"] for d in decisions)
    p95 = times[max(0, (95 * len(times) + 99) // 100 - 1)] if times else None
    report = {
        "schema": "p34-development-results-v1",
        "manifest": plan,
        "manifestHash": digest(plan),
        "games": len(rows),
        "statuses": dict(Counter(r["status"] for r in rows)),
        "scoreRate": sum(r["score"] for r in rows) / len(rows),
        "decisions": len(decisions),
        "searchStatuses": dict(Counter(d["status"] for d in decisions)),
        "stopReasons": dict(Counter(d["stopReason"] for d in decisions)),
        "p95Seconds": p95,
        "p95Passed": p95 is not None and p95 <= plan["p95SecondsTarget"],
        "illegalProposals": sum(r["illegalProposals"] for r in rows),
        "strengthCertified": False,
        "p34Complete": False,
        "unmetGates": [
            "six archetypes",
            "independent held-out combinations",
            "200 games per key matchup",
            "independent strong heuristic",
            "paired 95% score lower bound > 55%",
            "no significant >5pp key-matchup regression",
        ],
    }
    (output / "summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in report.items() if k != "manifest"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["freeze", "run"])
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.action == "freeze":
        freeze(args.manifest)
    elif args.output:
        run(json.loads(args.manifest.read_text(encoding="utf-8")), args.output)
    else:
        parser.error("run requires --output")
