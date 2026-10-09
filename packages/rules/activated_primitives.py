"""Composable activated abilities: costs, private deck windows and field moves."""

from ptcg.core.card import EnergyCard
from ptcg.core.enums import CardPosition, CardType, Coin, EnergyType
from ptcg.utils.utils import current_player, opponent_player, move_cards, switch_pokemon, flip_coin

KINDS = {"sequence", "coin_effect", "switch_self", "opponent_switch", "move_counter", "move_energy", "draw_until", "team_damage", "both_poison", "burn", "peek", "order_opponent_top", "discard_self_energy", "return_self_deck", "move_basic", "choose_status", "evolve_named", "bottom_leap", "both_recover_bench"}


def cost_cards(source, rule, player):
    cost = rule.get("cost", {})
    pool = source.attachment if cost.get("origin") == "attached" else player.hand
    return [c for c in pool if
            (not cost.get("energy") or isinstance(c, EnergyCard))
            and (not cost.get("basic") or isinstance(c, EnergyCard) and c.energyType == EnergyType.BASIC)
            and (not cost.get("type") or c.cardType == CardType[cost["type"]])
            and (not cost.get("name") or c.name == cost["name"])]


def counter_donors(source, rule, player):
    from packages.rules.maximum_hp import maximum
    return [c for c in player.active + player.bench if c.hp < maximum(c)
            and (rule.get("target") != "self" or c is not source)
            and (not rule.get("prefix") or c.name.startswith(rule["prefix"]))]


def energy_donors(rule, player):
    from packages.rules.core_fixes import refresh_energy
    out = []
    for c in player.bench:
        refresh_energy(c)
        if any(energy_matches(e, rule["type"]) for e in c.energy):
            out.append(c)
    return out


def available(source, rule, state):
    p, o = current_player(state), opponent_player(state)
    kind = rule["kind"]
    if kind == "bottom_leap":
        return source in p.bench and bool(p.left)
    if kind == "move_basic":
        return len(p.active+p.bench)>1 and any(isinstance(e,EnergyCard) and e.energyType == EnergyType.BASIC for c in p.active+p.bench for e in c.attachment)
    if kind == "evolve_named":
        return bool(p.left)
    if kind == "switch_self":
        return bool(p.bench) and (not rule.get("benchOnly") or source in p.bench)
    if kind == "opponent_switch":
        return bool(o.active and o.bench)
    if kind == "move_counter":
        return len(p.active + p.bench) > 1 and bool(counter_donors(source, rule, p))
    if kind == "move_energy":
        return bool(p.active and energy_donors(rule, p))
    if kind == "draw_until":
        return bool(p.left)
    if kind in ("peek", "order_opponent_top"):
        return bool(o.left if kind == "order_opponent_top" or rule.get("opponent") else p.left)
    return True


