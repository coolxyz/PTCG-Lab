"""Independent visible-state heuristic; no A1, search policy or private engine imports.

Candidate strength is measured, never implied by the policy name.
"""

from collections import Counter

from packages.battle.h2_v4.visible_math import damage, counters, mechanic

from packages.battle.h2_v4.tempo import attack_value, ready_value, development, immediate_win

VERSION = "h2-opportunity-cost-v8-candidate"


def deficit(cost, energy):
    have = Counter(energy)
    wild = have.pop("ANY", 0)
    missing = sum(
        max(0, n - have[t]) for t, n in Counter(cost).items() if t != "COLORLESS"
    )
    return max(0, missing - wild, len(cost) - len(energy))


def potential(card):
    return max((a.get("damage") or 0 for a in card.get("attacks", [])), default=0)


def utility(card, own):
    field = own["active"] + own["bench"]
    names = {c.get("name") for c in field}
    if card.get("kind") == "attack":
        return card.get("damage") or 0
    score = potential(card) / 8 + (card.get("hp") or 0) / 20
    if card.get("superType") == "ENERGY":
        return 25 - 4 * sum(c.get("superType") == "ENERGY" for c in own["hand"])
    if card.get("superType") == "POKEMON":
        score += 60 if set(card.get("evolveFrom", [])) & names else 0
        score += 35 if card.get("stage") == "BASIC" and len(field) < 3 else 0
        score -= 20 if card.get("name") in names else 0
        score -= (
            25
            if card.get("stage") not in (None, "BASIC")
            and not set(card.get("evolveFrom", [])) & names
            else 0
        )
    return score + development(card, own)


def attack_energy_priority(source, target, own, other):
    """Value attack opportunities created, not merely an arbitrary deficit reduction.

    Only the visible field and energy card are used; no assumed deck composition.
    Missing off-type requirements remain a cost even if colourless costs decrease.
    """
    if target.get("attackBlocked"):
        return -110
    energy = target.get("energy", [])
    provides = source.get("provides", []) or ([source["cardType"]] if source.get("cardType") else [])
    after = energy + provides
    attacks = [a for a in target.get("attacks", []) if a.get("name") not in target.get("limitedAttacks", [])]
    ready = max((attack_value(a, target, own, other) for a in attacks if deficit(a.get("cost", []), energy) == 0), default=0)
    scores = [-110]
    for attack in attacks:
        cost = attack.get("cost", [])
        before, remaining = deficit(cost, energy), deficit(cost, after)
        if remaining >= before:
            continue
        value = attack_value(attack, target, own, other)
        if remaining == 0:
            # A ready cheap attack must not hide the gain from a stronger attack.
            gain = max(0, value - ready)
            scores.append(150 + value + gain * 0.2 + (60 if target in own["active"] else 0) if gain > 0 else 20)
        else:
            missing_typed = sum(max(0, n - Counter(after)[t]) for t,n in Counter(cost).items() if t != "COLORLESS")
            scores.append(105 + min(value, 200)/10 - remaining*20 - missing_typed*15)
    if target.get("name") == "Joltik" and not energy and max(scores) > -110:
        scores.append(max(scores)+80)
    return max(scores)


def mobility_value(source, target, own, other):
    """Reward a funded switch plan, not idle energy on an arbitrary Pokemon."""
    if target not in own['active'] or own.get('turnFlags',{}).get('retreated',True) or target.get('retreatBlocked'):
        return -110
    provides=source.get('provides',[]) or ([source['cardType']] if source.get('cardType') else [])
    have,cost=len(target.get('energy',[])),len(target.get('retreatCost',[]))
    if not provides or not have < cost <= have+len(provides):return -110
    active_value=ready_value(target,own,other,deficit)
    replacement=max((ready_value(c,own,other,deficit) for c in own['bench']),default=-100)
    if replacement <= max(0,active_value)+20:return -110
    # If this same energy completes an attack elsewhere, do not spend the
    # attachment on a retreat payment. Future switch cards are not assumed.
    if any(attack_energy_priority(source,c,own,other)>=150 for c in own['active']+own['bench'] if c is not target):return -110
    if not own.get('turnFlags',{}).get('itemsBlocked',False):
        from packages.battle.h2_v4.visible_math import PUBLIC_RELEASE
        for card in own['hand']:
            mechanisms=next((e.get('mechanisms',[]) for e in PUBLIC_RELEASE['effects'] if e['effectKey']==card.get('id')),[])
            if 'switch' in mechanisms and 'gust' not in mechanisms and card.get('trainerType')=='ITEM':return -110
    return 125


def energy_priority(source, target, own, other):
    return max(attack_energy_priority(source,target,own,other),mobility_value(source,target,own,other))


