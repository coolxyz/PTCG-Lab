"""Preserve development; use public risk only for executable endgame choices."""

from copy import deepcopy
from itertools import combinations
import math
import time

from apps.api.expert_policies import dragapult_legacy as previous, dragapult_risk as risk
from packages.battle.h2_v4.tempo import estimate
from packages.simulation.a2 import legal

PROFILE = previous.PROFILE
VERSION = "h2-dragapult-tempo-risk-v6"
FIXED_ATTACKS = {"Phantom Dive", "Jet Headbutt", "Dragon Headbutt", "Itchy Pollen"}


def field(side):
    return side["active"] + side["bench"]


def prizes(side):
    return sum(c.get("prize", 1) for c in field(side) if c.get("hp", 0) <= 0)


def terminal(own, other):
    return terminal_score(own, other) == 1


def terminal_score(own, other):
    """Count simultaneous public win conditions, as the engine does.

    None means the visible projection has not ended the game. A draw is a
    distinct result, never a loss merely because the opponent takes a prize.
    """
    ours = int(prizes(other) >= own.get("prize_count", 6)) + int(not any(c.get("hp", 0) > 0 for c in field(other)))
    theirs = int(prizes(own) >= other.get("prize_count", 6)) + int(not any(c.get("hp", 0) > 0 for c in field(own)))
    if not (ours or theirs):
        return None
    return 1 if ours > theirs else 0 if ours < theirs else .5


def protected(card):
    return card.get("attackProtection", {}).get("effects")


def counter_target(own, other):
    candidates = [c for c in other["bench"] if not protected(c)] or other["bench"]
    alive = [c for c in candidates if c.get("hp", 0) > 0]
    dead = [c for c in candidates if c.get("hp", 0) <= 0]
    need = own.get("prize_count", 6) - prizes(other)
    if need <= 0 and dead:
        return dead[0]
    # Minimum-cost winning subset. Its cost falls with every chosen counter,
    # so recomputing at each prompt preserves the six-counter executable win.
    possible = [c for c in alive if c.get("hp", 999) <= 60]
    wins = []
    for n in range(1, len(possible) + 1):
        for subset in combinations(possible, n):
            cost = sum(math.ceil(c["hp"] / 10) for c in subset)
            if cost <= 6 and sum(c.get("prize", 1) for c in subset) >= need:
                wins.append((cost, subset))
    if wins:
        _, subset = min(wins, key=lambda pair: pair[0])
        return min(subset, key=lambda c: c["hp"])
    if not alive:
        return candidates[0]
    if not risk.monkeys(other) or other.get("prize_count", 6) > 3:
        return max(alive, key=previous.damage_target)

    def value(card):
        hp = card.get("hp", 999)
        base = previous.damage_target(card)
        if hp > 60:
            base += 160 if risk.ability(card, "Adrena-Brain") else 100 if risk.ability(card, "Freezing Shroud") else 0
            if 200 < hp <= 260 and risk.transfer_capacity(other):
                base -= 250
        return base

    chosen = max(alive, key=value)
    # Overkill is allowed only when this extra counter crosses a public lethal
    # transfer threshold. No monkeys => never waste pressure on a pending KO.
    if dead and chosen["hp"] > 10 and other.get("prize_count", 6) <= 2:
        before = risk.reply_prizes(own, other)
        b = deepcopy(other)
        next(c for c in b["bench"] if c.get("ref") == chosen.get("ref"))["hp"] -= 10
        if before < other["prize_count"] <= risk.reply_prizes(own, b):
            return dead[0]
    return chosen


def attack_state(attack, own, other):
    a, b = deepcopy(own), deepcopy(other)
    if b["active"]:
        b["active"][0]["hp"] -= estimate(attack, a["active"][0], a, b)
    if attack.get("name") == "Phantom Dive":
        for _ in range(6):
            if b["bench"]:
                target = counter_target(a, b)
                if not protected(target):
                    target["hp"] -= 10
    return a, b


def reply(own, other):
    a = {**own, "active": [c for c in own["active"] if c.get("hp", 0) > 0],
         "bench": [c for c in own["bench"] if c.get("hp", 0) > 0]}
    b = {**other, "active": [c for c in other["active"] if c.get("hp", 0) > 0],
         "bench": [c for c in other["bench"] if c.get("hp", 0) > 0]}
    return risk.reply_prizes(a, b)


def supported_attacks(own):
    if own.get("turnFlags", {}).get("attacksBlocked"):
        return []
    return [a for a in risk.ready_attacks((own["active"] or [{}])[0]) if a.get("name") in FIXED_ATTACKS]


