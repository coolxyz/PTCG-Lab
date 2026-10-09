"""Continuous non-HP Tool effects, evaluated against the current board."""

from ptcg.core.enums import CardType, PokemonType, PokemonRule, Stage
from ptcg.core.card import EnergyCard


def owner_of(card, state):
    return next(
        (p for p in (state.player1, state.player2) if card in p.active + p.bench), None
    )


def rules(card, state):
    owner = owner_of(card, state)
    if owner is None:
        return []
    opponent = state.player2 if owner is state.player1 else state.player1
    from packages.rules.stadiums import modifiers as stadium_modifiers
    result = stadium_modifiers(card, state)
    result.extend(r for r in getattr(owner, "timed_modifiers", []) if r["turn"] == state.turn_number)
    result.extend(r for r in getattr(card,"timed_modifiers",[]) if r["turn"] == state.turn_number)
    from packages.rules.tool_effects import enabled as tools_enabled
    for tool in card.attachment if tools_enabled(state) else []:
        rule = (getattr(tool, "spec", None) or {}).get("mechanic", {})
        if rule.get("kind") != "tool_modifier":
            continue
        condition = rule.get("condition")
        enabled = {
            None: True,
            "future": card.pokemonRule == PokemonRule.FUTURE,
            "ancient": card.pokemonRule == PokemonRule.ANCIENT,
            "stage2": card.stage == Stage.STAGE_2,
            "more_prizes": len(owner.prize) > len(opponent.prize),
            "hop": card.name.startswith("Hop's "),
            "basic": card.stage == Stage.BASIC,
            "cynthia": card.name.startswith("Cynthia's "),
            "lillie": card.name.startswith("Lillie's "),
            "poisoned": bool(getattr(card, "poisoned", False)),
            "tera": card.pokemonRule == PokemonRule.TERA,
            "stage1": card.stage == Stage.STAGE_1,
            "fighting": has_type(card, CardType.FIGHTING),
            "no_rule_box": card.pokemonType == PokemonType.NORMAL and card.pokemonRule not in (PokemonRule.RADIANT, PokemonRule.TERA) and not getattr(card, "isRadiant", False),
        }.get(condition, False)
        if enabled:
            result.append({**rule, "reaction": {**rule["reaction"], "tool": tool}} if rule.get("reaction") else rule)
    if tools_enabled(state):
        for player in (state.player1, state.player2):
            for holder in player.active:
                for tool in holder.attachment:
                    aura = (getattr(tool, "spec", None) or {}).get("mechanic", {}).get("activeAura")
                    if aura and card in owner.active:
                        result.append(aura)
    from packages.rules.abilities import enabled as ability_enabled

    seen = set()
    for player in (state.player1, state.player2):
        for holder in player.active + player.bench:
            if not ability_enabled(holder, state):
                continue
            from packages.rules.ability_protection import blocked
            if blocked(card,holder,state):
                continue
            for rule in (getattr(holder, "spec", None) or {}).get("abilities", []):
                if rule["kind"] != "continuous":
                    continue
                scope = rule.get("scope", "team")
                if (scope != "both" and (scope == "opponent") != (player is not owner)) or (
                    scope == "self" and holder is not card
                ):
                    continue
                if rule.get("holderZone") and holder not in getattr(
                    player, rule["holderZone"]
                ):
                    continue
                if rule.get("requiresNames") and not set(rule["requiresNames"]).issubset({c.name for c in player.active + player.bench}):
                    continue
                if rule.get("otherName") and not any(c is not holder and c.name == rule["otherName"] for c in player.active + player.bench):
                    continue
                if rule.get("requiresTrait") and not any(c.pokemonRule.name == rule["requiresTrait"] for c in player.active+player.bench):
                    continue
                if rule.get("requiresOpponentDiscardSubstring") and not any(rule["requiresOpponentDiscardSubstring"] in c.name for c in opponent.discard):
                    continue
                if rule.get("equalHands") and len(player.hand) != len(opponent.hand):
                    continue
                if rule.get("typedEnergyMinimum") and sum(energy_matches(e, rule["typedEnergyMinimum"]["type"]) for e in holder.energy) < rule["typedEnergyMinimum"]["count"]:
                    continue
                if rule.get("minimumCounters"):
                    from packages.rules.maximum_hp import maximum
                    if maximum(holder) - holder.hp < rule["minimumCounters"]:
                        continue
                if rule.get("fullHP"):
                    from packages.rules.maximum_hp import maximum
                    if holder.hp != maximum(holder):
                        continue
                if rule.get("affectedZone") and card not in getattr(
                    owner, rule["affectedZone"]
                ):
                    continue
                if rule.get("stage") == "basic" and card.stage != Stage.BASIC:
                    continue
                if rule.get("stage") == "evolved" and card.stage == Stage.BASIC:
                    continue
                if rule.get("types") and not any(has_type(card, t) for t in rule["types"]):
                    continue
                if rule.get("trait") and card.pokemonRule.name != rule["trait"]:
                    continue
                if rule.get("prefix") and not card.name.startswith(rule["prefix"]):
                    continue
                if rule.get("exceptName") == card.name:
                    continue
                if rule.get("affectedName") and card.name != rule["affectedName"]:
                    continue
                if rule.get("affectedNames") and card.name not in rule["affectedNames"]:
                    continue
                if rule.get("energy") and sum(
                    energy_matches(e, rule["energy"]) for e in card.energy
                ) < rule.get("energyMinimum", 1):
                    continue
                if rule.get("noEnergy") and any(
                    isinstance(c, EnergyCard) for c in card.attachment
                ):
                    continue
                if rule.get('specialEnergy'):
                    from ptcg.core.enums import EnergyType
                    if not any(isinstance(c, EnergyCard) and c.energyType == EnergyType.SPECIAL for c in card.attachment):
                        continue
                if rule.get("noStack") and rule["noStack"] in seen:
                    continue
                if rule.get("noStack"):
                    seen.add(rule["noStack"])
                result.append(rule)
    return result


