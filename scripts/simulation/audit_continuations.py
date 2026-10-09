"""Measure P3.3 facilities. A failed gate is recorded, never relabelled success."""

# ruff: noqa: E402 -- command-line entry point adds the repository root first
import argparse
from collections import Counter
import json
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.simulation.fork import RuleFork, Continuation
from packages.simulation.information import InformationSet, observation, sample
from packages.simulation.simulation import simulate, Budget
from packages.battle.runtime import Adapter, PlayerId, view, ENGINE_VERSION
from packages.battle.agent import predict
from packages.battle.service import MatchService
from packages.rules.invariants import check_conservation
from scripts.simulation.combinations import variants


def audit(games):
    decks = variants()
    prompts = Counter()
    actions = Counter()
    results = []
    serialized_bytes = 0
    began = time.monotonic()
    for index in range(games):
        branch = RuleFork(
            Adapter(
                510000 + index,
                MatchService._lines(decks[index % len(decks)]["entries"]),
                MatchService._lines(decks[(index * 7 + 3) % len(decks)]["entries"]),
            )
        )
        for step in range(2000):
            game = branch.game
            if game.done:
                break
            dto = view(game, game.actor)
            decision = dto["decision"]
            source = decision.get("sourceId") or decision.get("source") or "system"
            depth, gen = 0, game.env.reducer
            while getattr(gen, "gi_yieldfrom", None) is not None:
                gen = gen.gi_yieldfrom
                depth += 1
            prompts[
                f"{dto['phase']}:{decision['kind']}:{source}:depth{depth}:actor{game.actor.name}"
            ] += 1
            command = predict(dto)[0]
            if decision["kind"] == "options":
                action = next(
                    o
                    for o in decision["options"]
                    if o["id"] == command["choice"]["optionId"]
                )
                actions[action["actionType"]] += 1
            token = branch.checkpoint().to_json()
            serialized_bytes += len(token.encode("utf-8"))
            child = RuleFork.restore(Continuation.from_json(token))
            assert view(child.game, child.game.actor) == dto
            original = game.private_digest()
            child.submit(child.game.actor, command)
            assert game.private_digest() == original
            branch.submit(game.actor, command)
            assert child.game.private_digest() == game.private_digest()
            check_conservation(game.env.gamestate)
        results.append(
            {"game": index, "steps": branch.game.version, "finished": branch.game.done}
        )
        print(f"continuations {index + 1}/{games}", flush=True)
    elapsed = time.monotonic() - began
    # Exact observations, recorded player choices, independent prior and seed.
    own, opponent = [MatchService._lines(decks[i]["entries"]) for i in (0, 12)]
    game = Adapter(610001, own, opponent)
    snapshots, choices, sampling = [], {}, []
    for step in range(13):
        snapshots.append(observation(view(game, PlayerId.PLAYER1)))
        if step in (0, 1, 12):
            info = InformationSet(
                "PLAYER1", snapshots.copy(), choices.copy(), own, [opponent]
            )
            start = time.monotonic()
            result = sample(info, sampling_seed=710001, trials=100, count=1)
            sampling.append(
                {
                    "historySteps": step,
                    "status": result["status"],
                    "proposals": result["proposals"],
                    "accepted": len(result["particles"]),
                    "seconds": time.monotonic() - start,
                }
            )
        if game.done:
            break
        command = predict(view(game, game.actor))[0]
        if game.actor == PlayerId.PLAYER1:
            choices[str(step)] = command["choice"]
        game.submit(game.actor, command)
    token = RuleFork(Adapter(17, own, opponent)).checkpoint()
    weak = ["4 Gimmighoul P01 005", "56 Metal Energy SVE 008"]
    for viewer in PlayerId:
        game = Adapter(0, weak, weak)
        snapshots, choices = [], {}
        for step in range(33):
            snapshots.append(observation(view(game, viewer)))
            if step == 32:
                break
            dto = view(game, game.actor)
            command = predict(dto)[0]
            if game.env.phase == "playing":
                command["choice"] = {
                    "optionId": next(
                        o["id"]
                        for o in dto["decision"]["options"]
                        if o["actionType"] == "PassTurn"
                    )
                }
            if game.actor == viewer:
                choices[str(step)] = command["choice"]
            game.submit(game.actor, command)
        start = time.monotonic()
        result = sample(
            InformationSet(viewer.name, snapshots, choices, weak, [weak]),
            sampling_seed=9831,
            trials=100,
            count=2,
        )
        sampling.append(
            {
                "historySteps": 32,
                "viewer": viewer.name,
                "scenario": "weak-deck-draw-history",
                "status": result["status"],
                "proposals": result["proposals"],
                "accepted": len(result["particles"]),
                "seconds": time.monotonic() - start,
            }
        )
    worker = simulate(token, Budget(seconds=15, steps=32))
    worker.pop("digest", None)
    timeout = simulate(token, Budget(seconds=0.001, steps=2500))
    return {
        "schema": "p33-facility-audit-v1",
        "engineVersion": ENGINE_VERSION,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "games": results,
        "pauseCoverage": dict(prompts),
        "actionCoverage": dict(actions),
        "seconds": elapsed,
        "checkpointBytesTotal": serialized_bytes,
        "sampling": sampling,
        "worker": worker,
        "timeout": timeout,
        "facilityChecksPassed": all(r["finished"] for r in results)
        and all(r["status"] == "ok" for r in sampling)
        and worker["status"] == "ok"
        and timeout["status"] == "timeout",
        "scope": "Facility measurements only; final P3.3 verdict also requires directed coverage and backend/browser acceptance.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=3)
    parser.add_argument("--output", default="artifacts/simulation/continuations/audit.json")
    args = parser.parse_args()
    result = audit(args.games)
    output = ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "games": len(result["games"]),
                "sampling": result["sampling"],
                "facilityChecksPassed": result["facilityChecksPassed"],
            },
            ensure_ascii=False,
        )
    )
