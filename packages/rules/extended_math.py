"""Live-board values for the additional reviewed damage expression terms."""

from ptcg.core.card import PokemonCard, EnergyCard, ToolCard
from ptcg.core.enums import EnergyType, CardType, Stage, PokemonType, SpecialCondition
from packages.rules.core_fixes import refresh_energy


def value(rule, source, target, p, o, state):
    term = rule["term"]
    if term == 'empty_hand':
        return int(not p.hand)
    if term == "own_named_pokemon":
        return sum(c.name in rule["names"] for c in p.active + p.bench)
    if term == "bench_names":
        return int(set(rule["names"]).issubset({c.name for c in p.bench}))
    if term == "extra_attack_energy":
        return int(len(source.energy) - len(getattr(source, "resolving_attack_cost", [])) >= rule["number"])
    if term == "typed_field_minimum":
        return int(sum(sum(energy_matches(t, rule["type"]) for t in c.energy) for c in p.active + p.bench) >= rule["number"])
    from packages.rules.history import TERMS as HISTORY_TERMS, value as history_value
    if term in HISTORY_TERMS:
        return history_value(rule, source, p, state)
    from packages.rules.board_math import TERMS, value as board_value
    if term in TERMS:
        return board_value(rule, source, target, p, o, state)
    if term == "played_supporter":
        from ptcg.core.card import SupporterCard
        names = {c.name for c in p.deck+p.discard if isinstance(c,SupporterCard) and (not rule.get("trait") or getattr(getattr(c,"pokemonRule",None),"name",None) == rule["trait"])}
        return int(any(a.get("action_type")=="UseSupporterAction" and (not rule.get("name") or a.get("source")==rule["name"]) and (not rule.get("trait") or a.get("source") in names) for a in p.current_turn_actions))
    if term == "field_energy_minimum":
        return int(sum(len(c.energy) for c in p.active+p.bench)>=rule["number"])
    if term == "opponent_hand_trainers":
        from packages.rules.zone_effects import matches
        return sum(matches(c,"trainer") for c in o.hand)
    if term == "opponent_special_energy_cards":
        return sum(isinstance(e,EnergyCard) and e.energyType == EnergyType.SPECIAL for c in o.active+o.bench for e in c.attachment)
    if term == "opponent_ex_v_count":
        return sum(c.pokemonType in (PokemonType.EX,PokemonType.V,PokemonType.VSTAR) for c in o.active+o.bench)
    if term == "bench_prefix_counters":
        from packages.rules.attack_math import counters
        return sum(counters(c) for c in p.bench if c.name.startswith(rule["prefix"]))
    if term == "both_benches":
        return len(p.bench) + len(o.bench)
    if term in ("own_typed_energy", "bench_with_energy"):
        cards = p.bench if term == "bench_with_energy" else p.active + p.bench
        cards = [c for c in cards if not rule.get("prefix") or c.name.startswith(rule["prefix"])]
        counts = []
        for card in cards:
            refresh_energy(card)
            counts.append(
                sum(energy_matches(e, rule["type"]) for e in card.energy)
            )
        return (
            sum(bool(n) for n in counts) if term == "bench_with_energy" else sum(counts)
        )
    if term == "discard_energy":
        return sum(
            isinstance(c, EnergyCard)
            and (not rule.get("basic") or c.energyType == EnergyType.BASIC)
            for c in o.discard
        )
    if term == "own_named":
        return sum(
            isinstance(c, PokemonCard) and c.name == rule["name"]
            for c in (p.active + p.bench if rule["zone"] == "field" else p.discard)
        )
    if term == "typed_pokemon":
        return sum(
            isinstance(c, PokemonCard) and has_type(c, CardType[rule["type"]])
            for c in (
                p.active + p.bench
                if rule["zone"] == "field"
                else getattr(p, rule["zone"])
            )
        )
    if term == "opponent_typed_energy":
        for c in o.active + o.bench:
            refresh_energy(c)
        return sum(
            energy_matches(e, rule["type"])
            for c in o.active + o.bench
            for e in c.energy
        )
    if term in (
        "self_basic_energy_cards",
        "self_special_energy_cards",
        "self_has_special_energy",
    ):
        count = sum(
            isinstance(c, EnergyCard)
            and c.energyType
            == (
                EnergyType.BASIC
                if term == "self_basic_energy_cards"
                else EnergyType.SPECIAL
            )
            for c in source.attachment
        )
        return int(count > 0) if term == "self_has_special_energy" else count
    if term == "opponent_status_count":
        return (
            int(bool(getattr(target, "poisoned", False)))
            + int(bool(getattr(target, "burned", False)))
            + int(
                getattr(target, "special_condition", SpecialCondition.NONE)
                != SpecialCondition.NONE
            )
        )
    if term == "has_status":
        c = source if rule["owner"] == "self" else target
        return int(
            bool(getattr(c, rule["status"].lower(), False))
            if rule["status"] in ("POISONED", "BURNED")
            else getattr(c, "special_condition", SpecialCondition.NONE)
            == SpecialCondition[rule["status"]]
        )
    if term == "self_has_typed_energy":
        refresh_energy(source)
        return int(
            any(energy_matches(e, rule["type"]) for e in source.energy)
        )
    if term == "own_basic_energy_types":
        return len(
            {
                c.cardType
                for pokemon in p.active + p.bench
                for c in pokemon.attachment
                if isinstance(c, EnergyCard) and c.energyType == EnergyType.BASIC
            }
        )
    if term == "opponent_ability_count":
        from packages.rules.abilities import enabled

        return sum(
            bool(getattr(c, "ability", [])) and enabled(c, state)
            for c in o.active + o.bench
        )
    if term == "opponent_retreat":
        from packages.rules.modifiers import refresh_costs

        refresh_costs(target, state)
        return len(target.retreat)
    if term == "opponent_all_counters":
        from packages.rules.attack_math import counters

        return sum(counters(c) for c in o.active + o.bench)
    if term == "opponent_all_energy":
        for c in o.active + o.bench:
            refresh_energy(c)
        return sum(len(c.energy) for c in o.active + o.bench)
    if term == "own_field_count":
        return len(p.active + p.bench)
    if term == "own_stage_count":
        return sum(c.stage == Stage[rule["stage"]] for c in p.active + p.bench)
    if term == "opponent_ex_count":
        return sum(c.pokemonType == PokemonType.EX for c in o.active + o.bench)
    if term == "more_energy":
        return int(len(source.energy) > len(target.energy))
    if term == "own_stadium":
        return int(any(getattr(c, "playedFrom", None) == p.id for c in state.stadium))
    if term == "any_stadium":
        return int(bool(state.stadium))
    if term == "opponent_prizes_2_4":
        return int(len(o.prize) in (2, 4))
    if term == "opponent_has_tool":
        return int(any(isinstance(c, ToolCard) for c in target.attachment))
    if term == "opponent_small_hand":
        return int(len(o.hand) <= rule["threshold"])
    if term == "own_small_deck":
        return int(len(p.left) <= rule["threshold"])
    if term == "own_bench_named":
        return int(any(c.name == rule["name"] for c in p.bench))
    raise ValueError("Unknown reviewed expression: " + term)

from packages.rules.pokemon_types import has_type

from packages.rules.energy_units import matches as energy_matches