def attack_damage(source, target, damage, state):
    owner, defender = owner_of(source, state), owner_of(target, state)
    if damage <= 0 or owner is None or defender is None or owner is defender:
        return damage
    reduction = getattr(source, "attack_damage_reduction", {})
    if reduction.get("turn") == state.turn_number:
        damage -= reduction["amount"]
    bonus = getattr(source, "turn_damage_bonus", {})
    if target in defender.active and bonus.get("turn") == state.turn_number:
        damage += bonus["amount"]
    team_bonus = getattr(owner, "turn_damage_bonus", {})
    if target in defender.active and team_bonus.get("turn") == state.turn_number:
        damage += team_bonus["amount"]
    for rule in rules(source, state):
        if rule.get("attackName") and rule["attackName"] != getattr(source,"resolving_attack_name",None):
            continue
        if target not in defender.active and not rule.get("allTargets"):
            continue
        category = rule.get("target")
        if category == "v" and target.pokemonType not in (
            PokemonType.V,
            PokemonType.VSTAR,
        ):
            continue
        if category == "ex" and target.pokemonType != PokemonType.EX:
            continue
        if category == "ex_v" and target.pokemonType not in (PokemonType.EX, PokemonType.V, PokemonType.VSTAR):
            continue
        if category == "evolved" and target.stage == Stage.BASIC:
            continue
        if category == "ability":
            from packages.rules.abilities import enabled
            if not getattr(target, "ability", []) or not enabled(target, state):
                continue
        damage += rule.get("damage", 0)
    return max(0, damage)


def armor(target, state, source=None):
    if source is None:
        attacker = state.player1 if state.turn == state.player1.id else state.player2
        source = next(iter(attacker.active), None)
    owner, attacker = owner_of(target, state), owner_of(source, state)
    if owner is None or attacker is None or owner is attacker:
        return 0
    from packages.rules.attack_attachments import berries
    return 60 * len(berries(source, target, state)) + sum(r.get("armor", 0) for r in rules(target, state) if not r.get("sourceEvolved") or source.stage != Stage.BASIC)


