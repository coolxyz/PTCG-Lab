"""A1 v2: deterministic DTO-only priorities for the two reviewed archetypes.
No engine/state import, real seed, opponent deck, or private replay is accepted.
"""

import copy
from packages.simulation.resources import (
    RAIN,
    basic_energy,
    energy_goal,
    rain_attacker,
    rain_damage,
    resource_bonus,
)

from packages.simulation.heuristic import value as evaluate_card, tags, attachment_value

VERSION = "a1-general-v4-sync-recovery"

RANKS = {
    "PlayPokemonAction": 90,
    "EvolvePokemonAction": 100,
    "AttachEnergyAction": 85,
    "UseAbilityAction": 80,
    "UseSupporterAction": 65,
    "UseItemAction": 55,
    "UseToolAction": 50,
    "PutStadiumAction": 40,
    "UseStadiumAction": 35,
    "AttackAction": 60,
    "RetreatAction": -5,
    "PassTurn": -10,
}


def card_value(card, own, opponent=None):
    return evaluate_card(card, own, opponent or {"active": []})


def predict(view):
    view = copy.deepcopy(view)
    if view["observation"]["opponent"]["hand"] is not None:
        raise ValueError("HIDDEN_HAND_EXPOSED")
    d, obs = view["decision"], view["observation"]
    if not d:
        raise ValueError("NO_DECISION")
    own, opponent = obs["self"], obs["opponent"]
    reason = "依据可见场面与资源优先级选择"
    if d["kind"] == "selection":
        candidates = d["candidates"]
        # Costs are selected conservatively; optional deck searches choose useful
        # resources. Explicit separate prompts handle all multi-step continuations.
        phase = view.get("phase")
        source = d.get("source") or ""
        is_cost = d.get("zones") == ["CardPosition.HAND"] and phase == "playing"
        count = d["min"] if is_cost else min(d["max"], len(candidates))
        if phase == "active":
            count = 1
        if source == "Gholdengo ex" and all(
            basic_energy(c.get("card", {})) for c in candidates
        ):
            count = min(d["max"], max(d["min"], energy_goal(own, opponent)))

        def score(item):
            c = item.get("card", {})
            v = card_value(c, own, opponent)
            if phase == "active":
                v += max(0, 30 - 10 * len(c.get("retreatCost", [])))
            if c.get("kind") == "attack":
                v = c.get("damage", 0) or 0
            return v

        ordered = sorted(candidates, key=score, reverse=not is_cost)
        choice = {"selectedRefs": [c["ref"] for c in ordered[:count]]}
        reason = (
            "完成当前选牌；优先保留进化链与所需资源"
            if not d["hidden"]
            else "从未知卡牌中选择，不读取其身份"
        )
    else:

        def score(o):
            kind, source = o["actionType"], o.get("source", "")
            value = RANKS.get(kind, 5)
            if kind == "SetupOption":
                return 20 if o.get("value") == "first" else 0
            source_card = next(
                (
                    c
                    for c in own["hand"] + own["active"] + own["bench"]
                    if c.get("id") == o.get("sourceId")
                ),
                {},
            )
            if kind == "PlayPokemonAction":
                value += card_value(source_card, own, opponent) / 3
                if len(own["bench"]) >= 4 and source in {
                    c["name"] for c in own["bench"]
                }:
                    value -= 70
            if kind == "AttachEnergyAction":
                value += attachment_value(o, own)
            if kind == "UseAbilityAction":
                if "draw_choice" in tags(source_card):
                    value += 20
                if (
                    "return_to_deck" in tags(source_card)
                    and len(own["active"] + own["bench"]) < 2
                ):
                    value -= 150
            if kind == "UseSupporterAction":
                value += 20 if len(own["hand"]) <= 3 else -15
            if kind in ("UseItemAction", "UseSupporterAction"):
                card = next(
                    (c for c in own["hand"] if c.get("id") == o.get("sourceId")), {}
                )
                value += resource_bonus(card, own, opponent)
            if kind == "AttackAction":
                damage = (o.get("attack") or {}).get("damage") or 0
                if (o.get("attack") or {}).get("name") == RAIN:
                    attacker = rain_attacker(own) or (own["active"] or [{}])[0]
                    damage = rain_damage(
                        sum(basic_energy(c) for c in own["hand"]),
                        attacker,
                        (opponent["active"] or [{}])[0],
                    )
                    if damage == 0:
                        value = (
                            -20
                        )  # A known damage-only attack, not all zero-damage moves.
                if opponent["active"] and damage >= opponent["active"][0].get(
                    "hp", 999
                ):
                    value += 100
                value += min(25, damage / 10)
                recovery = (o.get("attack") or {}).get("recovery")
                if recovery and own["active"]:
                    attacker = own["active"][0]
                    missing = max(0, attacker.get("maximumHp", attacker.get("hp", 0)) - attacker.get("hp", 0))
                    heal = min(missing, recovery.get("amount", 0))
                    condition = attacker.get("special_condition", attacker.get("specialCondition"))
                    afflicted = attacker.get("poisoned") or attacker.get("burned") or condition not in (None, "NONE", "")
                    benefit = heal / 5 + (18 if recovery.get("cure") and afflicted else 0)
                    value += benefit if benefit else (-90 if not damage else 0)
            return value

        option = max(d["options"], key=score)
        choice = {"optionId": option["id"]}
    return {
        "commandId": f"ai-{view['stateVersion']}",
        "expectedStateVersion": view["stateVersion"],
        "decisionId": d["id"],
        "choice": choice,
    }, reason