def purposeful_attachment(source,target,own,other):
    """Do not save energy from a hand reset merely to pay a mismatched attack.

    Typed energy must satisfy a missing typed requirement or leave all typed
    requirements paid. Colourless-only attacks still accept any energy.
    """
    if mobility_value(source,target,own,other)>0:return True
    if target.get('attackBlocked'):return False
    energy=target.get('energy',[])
    provides=source.get('provides',[]) or ([source['cardType']] if source.get('cardType') else [])
    for attack in target.get('attacks',[]):
        if attack.get('name') in target.get('limitedAttacks',[]):continue
        cost=attack.get('cost',[])
        if deficit(cost,energy)<=deficit(cost,energy+provides):continue
        typed=[t for t in cost if t!='COLORLESS']
        before,after=deficit(typed,energy),deficit(typed,energy+provides)
        if after==0 or after<before:return True
    return False


def preserve_before_reset(dto, choice):
    from packages.battle.h2_v4.visible_math import PUBLIC_RELEASE
    d=dto['decision']
    if d['kind']!='options':return choice
    own,other=dto['observation']['self'],dto['observation']['opponent']
    if own.get('turnFlags',{}).get('energyPlayed',True):return choice
    selected=next(o for o in d['options'] if o['id']==choice['optionId'])
    if selected['actionType'] not in ('UseSupporterAction','UseItemAction'):return choice
    mechanisms=next((e.get('mechanisms',[]) for e in PUBLIC_RELEASE['effects'] if e['effectKey']==selected.get('sourceId')),[])
    if not set(mechanisms)&{'hand_reset','shuffle_draw','discard_draw'}:return choice
    candidates=[]
    for option in d['options']:
        if option['actionType']!='AttachEnergyAction':continue
        source=next((c for c in own['hand'] if c.get('ref')==option.get('sourceRef')),None)
        target=next((c for c in own['active']+own['bench'] if c.get('ref')==option.get('targetRef')),None)
        if source and target and purposeful_attachment(source,target,own,other):
            value=energy_priority(source,target,own,other)
            if value>30:candidates.append((value,option)) # exclude v5's low-value redundant-attack score
    if not candidates:return choice
    return {'optionId':max(candidates,key=lambda x:x[0])[1]['id']}


def recovery_energy_value(card, own, other):
    """Separate immediately usable energy from unproven next-turn reserves.

    Recovery does not reset the once-per-turn attachment. Missing flags are
    treated conservatively; reserve value stays at the existing generic utility.
    No assumption about surviving to next turn or a future energy ability.
    """
    reserve = utility(card, own)
    flags = own.get("turnFlags", {})
    if flags.get("energyPlayed", True):
        return reserve
    field = own["active"] + own["bench"]
    best = max((energy_priority(card, p, own, other) for p in field), default=-110)
    available = max((energy_priority(e, p, own, other) for e in own["hand"] if e.get("superType") == "ENERGY" for p in field), default=-110)
    return max(reserve, best if best > available else 0)


