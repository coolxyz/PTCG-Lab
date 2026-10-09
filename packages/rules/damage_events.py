"""Remember damage facts, then resolve reactions after all attack effects.

An Active defender can move to the Bench before retaliation. Survival tests
full HP and lethal damage at impact, but ability suppression at resolution.
"""

from packages.rules.maximum_hp import maximum, reconcile
from packages.rules.abilities import enabled

KINDS = {"retaliate", "survive_damage", "prize_reduction", "knockout_retaliate", "damage_choice"}


def rules(card, state=None, active_only=False):
    result = list((getattr(card, "spec", None) or {}).get("abilities", []))
    if active_only and not enabled(card, state):
        result = []
    if state is not None:
        from packages.rules.modifiers import rules as modifiers
        result.extend(r["reaction"] for r in modifiers(card, state) if r.get("reaction"))
    result.extend({'kind':'retaliate','counters':r['retaliation'],'energyEffect':True} for e in card.attachment
                  if (r := (getattr(e,'spec',None) or {}).get('mechanic',{})).get('retaliation'))
    temporary = getattr(card, "damage_retaliation", {})
    if state and temporary.get("turn") == state.turn_number:
        result.append(temporary)
    if any(
        (getattr(e, "spec", None) or {}).get("mechanic", {}).get("legacy")
        for e in card.attachment
    ):
        result.append(
            {"kind": "prize_reduction", "count": 1, "onceFlag": "legacy_energy_used"}
        )
    return result


def owner_of(card, state):
    return next(
        (p for p in (state.player1, state.player2) if card in p.active + p.bench), None
    )


def deal(source, target, amount, state):
    from packages.rules.attack_attachments import consume
    consume(source, target, amount, state)
    amount = int(amount)
    owner = owner_of(target, state)
    attacker = owner_of(source,state)
    if amount > 0 and owner is not None and attacker is not None and owner is not attacker:
        target.damage_history = [e for e in getattr(target, "damage_history", []) if e["turn"] >= state.turn_number-1] + [{"turn": state.turn_number, "amount": amount}]
    team_reactions = owner and any(r.get("team") and r["kind"] == "damage_choice" for c in owner.active + owner.bench for r in (getattr(c, "spec", None) or {}).get("abilities", []))
    if amount > 0 and owner and (team_reactions or any(r["kind"] in KINDS for r in rules(target, state)) or getattr(attacker,"tera_bonus_prize_turn",None) == state.turn_number):
        if not hasattr(state, "pending_damage_events"):
            state.pending_damage_events = []
        state.pending_damage_events.append(
            {
                "amount": amount,
                "source": source,
                "target": target,
                "owner": owner.id,
                "sourceOwner": getattr(owner_of(source, state), "id", None),
                "wasActive": target in owner.active,
                "full": target.hp == maximum(target),
                "lethal": target.hp <= amount,
            }
        )
    target.hp -= amount


