"""Resolve a simultaneous knockout group before testing either player's victory."""

from ptcg.core.action import choose_card_actions
from ptcg.core.enums import CardPosition
from ptcg.core.exceptions import GameTermination
from ptcg.core.reducer import reduce_choose_card_actions, _force_active_replacement
from ptcg.utils.utils import move_cards
from packages.rules.core_fixes import discard_pokemon, exp_share


def resolve_group(state, damage_targets=(), checkup=False):
    from packages.rules.maximum_hp import reconcile
    from packages.rules.damage_events import finish

    from packages.rules.attack_attachments import restore
    restore(state)
    finish(state)
    from packages.rules.reaction_choices import finish as finish_choices
    yield from finish_choices(state)
    reconcile(state)
    players = [state.player1, state.player2]
    players.sort(key=lambda p: p.id != state.turn)
    dead = [(p, c) for p in players for c in list(p.active + p.bench) if c.hp <= 0]
    for player in players:
        for source in player.active + player.bench:
            target = getattr(source, "pending_knockout_protection", None)
            if target is not None:
                if source.hp > 0 and any(c is target for _, c in dead) and target in damage_targets:
                    source.attack_protection = {"turn": state.turn_number + 1, "damage": True, "effects": True}
                del source.pending_knockout_protection
    if not dead:
        empty = [p for p in players if not p.active and not p.bench]
        if empty:
            state.termination_reason = "simultaneous_knockout"
            state.group_winner = None if len(empty) == 2 else next(p.id for p in players if p is not empty[0])
            raise GameTermination
        for player in players:
            if not player.active:
                yield from _force_active_replacement(player, state, state.turn)
        return
    prizes = {p.id: 0 for p in players}
    while dead:
        from packages.rules.knockout_prizes import prepare
        prize_values = prepare(dead, state)
        for owner, card in dead:
            from packages.rules.history import knocked_out
            knocked_out(card, owner, state, card in damage_targets)
            other = next(p for p in players if p is not owner)
            if card in damage_targets:
                yield from exp_share(card, other, owner, state)
            for attached in list(card.attachment):
                from packages.rules.tool_effects import enabled as tools_enabled
                from ptcg.core.card import ToolCard
                if isinstance(attached, ToolCard) and not tools_enabled(state):
                    continue
                if hasattr(attached, "on_knocked_out"):
                    yield from attached.on_knocked_out(
                        card, card in owner.active, other, owner, state
                    )
            prizes[other.id] += prize_values[id(card)]
            state.auto_events.append(f"{card.name} was knocked out.")
            discard_pokemon(owner, card)
            if not checkup and owner.id != state.turn:
                owner.hasPokemonDead = True
        from packages.rules.field_capacity import settle as settle_capacity
        yield from settle_capacity(state)
        reconcile(state)
        dead = [(p, c) for p in players for c in list(p.active + p.bench) if c.hp <= 0]
    for player in players:
        count = min(prizes[player.id], len(player.prize))
        if count:
            from packages.rules.prizes import take
            yield from take(player, count, state)

    reconcile(state)
    wins = []
    for player in players:
        other = next(p for p in players if p is not player)
        wins.append(int(not player.prize) + int(not (other.active or other.bench)))
    if any(wins):
        state.termination_reason = "simultaneous_knockout"
        state.group_winner = (
            None if wins[0] == wins[1] else players[0 if wins[0] > wins[1] else 1].id
        )
        raise GameTermination
    # When both Actives leave play, the next player replaces first.
    for player in reversed(players):
        if not player.active:
            yield from _force_active_replacement(player, state, state.turn)
    if reconcile(state):
        yield from resolve_group(state, checkup=checkup)
