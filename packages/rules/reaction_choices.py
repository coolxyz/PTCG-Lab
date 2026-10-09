"""Owner-controlled reactions resolved before a knockout stack is discarded."""

from ptcg.core.card import EnergyCard, PokemonCard
from ptcg.core.enums import CardPosition, EnergyType
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import move_cards, shuffle_cards
from packages.rules.effects import NumberOption
from packages.rules.abilities import enabled


def capture(event, state):
    owner = state.player1 if state.player1.id == event["owner"] else state.player2
    if event["sourceOwner"] == owner.id:
        return
    target = event["target"]
    for holder in owner.active + owner.bench:
        rules = list((getattr(holder, "spec", None) or {}).get("abilities", [])) if enabled(holder, state) else []
        from packages.rules.modifiers import rules as modifiers
        rules += [r["reaction"] for r in modifiers(holder, state) if r.get("reaction")]
        for rule in rules:
            if rule["kind"] != "damage_choice":
                continue
            if not rule.get("team") and holder is not target:
                continue
            if rule.get("knockout") and (not event["lethal"] or target.hp > 0):
                continue
            if rule.get("activeOnly") and not event["wasActive"]:
                continue
            if rule.get("retreat") is not None and len(holder.retreat) != rule["retreat"]:
                continue
            if rule.get("targetType"):
                from packages.rules.pokemon_types import has_type
                if not has_type(target, rule["targetType"]):
                    continue
            state.reaction_choice_queue = getattr(state, "reaction_choice_queue", []) + [(owner, holder, target, event["source"], dict(rule))]


def choose(owner, source, cards, state, minimum=1, maximum=1):
    if not cards or maximum <= 0:
        return []
    return (yield from reduce_choose_card_actions(choose_card_actions(
        owner.id, owner.id, min(minimum, len(cards)), min(maximum, len(cards)), cards,
        indexed=True, source=source), state))


def finish(state):
    from packages.rules.entry_effects import transfer
    from packages.rules.zone_effects import discard_attached
    while getattr(state, "reaction_choice_queue", []):
        # Reactions belong to the defender, even while the attacker owns turn.
        index = 0
        if len(state.reaction_choice_queue) > 1:
            owner = state.reaction_choice_queue[0][0]
            options = [NumberOption(owner, i, r.get("name", h.name)) for i, (p, h, _, _, r) in enumerate(state.reaction_choice_queue) if p is owner]
            if len(options) > 1:
                answer = yield (state.get_obs(owner.id), 0, False, {"raw_available_actions": options})
                index = answer.value
        owner, holder, target, source, rule = state.reaction_choice_queue.pop(index)
        opponent = state.player2 if owner is state.player1 else state.player1
        if rule.get("optional"):
            answer = yield (state.get_obs(owner.id), 0, False, {"raw_available_actions": [
                NumberOption(owner, 0, "不使用"), NumberOption(owner, 1, rule.get("name", "使用特性"))]})
            if not answer.value:
                continue
        op = rule["operation"]
        if op == "discard_attacker_energy":
            from packages.rules.ability_protection import blocked
            if not rule.get('tool') and blocked(source,holder,state):continue
            cards = yield from choose(owner, holder, [e for e in source.attachment if isinstance(e, EnergyCard)], state)
            discard_attached(source, cards, opponent)
        elif op == "search_hand":
            cards = yield from choose(owner, holder, list(owner.left), state, 0 if rule.get("upTo") else 1, rule.get("count", 1))
            move_cards(cards, (owner.id, CardPosition.LEFT), (owner.id, CardPosition.HAND), state)
            shuffle_cards(owner.left, state)
        elif op == "draw":
            move_cards(list(owner.left[:rule["count"]]), (owner.id, CardPosition.LEFT), (owner.id, CardPosition.HAND), state)
        elif op == "move_attacker_energy":
            from packages.rules.ability_protection import blocked
            if not rule.get('tool') and blocked(source,holder,state):continue
            if opponent.bench:
                cards = yield from choose(owner, holder, [e for e in source.attachment if isinstance(e, EnergyCard)], state)
                if cards:
                    recipient = (yield from choose(owner, holder, list(opponent.bench), state))[0]
                    transfer(source, recipient, cards)
        elif op == "random_hand":
            cards = list(opponent.hand)
            shuffle_cards(cards, state)
            move_cards(cards[:rule["count"]], (opponent.id, CardPosition.HAND), (opponent.id, CardPosition.DISCARD), state)
        elif op == "search_bench":
            pool = [c for c in owner.left if isinstance(c, PokemonCard) and rule["substring"] in c.name]
            cards = yield from choose(owner, holder, pool, state, 0, min(rule["count"], owner.benchSize-len(owner.bench)))
            move_cards(cards, (owner.id, CardPosition.LEFT), (owner.id, CardPosition.BENCH), state)
            shuffle_cards(owner.left, state)
        else:
            pool = [e for e in target.attachment if isinstance(e, EnergyCard)
                    and (not rule.get("basic") or e.energyType == EnergyType.BASIC)
                    and (not rule.get("type") or any(energy_matches(t, rule["type"]) for t in e.provides))]
            if op == "recover_energy":
                for e in pool:
                    target.attachment.remove(e)
                    owner.hand.append(e)
                    e.cardPosition, e.index = CardPosition.HAND, len(owner.hand)
                from packages.rules.core_fixes import refresh_energy
                target.dynamic_energy = True
                refresh_energy(target)
            else:
                recipients = [holder] if rule.get("toSelf") else list(owner.bench)
                if not pool or not recipients:
                    continue
                cards = yield from choose(owner, holder, pool, state, 0, rule["count"])
                if cards:
                    if rule.get("spread"):
                        for energy in cards:
                            chosen = yield from choose(owner, holder, recipients, state)
                            transfer(target, chosen[0], [energy])
                    else:
                        chosen = yield from choose(owner, holder, recipients, state)
                        transfer(target, chosen[0], cards)

from packages.rules.energy_units import matches as energy_matches