def decide(dto):
    obs, d = dto["observation"], dto["decision"]
    if obs["opponent"]["hand"] is not None:
        raise ValueError("HIDDEN_HAND_EXPOSED")
    own, other = obs["self"], obs["opponent"]
    field = own["active"] + own["bench"]
    active = (own["active"] or [{}])[0]
    defender = (other["active"] or [{}])[0]
    if d["kind"] == "selection":
        cost = d.get("zones") == ["CardPosition.HAND"] and dto.get("phase") == "playing"
        count = d["min"] if cost else min(d["max"], len(d["candidates"]))

        def rank(item):
            c = item.get("card", {})
            if d.get("hidden"):
                return 0
            if c.get("superType") == "ENERGY" and not cost and d.get("source") == "Night Stretcher":
                return recovery_energy_value(c, own, other)
            if c.get("kind") == "attack":
                # Copied attacks use the physical active attacker for damage counters.
                owner = next(
                    (
                        p
                        for p in field
                        + other.get("active", [])
                        + other.get("bench", [])
                        if any(
                            a.get("name") == c.get("name") for a in p.get("attacks", [])
                        )
                    ),
                    active,
                )
                if d.get("sourceId") == active.get("id") and immediate_win(
                    c, owner, own, other
                ):
                    return 10000
                return damage(c, active, defender, own, other, mechanic(owner, c))
            if d.get("source") == "Joltik" and item.get("side") == "self":

                def charge_gain(a):
                    energy = c.get("energy", [])
                    gains = sum(
                        deficit(a.get("cost", []), energy)
                        - deficit(a.get("cost", []), energy + [t])
                        for t in ["GRASS", "LIGHTNING"]
                    )
                    unavailable = sum(
                        t not in ("COLORLESS", "GRASS", "LIGHTNING") and t not in energy
                        for t in a.get("cost", [])
                    )
                    return (
                        gains * 100
                        - unavailable * 160
                        + min(a.get("damage") or 0, 250) / 10
                    )

                return max((charge_gain(a) for a in c.get("attacks", [])), default=-100)
            if d.get("source") == "Gardevoir ex" and item.get("side") == "self":
                ready_gain = max(
                    (
                        deficit(a.get("cost", []), c.get("energy", []))
                        - deficit(a.get("cost", []), c.get("energy", []) + ["PSYCHIC"])
                        for a in c.get("attacks", [])
                    ),
                    default=0,
                )
                return ready_gain * 100 + (
                    30 if c.get("ref") == active.get("ref") else 0
                )
            if d.get("source") == "Munkidori" and item.get("side") == "self":
                # Avoid stripping the damage counters that power an attack unless necessary.
                attack_loss = max(
                    (
                        damage(a, c, defender, own, other)
                        - damage(
                            a,
                            {**c, "hp": c.get("hp", 0) + min(3, counters(c)) * 10},
                            defender,
                            own,
                            other,
                        )
                        for a in c.get("attacks", [])
                    ),
                    default=0,
                )
                return (
                    min(3, counters(c)) * 30
                    - attack_loss
                    + (20 if c.get("hp", 999) <= 30 else 0)
                )
            if item.get("side") == "opponent":
                # Prefer a vulnerable prize target for gust / damage placement.
                return -(c.get("hp") or 999) + (50 if "ex" in c.get("name", "") else 0)
            if dto.get("phase") == "active" or (
                item.get("side") == "self" and d.get("zones") == ["CardPosition.BENCH"]
            ):
                if dto.get("phase") == "active" and c.get("name") == "Joltik":
                    return 1000
                if dto.get("phase") == "active" and c.get("name") == "N's Zorua":
                    return 800
                return (
                    ready_value(c, own, other, deficit) * 4
                    + utility(c, own)
                    - 10 * len(c.get("retreatCost", []))
                )
            return utility(c, own)

        ordered = sorted(d["candidates"], key=rank, reverse=not cost)
        if (
            not cost
            and count > 1
            and d.get("zones") == ["CardPosition.LEFT"]
            and all(c.get("card", {}).get("superType") == "POKEMON" for c in ordered)
        ):
            unique, repeated, seen = [], [], set()
            for item in ordered:
                name = item.get("card", {}).get("name")
                (repeated if name in seen else unique).append(item)
                seen.add(name)
            ordered = unique + repeated
        choice = {"selectedRefs": [c["ref"] for c in ordered[:count]]}
    else:

        def rank(o):
            kind = o["actionType"]
            source = next(
                (c for c in own["hand"] + field if c.get("id") == o.get("sourceId")), {}
            )
            if kind == "NumberOption" and o.get("label", "").startswith("移动 "):
                return o["value"]
            if kind == "SetupOption":
                return 20 if o.get("value") == "first" else 0
            if kind == "AttackAction":
                attack = o.get("attack") or {}
                return attack_value(attack, active, own, other)
            if kind == "AttachEnergyAction":
                target = next(
                    (c for c in field if c.get("ref") == o.get("targetRef")), {}
                )
                return energy_priority(source, target, own, other)

            if (
                kind == "RetreatAction"
                or (kind == "UseAbilityAction" and source.get("name") == "Pecharunt ex")
                or (kind == "UseItemAction" and source.get("name") == "Switch")
            ):
                targets = own["bench"]
                if kind == "UseAbilityAction":
                    targets = [
                        c
                        for c in targets
                        if c.get("cardType") == "DARK"
                        and c.get("name") != "Pecharunt ex"
                    ]
                gain = max(
                    (ready_value(c, own, other, deficit) for c in targets), default=-100
                ) - ready_value(active, own, other, deficit)
                return 180 + gain if gain > 20 else -150
            if kind == "EvolvePokemonAction":
                return 145 + utility(source, own)
            if kind == "PlayPokemonAction":
                names = [c.get("name") for c in field]
                if names.count(source.get("name")) >= 2 or (
                    source.get("name") == "N's Zorua"
                    and sum(n in ("N's Zorua", "N's Zoroark ex") for n in names) >= 2
                ):
                    return -180
                return (
                    120 + utility(source, own) - (110 if len(own["bench"]) >= 4 else 0)
                )
            if kind == "UseAbilityAction" and source.get("name") == "Gardevoir ex":
                psychic = [
                    c
                    for c in field
                    if c.get("cardType") == "PSYCHIC" and c.get("hp", 0) > 20
                ]
                useful = any(
                    any(
                        deficit(a.get("cost", []), c.get("energy", []))
                        > deficit(a.get("cost", []), c.get("energy", []) + ["PSYCHIC"])
                        for a in c.get("attacks", [])
                    )
                    for c in psychic
                )
                return 155 if useful else -20
            if kind == "UseSupporterAction":
                return 100 + max(0, 5 - len(own["hand"])) * 12
            return {
                "UseAbilityAction": 130,
                "UseItemAction": 105,
                "UseToolAction": 95,
                "PutStadiumAction": 80,
                "UseStadiumAction": 75,
                "RetreatAction": -5,
                "PassTurn": -100,
            }.get(kind, 0)

        choice = {"optionId": max(d["options"], key=rank)["id"]}
    choice = preserve_before_reset(dto, choice)
    return {
        "commandId": f"h2-{dto['stateVersion']}",
        "expectedStateVersion": dto["stateVersion"],
        "decisionId": d["id"],
        "choice": choice,
    }, False

