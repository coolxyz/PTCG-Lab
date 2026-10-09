def allowed(source, attack, state):
    from packages.rules.modifiers import rules, owner_of

    owner = owner_of(source, state)
    if getattr(owner, "attack_blocked_turn", None) == state.turn_number:
        return False
    energy_limit = getattr(owner, "low_energy_attack_lock", {})
    if energy_limit.get("turn") == state.turn_number and len(source.energy) <= energy_limit["maximum"]:
        return False
    for effect in rules(source, state):
        if effect.get("blockAtHP") and source.hp <= effect["blockAtHP"]:
            return False
        if effect.get("requiresOpponentEXV"):
            from ptcg.core.enums import PokemonType
            opponent = state.player2 if owner is state.player1 else state.player1
            if not any(c.pokemonType in (PokemonType.EX, PokemonType.V, PokemonType.VSTAR) for c in opponent.active + opponent.bench):
                return False
        required = effect.get("attackRequires")
        if (
            required
            and sum(
                c.name.startswith(required["prefix"])
                for c in owner.active + owner.bench
            )
            < required["count"]
        ):
            return False
    lock = getattr(source, "attack_locks", {}).get(attack.name)
    if lock == "active" or lock == state.turn_number:
        return False
    rule = getattr(attack, "compiled_rule", {}).get("mechanic", {})
    from packages.rules.attack_contracts import permitted
    if owner is not None and not permitted(rule, source, owner, state):
        return False
    if rule.get("firstTurnForbidden"):
        owner = state.player1 if state.turn == state.player1.id else state.player2
        if owner.firstTurn:
            return False
    return True
