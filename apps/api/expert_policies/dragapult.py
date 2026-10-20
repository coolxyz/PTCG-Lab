"""Preserve development and recognize draws; Tools get bounded current roles.

The unvalidated v7 global energy and paid-retreat overrides are withdrawn.
"""
from copy import deepcopy
from collections import Counter
import time

from apps.api.expert_policies import dragapult_tempo as tempo, dragapult_legacy as previous
from packages.battle.h2_v4.tempo import attack_value
from packages.simulation.a2 import legal

PROFILE = previous.PROFILE
VERSION = "h2-dragapult-draw-development-v9"
TERMINAL_ROUTES = ("executable-terminal-plan", "executable-draw-plan")


def ready(card, own, other):
    return max((attack_value(a, card, own, other)
                + (40 if a["name"] == "Itchy Pollen" else 30 if a["name"] == "Phantom Dive" else 0)
                for a in tempo.risk.ready_attacks(card)), default=-100)


def attack_permission(dto):
    own = dto["observation"]["self"]
    flags = own.get("turnFlags", {})
    first = flags.get("firstPersonalTurn") and (not dto.get("startingPlayer") or dto["startingPlayer"].lower() == own.get("id"))
    return not (flags.get("attacksBlocked") or first)


def crystal_card(option, own):
    card = next(c for c in tempo.field(own) if c.get("ref") == option.get("targetRef"))
    # Do not occupy a basic reserve's Tool slot for a speculative evolution.
    if card["name"] == "Dragapult ex":
        return 300 + 20 * bool(card.get("energy")) + card.get("hp", 0) / 100
    if card["name"] == "Drakloak" and card.get("energy"):
        return 200 + card.get("hp", 0) / 100
    return -100


def crystal_projection(card):
    card = deepcopy(card)
    for attack in card.get("attacks", []):
        cost = list(attack.get("cost", []))
        if cost:
            missing = Counter(cost) - Counter(card.get("energy", []))
            typed = [t for t in cost if t != "COLORLESS" and missing[t]]
            cost.remove(typed[0] if typed else "COLORLESS" if "COLORLESS" in cost else cost[0])
        attack["cost"] = cost
    return card


def current_tool(dto):
    own, other = dto["observation"]["self"], dto["observation"]["opponent"]
    if not own["active"] or not attack_permission(dto) or any(c.get("name") == "Jamming Tower" for c in dto["observation"].get("stadium", [])):
        return None
    active = own["active"][0]
    flags = own.get("turnFlags", {})
    for option in dto["decision"]["options"]:
        if option["actionType"] != "UseToolAction" or option.get("targetRef") != active.get("ref"):
            continue
        if option["source"] == "Sparkling Crystal" and active["name"] == "Dragapult ex":
            if ready(crystal_projection(active), own, other) > max(100, ready(active, own, other)):
                return option
        if option["source"] == "Rescue Board" and not flags.get("retreated") and not active.get("retreatBlocked"):
            before = len(active.get("retreatCost", []))
            after = 0 if active.get("hp", 0) <= 30 else max(0, before - 1)
            if after <= len(active.get("energy", [])) < before:
                best = max((ready(c, own, other) for c in own["bench"]), default=-100)
                if best >= 100 and best > ready(active, own, other) + 20:
                    return option
    return None


def decide(dto, *, entries, environment):
    started = time.perf_counter()
    cmd, detail = tempo.decide(dto, entries=entries, environment=environment)
    if detail.get("route") == "frozen-h2":
        return cmd, detail
    d = dto["decision"]
    reason = detail.get("reason")
    if dto.get("phase") == "playing" and d["kind"] == "options" and reason not in TERMINAL_ROUTES:
        options = d["options"]
        chosen = next(o for o in options if o["id"] == cmd["choice"]["optionId"])
        proposal = None
        if chosen["actionType"] in ("AttachEnergyAction", "RetreatAction", "PassTurn", "UseToolAction", "AttackAction"):
            proposal = current_tool(dto)
            if proposal:
                reason = "tool-enables-current-attack"
        if proposal is None and chosen["actionType"] == "UseToolAction" and chosen.get("source") == "Sparkling Crystal":
            own = dto["observation"]["self"]
            choices = [o for o in options if o["actionType"] == "UseToolAction" and o.get("source") == "Sparkling Crystal" and crystal_card(o, own) > 0]
            if choices:
                proposal = max(choices, key=lambda o: crystal_card(o, own))
                reason = "crystal-for-funded-main-line"
            else:
                filtered = {**dto, "decision": {**d, "options": [o for o in options if not (o["actionType"] == "UseToolAction" and o.get("source") == "Sparkling Crystal")]}}
                cmd, _ = tempo.decide(filtered, entries=entries, environment=environment)
                reason = "hold-crystal-without-current-recipient"
        if proposal:
            cmd = {**cmd, "choice": {"optionId": proposal["id"]}}
    assert legal(dto, cmd), "INVALID_DRAW_DEVELOPMENT_PROPOSAL"
    return cmd, {**detail, "route": VERSION, "reason": reason,
                 "aiMilliseconds": (time.perf_counter() - started) * 1000}
