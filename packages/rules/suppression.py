"""Resolve ability locks, preserving the already-active lock in mutual cases.

Official Klefki setup Q&A gives the starting player's ability priority.
Each transition retains the prior effective holders before admitting new ones.
"""

from ptcg.core.enums import PokemonType, PokemonRule, Stage


def contracts(holder):
    rules = [
        r
        for r in (getattr(holder, "spec", None) or {}).get("abilities", [])
        if r["kind"] == "suppress_abilities"
    ]
    if any(
        getattr(a, "suppresses_opponent_active_abilities", False)
        for a in getattr(holder, "ability", [])
    ):
        rules.append(
            {
                "targets": "opponent_active",
                "activeOnly": True,
                "exceptAbility": "Midnight Fluttering",
            }
        )
    return rules


def affects(rule, holder, owner, card, target_owner):
    if rule.get("activeOnly") and holder not in owner.active:
        return False
    if rule.get("benchOnly") and holder not in owner.bench:
        return False
    if rule.get("exceptAbility") and any(
        getattr(a, "name", None) == rule["exceptAbility"]
        for a in getattr(card, "ability", [])
    ):
        return False
    category = rule["targets"]
    if category == "opponent_active":
        return owner is not target_owner and card in target_owner.active
    if category == "basic_v":
        return card.stage == Stage.BASIC and card.pokemonType == PokemonType.V
    if category == "basic":
        return card.stage == Stage.BASIC
    if category == "self_knockout":
        return any(r.get("selfKnockout") for r in (getattr(card,"spec",None) or {}).get("abilities", []))
    if category == "bench_stage2":
        return card in target_owner.bench and card.stage == Stage.STAGE_2
    if category == "opponent_damaged_nonex":
        from packages.rules.maximum_hp import maximum
        return owner is not target_owner and card.hp < maximum(card) and card.pokemonType != PokemonType.EX
    if category == "rule_box_except_future":
        return card.pokemonRule != PokemonRule.FUTURE and (
            card.pokemonType != PokemonType.NORMAL
            or card.pokemonRule in (PokemonRule.RADIANT, PokemonRule.TERA)
        )
    raise ValueError(category)


def enabled(card, state, visiting=()):
    from packages.rules.stadiums import rules as stadium_rules
    players = (state.player1, state.player2)
    owner = next((p for p in players if card in p.active + p.bench), None)
    suppressed_types = {r.get("suppressType") for r in stadium_rules(state)} - {None}
    if owner is None or getattr(card, "ability_blocked_turn", None) == state.turn_number or card.cardType.name in suppressed_types:
        return False
    ordered_players = sorted(players, key=lambda p: p.id != getattr(state, "starting_player", state.turn))
    holders = []
    for p in ordered_players:
        for c in p.active+p.bench:
            rs = [r for r in contracts(c) if (not r.get("activeOnly") or c in p.active) and (not r.get("benchOnly") or c in p.bench)]
            if rs:
                holders.append((p, c, rs))
    if not holders:
        if hasattr(state, "effective_lock_holders"):
            state.effective_lock_holders = []
            state.suppression_graph = ((), (), ())
        return True
    # Cache by actual lock graph, including damage, zone and one-turn suppression.
    edges = [tuple(j for j,(q,h,rs) in enumerate(holders) if h is not c and any(affects(r,h,q,c,p) for r in rs)) for p,c,_ in holders]
    forced = tuple(getattr(c,"ability_blocked_turn",None) == state.turn_number or c.cardType.name in suppressed_types for _,c,_ in holders)
    # Physical references are stable through checkpoint replay; memory addresses
    # would make otherwise identical games have different private digests.
    signature = (tuple(c for _,c,_ in holders), tuple(edges), forced)
    if getattr(state,"suppression_graph",None) != signature:
        previous = getattr(state,"effective_lock_holders",[])
        n = len(holders)
        priority = [i for i,(_,c,_) in enumerate(holders) if c in previous] + [i for i,(_,c,_) in enumerate(holders) if c not in previous]
        # Usually the first fixed-point iteration solves an acyclic graph.
        bits = tuple(c in previous and not forced[i] for i,(_,c,_) in enumerate(holders))
        seen = set()
        while bits not in seen:
            seen.add(bits)
            new = tuple(not forced[i] and not any(bits[j] for j in edges[i]) for i in range(n))
            if new == bits:
                break
            bits = new
        else:
            candidates = []
            for mask in range(1 << n):
                option = tuple(bool(mask & (1 << i)) for i in range(n))
                if all(option[i] == (not forced[i] and not any(option[j] for j in edges[i])) for i in range(n)):
                    candidates.append(option)
            if not candidates:
                raise ValueError("Ability-lock graph has no stable resolution")
            bits = max(candidates, key=lambda option: tuple(option[i] for i in priority))
        state.effective_lock_holders = [c for i,(_,c,_) in enumerate(holders) if bits[i]]
        state.suppression_graph = signature
    return not any(h is not card and h in state.effective_lock_holders and any(affects(r,h,p,card,owner) for r in rs) for p,h,rs in holders)
