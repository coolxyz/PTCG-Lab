"""Attack-only field operations with effect protection and physical ownership."""

from ptcg.core.enums import CardPosition, SpecialCondition, Stage, CardType
from ptcg.core.card import PokemonCard, EnergyCard
from ptcg.core.action import choose_card_actions
from ptcg.core.reducer import reduce_choose_card_actions
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, switch_pokemon
from packages.rules.entry_effects import choice
from packages.rules.protection import blocked


def resolve(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    op = r["operation"]
    from packages.rules.expanded_operations import OPERATIONS as EXPANDED, resolve as expanded
    if op in EXPANDED:
        yield from expanded(r, action, state)
        return
    from packages.rules.advanced_attacks import OPERATIONS as ADVANCED, field
    from packages.rules.remaining_attacks import OPERATIONS, resolve as remaining
    from packages.rules.transfer_attacks import OPERATIONS as TRANSFERS, resolve as transfer
    if op in ADVANCED:
        yield from field(r, action, state)
    elif op in TRANSFERS:
        yield from transfer(r, action, state)
    elif op in OPERATIONS:
        yield from remaining(r, action, state)
    elif op == "copy_supporter":
        from packages.rules.supporter_copy import resolve as copy_supporter
        yield from copy_supporter(source, state)
    elif op == "fluorite":
        from packages.rules.zone_effects import discard_attached
        from packages.rules.healing import value
        discard_attached(source, [e for e in source.attachment if isinstance(e, EnergyCard)], p)
        for c in p.active + p.bench:
            if c.pokemonRule.name == "TERA":
                c.hp = value(c, None, state, record=True)
    elif op == "discard_energy_types":
        from packages.rules.energy_selection import choose_types
        from packages.rules.zone_effects import discard_attached
        cards = yield from choose_types(source, r["types"], action, state)
        discard_attached(source, cards, p)
    elif op == "discard_all_special":
        from ptcg.core.enums import EnergyType
        from packages.rules.zone_effects import discard_attached
        for c in o.active+o.bench:
            if not blocked(c,state,"effects",source):
                discard_attached(c,[e for e in c.attachment if isinstance(e,EnergyCard) and e.energyType != EnergyType.BASIC],o)
    elif op == "return_own_bench":
        from packages.rules.field_moves import return_stack
        cards = yield from choice(source,list(p.bench),state)
        for c in cards:
            return_stack(c,p,"left")
        shuffle_cards(p.left,state)
    elif op == "opponent_reset":
        move_cards(list(o.hand),(o.id,CardPosition.HAND),(o.id,CardPosition.LEFT),state)
        shuffle_cards(o.left,state)
        move_cards(list(o.left[:r["count"]]),(o.id,CardPosition.LEFT),(o.id,CardPosition.HAND),state)
    elif op == "remaining_hp":
        targets = list(o.active if r["zone"] == "active" else o.bench if r["zone"] == "bench" else o.active+o.bench)
        if r.get("select"):
            targets = yield from choice(source,targets,state)
        for c in targets:
            if not blocked(c,state,"counters",source):
                c.hp = min(c.hp,r["remaining"])
    elif op == "hand_interference":
        from packages.rules.zone_effects import matches
        from packages.rules.entry_effects import reveal
        reveal(list(o.hand),state,o)
        cards = [c for c in o.hand if matches(c,r["filter"])]
        if not r.get("all"):
            cards = yield from choice(source,cards,state,1,r["count"])
        move_cards(cards,(o.id,CardPosition.HAND),(o.id,CardPosition[r["destination"].upper()]),state)
    elif op == "order_deck":
        owner = o if r.get("opponent") else p
        if r.get("either"):
            from packages.rules.effects import NumberOption
            option = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": [NumberOption(p, 0, "自己的牌库"), NumberOption(p, 1, "对手的牌库")]})
            owner = o if option.value else p
        rest = list(owner.left[:r["count"]])
        ordered = []
        while rest:
            c = (yield from choice(source,rest,state))[0]
            rest.remove(c)
            ordered.append(c)
        owner.left[:] = ordered+[c for c in owner.left if c not in ordered]
        for i,c in enumerate(owner.left):
            c.index = i+1
    elif op == "peek_top":
        cards = yield from choice(source,list(p.left[:1]),state,0,1)
        move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition[r["destination"].upper()]),state)
    elif op == "timed_modifier":
        recipient = source if r.get("self") else target
        if recipient is target and blocked(target, state, "effects", source):
            return
        until = state.turn_number + (2 if r.get("nextOwnTurn") else 1)
        recipient.timed_modifiers = [x for x in getattr(recipient,"timed_modifiers",[]) if x.get("key") != r["key"]] + [{**r["modifier"],"turn":until,"key":r["key"]}]
    elif op == "attack_coin_check":
        if not blocked(target,state,"effects",source):
            target.attack_coin_check = {"turn":state.turn_number+1,"count":r["count"]}
    elif op == "named_lock":
        from packages.rules.effects import NumberOption
        if target.attacks and not blocked(target,state,"effects",source):
            selected = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,i,a.name) for i,a in enumerate(target.attacks)]})
            target.attack_locks = {**getattr(target,"attack_locks",{}), target.attacks[selected.value].name:state.turn_number+1}
    elif op == "player_lock":
        player = p if r.get("self") else o
        for flag in r["flags"]:
            setattr(player,flag,state.turn_number+(2 if r.get("self") else 1))
    elif op == "evolution_lock":
        if not blocked(target,state,"effects",source):
            target.evolution_blocked_turn=state.turn_number+1
    elif op == "self_retreat_lock":
        source.retreat_blocked_turn=state.turn_number+2
    elif op == "choice_trainer_lock":
        from packages.rules.effects import NumberOption
        selected = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,0,"禁止物品"),NumberOption(p,1,"禁止支援者")]})
        setattr(o,"item_blocked_turn" if selected.value == 0 else "supporter_blocked_turn",state.turn_number+1)
    elif op == "conditional":
        checks = {
            "stadium": bool(state.stadium),
            "second_first": p.firstTurn and p.id != state.starting_player,
            "one_prize": len(p.prize) == 1,
            "dragon_target": has_type(target, CardType.DRAGON),
            "small_deck": len(p.left) <= r.get("maximum", 0),
            "bench_name": any(c.name == r.get("name") for c in p.bench),
        }
        if checks[r["condition"]]:
            from packages.rules.plain import PlainPokemon
            for child in r["effects"]:
                yield from PlainPokemon.resolve_mechanic(source, child, action, state)
    elif op == "allocate_counters":
        targets = list(o.bench if r.get("zone") == "bench" else o.active + o.bench)
        for _ in range(r["count"]):
            chosen = yield from choice(source, targets, state)
            if chosen and not blocked(chosen[0], state, "counters", source):
                chosen[0].hp -= 10
        # Fainting is checked only after the entire allocation, including
        # legal allocation beyond a target's remaining HP.
        action.group_damage_targets = [target] if getattr(action, "damage_dealt", 0) else []
    elif op == "both_draw":
        for player in (p, o):
            move_cards(list(player.left[:r["count"]]), (player.id, CardPosition.LEFT), (player.id, CardPosition.HAND), state)
    elif op == "recover_bench":
        cards = [c for c in p.discard if isinstance(c, PokemonCard)
                 and (not r.get("type") or has_type(c, CardType[r["type"]]))
                 and (not r.get("name") or c.name == r["name"])]
        count = min(r["count"], max(0, p.benchSize - len(p.bench)))
        if count:
            selected = yield from choice(source, cards, state, 0 if r.get("optional") else 1, count)
            move_cards(selected, (p.id, CardPosition.DISCARD), (p.id, CardPosition.BENCH), state)
    elif op == "gust_status":
        if not o.bench or blocked(target, state, "effects", source):
            return
        chooser = o if r.get("opponentChooses") else p
        selected = yield from reduce_choose_card_actions(choose_card_actions(chooser.id, chooser.id, 1, 1, list(o.bench), source=source), state)
        new = selected[0]
        switch_pokemon(target, new, o)
        if not blocked(new, state, "effects", source):
            from packages.rules.status_immunity import apply_status
            apply_status(new, SpecialCondition[r["status"]])
    elif op in ("return_opponent_energy", "move_opponent_energy"):
        if blocked(target, state, "effects", source):
            return
        if r.get("optional") and not (yield from choice(source, [source], state, 0, 1)):
            return
        from packages.rules.energy_selection import choose_units
        from packages.rules.entry_effects import transfer
        if op == "move_opponent_energy" and not o.bench:
            return
        cards = yield from choose_units(target, r.get("count",1), action, state)
        if cards:
            if op == "move_opponent_energy":
                recipient = (yield from choice(source, list(o.bench), state))[0]
                transfer(target, recipient, cards)
            else:
                from packages.rules.core_fixes import refresh_energy
                for c in cards:
                    target.attachment.remove(c)
                    fresh = type(c)()
                    vars(c).clear()
                    vars(c).update(vars(fresh))
                    o.hand.append(c)
                for i,c in enumerate(o.hand):
                    c.index, c.cardPosition = i+1, CardPosition.HAND
                for i,c in enumerate(target.attachment):
                    c.index = i+1
                target.dynamic_energy = True
                refresh_energy(target)
    elif op == "player_reset":
        from packages.rules.effects import NumberOption
        selected = yield (state.get_obs(p.id), 0, False, {"raw_available_actions":[NumberOption(p,0,"选择自己"),NumberOption(p,1,"选择对手")]})
        player = p if selected.value == 0 else o
        move_cards(list(player.hand), (player.id, CardPosition.HAND), (player.id, CardPosition.LEFT), state)
        shuffle_cards(player.left,state)
        move_cards(list(player.left[:r["count"]]), (player.id, CardPosition.LEFT), (player.id, CardPosition.HAND), state)
    elif op == "chosen_status":
        from packages.rules.effects import NumberOption
        labels = {"POISONED":"中毒", "BURNED":"灼伤", "ASLEEP":"睡眠", "PARALYZED":"麻痹", "CONFUSED":"混乱"}
        selected = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,i,labels[status]) for i,status in enumerate(r["statuses"])]})
        if not blocked(target,state,"effects",source):
            status = r["statuses"][selected.value]
            if status in ("POISONED","BURNED"):
                setattr(target,status.lower(),True)
                if status == "POISONED": target.poison_damage=10
            else:
                from packages.rules.status_immunity import apply_status
                apply_status(target,SpecialCondition[status])
    else:
        raise ValueError("Unknown attack operation: " + op)

from packages.rules.pokemon_types import has_type
