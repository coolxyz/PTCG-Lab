"""Load only the checked P0.1 overlay. Never silently fall back to upstream."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OVERLAY = ROOT / "runtime/engine"
manifest = json.loads((ROOT / "artifacts/engine/overlay-hashes.json").read_text())
for name, expected in manifest.items():
    if hashlib.sha256((OVERLAY / name).read_bytes()).hexdigest() != expected:
        raise RuntimeError("ENGINE_OVERLAY_MISMATCH")
sys.path.insert(0, str(OVERLAY))
from loguru import logger  # noqa: E402
from ptcg.core.enums import PlayerId as PlayerId  # noqa: E402
from packages.rules.adapter import Adapter as Adapter, implementation_id  # noqa: E402
from packages.engine_adapter.base import RejectedCommand as RejectedCommand  # noqa: E402

logger.disable("ptcg")

ENGINE_VERSION = implementation_id()


def view(game, viewer):
    result = game.view(viewer)
    result["winner"] = getattr(game.info.get("winner"), "name", None)
    # Decision-scoped public instance references: never export hidden objects.
    locations = {}
    players = list(game.env.get_players())
    for side in ("self", "opponent"):
        player = next(p for p in players if (p.id == viewer) == (side == "self"))
        for zone in ("active", "bench", "discard", "hand"):
            if side == "opponent" and zone == "hand":
                continue
            for index, (dto, obj) in enumerate(
                zip(result["observation"][side][zone], getattr(player, zone))
            ):
                ref = f"v{game.version}:{side}:{zone}:{index}"
                dto["ref"] = ref
                locations[id(obj)] = ref
        result["observation"][side]["benchCapacity"] = max(5, len(player.bench))
    for index, (dto, obj) in enumerate(
        zip(result["observation"].get("stadium", []), game.env.gamestate.stadium)
    ):
        ref = f"v{game.version}:stadium:{index}"
        dto["ref"] = ref
        locations[id(obj)] = ref
    decision = result["decision"]
    if decision:
        if decision["kind"] == "selection":
            decision["indexed"] = game.actions.indexed
            from packages.battle.prompts import describe

            decision["zones"] = (
                []
                if decision["hidden"]
                else sorted(
                    {
                        str(getattr(c, "cardPosition", ""))
                        for c in game.actions.candidates
                    }
                )
            )
            prompt = game.info.get("prompt")
            source = getattr(prompt, "source", None)
            # Structured, actor-only metadata; never forward free-form engine logs.
            decision["source"] = getattr(source, "name", None)
            decision["sourceId"] = getattr(source, "id", None)
            # Public field positions disambiguate identical Pokémon. Deck/hand refs
            # stay decision-scoped, and hidden choices carry no identity/position.
            for item, original in zip(decision["candidates"], game._candidate_order()):
                card = game.actions.candidates[original]
                zone = str(getattr(card, "cardPosition", ""))
                if not decision["hidden"] and id(card) in locations:
                    item["boardRef"] = locations[id(card)]
                if not decision["hidden"] and zone in (
                    "CardPosition.ACTIVE",
                    "CardPosition.BENCH",
                ):
                    item["position"] = zone
                    item["index"] = getattr(card, "index", None)
                    for player in game.env.get_players():
                        if any(card is c for c in player.active + player.bench):
                            item["side"] = "self" if player.id == viewer else "opponent"
            decision["instruction"] = describe(game, decision)
        else:
            for option, action in zip(decision["options"], game.actions):
                for role in ("source", "target", "active_pokemon"):
                    obj = getattr(action, role, None)
                    if hasattr(obj, "name"):
                        option[role] = obj.name
                        option[role + "Id"] = getattr(obj, "id", None)
                        option[role + "Ref"] = locations.get(id(obj))
    return result