def resolve(source, rule, state):
    from packages.rules.entry_effects import choice, effect, transfer
    from packages.rules.zone_effects import discard_attached
    p, o = current_player(state), opponent_player(state)
    kind = rule["kind"]
    if kind == "bottom_leap":
        move_cards(list(p.left[-1:]), (p.id, CardPosition.LEFT), (p.id, CardPosition.DISCARD), state)
        from packages.rules.field_moves import return_discard_stack
        return_discard_stack(source, p, "left", top=True)
    elif kind == "both_recover_bench":
        from ptcg.core.enums import Stage
        from ptcg.core.card import PokemonCard
        from ptcg.core.action import choose_card_actions
        from ptcg.core.reducer import reduce_choose_card_actions
        for owner in (o, p):
            pool = [c for c in owner.discard if isinstance(c, PokemonCard) and c.stage == Stage.BASIC]
            if pool and len(owner.bench) < owner.benchSize:
                cards = yield from reduce_choose_card_actions(choose_card_actions(owner.id, owner.id, 1, 1, pool, indexed=True, source=source), state)
                move_cards(cards, (owner.id, CardPosition.DISCARD), (owner.id, CardPosition.BENCH), state)
    elif kind == "return_self_deck":
        from packages.rules.field_moves import return_stack
        from ptcg.utils.utils import shuffle_cards
        from ptcg.core.reducer import _force_active_replacement
        return_stack(source,p,"left")
        shuffle_cards(p.left,state)
        if not p.active and p.bench:
            yield from _force_active_replacement(p,state,p.id)
        if not p.active and not p.bench:
            from packages.rules.knockouts import resolve_group
            yield from resolve_group(state)
    elif kind == "move_basic":
        donors = [c for c in p.active+p.bench if any(isinstance(e,EnergyCard) and e.energyType == EnergyType.BASIC for e in c.attachment)]
        donor = (yield from choice(source,donors,state))[0]
        cards = yield from choice(source,[e for e in donor.attachment if isinstance(e,EnergyCard) and e.energyType == EnergyType.BASIC],state)
        recipient = (yield from choice(source,[c for c in p.active+p.bench if c is not donor],state))[0]
        transfer(donor,recipient,cards)
    elif kind == "choose_status":
        from packages.rules.effects import NumberOption
        from ptcg.core.enums import SpecialCondition
        from packages.rules.status_immunity import apply_status
        labels = {"BURNED":"灼伤","CONFUSED":"混乱","POISONED":"中毒"}
        selected = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,i,labels[v]) for i,v in enumerate(rule["statuses"])]})
        status = rule["statuses"][selected.value]
        if status == "CONFUSED":
            apply_status(o.active[0],SpecialCondition.CONFUSED)
        else:
            setattr(o.active[0],status.lower(),True)
            if status == "POISONED":
                o.active[0].poison_damage = 10
    elif kind == "evolve_named":
        from ptcg.core.action import EvolvePokemonAction
        from ptcg.core.reducer import reduce_evolve_pokemon_action
        from ptcg.utils.utils import shuffle_cards
        cards = yield from choice(source,[c for c in p.left if c.name in rule["names"]],state,0,1)
        if cards:
            move_cards(cards,(p.id,CardPosition.LEFT),(p.id,CardPosition.HAND),state)
            reduce_evolve_pokemon_action(EvolvePokemonAction(p.id,cards[0],source),state)
        shuffle_cards(p.left,state)
    elif kind == "sequence":
        for child in rule["effects"]:
            yield from effect(source, child, state)
    elif kind == "coin_effect":
        child = rule.get("heads" if flip_coin(state) == Coin.HEAD else "tails")
        if child:
            yield from effect(source, child, state)
    elif kind == "switch_self":
        if source in p.bench:
            switch_pokemon(p.active[0], source, p)
        elif p.bench:
            selected = yield from choice(source, list(p.bench), state)
            switch_pokemon(source, selected[0], p)
    elif kind == "opponent_switch":
        from ptcg.core.action import choose_card_actions
        from ptcg.core.reducer import reduce_choose_card_actions
        if o.active and o.bench:
            selected = yield from reduce_choose_card_actions(choose_card_actions(o.id, o.id, 1, 1, list(o.bench), source=source), state)
            switch_pokemon(o.active[0], selected[0], o)
    elif kind == "move_counter":
        donors = counter_donors(source, rule, p)
        if donors:
            donor = (yield from choice(source, donors, state))[0]
            targets = [source] if rule.get("target") == "self" else [c for c in p.active + p.bench if c is not donor]
            if targets:
                target = (yield from choice(source, targets, state))[0]
                donor.hp += 10
                target.hp -= 10
    elif kind == "move_energy":
        from packages.rules.energy_selection import choose_units
        from types import SimpleNamespace
        donors = energy_donors(rule, p)
        if donors and p.active:
            donor = (yield from choice(source, donors, state))[0]
            cards = yield from choose_units(donor, 1, SimpleNamespace(source=source), state, rule["type"])
            transfer(donor, p.active[0], cards)
    elif kind == "draw_until":
        move_cards(list(p.left[:max(0, rule["count"] - len(p.hand))]), (p.id, CardPosition.LEFT), (p.id, CardPosition.HAND), state)
    elif kind == "team_damage":
        old = getattr(p, "turn_damage_bonus", {})
        amount = old.get("amount", 0) if old.get("turn") == state.turn_number else 0
        p.turn_damage_bonus = {"turn": state.turn_number, "amount": amount + rule["amount"]}
    elif kind == "both_poison":
        for player in (p, o):
            for c in player.active:
                c.poisoned, c.poison_damage = True, 10
    elif kind == "burn":
        for c in o.active:
            c.burned = True
    elif kind == "discard_self_energy":
        from packages.rules.energy_selection import choose_units
        from types import SimpleNamespace
        cards = yield from choose_units(source, 1, SimpleNamespace(source=source), state)
        discard_attached(source, cards, p)
    elif kind == "peek":
        for owner in ([o, p] if rule.get("both") else [o if rule.get("opponent") else p]):
            # A choice exposes the private card only to the acting player.
            selected = yield from choice(source, list(owner.left[:1]), state, 0 if rule.get("discard") else 1)
            if rule.get("discard") and selected:
                move_cards(selected, (owner.id, CardPosition.LEFT), (owner.id, CardPosition.DISCARD), state)
    elif kind == "order_opponent_top":
        window = list(o.left[:2])
        if window:
            keep = (yield from choice(source, window, state))[0]
            move_cards([c for c in window if c is not keep], (o.id, CardPosition.LEFT), (o.id, CardPosition.LEFT), state)

from packages.rules.energy_units import matches as energy_matches
