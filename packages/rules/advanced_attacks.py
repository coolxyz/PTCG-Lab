"""Targeted and window attacks with choices made before simultaneous damage."""

from ptcg.core.card import EnergyCard, PokemonCard, ToolCard
from ptcg.core.enums import CardPosition, CardType, EnergyType, Coin, PokemonRule
from ptcg.core.reducer import reduce_attack_damage, _calculate_damage
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, next_turn, flip_coin
from packages.rules.entry_effects import choice, reveal
from packages.rules.protection import blocked


def targeted(source, amounts, state, ignore_wr=False):
    from packages.rules.damage_events import deal
    from packages.rules.core_fixes import shield_damage
    from packages.rules.modifiers import attack_damage
    o = opponent_player(state)
    for target, amount in amounts.items():
        actual = (_calculate_damage(source, target, amount, state)
                  if target in o.active and not ignore_wr else
                  shield_damage(target, attack_damage(source, target, amount, state), state, source))
        deal(source, target, actual, state)
        current_player(state).reward.apply_damage_dealt_reward(actual)


def resolve(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, op = action.source, r["op"]
    targets, selected, window = {}, [], []
    if op == "window_damage":
        window = list(p.left[:r["count"]])
        reveal(window, state, p)
        selected = [c for c in window if isinstance(c, EnergyCard)] if r["filter"] == "energy" else [c for c in window if getattr(c, "pokemonRule", None) == PokemonRule.FUTURE or getattr(c, "future", False) or (getattr(c, "spec", None) or {}).get("future")]
        amount = len(selected) * r["factor"]
    elif op == "hand_target":
        selected = yield from choice(source, [c for c in p.hand if isinstance(c, EnergyCard)], state, 0, r["count"])
        move_cards(selected, (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        amount = len(selected) * r["factor"]
    elif op == "coin_target":
        amount = r["amount"] if flip_coin(state) == Coin.HEAD else 0
    elif op == "opponent_tail_damage":
        amount = sum(flip_coin(state, o) == Coin.TAIL for _ in o.bench) * r["factor"]
        action.attack.damage = amount
        yield from reduce_attack_damage(action, state, apply_weakness_resistance=False)
        next_turn(state)
        return
    elif op == "repeat_target":
        for _ in range(r["count"]):
            picked = yield from choice(source, list(o.active + o.bench), state)
            if picked:
                target = picked[0]
                targets[target] = targets.get(target, 0) + r["amount"]
        targeted(source, targets, state, ignore_wr=True)
    if op != "repeat_target":
        if r.get("target"):
            picked = yield from choice(source, list(o.active + o.bench), state)
            if picked:
                targets[picked[0]] = amount
                targeted(source, targets, state)
        else:
            action.attack.damage = amount
            def after(action, state):
                move_cards(selected, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
                shuffle_cards(p.left, state)
                if False:
                    yield
            yield from reduce_attack_damage(action, state, after_damage=after)
            next_turn(state)
            return
    if op == "window_damage":
        move_cards(selected, (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
        shuffle_cards(p.left, state)
    from packages.rules.knockouts import resolve_group
    yield from resolve_group(state, damage_targets=list(targets))
    next_turn(state)


OPERATIONS = {"discard_field_units", "return_enemy_energy", "typed_pokemon_search", "distinct_energy_search", "discard_energy_counters", "strip_before", "pokemon_only_return", "attach_opponent", "coin_mill_names", "replace_pokemon", "weakness_override", "target_turn_flag", "delayed_prizes", "future_shield", "coin_knockout", "own_named_damage", "peek_prize"}


def field(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target, op = action.source, action.target, r["operation"]
    from packages.rules.zone_effects import discard_attached
    if op == "peek_prize":
        from packages.rules.prizes import choose
        cards = yield from choose(o, state, 1, source, [c for c in o.prize if not getattr(c, "prize_face_up", False)])
        if cards:
            yield from choice(source, cards, state)
    elif op == "coin_knockout":
        if flip_coin(state) == Coin.HEAD and not blocked(target, state, "effects", source):
            target.hp = 0
        action.group_damage_targets = [source]
    elif op == "own_named_damage":
        from packages.rules.core_fixes import shield_damage
        from packages.rules.damage_events import deal
        for c in p.active + p.bench:
            if c.name in r["names"]:
                deal(source, c, shield_damage(c, r["amount"], state, source), state)
    elif op == "target_turn_flag":
        if not blocked(target, state, "effects", source):
            setattr(target, r["flag"], state.turn_number + 1)
    elif op == "delayed_prizes":
        if not blocked(target, state, "effects", source):
            target.delayed_prize_bonus = {"turn": state.turn_number + 2, "count": r["count"]}
    elif op == "future_shield":
        source.future_team_shield = state.turn_number + 1
    elif op == "replace_pokemon":
        if flip_coin(state) == Coin.HEAD:
            from packages.rules.pokemon_replacement import choose_replace
            yield from choose_replace(source, p, state)
        action.group_damage_targets = []
    elif op == "weakness_override":
        if not blocked(target, state, "effects", source):
            target.weakness_override = {"until": state.turn_number + 2, "type": r["type"]}
    elif op == "discard_field_units":
        from packages.rules.core_fixes import refresh_energy
        for c in p.active + p.bench:
            refresh_energy(c)
        pairs = [(c, e) for c in p.active + p.bench for e in c.attachment if isinstance(e, EnergyCard) and any(energy_matches(t, r["type"]) for t in e.provides)]
        remaining = r["count"]
        while pairs and remaining > 0:
            e = (yield from choice(source, [e for _, e in pairs], state))[0]
            holder = next(c for c, card in pairs if card is e)
            remaining -= sum(energy_matches(t, r["type"]) for t in e.provides)
            discard_attached(holder, [e], p)
            pairs = [(c, card) for c, card in pairs if card is not e]
    elif op == "return_enemy_energy":
        from packages.rules.energy_selection import choose_units
        cards = yield from choose_units(target, r["count"], action, state)
        if not blocked(target, state, "effects", source):
            for c in cards:
                target.attachment.remove(c)
                vars(c).clear()
                vars(c).update(vars(type(c)()))
                c.cardPosition = CardPosition.LEFT
                o.left.append(c)
            shuffle_cards(o.left, state)
            from packages.rules.core_fixes import refresh_energy
            target.dynamic_energy = True
            refresh_energy(target)
            for i, c in enumerate(target.attachment):
                c.index = i + 1
    elif op in ("typed_pokemon_search", "distinct_energy_search"):
        if op == "typed_pokemon_search":
            from packages.rules.pokemon_types import has_type
            types = {e.cardType for e in source.attachment if isinstance(e, EnergyCard) and e.energyType == EnergyType.BASIC}
            pool = [c for c in p.left if isinstance(c, PokemonCard) and any(has_type(c, t) for t in types)]
            cards = yield from choice(source, pool, state, 0, r["count"])
        else:
            pool = [c for c in p.left if isinstance(c, EnergyCard) and c.energyType == EnergyType.BASIC]
            cards = []
            while pool and len(cards) < r["count"]:
                picked = yield from choice(source, pool, state, 0, 1)
                if not picked:
                    break
                cards += picked
                pool = [c for c in pool if c.cardType != picked[0].cardType]
        reveal(cards, state, p)
        move_cards(cards, (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
        shuffle_cards(p.left, state)
    elif op == "discard_energy_counters":
        cards = [c for c in p.discard if isinstance(c, EnergyCard) and c.energyType == EnergyType.BASIC and c.cardType == CardType[r["type"]]]
        reveal(cards, state, p)
        chosen = yield from choice(source, list(o.active + o.bench), state)
        if chosen and not blocked(chosen[0], state, "counters", source):
            chosen[0].hp -= len(cards) * r["amount"]
        move_cards(cards, (p.id, CardPosition.DISCARD), (p.id, CardPosition.LEFT), state)
        shuffle_cards(p.left, state)
        action.group_damage_targets = []
    elif op == "strip_before":
        cards = [c for c in target.attachment if isinstance(c, ToolCard) or r.get("special") and isinstance(c, EnergyCard) and c.energyType == EnergyType.SPECIAL]
        if not blocked(target, state, "effects", source):
            discard_attached(target, cards, o)
            if cards and r.get("paralyze"):
                from ptcg.core.enums import SpecialCondition
                from packages.rules.status_immunity import apply_status
                apply_status(target, SpecialCondition.PARALYZED)
    elif op == "pokemon_only_return":
        if not (yield from choice(source, [source], state, 0, 1)):
            return
        from packages.rules.field_moves import return_stack
        stack = return_stack(source, p, "hand")
        move_cards([c for c in stack if not isinstance(c, PokemonCard)], (p.id, CardPosition.HAND), (p.id, CardPosition.DISCARD), state)
        action.group_damage_targets = [target] if getattr(action, "damage_dealt", 0) else []
    elif op == "attach_opponent":
        from packages.rules.core_fixes import refresh_energy
        cards = yield from choice(source, [c for c in o.discard if isinstance(c, EnergyCard)], state, 0, r["count"])
        for c in cards:
            chosen = yield from choice(source, list(o.active + o.bench), state)
            if chosen and not blocked(chosen[0], state, "effects", source):
                recipient = chosen[0]
                move_cards(c, (o.id, CardPosition.DISCARD), (o.id, CardPosition.ACTIVE_ATTACHMENT if recipient in o.active else CardPosition.BENCH_ATTACHMENT, recipient.index), state)
                recipient.dynamic_energy = True
                refresh_energy(recipient)
    elif op == "coin_mill_names":
        count = sum(c.name == r["name"] for c in p.active + p.bench)
        n = sum(flip_coin(state) == Coin.HEAD for _ in range(count)) * r["count"]
        move_cards(list(o.left[:n]), (o.id, CardPosition.LEFT), (o.id, CardPosition.DISCARD), state)

from packages.rules.energy_units import matches as energy_matches
