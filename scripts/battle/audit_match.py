"""Read-only replay audit of one saved real match. Never exports seed or hands."""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def audit(path, match_id=None):
    from packages.battle.service import runtime

    rt = runtime()
    from packages.rules.invariants import check_conservation

    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        # Freeze a consistent read snapshot if the match is still in progress.
        db.execute("BEGIN")
        match = (
            db.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
            if match_id
            else db.execute(
                "SELECT * FROM matches ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        )
        if match is None:
            raise ValueError("MATCH_NOT_FOUND")
        record = json.loads(match["body"])
        frames = {
            r["seq"]: json.loads(r["body"])
            for r in db.execute(
                "SELECT seq,body FROM match_frames WHERE match_id=? ORDER BY seq",
                (match["id"],),
            )
        }
    if record["implementation"] != rt.ENGINE_VERSION:
        raise ValueError("ENGINE_VERSION_MISMATCH")
    game = rt.Adapter(**record["config"])
    attacks, transitions, mismatches = [], [], []
    for seq, row in enumerate(record["commands"], 1):
        actor = rt.PlayerId[row["viewer"]]
        before = rt.view(game, actor)
        d = before["decision"]
        if d["kind"] == "options":
            option = next(
                o
                for o in d["options"]
                if o["id"] == row["command"]["choice"]["optionId"]
            )
            if option["actionType"] == "AttackAction":
                hand = before["observation"]["self"]["hand"]
                attacks.append(
                    {
                        "seq": seq,
                        "actor": row["viewer"],
                        "attack": option["attack"],
                        "source": option.get("source"),
                        "basicEnergyInHand": sum(
                            c.get("superType") == "ENERGY"
                            and c.get("energyType") == "BASIC"
                            for c in hand
                        ),
                    }
                )
        old_prizes = [len(p.prize) for p in game.env.get_players()]
        game.submit(actor, row["command"])
        check_conservation(game.env.gamestate)
        after = rt.view(game, rt.PlayerId.PLAYER1)
        fields = (
            "stateVersion",
            "observation",
            "done",
            "winner",
            "phase",
            "decision",
            "startingPlayer",
            "setupEvents",
            "publicReveals",
        )
        # Compare historical contracts while permitting only the documented
        # additive public P3 fields. Existing candidate refs remain compared.
        additions = {
            "ref",
            "benchCapacity",
            "sourceId",
            "targetId",
            "active_pokemonId",
            "sourceRef",
            "targetRef",
            "active_pokemonRef",
            "boardRef",
        }

        def compatible(current, saved):
            if isinstance(current, dict) and isinstance(saved, dict):
                return {
                    k: compatible(v, saved.get(k))
                    for k, v in current.items()
                    if k in saved or k not in additions
                }
            if isinstance(current, list) and isinstance(saved, list):
                return [
                    compatible(v, saved[i] if i < len(saved) else None)
                    for i, v in enumerate(current)
                ]
            return current

        for key in fields:
            if frames[seq][key] != compatible(after[key], frames[seq][key]):
                mismatches.append({"seq": seq, "field": key})
        new_prizes = [len(p.prize) for p in game.env.get_players()]
        if old_prizes != new_prizes:
            transitions.append({"seq": seq, "before": old_prizes, "after": new_prizes})
    return {
        "matchId": match["id"],
        "status": match["status"],
        "steps": game.version,
        "replayedFrameChecks": game.version,
        "cardConservationChecks": game.version,
        "projectionMismatches": mismatches,
        "stateDigestMatches": game.private_digest() == record["stateDigest"],
        "winner": getattr(game.info.get("winner"), "name", None),
        "terminationReason": game.env.gamestate.termination_reason,
        "attacks": attacks,
        "prizeTransitions": transitions,
        "scope": "Deterministic recovery and physical-card conservation; rule correctness requires independent adjudication.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "var/app.sqlite"))
    parser.add_argument("--match")
    parser.add_argument(
        "--output", default=str(ROOT / "artifacts/battle/actual-match-replay-check.json")
    )
    args = parser.parse_args()
    result = audit(args.db, args.match)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(
        json.dumps(
            {
                k: v
                for k, v in result.items()
                if k not in ("attacks", "prizeTransitions")
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    sys.exit(bool(result["projectionMismatches"]) or not result["stateDigestMatches"])
