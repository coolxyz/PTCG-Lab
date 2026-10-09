"""Replace a physical Pokemon card while retaining its in-play state."""

from ptcg.core.card import PokemonCard
from ptcg.core.enums import CardPosition
from ptcg.utils.utils import shuffle_cards
from packages.rules.entry_effects import choice

PRINTED = {"superType", "name", "set_name", "number", "id", "hp", "pokemonType", "pokemonRule", "stage", "cardType", "retreat", "weakness", "resistance", "prize", "attacks", "ability", "evolveFrom", "text", "spec", "maximum_hp", "base_hp", "printed_hp"}


def permitted(card, by_hero=False):
    return by_hero or not any(r["kind"] == "hero_only" for r in (getattr(card, "spec", None) or {}).get("abilities", []))


def replace(source, new, owner, state, origin="left", destination="left"):
    from packages.rules.maximum_hp import maximum
    from packages.rules.core_fixes import refresh_energy
    counters = maximum(source) - source.hp
    runtime = {k: v for k, v in vars(source).items() if k not in PRINTED}
    getattr(owner, origin).remove(new)
    zone = owner.active if source in owner.active else owner.bench
    zone[zone.index(source)] = new
    vars(new).update(runtime)
    new.rules_state = state
    new.hp = type(new)().hp - counters
    vars(source).clear()
    vars(source).update(vars(type(source)()))
    source.cardPosition = CardPosition[destination.upper()]
    getattr(owner, destination).append(source)
    source.index = len(getattr(owner, destination))
    for container in {origin, destination}:
        for index, card in enumerate(getattr(owner, container), 1):
            card.index = index
    new.dynamic_energy = True
    refresh_energy(new, state)
    from packages.rules.maximum_hp import reconcile
    reconcile(state)
    return new


def choose_replace(source, owner, state, name=None):
    pool = [c for c in owner.left if isinstance(c, PokemonCard) and (not name or c.name == name) and permitted(c, by_hero=name == "Palafin ex")]
    cards = yield from choice(source, pool, state, 0, 1)
    if cards:
        replace(source, cards[0], owner, state)
    shuffle_cards(owner.left, state)
