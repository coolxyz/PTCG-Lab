"""Pure reviewed expressions evaluated from the physical attacker/defender."""
from packages.rules.maximum_hp import maximum


from ptcg.core.enums import SpecialCondition, Stage, PokemonType, CardType
from ptcg.core.card import ToolCard, PokemonCard
from ptcg.utils.utils import current_player, opponent_player


def counters(card):
    return max(0, maximum(card) - card.hp) // 10


def damage(mechanic, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    values = {
        "self_counters": counters(source),
        "opponent_counters": counters(target),
        "opponent_energy": len(target.energy),
        "self_energy": len(source.energy),
        "active_energy": len(source.energy) + len(target.energy),
        "own_bench": len(p.bench),
        "opponent_bench": len(o.bench),
        "opponent_prizes_taken": 6 - len(o.prize),
        "own_prizes_taken": 6 - len(p.prize),
        "own_prizes": len(p.prize),
        "opponent_prizes": len(o.prize),
        "own_hand": len(p.hand),
        "opponent_hand": len(o.hand),
        "own_tools": sum(
            isinstance(a, ToolCard) for c in p.active + p.bench for a in c.attachment
        ),
        "united_wings": sum(
            isinstance(c, PokemonCard)
            and any(a.name == "United Wings" for a in c.attacks)
            for c in p.discard
        ),
        "opponent_evolved": int(target.stage != Stage.BASIC),
        "opponent_basic": int(target.stage == Stage.BASIC),
        "opponent_ex": int(target.pokemonType == PokemonType.EX),
        "opponent_ex_or_v": int(
            target.pokemonType in (PokemonType.EX, PokemonType.V, PokemonType.VSTAR)
        ),
        "more_prizes": int(len(p.prize) > len(o.prize)),
        "equal_hands": int(len(p.hand) == len(o.hand)),
        "own_energy": sum(len(c.energy) for c in p.active + p.bench),
        "self_damaged": int(counters(source) > 0),
        "opponent_damaged": int(counters(target) > 0),
        "opponent_special": int(
            getattr(target, "special_condition", SpecialCondition.NONE)
            != SpecialCondition.NONE
            or getattr(target, "poisoned", False)
            or getattr(target, "burned", False)
        ),
    }
    if mechanic["term"] == "self_typed_energy":
        units = sum(
            energy_matches(e, mechanic["type"]) for e in source.energy
        )
    elif mechanic["term"] not in values:
        from packages.rules.extended_math import value

        units = value(mechanic, source, target, p, o, state)
    else:
        units = values[mechanic["term"]]
    if 'minimum' in mechanic:
        units = int(units >= mechanic['minimum'])
    value = units * mechanic["factor"]
    mode = mechanic["mode"]
    return max(
        0,
        value
        if mode == "multiply"
        else action.attack.damage + (value if mode == "add" else -value),
    )

from packages.rules.energy_units import matches as energy_matches
