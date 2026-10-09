"""Pure core replacements loaded only by the reproducible P0.1 engine overlay."""
from collections import Counter
from ptcg.core.card import PokemonCard, EnergyCard
from ptcg.core.enums import CardType, CardPosition, PokemonPosition


def check_energy(cost, energy):
    available = Counter(energy)
    wildcard = available.pop(CardType.ANY, 0)
    restricted = available.pop(CardType.PSYCHIC_DARK, 0)
    colored = Counter(c for c in cost if c != CardType.COLORLESS)
    deficit = sum(max(0, n - available[t]) for t, n in colored.items())
    restricted_deficit = sum(max(0, colored[t] - available[t]) for t in (CardType.PSYCHIC, CardType.DARK))
    return len(energy) >= len(cost) and deficit - min(restricted, restricted_deficit) <= wildcard


def discard_card(player, card):
    # Callers remove from their source zone; retain physical identity.
    fresh = type(card)()
    vars(card).clear()
    vars(card).update(vars(fresh))
    card.cardPosition = CardPosition.DISCARD
    player.discard.append(card)
    card.index = len(player.discard)


def discard_pokemon(player, pokemon):
    target = pokemon[0] if isinstance(pokemon, list) else pokemon
    zone = player.active if target in player.active else player.bench
    zone.remove(target)
    def flatten(card):
        for child in list(getattr(card, 'attachment', [])) + list(getattr(card, 'evolved', [])):
            flatten(child)
        discard_card(player, card)
    flatten(target)
    for i,c in enumerate(player.bench): c.index = i + 1


def refresh_energy(pokemon, state=None):
    if any(hasattr(c,'effective_provides') for c in pokemon.attachment) or getattr(pokemon,'dynamic_energy',False):
        pokemon.dynamic_energy = True
        pokemon.energy = []
        for card in pokemon.attachment:
            if isinstance(card, EnergyCard):
                if hasattr(card, 'effective_provides'):
                    card.provides = card.effective_provides(pokemon, state) if (getattr(card, 'spec', None) or {}).get('energyType') == 'special' else card.effective_provides(pokemon)
                pokemon.energy.extend(card.provides)
    from packages.rules.special_energy import clear_conditions
    clear_conditions(pokemon)


def shield_damage(target, damage, state, source=None):
    from packages.rules.attack_attachments import with_berries
    return with_berries(_shield_damage(target, damage, state, source), source, target, damage, state)


def _shield_damage(target, damage, state, source=None):
    if damage <= 0:return 0
    from packages.rules.protection import blocked
    if blocked(target,state,'damage',source):return 0
    from ptcg.core.enums import PokemonRule, PokemonPosition
    if target.position == PokemonPosition.BENCH and target.pokemonRule == PokemonRule.TERA:
        return 0
    from packages.rules.abilities import armor
    from packages.rules.modifiers import armor as tool_armor
    damage = max(0, damage - armor(target, state) - tool_armor(target, state, source))
    shield = getattr(target, 'damage_shield', {})
    if shield.get('turn') == state.turn_number:
        if shield.get('maximum') is not None and damage <= shield['maximum']:
            return 0
        damage = max(0, damage - shield['amount'])
    from packages.rules.damage_events import coin_armor
    from packages.rules.modifiers import rules
    if any(r.get("preventDamageAtLeast") and damage >= r["preventDamageAtLeast"] for r in rules(target,state)):return 0
    return coin_armor(target, damage, state)


def condition_checkup(state):
    from ptcg.core.enums import SpecialCondition, Coin
    from ptcg.utils.utils import flip_coin
    for owner in (state.player1, state.player2):
        for pokemon in owner.active:
            condition = getattr(pokemon, 'special_condition', SpecialCondition.NONE)
            if condition == SpecialCondition.ASLEEP:
                from packages.rules.abilities import enabled
                rules = (getattr(pokemon,'spec',None) or {}).get('abilities',[]) if enabled(pokemon,state) else []
                count = max([getattr(pokemon,'sleep_coins',1)] + [r.get('sleepCoins',1) for r in rules])
                recovered = all([flip_coin(state, during_turn=False) == Coin.HEAD for _ in range(count)])
                if recovered:
                    del pokemon.special_condition
                    if hasattr(pokemon,'sleep_coins'):del pokemon.sleep_coins
                elif any(r.get('sleepHeal') for r in rules):
                    from packages.rules.maximum_hp import maximum
                    pokemon.hp = healed(pokemon, None, state, record=True)
            elif condition == SpecialCondition.PARALYZED and owner.id == state.turn:
                del pokemon.special_condition


def end_turn_tools(state):
    if getattr(state, 'checkup_conditions_done', False):
        del state.checkup_conditions_done
    else:
        condition_checkup(state)
    for owner in (state.player1, state.player2):
        if getattr(owner, 'item_blocked_turn', None) == state.turn_number:
            del owner.item_blocked_turn
        for pokemon in owner.active + owner.bench:
            if hasattr(pokemon,'attack_locks'):
                pokemon.attack_locks={name:until for name,until in pokemon.attack_locks.items() if until=='active' or until>state.turn_number}
                if not pokemon.attack_locks:del pokemon.attack_locks
            if getattr(pokemon, 'attack_protection', {}).get('turn') == state.turn_number:
                del pokemon.attack_protection
            if getattr(pokemon, 'damage_shield', {}).get('turn') == state.turn_number:
                del pokemon.damage_shield
    p = state.player1 if state.turn == state.player1.id else state.player2
    p.used_named_abilities=[]
    for pokemon in p.active + p.bench:
        if getattr(pokemon, 'retreat_blocked_turn', None) == state.turn_number:
            del pokemon.retreat_blocked_turn
        if getattr(pokemon, 'attack_blocked_turn', None) == state.turn_number:
            del pokemon.attack_blocked_turn
        for card in list(pokemon.attachment):
            from packages.rules.tool_effects import enabled as tools_enabled
            if (card.id == 'PAR-178' or (getattr(card, 'spec', None) or {}).get('mechanic', {}).get('discardEndTurn')) and tools_enabled(state):
                pokemon.attachment.remove(card)
                discard_card(p, card)


def exp_share(target, attacker, defender, state):
    from packages.rules.tool_effects import enabled as tools_enabled
    if not tools_enabled(state):
        return
    from ptcg.core.enums import EnergyType
    from ptcg.core.action import choose_card_actions
    from ptcg.core.reducer import reduce_choose_card_actions
    if target not in defender.active or attacker.id == defender.id:
        return
    holders = [p for p in defender.bench for c in p.attachment
               if c.id == 'P01-002' or (getattr(c, 'spec', None) or {}).get('mechanic', {}).get('expShare')]
    # Owner selects resolution order where several holders compete for energy.
    while holders:
        selected = yield from reduce_choose_card_actions(choose_card_actions(
            defender.id, defender.id, 1, 1, list(dict.fromkeys(holders))), state)
        holder = selected[0]
        holders.remove(holder)
        basic = [c for c in target.attachment if isinstance(c,EnergyCard) and c.energyType==EnergyType.BASIC]
        if not basic: break
        chosen = yield from reduce_choose_card_actions(choose_card_actions(
            defender.id, defender.id, 0, 1, basic), state)
        for card in chosen:
            target.attachment.remove(card)
            holder.attachment.append(card)
            holder.energy.extend(card.provides)
            card.cardPosition = CardPosition.BENCH_ATTACHMENT
            card.index = len(holder.attachment)

from packages.rules.healing import value as healed