def gusted(other, index):
    b = deepcopy(other)
    b["active"][0], b["bench"][index] = b["bench"][index], b["active"][0]
    return b


def retreated(own, index):
    a = deepcopy(own)
    old = a["active"][0]
    old["energy"] = old.get("energy", [])[len(old.get("retreatCost", [])):]
    a["active"][0], a["bench"][index] = a["bench"][index], old
    return a


def blast_rule(card):
    return next((a for a in risk.SPECS.get(card.get("id"), {}).get("abilities", [])
                 if a.get("selfKnockout") and a.get("effect", {}).get("kind") == "counters"), None)


def blast_state(own, other, source, target, rule):
    a, b = deepcopy(own), deepcopy(other)
    next(c for c in field(a) if c.get("ref") == source.get("ref"))["hp"] = 0
    next(c for c in field(b) if c.get("ref") == target.get("ref"))["hp"] -= rule["effect"]["amount"]
    return a, b


def winning_blast_target(own, other, source, attacks):
    rule = blast_rule(source)
    if not rule:
        return None
    for target in field(other):
        if protected(target):
            continue
        a, b = blast_state(own, other, source, target, rule)
        if terminal(a, b):
            return target
        if terminal_score(a, b) is not None:
            continue  # the ability has already ended the game; no follow-up
        # A bench ability preserves this already legal active attack. Do not
        # invent a future promotion, attachment, attack permission or draw.
        if source in own["bench"] and b["active"] and b["active"][0].get("hp", 0) > 0:
            if any(terminal(*attack_state(attack, a, b)) for attack in attacks):
                return target
    return None


def drawing_blast_target(own, other, source):
    rule = blast_rule(source)
    if not rule:
        return None
    for target in field(other):
        # Do not infer a certain draw across protection or prize-changing
        # mechanics. Actual target prompts remain engine-authoritative.
        if protected(target) or target.get("abilityEffectProtected") or "Mist Energy" in target.get("attachment", []):
            continue
        uncertain = any(r.get("kind") in ("ability_protection", "knockout_prizes")
                        for c in (source, target) if not c.get("abilitiesSuppressed")
                        for r in risk.SPECS.get(c.get("id"), {}).get("abilities", []))
        if not uncertain and terminal_score(*blast_state(own, other, source, target, rule)) == .5:
            return target
    return None


