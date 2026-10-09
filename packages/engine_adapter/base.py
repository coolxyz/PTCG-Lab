"""P0 in-process transport boundary. Not a production/fair-play certification.

No upstream objects, RNG seed, raw info, or state digests enter the player DTO.
Seed + accepted commands restore generator continuations by re-execution.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from enum import Enum
from typing import Any

from ptcg import PokemonTCG
from ptcg.core.action import ChooseCardActionSpace
from ptcg.core.attack import Attack
from ptcg.core.enums import PlayerId

ENGINE_COMMIT = "92c3cc4fe85a26f102d7bb6b3e8be7678512d2e5"


def wire(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def private_graph(value, seen=None):
    """Diagnostic graph signature, includes object aliases and dynamic effect flags.

    This is deliberately not a restore format. Replay continuations are tested
    by executing further commands after reconstruction.
    """
    if seen is None:
        seen = {}
    if isinstance(value, str):
        # Upstream PassTurn diagnostic history stores str(Player), including
        # process-local addresses. Normalize ONLY that diagnostic representation.
        return re.sub(r"<ptcg\.core\.player\.Player object at 0x[0-9a-f]+>", "<Player diagnostic>", value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, Enum):
        return [type(value).__name__, value.name]
    if isinstance(value, (list, tuple)):
        return [private_graph(v, seen) for v in value]
    if isinstance(value, dict):
        return {str(k): private_graph(v, seen) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if id(value) in seen:
        return {"ref": seen[id(value)]}
    seen[id(value)] = len(seen)
    if hasattr(value, "getstate"):
        return {"rng": private_graph(value.getstate(), seen)}
    if hasattr(value, "__dict__"):
        return {"class": type(value).__name__, "fields": private_graph(vars(value), seen)}
    raise TypeError(f"Unsupported diagnostic value: {type(value)}")


class RejectedCommand(ValueError):
    pass


class Adapter:
    def __init__(self, seed=0, deck1="gholdengo_ex", deck2="charizard_ex"):
        self.config = dict(seed=seed, deck1=deck1, deck2=deck2)
        self.env = PokemonTCG(**self.config, record_game=False)
        self.obs, _, self.done, self.info = self.env.reset()
        self.version = 0
        self.commands = []
        self.receipts = {}
        self.failed = False

    @property
    def actions(self):
        return self.info.get("raw_available_actions", [])

    @property
    def actor(self):
        return self.actions[0].playerId if not self.done and len(self.actions) else None

    def private_digest(self):
        return digest({"state": private_graph(self.env.gamestate), "rng": private_graph(self.env.rng),
                       "done": self.done, "winner": str(self.info.get("winner"))})

    @staticmethod
    def _card(card):
        if isinstance(card, Attack):
            return {"kind": "attack", "name": card.name, "damage": card.damage,
                    "cost": [energy.name for energy in (card.cost or [])], "text": card.text}
        result = card.to_dict()
        result["kind"] = "card"
        # Upstream to_dict omits damage counters; include visible play state.
        for key in ("damage_counters", "firstTurnPlayed", "abilityUsed"):
            if hasattr(card, key):
                result[key] = getattr(card, key)
        return result

    def _candidate_order(self):
        actions = self.actions
        order = list(range(len(actions.candidates)))
        if not actions.hidden and not actions.indexed:
            # Searching a deck may reveal contents, never its internal order.
            order.sort(key=lambda n: json.dumps(self._card(actions.candidates[n]), sort_keys=True))
        return order

    def view(self, viewer: PlayerId) -> dict[str, Any]:
        if self.failed:
            raise RuntimeError("Worker must restore after engine failure")
        observation = self.env.observe(viewer)
        player = self.env.gamestate.player1 if viewer == PlayerId.PLAYER1 else self.env.gamestate.player2
        opponent = self.env.gamestate.player2 if viewer == PlayerId.PLAYER1 else self.env.gamestate.player1
        for key, current in (("self", player), ("opponent", opponent)):
            for zone in ("active", "bench"):
                observation[key][zone] = [self._card(c) for c in getattr(current, zone)]
        result = {"stateVersion": self.version, "observation": observation, "done": self.done,
                  "decision": None}
        if viewer != self.actor:
            return wire(result)
        decision = {"id": f"d{self.version}", "actor": viewer.name}
        actions = self.actions
        if isinstance(actions, ChooseCardActionSpace):
            decision.update(kind="selection", min=actions.min_cnt, max=actions.max_cnt,
                            hidden=actions.hidden, candidates=[])
            for i, original in enumerate(self._candidate_order()):
                item = {"ref": f"d{self.version}:c{i}"}
                if not actions.hidden or getattr(actions.candidates[original], 'prize_face_up', False):
                    item["card"] = self._card(actions.candidates[original])
                decision["candidates"].append(item)
        else:
            decision.update(kind="options", options=[])
            for i, action in enumerate(actions):
                # Ordinary actions operate on the actor's hand / public board.
                description = action.to_dict()
                description.pop("playerId", None)
                for role in ("source", "target", "active_pokemon"):
                    obj = getattr(action, role, None)
                    if hasattr(obj, "cardPosition"):
                        description[role + "Position"] = str(obj.cardPosition)
                        description[role + "Index"] = getattr(obj, "index", None)
                decision["options"].append({"id": f"d{self.version}:o{i}", **description})
        result["decision"] = decision
        return wire(result)

    def submit(self, viewer: PlayerId, command: dict):
        command = wire(command)
        command_id = command.get("commandId")
        if not isinstance(command_id, str) or not command_id:
            raise RejectedCommand("MISSING_COMMAND_ID")
        request_digest = digest({"viewer": viewer.name, "command": command})
        if command_id in self.receipts:
            original, receipt = self.receipts[command_id]
            if original != request_digest:
                raise RejectedCommand("IDEMPOTENCY_CONFLICT")
            return copy.deepcopy(receipt)
        if self.failed or self.done or viewer != self.actor:
            raise RejectedCommand("NOT_ACTOR")
        if command.get("expectedStateVersion") != self.version or command.get("decisionId") != f"d{self.version}":
            raise RejectedCommand("STALE_DECISION")
        actions = self.actions
        choice = command.get("choice", {})
        if isinstance(actions, ChooseCardActionSpace):
            refs = choice.get("selectedRefs", [])
            valid = {f"d{self.version}:c{i}": original for i, original in enumerate(self._candidate_order())}
            if not isinstance(refs, list) or not all(isinstance(r, str) for r in refs) or len(set(refs)) != len(refs) or any(r not in valid for r in refs):
                raise RejectedCommand("INVALID_SELECTION")
            try:
                action = actions.action_for_candidate_indices([valid[r] for r in refs])
            except (ValueError, IndexError) as exc:
                raise RejectedCommand("INVALID_SELECTION") from exc
        else:
            options = {f"d{self.version}:o{i}": i for i in range(len(actions))}
            option = choice.get("optionId")
            if not isinstance(option, str) or option not in options:
                raise RejectedCommand("INVALID_OPTION")
            action = actions[options[option]]
        try:
            self.obs, _, self.done, self.info = self.env.step(action)
        except Exception:
            self.failed = True
            raise
        self.commands.append({"viewer": viewer.name, "command": command})
        self.version += 1
        receipt = {"accepted": True, "stateVersion": self.version, "done": self.done}
        self.receipts[command_id] = (request_digest, receipt)
        return copy.deepcopy(receipt)

    def export_private_replay(self):
        return wire({"engineCommit": ENGINE_COMMIT, "config": self.config, "commands": self.commands})

    @classmethod
    def replay(cls, record):
        if record["engineCommit"] != ENGINE_COMMIT:
            raise ValueError("ENGINE_VERSION_MISMATCH")
        game = cls(**record["config"])
        for row in record["commands"]:
            game.submit(PlayerId[row["viewer"]], row["command"])
        return game


def policy(view, rng, mode="development"):
    """Two deliberately weak DTO-only baselines, never inspect upstream objects."""
    d = view["decision"]
    if d is None:
        raise ValueError("No decision")
    if d["kind"] == "selection":
        candidates = d["candidates"]
        count = rng.randint(d["min"], min(d["max"], len(candidates)))
        choice = {"selectedRefs": [c["ref"] for c in rng.sample(candidates, count)]}
    else:
        ranks = {"PlayPokemonAction": 90, "EvolvePokemonAction": 85, "AttachEnergyAction": 80,
                 "UseAbilityAction": 75, "UseSupporterAction": 70, "UseItemAction": 65,
                 "UseToolAction": 60, "PutStadiumAction": 55, "UseStadiumAction": 50,
                 "AttackAction": 40, "RetreatAction": 10, "PassTurn": 0}
        if mode == "tempo":
            ranks["AttackAction"] = 100
        if mode == "random":
            selected = rng.choice(d["options"])
        else:
            scored = [(ranks.get(o["actionType"], 5), rng.random(), o) for o in d["options"]]
            selected = max(scored, key=lambda row: row[:2])[2]
        choice = {"optionId": selected["id"]}
    return {"commandId": f"cmd{view['stateVersion']}", "expectedStateVersion": view["stateVersion"],
            "decisionId": d["id"], "choice": choice}