def finish(state):
    events = getattr(state, "pending_damage_events", [])
    if hasattr(state, "pending_damage_events"):
        del state.pending_damage_events
    if not events:
        return False
    reconcile(state)
    for event in events:
        target, source = event["target"], event["source"]
        owner = owner_of(target, state)
        if not owner or owner.id != event["owner"]:
            continue
        opponent_attack = event["sourceOwner"] != owner.id
        for rule in rules(target, state, active_only=True):
            if (
                rule["kind"] == "retaliate"
                and (event["wasActive"] or rule.get("anyZone"))
                and opponent_attack
            ):
                source_owner = owner_of(source, state)
                if not source_owner:
                    continue
                from packages.rules.ability_protection import blocked
                if not rule.get('energyEffect') and not rule.get('tool') and blocked(source,target,state):
                    continue
                from ptcg.core.card import ToolCard
                from ptcg.core.enums import CardType

                if rule.get("requiresTool") and not any(
                    isinstance(c, ToolCard) for c in target.attachment
                ):
                    continue
                source.hp -= event["amount"] if rule.get("equalDamage") else rule.get("counters", 0)
                if rule.get("counterPerEnergy"):
                    source.hp -= rule["counterPerEnergy"] * sum(
                        energy_matches(e, rule["type"])
                        for e in target.energy
                    )
                if rule.get("status") in ("POISONED", "BURNED") and source in source_owner.active:
                    setattr(source, rule["status"].lower(), True)
                    if rule["status"] == "POISONED":
                        source.poison_damage = 10
                if rule.get("counterPerFamily"):
                    source.hp -= rule["counterPerFamily"] * sum(
                        c.name in rule["names"] for c in owner.active + owner.bench
                    )
                if rule.get("consume") and rule.get("tool") in target.attachment:
                    from packages.rules.zone_effects import discard_attached
                    discard_attached(target, [rule["tool"]], owner)
            elif (
                rule["kind"] == "survive_damage"
                and (event["full"] or rule.get("anyHP"))
                and event["lethal"]
                and target.hp <= 0
                and (opponent_attack or not rule.get("opponentOnly"))
            ):
                if rule.get("coin"):
                    from ptcg.utils.utils import flip_coin
                    from ptcg.core.enums import Coin
                    if flip_coin(state, owner) != Coin.HEAD:
                        continue
                target.hp = rule["remainingHP"]
                if rule.get("consume") and rule.get("tool") in target.attachment:
                    from packages.rules.zone_effects import discard_attached
                    discard_attached(target, [rule["tool"]], owner)
                if hasattr(target, "knockout_bonus_prizes"):
                    del target.knockout_bonus_prizes
    # Prize reductions inspect the field before any simultaneous KO is removed.
    for event in events:
        from packages.rules.reaction_choices import capture
        capture(event, state)
        target, source = event["target"], event["source"]
        owner = owner_of(target, state)
        if (
            not owner
            or target.hp > 0
            or not event["lethal"]
            or event["sourceOwner"] == owner.id
        ):
            continue
        attacker = state.player1 if event["sourceOwner"] == state.player1.id else state.player2
        if event["wasActive"] and getattr(attacker,"tera_bonus_prize_turn",None) == state.turn_number and source.pokemonRule.name == "TERA":
            target.knockout_bonus_prizes = getattr(target,"knockout_bonus_prizes",0)+1
        reduction = 0
        for r in rules(target, state, active_only=True):
            if r["kind"] == "knockout_retaliate":
                from packages.rules.ability_protection import blocked
                if not r.get('tool') and blocked(source,target,state):
                    continue
                if r.get("activeOnly") and not event["wasActive"]:
                    continue
                if r.get("coin"):
                    from ptcg.utils.utils import flip_coin
                    from ptcg.core.enums import Coin
                    if flip_coin(state, owner) != Coin.HEAD:
                        continue
                if owner_of(source, state) is not None:
                    if r.get("knockout"):
                        source.hp = 0
                    else:
                        source.hp -= r["counters"]
                continue
            if r["kind"] != "prize_reduction":
                continue
            if r.get("requiresName") and not any(
                c.name == r["requiresName"] for c in owner.active + owner.bench
            ):
                continue
            if r.get("onceFlag") and getattr(owner, r["onceFlag"], False):
                continue
            reduction += r["count"]
            if r.get("onceFlag"):
                setattr(owner, r["onceFlag"], True)
        if reduction:
            target.knockout_prize_reduction = reduction
    return True


def coin_armor(target, damage, state):
    if (
        damage > 0
        and enabled(target, state)
        and any(r["kind"] == "coin_armor"
                and (not r.get("energy") or any(energy_matches(e, r["energy"]) for e in target.energy))
                and (not r.get("status") or getattr(getattr(target, "special_condition", None), "name", None) == r["status"])
                for r in rules(target))
    ):
        from ptcg.utils.utils import flip_coin
        from ptcg.core.enums import Coin

        if flip_coin(state, owner_of(target, state)) == Coin.HEAD:
            return 0
    return damage

from packages.rules.energy_units import matches as energy_matches
