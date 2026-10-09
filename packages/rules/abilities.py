"""Shared activated abilities. Costs are paid before effects; no attack turn end."""

from packages.rules.maximum_hp import maximum


from ptcg.core.ability import ActiveAbility, PassiveAbility
from ptcg.core.action import UseAbilityAction, choose_card_actions
from ptcg.core.enums import AbilityType, CardPosition, EnergyType, CardType
from ptcg.core.card import EnergyCard
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import (
    current_player,
    opponent_player,
    move_cards,
    shuffle_cards,
    switch_pokemon,
)
from packages.rules.core_fixes import refresh_energy


def initialize(source):
    if source.spec.get("abilities"):
        source.abilityUsed = False
    source.ability = []
    for rule in source.spec.get("abilities", []):
        passive = rule["trigger"] != "activated"
        cls = PassiveAbility if passive else ActiveAbility
        source.ability.append(
            cls(
                {
                    "name": rule["name"],
                    "text": rule["text"],
                    "abilityType": AbilityType.PASSIVE_ABILITY
                    if passive
                    else AbilityType.ACTIVE_ABILITY,
                    "onceUsedPerTurn": not passive
                    and rule.get("usageLimit") != "unlimited",
                }
            )
        )


def enabled(source, state):
    from packages.rules.suppression import enabled as resolved
    return resolved(source, state)


def energies(rule, player):
    return [
        c
        for c in player.hand
        if isinstance(c, EnergyCard)
        and c.energyType == EnergyType.BASIC
        and c.cardType == CardType[rule["type"]]
    ]


def available(source, state):
    p = current_player(state)
    if (
        not source.spec.get("abilities")
        or source not in p.active + p.bench
        or source.abilityUsed
        or not enabled(source, state)
    ):
        return []
    out = []
    for rule, ability in zip(source.spec.get("abilities", []), source.ability):
        kind = rule["kind"]
        if kind == "hand_bench":
            continue
        if kind == "activated_effect":
            from packages.rules.activated_effects import available as effect_available

            if not effect_available(source, rule, state):
                continue
        if rule.get("activeOnly") and source not in p.active:
            continue
        if rule.get("sharedName") in getattr(p, "used_named_abilities", []):
            continue
        if kind in ("draw_until", "search_any") and not p.left:
            continue
        if kind == "search_hand" and not p.left:
            continue
        if kind in ("search_bench", "search_attach") and not p.left:
            continue
        if kind == "search_bench" and len(p.bench) >= p.benchSize:
            continue
        if kind == "attach_energy":
            from packages.rules.ability_energy import candidates

            cards, targets = candidates(rule, p)
            if not cards or not targets:
                continue
        if rule.get("stadiumRequired") and not state.stadium:
            continue
        if kind == "reveal_opponent_hand" and not opponent_player(state).hand:
            continue
        if kind == "recover_active_status":
            from ptcg.core.enums import SpecialCondition

            active = p.active[0]
            if not (
                getattr(active, "poisoned", False)
                or getattr(active, "burned", False)
                or getattr(active, "special_condition", SpecialCondition.NONE)
                != SpecialCondition.NONE
            ):
                continue
        if kind == "draw_until" and len(p.hand) >= rule["count"]:
            continue
        if kind == "heal" and not any(healed(c,1,state)>c.hp for c in p.active + p.bench):
            continue
        if rule["trigger"] != "activated":
            continue
        if kind == "switch" and not p.bench:
            continue
        if kind == "draw" and not p.left:
            continue
        if kind == "discard_draw" and (len(p.hand) < rule["cost"] or not p.left):
            continue
        if kind == "bottom_hand_draw" and not p.hand:
            continue
        if kind == "both_draw" and not (p.left or opponent_player(state).left):
            continue
        if kind == "attach_draw" and (
            not energies(rule, p) or (rule["target"] == "bench" and not p.bench)
        ):
            continue
        out.append(UseAbilityAction(p.id, source, ability))
    return out