def decide(dto, *, entries, environment):
    started = time.perf_counter()
    cmd, detail = previous.decide(dto, entries=entries, environment=environment)
    if detail.get("route") != previous.VERSION or dto.get("phase") != "playing":
        return cmd, detail
    own, other = dto["observation"]["self"], dto["observation"]["opponent"]
    d = dto["decision"]
    reason = detail["reason"]
    if d["kind"] == "selection" and not d.get("hidden"):
        cs = d["candidates"]
        enemies = [c for c in cs if c.get("side") == "opponent"]
        target_ref = None
        if enemies and len(enemies) == len(cs):
            if d.get("source") == "Dragapult ex":
                b = {**other, "bench": [{**c["card"], "ref": c.get("boardRef")} for c in enemies]}
                target_ref = counter_target(own, b).get("ref")
                reason = "finish-prizes-preserve-pressure"
            elif d.get("source") in ("Boss's Orders", "Counter Catcher"):
                choices = []
                for item in enemies:
                    index = next(i for i, c in enumerate(other["bench"]) if c.get("ref") == item.get("boardRef"))
                    for attack in supported_attacks(own):
                        a, b = attack_state(attack, own, gusted(other, index))
                        choices.append(((terminal(a, b), -reply(a, b), prizes(b)), item.get("boardRef")))
                winning = [choice for choice in choices if choice[0][0]]
                if winning:
                    target_ref = max(winning, key=lambda pair: pair[0])[1]
                    reason = "executable-gust-finish"
            else:
                source = next((c for c in field(own) if c.get("id") == d.get("sourceId")), {})
                followup = [] if own.get("turnFlags", {}).get("firstPersonalTurn") else supported_attacks(own)
                target = winning_blast_target(own, other, source, followup)
                if target:
                    target_ref = target.get("ref")
                    reason = "executable-blast-finish"
                elif drawing_blast_target(own, other, source):
                    target_ref = drawing_blast_target(own, other, source).get("ref")
                    reason = "execute-simultaneous-draw-target"
        elif own["active"] and cs and d.get("source") is None and other.get("prize_count", 6) <= 2 and all(
            c.get("side") == "self" and c.get("position") == "CardPosition.BENCH" for c in cs
        ):
            # Actual retreat target choice, never ordinary forced promotion.
            choices = []
            for item in cs:
                index = next(i for i, c in enumerate(own["bench"]) if c.get("ref") == item.get("boardRef"))
                a = retreated(own, index)
                for attack in supported_attacks(a):
                    aa, bb = attack_state(attack, a, other)
                    choices.append(((terminal(aa, bb), -reply(aa, bb), prizes(bb)), item.get("boardRef")))
            if choices:
                target_ref = max(choices, key=lambda pair: pair[0])[1]
                reason = "executable-retreat-rescue"
        if target_ref:
            chosen = next(c for c in cs if c.get("boardRef") == target_ref)
            cmd = {**cmd, "choice": {"selectedRefs": [chosen["ref"]]}}
    elif d["kind"] == "options":
        options = d["options"]
        attacks = [o for o in options if o["actionType"] == "AttackAction" and o.get("attack", {}).get("name") in FIXED_ATTACKS]
        attack_rules = [o["attack"] for o in attacks]
        chosen = next(o for o in options if o["id"] == cmd["choice"]["optionId"])
        wins = []
        for o in attacks:
            if terminal(*attack_state(o["attack"], own, other)):
                wins.append((0, o))
        for o in options:
            kind = o["actionType"]
            if kind in ("UseSupporterAction", "UseItemAction") and o.get("source") in ("Boss's Orders", "Counter Catcher"):
                if any(terminal(*attack_state(attack, own, gusted(other, i)))
                       for i in range(len(other["bench"])) for attack in attack_rules):
                    wins.append((1, o))
            elif kind == "UseAbilityAction":
                source = next((c for c in field(own) if c.get("ref") == o.get("sourceRef")), {})
                if winning_blast_target(own, other, source, attack_rules):
                    wins.append((2, o))
        if wins:
            choice = min(wins, key=lambda pair: pair[0])[1]
            cmd = {**cmd, "choice": {"optionId": choice["id"]}}
            reason = "executable-terminal-plan"
        elif chosen["actionType"] == "UseAbilityAction" and drawing_blast_target(
            own, other, next((c for c in field(own) if c.get("ref") == chosen.get("sourceRef")), {})
        ):
            # Preserve the preferred legal draw instead of replacing it with
            # PassTurn. Known winning routes above still take precedence.
            reason = "executable-draw-plan"
        elif chosen["actionType"] == "AttackAction" and chosen in attacks and other.get("prize_count", 6) <= 2:
            # An actual attack is the anchor. Absence of an attack is not a
            # numerical loss, and cannot preempt early development or promotion.
            a, b = attack_state(chosen["attack"], own, other)
            if reply(a, b) >= other["prize_count"]:
                alternatives = []
                for o in attacks:
                    aa, bb = attack_state(o["attack"], own, other)
                    if reply(aa, bb) < other["prize_count"]:
                        alternatives.append((prizes(bb), o))
                for o in options:
                    if o["actionType"] == "RetreatAction":
                        for i in range(len(own["bench"])):
                            aa = retreated(own, i)
                            for attack in supported_attacks(aa):
                                aaa, bb = attack_state(attack, aa, other)
                                if reply(aaa, bb) < other["prize_count"]:
                                    alternatives.append((prizes(bb), o))
                if alternatives:
                    choice = max(alternatives, key=lambda pair: pair[0])[1]
                    cmd = {**cmd, "choice": {"optionId": choice["id"]}}
                    reason = "strict-terminal-reply-improvement"
        else:
            source = next((c for c in field(own) if c.get("ref") == chosen.get("sourceRef")), {})
            if chosen["actionType"] == "UseAbilityAction" and blast_rule(source) and other.get("prize_count", 6) <= source.get("prize", 1):
                safe = [o for o in options if not (o["actionType"] == "UseAbilityAction" and blast_rule(
                    next((c for c in field(own) if c.get("ref") == o.get("sourceRef")), {})))]
                if safe:
                    cmd, _ = previous.decide({**dto, "decision": {**d, "options": safe}}, entries=entries, environment=environment)
                    reason = "do-not-donate-last-prize"
    assert legal(dto, cmd), "INVALID_TEMPO_RISK_PROPOSAL"
    return cmd, {**detail, "route": VERSION, "reason": reason,
                 "aiMilliseconds": (time.perf_counter() - started) * 1000}
