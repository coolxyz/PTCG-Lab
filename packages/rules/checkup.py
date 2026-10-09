"""Damage conditions resolve between turns, before the next mandatory draw."""

from ptcg.core.enums import Coin
from ptcg.utils.utils import flip_coin
from packages.rules.knockouts import resolve_group


def passive_rules(player, state):
    from packages.rules.abilities import enabled

    return [
        r
        for c in player.active + player.bench
        if enabled(c, state)
        for r in (getattr(c, "spec", None) or {}).get("abilities", [])
        if r["kind"] == "checkup" and (not r.get("activeOnly") or c in player.active)
    ]


def needed(state):
    return any(
        r.get("basicCounters") or r.get("teamHeal") or r.get("opponentActiveCounters") or r.get("abilityCounters")
        for p in (state.player1, state.player2)
        for r in passive_rules(p, state)
    ) or any(
        getattr(c, "poisoned", False) or getattr(c, "burned", False)
        for p in (state.player1, state.player2)
        for c in p.active
    )


def finish(state):
    """The next player orders checkup effects; Special Conditions are one block.

    Official Advanced Players' Rulebook 2025, Pokemon Checkup:
    https://asia.pokemon-card.com/sg/wp-content/uploads/sites/6/2025/10/EN_advanced_manual-2025.pdf
    """
    from packages.rules.core_fixes import condition_checkup
    from ptcg.utils.utils import _complete_next_turn
    from ptcg.core.enums import Stage, SpecialCondition
    from packages.rules.abilities import enabled
    from packages.rules.effects import NumberOption
    from packages.rules.healing import value as healed

    state.resolving_checkup = True
    del state.pending_checkup
    players = [state.player1, state.player2]
    chooser = next(p for p in players if p.id != state.turn)
    jobs = []
    for owner in players:
        for holder in owner.active + owner.bench:
            if not enabled(holder, state):
                continue
            for r in (getattr(holder, "spec", None) or {}).get("abilities", []):
                if r["kind"] != "checkup" or r.get("activeOnly") and holder not in owner.active:
                    continue
                if any(r.get(k) for k in ("basicCounters", "teamHeal", "opponentActiveCounters", "abilityCounters")):
                    jobs.append((holder, owner, r))
    if any(getattr(c,"poisoned",False) or getattr(c,"burned",False) or getattr(c,"special_condition",SpecialCondition.NONE) != SpecialCondition.NONE for p in players for c in p.active):
        jobs.insert(0, None)
    while jobs:
        index = 0
        # Counter-only jobs commute: knockouts happen after the whole checkup.
        # Healing is capped at maximum HP, so its order relative to damage can
        # change the result and must remain a player decision.
        if len(jobs) > 1 and any(job is not None and job[2].get("teamHeal") for job in jobs):
            options = [NumberOption(chooser,i,"特殊状态检查" if job is None else f"{job[0].name}：{job[2].get('name','检查效果')}") for i,job in enumerate(jobs)]
            selected = yield (state.get_obs(chooser.id),0,False,{"raw_available_actions":options})
            index = selected.value
        job = jobs.pop(index)
        if job is None:
            # Do not interleave any ability with Poison/Burn/Sleep/Paralysis.
            for player in players:
                other = next(p for p in players if p is not player)
                opposing = passive_rules(other,state)
                for card in player.active:
                    if getattr(card,"poisoned",False):
                        from packages.rules.stadiums import modifiers as stadium_modifiers
                        card.hp -= getattr(card,"poison_damage",10) + sum(r.get("poisonBonus",0) for r in opposing) + sum(r.get("poisonBonus",0) for r in stadium_modifiers(card,state))
            for player in players:
                other = next(p for p in players if p is not player)
                opposing = passive_rules(other,state)
                for card in player.active:
                    if getattr(card,"burned",False):
                        card.hp -= 20 + sum(r.get("burnBonus",0) for r in opposing)
                        if flip_coin(state, during_turn=False) == Coin.HEAD:
                            del card.burned
            condition_checkup(state)
            continue
        holder, owner, r = job
        from packages.rules.ability_protection import blocked
        if not enabled(holder,state):
            continue
        other = next(p for p in players if p is not owner)
        if r.get("basicCounters"):
            for card in other.active + other.bench:
                if card.stage == Stage.BASIC and not blocked(card,holder,state):
                    card.hp -= r["basicCounters"]
        if r.get("teamHeal"):
            for card in owner.active + owner.bench:
                card.hp = healed(card,r["teamHeal"],state, record=True)
        if r.get("opponentActiveCounters"):
            for card in other.active:
                if not blocked(card,holder,state):
                    card.hp -= r["opponentActiveCounters"]
        if r.get("abilityCounters"):
            for player in players:
                for card in player.active + player.bench:
                    if card.name != r["exceptName"] and getattr(card,"ability",[]) and enabled(card,state) and not blocked(card,holder,state):
                        card.hp -= r["abilityCounters"]
    yield from resolve_group(state,checkup=True)
    state.checkup_conditions_done = True
    _complete_next_turn(state)
    state.resolving_checkup = False