def resolve(source, action, state):
    rule = source.spec["abilities"][source.ability.index(action.ability)]
    p = current_player(state)
    source.abilityUsed = rule.get("usageLimit") != "unlimited"
    kind = rule["kind"]
    if kind == "hand_bench":
        move_cards(source, (p.id, CardPosition.HAND), (p.id, CardPosition.BENCH), state)
        source.firstTurnPlayed = True
        return
    if rule.get("sharedName"):
        p.used_named_abilities = getattr(p, "used_named_abilities", []) + [
            rule["sharedName"]
        ]
    if kind == "activated_effect":
        from packages.rules.activated_effects import resolve as resolve_effect

        yield from resolve_effect(source, rule, state)
        return
    if kind == "attach_energy":
        from packages.rules.ability_energy import resolve as resolve_energy

        yield from resolve_energy(source, rule, state)
        return
    if kind in ("search_hand", "search_bench", "search_attach", "reveal_opponent_hand"):
        from packages.rules.zone_effects import resolve as resolve_zone

        yield from resolve_zone(rule, action, state)
        return
    if kind == "recover_active_status":
        for attr in ("special_condition", "poisoned", "poison_damage", "burned"):
            if hasattr(p.active[0], attr):
                delattr(p.active[0], attr)
        return
    if kind == "poison_active":
        target = opponent_player(state).active[0]
        target.poisoned = True
        target.poison_damage = 10
        return
    if kind == "search_any":
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, list(p.left), source=source), state
        )
        move_cards(chosen, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        shuffle_cards(p.left, state)
        return
    if kind == "heal":
        choices = [c for c in p.active + p.bench if healed(c,1,state)>c.hp]
        targets = (
            choices
            if rule.get("all")
            else (
                yield from reduce_choose_card_actions(
                    choose_card_actions(p.id, p.id, 1, 1, choices, source=source), state
                )
            )
        )
        for target in targets:
            target.hp = healed(target, rule["amount"], state, record=True)
        return
    if kind == "switch":
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, list(p.bench), source=source), state
        )
        switch_pokemon(p.active[0], chosen[0], p)
        return
    if kind == "discard_draw":
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(
                p.id, p.id, rule["cost"], rule["cost"], list(p.hand), source=source
            ),
            state,
        )
        move_cards(
            chosen, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state
        )
    elif kind == "bottom_hand_draw":
        chosen = list(p.hand)
        shuffle_cards(chosen, state)
        move_cards(chosen, (p.id, CardPosition.HAND), (p.id, CardPosition.LEFT), state)
    elif kind == "self_counter_draw":
        source.hp -= 10
    elif kind == "attach_draw":
        chosen = yield from reduce_choose_card_actions(
            choose_card_actions(p.id, p.id, 1, 1, energies(rule, p), source=source),
            state,
        )
        target = source
        if rule["target"] == "bench":
            target = (
                yield from reduce_choose_card_actions(
                    choose_card_actions(p.id, p.id, 1, 1, list(p.bench), source=source),
                    state,
                )
            )[0]
        zone = (
            CardPosition.ACTIVE_ATTACHMENT
            if target in p.active
            else CardPosition.BENCH_ATTACHMENT
        )
        move_cards(chosen, (p.id, CardPosition.HAND), (p.id, zone, target.index), state)
        target.dynamic_energy = True
        refresh_energy(target)
    for player in [p, opponent_player(state)] if kind == "both_draw" else [p]:
        count = (
            max(0, rule["count"] - len(p.hand))
            if kind == "draw_until"
            else rule["count"]
        )
        move_cards(
            list(player.left[:count]),
            (player.id, CardPosition.LEFT),
            (player.id, CardPosition.HAND),
            state,
        )
    if source.hp <= 0:
        from packages.rules.knockouts import resolve_group

        yield from resolve_group(state)


def armor(source, state):
    if not enabled(source, state):
        return 0
    return sum(
        r["amount"]
        for r in getattr(source, "spec", {}).get("abilities", [])
        if r["kind"] == "armor"
    )

from packages.rules.healing import value as healed
