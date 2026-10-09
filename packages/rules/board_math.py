"""Counts and predicates share physical zones, traits and current damage."""

from ptcg.core.card import EnergyCard, ToolCard, PokemonCard, SupporterCard
from ptcg.core.enums import SpecialCondition

TERMS = {
    "opponent_exact_prizes",
    "self_tool",
    "bench_damaged",
    "target_type",
    "bench_types",
    "discard_named",
    "self_status",
    "named_counters",
    "field_matching",
    "shared_type",
    "discard_trait",
    "own_discard_energy",
    "opponent_max_prizes",
    "opponent_min_bench",
    "target_min_retreat",
    "target_trait",
    "attached_named",
    "target_stage",
    "self_undamaged",
    "rocket_supporters",
    "equal_energy",
}


def value(r, source, target, p, o, state):
    from packages.rules.attack_math import counters
    from packages.rules.maximum_hp import maximum

    term = r["term"]
    if term == "opponent_exact_prizes":
        return int(len(o.prize) == r["number"])
    if term == "opponent_max_prizes":
        return int(len(o.prize) <= r["number"])
    if term == "opponent_min_bench":
        return int(len(o.bench) >= r["number"])
    if term == "target_min_retreat":
        from packages.rules.modifiers import refresh_costs

        refresh_costs(target, state)
        return int(len(target.retreat) >= r["number"])
    if term in ("target_type", "target_trait", "target_stage"):
        field, key = {
            "target_type": ("cardType", "type"),
            "target_trait": ("pokemonRule", "trait"),
            "target_stage": ("stage", "stage"),
        }[term]
        return int(getattr(target, field).name == r[key])
    if term == "equal_energy":
        return int(len(source.energy) == len(target.energy))
    if term == "self_tool":
        return int(any(isinstance(c, ToolCard) for c in source.attachment))
    if term == "self_undamaged":
        return int(counters(source) == 0)
    if term == "self_status":
        return int(
            bool(
                getattr(source, "poisoned", False)
                or getattr(source, "burned", False)
                or getattr(source, "special_condition", SpecialCondition.NONE)
                != SpecialCondition.NONE
            )
        )
    if term == "bench_damaged":
        cards = [c for c in p.bench if not r.get("name") or c.name == r["name"]]
        return int(
            bool(cards)
            and (
                all(counters(c) > 0 for c in cards)
                if r.get("all")
                else any(counters(c) > 0 for c in cards)
            )
        )
    if term == "bench_types":
        from packages.rules.pokemon_types import types
        return len({t for c in p.bench for t in types(c, state)})
    if term == "shared_type":
        from packages.rules.pokemon_types import types
        return int(
            bool(
                {t for c in p.active + p.bench for t in types(c, state)}
                & {t for c in o.active + o.bench for t in types(c, state)}
            )
        )
    if term == "named_counters":
        return sum(counters(c) for c in p.active + p.bench if c.name == r["name"])
    if term == "attached_named":
        return int(
            any(
                isinstance(c, EnergyCard) and c.name == r["name"]
                for c in source.attachment
            )
        )
    if term in (
        "discard_named",
        "discard_trait",
        "own_discard_energy",
        "rocket_supporters",
    ):
        count = sum(
            c.name == r["name"]
            if term == "discard_named"
            else getattr(getattr(c, "pokemonRule", None), "name", None) == r["trait"]
            if term == "discard_trait"
            else isinstance(c, EnergyCard)
            if term == "own_discard_energy"
            else isinstance(c, SupporterCard) and "Team Rocket" in c.name
            for c in p.discard
        )
        return int(count > 0) if r.get("boolean") else count
    if term == "field_matching":
        players = (
            (p, o)
            if r.get("owner") == "both"
            else (o,)
            if r.get("owner") == "opponent"
            else (p,)
        )
        cards = [
            c
            for player in players
            for c in (
                player.bench
                if r.get("zone") == "bench"
                else player.active + player.bench
            )
        ]
        count = sum(
            isinstance(c, PokemonCard)
            and (not r.get("type") or has_type(c, r["type"]))
            and (not r.get("stage") or c.stage.name == r["stage"])
            and (not r.get("trait") or c.pokemonRule.name == r["trait"])
            and (not r.get("name") or c.name == r["name"])
            and (not r.get("names") or c.name in r["names"])
            and (not r.get("prefix") or c.name.startswith(r["prefix"]))
            and (not r.get("contains") or any(n in c.name for n in r["contains"]))
            and (not r.get("damaged") or counters(c) > 0)
            and (not r.get("maximumHP") or maximum(c) == r["maximumHP"])
            for c in cards
        )
        return int(count > 0) if r.get("boolean") else count
    raise ValueError(term)

from packages.rules.pokemon_types import has_type
