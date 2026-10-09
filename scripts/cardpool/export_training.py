"""Small observable A1 dataset; rule-reproduction seeds are not policy features."""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from packages.battle.runtime import Adapter, view, ENGINE_VERSION  # noqa: E402
from packages.battle.decision import decide  # noqa: E402
from packages.battle.agent import VERSION as AI_VERSION  # noqa: E402
from packages.battle.service import MatchService  # noqa: E402
from packages.collection.domain import CATALOG_VERSION, content_hash  # noqa: E402
from packages.simulation.registry import VERSION as EFFECT_VERSION  # noqa: E402
from packages.cardpool.scope import VERSION as SCOPE_VERSION  # noqa: E402
from packages.cardpool.training import record, finish  # noqa: E402
from scripts.cardpool.combinations import decks  # noqa: E402


def export(output, games=2):
    variants, _ = decks()
    count = 0
    with output.open("w", encoding="utf-8") as stream:
        for index in range(games):
            pair = [
                variants[index % len(variants)]["entries"],
                variants[(index + 25) % len(variants)]["entries"],
            ]
            game = Adapter(
                910000 + index, *(MatchService._lines(entries) for entries in pair)
            )
            rows = []
            for _ in range(2000):
                if game.done:
                    break
                dto = view(game, game.actor)
                cmd, _ = decide(dto)
                own = pair[0 if game.actor.name == "PLAYER1" else 1]
                item = record(
                    dto,
                    cmd,
                    own_deck=own,
                    versions={
                        "engine": ENGINE_VERSION,
                        "effects": EFFECT_VERSION,
                        "catalog": CATALOG_VERSION,
                        "scope": SCOPE_VERSION,
                        "ownDeck": content_hash(own),
                    },
                    policy_version=AI_VERSION,
                )
                item.update(gameIndex=index, decisionIndex=game.version)
                rows.append(item)
                game.submit(game.actor, cmd)
            if not game.done:
                raise RuntimeError("TRAINING_GAME_TRUNCATED")
            winner = getattr(game.info.get("winner"), "name", None)
            for item in finish(rows, winner):
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")
                count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--games", type=int, default=2)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "artifacts/cardpool/training-sample.jsonl"
    )
    args = parser.parse_args()
    print({"records": export(args.output, args.games), "games": args.games})
