"""Trainer field operations share attachment and physical movement primitives."""

from types import SimpleNamespace
from ptcg.core.card import EnergyCard
from ptcg.core.enums import EnergyType, Coin
from ptcg.utils.utils import current_player, opponent_player, flip_coin
from packages.rules.attachment_effects import recipients, matches, resolve as attach
from packages.rules.entry_effects import choice, transfer

KINDS = {"attach_multiple", "return_pokemon", "transfer_energy", "discard_field_energy"}


def energy_matches(card, rule):
    return (
        isinstance(card, EnergyCard)
        and (not rule.get("basic") or card.energyType == EnergyType.BASIC)
        and (not rule.get("special") or card.energyType != EnergyType.BASIC)
    )


def holders(player, rule):
    return [
        c
        for c in player.active + player.bench
        if any(energy_matches(e, rule) for e in c.attachment)
    ]


def playable(rule, p, o):
    if rule["kind"] == "attach_multiple":
        if not recipients(None, rule, p):
            return False
        if rule["origin"] in ("left", "top"):
            return bool(p.left)
        return any(matches(c, rule) for c in getattr(p, rule["origin"]))
    if rule["kind"] == "return_pokemon":
        from packages.rules.trainers import matches as match_card

        return any(match_card(c, rule["filter"]) for c in p.active + p.bench)
    if rule["kind"] == "transfer_energy":
        return len(p.active + p.bench) > 1 and bool(holders(p, rule))
    if rule["kind"] == "discard_field_energy":
        return bool(holders(o, rule))
    return True


def resolve(source, rule, state):
    p, o = current_player(state), opponent_player(state)
    kind = rule["kind"]
    if kind == "attach_multiple":
        if rule.get("blockAttack"):
            p.attack_blocked_turn = state.turn_number
        yield from attach(source, rule, state)
    elif kind == "return_pokemon":
        from packages.rules.trainers import matches as match_card
        from packages.rules.field_moves import return_stack
        from ptcg.core.reducer import _force_active_replacement
        from packages.rules.knockouts import resolve_group
        from ptcg.core.exceptions import GameTermination

        pool = [c for c in p.active + p.bench if match_card(c, rule["filter"])]
        selected = yield from choice(source, pool, state)
        for card in selected:
            return_stack(card, p, rule["destination"])
        if not p.active and not p.bench:
            state.termination_reason, state.group_winner = "simultaneous_knockout", o.id
            raise GameTermination
        if not p.active:
            yield from _force_active_replacement(p, state, p.id)
        yield from resolve_group(state)
    elif kind == "transfer_energy":
        holder = (yield from choice(source, holders(p, rule), state))[0]
        cards = [c for c in holder.attachment if energy_matches(c, rule)]
        if rule.get("units"):
            from packages.rules.effects import NumberOption
            decision = yield (state.get_obs(p.id),0,False,{"raw_available_actions":[NumberOption(p,n,f"移动{n}个能量") for n in range(rule["units"]+1)]})
            if not decision.value:
                return
            from packages.rules.energy_selection import choose_units
            selected = yield from choose_units(holder,decision.value,SimpleNamespace(source=source),state)
        else:
            selected = yield from choice(source, cards, state)
        recipient = (
            yield from choice(
                source, [c for c in p.active + p.bench if c is not holder], state
            )
        )[0]
        transfer(holder, recipient, selected)
    elif kind == "discard_field_energy":
        if rule.get("coin") and flip_coin(state) != Coin.HEAD:
            return
        targets = holders(o, rule)
        if not rule.get("each"):
            targets = yield from choice(source, targets, state)
        from packages.rules.energy_selection import choose_units
        from packages.rules.zone_effects import discard_attached

        for target in targets:
            cards = [c for c in target.attachment if energy_matches(c, rule)]
            if rule.get("special"):
                selected = yield from choice(source, cards, state)
            else:
                selected = yield from choose_units(
                    target, 1, SimpleNamespace(source=source), state
                )
            discard_attached(target, selected, o)
