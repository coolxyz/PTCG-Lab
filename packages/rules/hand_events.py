"""Snapshot triggers caused by physical cards played from hand."""

from packages.rules.abilities import enabled


def capture(target, owner, state, event):
    jobs = []
    if event == "energy":
        from ptcg.core.enums import Stage
        from packages.rules.stadiums import modifiers
        from packages.rules.pokemon_types import has_type
        for r in modifiers(target, state):
            if r.get("handEnergyCounters") and target.stage == Stage.BASIC and not has_type(target, r["exceptType"]):
                jobs.append((owner, target, target, {"name": "Calamitous Snowy Mountain", "counters": r["handEnergyCounters"]}))
    seen = set()
    for player in (state.player1, state.player2):
        for holder in player.active + player.bench:
            if not enabled(holder, state):
                continue
            for rule in (getattr(holder, "spec", None) or {}).get("abilities", []):
                if rule["kind"] != "hand_event" or rule["event"] != event:
                    continue
                if bool(rule.get("opponent")) != (player is not owner):
                    continue
                if rule.get("holderActive") and holder not in player.active:
                    continue
                if rule.get("self") and target is not holder:
                    continue
                if rule.get("benchOnly") and holder not in player.bench:
                    continue
                if rule.get("requiresName") and not any(c.name == rule["requiresName"] for c in player.active + player.bench):
                    continue
                if rule.get("once") and getattr(holder, "hand_event_turn", None) == state.turn_number:
                    continue
                key = (player.id, rule.get("noStack"))
                if key[1] and key in seen:
                    continue
                seen.add(key)
                jobs.append((player, holder, target, dict(rule)))
    state.hand_event_queue = getattr(state, "hand_event_queue", []) + jobs


def finish(state):
    from packages.rules.effects import NumberOption
    from packages.rules.healing import value as healed
    from ptcg.utils.utils import switch_pokemon
    while getattr(state, "hand_event_queue", []):
        # The player whose turn it is chooses simultaneous triggered effects.
        p = state.player1 if state.turn == state.player1.id else state.player2
        index = 0
        if len(state.hand_event_queue) > 1:
            options = [NumberOption(p, i, r.get("name", h.name)) for i, (_, h, _, r) in enumerate(state.hand_event_queue)]
            result = yield (state.get_obs(p.id), 0, False, {"raw_available_actions": options})
            index = result.value
        owner, holder, target, rule = state.hand_event_queue.pop(index)
        if not any(target in p.active + p.bench for p in (state.player1, state.player2)):
            continue
        if rule.get("optional"):
            if rule.get("once") and getattr(holder, "hand_event_turn", None) == state.turn_number:
                continue
            result = yield (state.get_obs(owner.id), 0, False, {"raw_available_actions": [
                NumberOption(owner, 0, "不使用"), NumberOption(owner, 1, rule.get("name", "使用特性"))]})
            if not result.value:
                continue
            holder.hand_event_turn = state.turn_number
        if rule.get("heal"):
            target.hp = healed(target, rule["heal"], state, record=True)
        if rule.get("counters"):
            target.hp -= rule["counters"]
        if rule.get("switch") and target in owner.bench and owner.active:
            switch_pokemon(owner.active[0], target, owner)


def bench_actions(player, state):
    from ptcg.core.action import UseAbilityAction
    from ptcg.core.enums import Stage
    if len(player.bench) >= player.benchSize:
        return []
    opponent = state.player2 if player is state.player1 else state.player1
    result = []
    for card in player.hand:
        for rule, ability in zip((getattr(card, "spec", None) or {}).get("abilities", []), getattr(card, "ability", [])):
            if rule["kind"] != "hand_bench":
                continue
            if rule.get("behind") and len(player.prize) <= len(opponent.prize):
                continue
            if rule.get("opponentStage2") and not any(c.stage == Stage.STAGE_2 for c in opponent.active + opponent.bench):
                continue
            result.append(UseAbilityAction(player.id, card, ability))
    return result
