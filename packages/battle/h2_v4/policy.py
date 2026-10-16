"""Independent visible-state heuristic; no A1, search policy or private engine imports.

Candidate strength is measured, never implied by the policy name.
"""

from collections import Counter

from packages.battle.h2_v4.visible_math import damage, counters, mechanic

from packages.battle.h2_v4.tempo import attack_value, ready_value, development, immediate_win

VERSION = "h2-visible-tempo-v4"


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
                energy = target.get("energy", [])
                costs = [
                    deficit(a.get("cost", []), energy)
                    for a in target.get("attacks", [])
                ]
                provides = source.get("provides", []) or (
                    [source["cardType"]] if source.get("cardType") else []
                )
                gains = [
                    deficit(a.get("cost", []), energy)
                    - deficit(a.get("cost", []), energy + provides)
                    for a in target.get("attacks", [])
                ]
                return (
                    (110 if any(g > 0 for g in gains) else 30)
                    + (
                        40
                        if any(
                            before == 1 and gain == 1
                            for before, gain in zip(costs, gains)
                        )
                        else 0
                    )
                    + (15 if target in own["active"] else 0)
                    - (70 if costs and min(costs) == 0 else 0)
                    + (80 if target.get("name") == "Joltik" and not energy else 0)
                )
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
    return {
        "commandId": f"h2-{dto['stateVersion']}",
        "expectedStateVersion": dto["stateVersion"],
        "decisionId": d["id"],
        "choice": choice,
    }, False

