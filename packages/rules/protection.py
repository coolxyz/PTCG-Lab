"""Attack damage and attack effects are separate protection checks."""

from ptcg.core.enums import Stage, PokemonType
from ptcg.utils.utils import current_player


def blocked(target, state, kind, source=None):
    attacker = source or next(iter(current_player(state).active), None)
    if attacker is None:
        return False
    owner = next(
        (p for p in (state.player1, state.player2) if target in p.active + p.bench),
        None,
    )
    if owner is None or attacker in owner.active + owner.bench:
        return False
    rules = []
    from packages.rules.stadiums import modifiers as stadium_modifiers
    rules.extend(r["protection"] for r in stadium_modifiers(target, state) if r.get("protection"))
    if kind == "damage" and target.pokemonRule.name == "FUTURE" and attacker.pokemonType == PokemonType.EX and any(getattr(c, "future_team_shield", None) == state.turn_number for c in owner.active):
        return True
    if any((getattr(e, "spec", None) or {}).get("mechanic", {}).get("protectEffects") for e in target.attachment):
        rules.append({"effects": True})
    temporary = getattr(target, "attack_protection", {})
    if temporary.get("turn") == state.turn_number:
        rules.append(temporary)
    from packages.rules.abilities import enabled

    for holder in owner.active + owner.bench:
        if not enabled(holder, state):
            continue
        rules.extend(
            a
            for a in (getattr(holder, "spec", None) or {}).get("abilities", [])
            if a["kind"] == "protection"
            and (holder is target or a.get("scope") == "team")
            and (not a.get("holderActive") or holder in owner.active)
        )
    for rule in rules:
        if not (rule.get(kind) or (kind == "counters" and rule.get("effects"))):
            continue
        if rule.get("hasEnergy") and not target.energy:
            continue
        if rule.get("zone") == "bench" and target not in owner.bench:
            continue
        if rule.get("targetStage") and target.stage.name != rule["targetStage"]:
            continue
        if rule.get("targetPrefix") and not target.name.startswith(rule["targetPrefix"]):
            continue
        if rule.get("noRuleBox"):
            from ptcg.core.enums import PokemonRule
            if target.pokemonType != PokemonType.NORMAL or target.pokemonRule in (PokemonRule.RADIANT, PokemonRule.TERA) or getattr(target, "isRadiant", False):
                continue
        criterion = rule.get("source", "any")
        if rule.get("sourceType") and not has_type(attacker, rule["sourceType"]):
            continue
        if rule.get("sourceTrait") and attacker.pokemonRule.name != rule["sourceTrait"]:
            continue
        if rule.get("exceptSourceName") == attacker.name:
            continue
        if rule.get("equalActiveEnergy"):
            opponent = state.player2 if owner is state.player1 else state.player1
            if not opponent.active or len(target.energy) != len(opponent.active[0].energy):
                continue
        if criterion == "tera":
            from ptcg.core.enums import PokemonRule
            if attacker.pokemonRule != PokemonRule.TERA:
                continue
        if rule.get("exceptType") and has_type(attacker, rule["exceptType"]):
            continue
        if criterion == "ability" and not (
            getattr(attacker, "ability", []) and enabled(attacker, state)
        ):
            continue
        if criterion == "basic" and attacker.stage != Stage.BASIC:
            continue
        if criterion == "basic_ex" and (attacker.stage != Stage.BASIC or attacker.pokemonType != PokemonType.EX):
            continue
        if criterion == "ex" and attacker.pokemonType != PokemonType.EX:
            continue
        if criterion == "ex_v" and attacker.pokemonType not in (
            PokemonType.EX,
            PokemonType.V,
            PokemonType.VSTAR,
        ):
            continue
        return True
    return False

from packages.rules.pokemon_types import has_type