def refresh_costs(card, state):
    active = rules(card, state)
    affects_retreat = any(
        "retreatFree" in r or "retreatLess" in r or "retreatMore" in r for r in active
    )
    if affects_retreat or getattr(card, "tool_retreat_modified", False):
        card.tool_retreat_modified = affects_retreat
        base = list(type(card)().retreat)
        from packages.rules.tool_effects import enabled as tools_enabled
        if tools_enabled(state) and any(c.id == "TEF-159" for c in card.attachment):
            base = [] if card.hp <= 30 else base[1:]
        less = sum(r.get("retreatLess", 0) for r in active)
        base += [CardType.COLORLESS] * sum(r.get("retreatMore", 0) for r in active)
        card.retreat = [] if any(r.get("retreatFree") for r in active) else base[less:]
    affects_attacks = any(
        r.get("attackCost") is not None or r.get("attackLess") or r.get("attackLessAny") or r.get("attackLessPerOpponentPrize") or r.get("attackMore") or r.get("attackFree") or r.get("attackColorlessFree") or r.get("attackLessPerOpponentBench") or r.get("attackLessPerDiscardName") for r in active
    )
    contracts = any(getattr(a, "compiled_rule", {}).get("mechanic", {}).get("costIf") for a in card.attacks)
    if affects_attacks or contracts or getattr(card, "tool_attacks_modified", False):
        card.tool_attacks_modified = affects_attacks
        for attack, original in zip(card.attacks, type(card)().attacks):
            attack.cost = effective_attack_cost(card, attack, original.cost, state, active)


def effective_attack_cost(card, attack, printed, state, active=None):
    active = rules(card, state) if active is None else active
    cost = list(printed)
    from packages.rules.attack_contracts import cost as conditional_cost
    cost = conditional_cost(getattr(attack, "compiled_rule", {}).get("mechanic", {}), card, owner_of(card, state), cost)
    cost += [CardType.COLORLESS] * sum(r.get("attackMore", 0) for r in active)
    owner = owner_of(card, state)
    opponent = state.player2 if owner is state.player1 else state.player1
    less = sum(
        r.get("attackLess", 0)
        + r.get("attackLessPerOpponentPrize", 0) * (6 - len(opponent.prize))
        + r.get("attackLessPerOpponentBench", 0) * len(opponent.bench)
        + sum(c.name == r.get("attackLessPerDiscardName") for c in owner.discard)
        for r in active
        if not r.get("attackName") or r["attackName"] == attack.name
    )
    for _ in range(less):
        if CardType.COLORLESS in cost:
            cost.remove(CardType.COLORLESS)
    if any(r.get("attackColorlessFree") for r in active):
        cost = [c for c in cost if c != CardType.COLORLESS]
    relevant = [r for r in active if not r.get("attackName") or r["attackName"] == attack.name]
    for _ in range(sum(r.get("attackLessAny", 0) for r in relevant)):
        if not cost:
            break
        # Choose a waived type that pays the actual deficit. The
        # physical attached energies and their damage counts stay intact.
        from collections import Counter
        required, supplied = Counter(cost), Counter(card.energy)
        missing = [t for t in cost if t != CardType.COLORLESS and required[t] > supplied[t]]
        cost.remove(missing[0] if missing else CardType.COLORLESS if CardType.COLORLESS in cost else cost[0])
    fixed = next((r["attackCost"] for r in relevant if "attackCost" in r),None)
    return [CardType[t] for t in fixed] if fixed is not None else [] if any(r.get("attackFree") for r in relevant) else cost


def ignore_target_effects(source, target, state):
    owner, opponent = owner_of(source, state), owner_of(target, state)
    return (
        owner is not None
        and opponent is not None
        and owner is not opponent
        and target in opponent.active
        and any(r.get("ignoreTargetEffects") for r in rules(source, state))
    )

from packages.rules.pokemon_types import has_type

from packages.rules.energy_units import matches as energy_matches
