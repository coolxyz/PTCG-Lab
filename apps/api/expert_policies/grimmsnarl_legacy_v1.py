"""Exact-cardlist experiment. Rebuild a visible plan at every decision boundary.

No engine access, opponent identity, deck order, or saved hidden state enters policy.
The caller supplies only its own registered construction and environment binding.
"""
from collections import Counter
from hashlib import sha256
import json
import time
from pathlib import Path

from packages.battle import h2
from packages.battle.h2_v4 import policy as base
from packages.battle.h2_v4.tempo import attack_value, ready_value
from packages.battle.h2_v4.visible_math import counters
from packages.simulation.a2 import legal

ROOT = Path(__file__).resolve().parents[3]
PROFILE = json.loads((Path(__file__).parent / 'grimmsnarl-profile.json').read_text())
VERSION = 'h2-grimmsnarl-plan-v1'
MAIN = "Marnie's Grimmsnarl ex"
MID = "Marnie's Morgrem"
BASIC = "Marnie's Impidimp"
LINE = (BASIC, MID, MAIN)


def deck_hash(entries):
    counts = Counter()
    for e in entries:
        counts[e['printingId']] += e['quantity']
    return sha256(json.dumps(sorted(counts.items()), ensure_ascii=False).encode()).hexdigest()


def environment_hash():
    return sha256(json.dumps({p: sha256((ROOT / p).read_bytes()).hexdigest()
                             for p in PROFILE['environmentFiles']}, sort_keys=True).encode()).hexdigest()


def plan(dto):
    own, other = dto['observation']['self'], dto['observation']['opponent']
    field = own['active'] + own['bench']
    names = Counter(c['name'] for c in field)
    hand = Counter(c['name'] for c in own['hand'])
    main = sorted((c for c in field if c['name'] in LINE),
                  key=lambda c: (LINE.index(c['name']), min(2, c.get('energy', []).count('DARK')), c.get('hp', 0)), reverse=True)
    lead = main[0] if main else {}
    needs = []
    if not main:
        needs.append(BASIC)
    elif lead['name'] == BASIC:
        needs.extend([MAIN, 'Rare Candy'] if hand[MAIN] and hand['Rare Candy'] else [MID, MAIN])
    elif lead['name'] == MID:
        needs.append(MAIN)
    if len(main) < 2:
        needs.append(BASIC)
    if not names['Munkidori']:
        needs.append('Munkidori')
    if not names['Froslass']:
        needs.append('Froslass' if names['Snorunt'] else 'Snorunt')
    # The same resource quotas drive search, recovery and payment, including copies.
    reserve = {n: 1 for n in needs}
    if hand[MAIN] and any(c['name'] == BASIC for c in main):
        reserve['Rare Candy'] = 1
        reserve[MAIN] = 1
    reserve['Darkness Energy'] = min(2, max(1, 2 - lead.get('energy', []).count('DARK')))
    reserve['Arven'] = int(not own.get('turnFlags', {}).get('supporterPlayed') and bool(needs))
    return {'stateVersion': dto['stateVersion'], 'goal': 'sustain-shadow-bullet' if names[MAIN] else 'establish-shadow-bullet',
            'leadRef': lead.get('ref'), 'leadName': lead.get('name'), 'needs': list(dict.fromkeys(needs)),
            'reserve': reserve, 'fieldNames': dict(names), 'handNames': dict(hand),
            'movableCounters': max((counters(c) for c in field), default=0),
            'opponentPrizes': other['prize_count'],
            'chain': ['establish-line', 'search-evolution', 'evolve/punk-up', 'manual-energy-by-purpose', 'promote-ready-attacker', 'attack'],
            'revalidation': 'rebuild-from-current-visible-state'}


def resource(c, p, own):
    """Marginal acquisition value; protection uses the same value and copy quotas."""
    n = c.get('name')
    needs = p['needs']
    if n in needs:
        return (240 - needs.index(n) * 22) if not p['handNames'].get(n) else 35
    if n == 'Darkness Energy':
        return 150 if p['handNames'].get(n, 0) < p['reserve'][n] else 10
    if n == 'Rare Candy' and p['reserve'].get(n):
        return 230
    if n in ('Poké Pad', 'Buddy-Buddy Poffin', 'Ultra Ball') and needs:
        return 170
    if n == 'Arven' and p['reserve'].get(n):
        return 160
    if n in ('Iono', "Lillie's Determination"):
        return 100 if len(own['hand']) <= 4 else 40
    return base.utility(c, own)


def energy_value(c, own, other, p):
    """Competing uses of this turn's Darkness attachment, not printed attack cost alone."""
    n, energy = c.get('name'), c.get('energy', [])
    dark = energy.count('DARK')
    if n == MAIN and dark < 2:
        return 290 if dark == 1 else 205
    if n == 'Munkidori' and not dark:
        # Ability use must have an actual movable counter; future Froslass is weaker.
        return 245 if p['movableCounters'] else (135 if p['fieldNames'].get('Froslass') else 40)
    if n in (BASIC, MID) and dark < 2:
        # Evolution itself can accelerate: do not spend the attachment first when legal.
        return 175 if p['handNames'].get(MAIN) else 120
    if n == 'Bloodmoon Ursaluna ex' and not c.get('attackBlocked'):
        missing = max(0, 5 - (6 - other['prize_count']) - len(energy))
        return 270 if missing == 1 else (90 if missing == 2 else -90)
    if c in own['active'] and not own.get('turnFlags', {}).get('retreated'):
        cost = len(c.get('retreatCost', []))
        better = max((ready_value(b, own, other, base.deficit) for b in own['bench']), default=-100)
        now = ready_value(c, own, other, base.deficit)
        if len(energy) < cost <= len(energy) + 1 and better > now + 20:
            return 220
    return -120


def expert(dto, p):
    own, other = dto['observation']['self'], dto['observation']['opponent']
    d = dto['decision']
    field = own['active'] + own['bench']
    active = (own['active'] or [{}])[0]
    original, _ = base.decide(dto)
    if d['kind'] == 'selection':
        cs = d['candidates']
        if not cs or d.get('hidden'):
            return original, 'hidden-or-empty-selection'
        cost = d.get('zones') == ['CardPosition.HAND'] and dto.get('phase') == 'playing'
        # Opponent targeting and damage movement retain shared public tactical logic.
        if not cost and (any(c.get('side') == 'opponent' for c in cs) or d.get('source') == 'Munkidori' or any(c.get('card', {}).get('kind') == 'attack' for c in cs)):
            return original, 'shared-tactics'
        count = d['min'] if cost else min(d['max'], len(cs))
        if cost:
            # Keep the most valuable quota copies; don't protect every duplicate.
            keep = {}
            for name, quota in p['reserve'].items():
                matches = [c for c in cs if c.get('card', {}).get('name') == name]
                for c in matches[:quota]:
                    keep[c['ref']] = 500 + resource(c['card'], p, own)
            ordered = sorted(cs, key=lambda c: keep.get(c['ref'], resource(c.get('card', {}), p, own) / 10))
            reason = 'pay-with-surplus-preserve-plan'
        elif d.get('source') == MAIN and all(c.get('card', {}).get('superType') == 'ENERGY' for c in cs):
            # Only take the energy required by currently visible Marnie lines.
            missing = sum(max(0, 2-c.get('energy', []).count('DARK')) for c in field if c['name'] in LINE)
            count = max(d['min'], min(count, missing))
            ordered = cs
            reason = 'punk-up-preserve-deck-energy'
        elif d.get('source') == MAIN and all(c.get('side') == 'self' for c in cs):
            ordered = sorted(cs, key=lambda c: energy_value(c['card'], own, other, p), reverse=True)
            reason = 'punk-up-complete-main-before-backup'
        elif dto.get('phase') == 'active' or any(c.get('side') == 'self' and c.get('boardRef') for c in cs):
            return original, 'shared-promotion-or-board-selection'
        else:
            ordered = sorted(cs, key=lambda c: resource(c.get('card', {}), p, own), reverse=True)
            reason = 'search-or-recover-plan-component'
            if count > 1:
                # Spread multi-Pokemon searches across useful roles, not four monkeys.
                unique, rest, seen = [], [], set()
                for c in ordered:
                    n = c.get('card', {}).get('name')
                    (rest if n in seen else unique).append(c)
                    seen.add(n)
                ordered = unique + rest
        choice = {'selectedRefs': [c['ref'] for c in ordered[:count]]}
    else:
        def score(o):
            kind, n = o['actionType'], o.get('source')
            source = next((c for c in own['hand'] + field if c.get('ref') == o.get('sourceRef')), {})
            if kind == 'NumberOption' and o.get('label') in ('使用特性', '不使用特性'):
                # This exact list's optional entry ability is Punk Up. The generic
                # policy otherwise selects option zero and silently declines it.
                return 300 if o.get('value') == 1 else 0
            if kind == 'AttackAction':
                return attack_value(o.get('attack') or {}, active, own, other)
            if kind == 'EvolvePokemonAction':
                return 350 if n == MAIN else (280 if n == MID else 190)
            if kind == 'AttachEnergyAction':
                target = next((c for c in field if c.get('ref') == o.get('targetRef')), {})
                return energy_value(target, own, other, p)
            if kind == 'PlayPokemonAction':
                if n == BASIC:
                    return 300 if not any(p['fieldNames'].get(x) for x in LINE) else (200 if sum(p['fieldNames'].get(x,0) for x in LINE)<2 else -120)
                if n == 'Munkidori':
                    return 230 if not p['fieldNames'].get(n) else (125 if p['movableCounters'] >= 3 and p['fieldNames'].get(n,0)<2 else -120)
                if n in ('Snorunt', 'Shaymin', 'Budew'):
                    return 180 if not p['fieldNames'].get(n) and len(own['bench']) < 4 else -120
                if n == 'Bloodmoon Ursaluna ex':
                    return 190 if other['prize_count'] <= 3 else -110
            if kind == 'RetreatAction':
                gain = max((ready_value(c, own, other, base.deficit) for c in own['bench']), default=-100) - ready_value(active, own, other, base.deficit)
                return 180 + gain if gain > 20 else -150
            if kind == 'UseAbilityAction' and n == 'Munkidori':
                return 330 if p['movableCounters'] else -150
            if kind == 'UseItemAction':
                if n == 'Rare Candy':
                    return 345
                if n in ('Poké Pad', 'Buddy-Buddy Poffin'):
                    return 250 if p['needs'] else 85
                if n in ('Secret Box', 'Ultra Ball'):
                    available = Counter(c['name'] for c in own['hand'] if c.get('ref') != source.get('ref'))
                    surplus = sum(max(0, v - p['reserve'].get(k, 0)) for k,v in available.items())
                    return 220 if surplus >= (3 if n == 'Secret Box' else 2) else -130
                if n == 'Night Stretcher':
                    best = max((resource(c,p,own) for c in own['discard']), default=0)
                    return best if best >= 150 else 80
            if kind == 'UseSupporterAction':
                if n == 'Arven':
                    return 225 if p['needs'] else 100
                if n in ('Iono', "Lillie's Determination"):
                    return 100 + max(0, 5-len(own['hand']))*12
            if kind == 'UseStadiumAction' and n in ('Artazon','Spikemuth Gym'):
                return 240 if p['needs'] else 75
            # Retain baseline choice for unsupported effects/options, with lower priority
            # than explicit plan steps. Legal options remain the only action source.
            return 110 if o['id'] == original['choice'].get('optionId') else ({'UseAbilityAction':130,'UseItemAction':105,'UseToolAction':95,'PutStadiumAction':80,'PassTurn':-100}.get(kind, 0))
        ranked = sorted(d['options'], key=score, reverse=True)
        choice = {'optionId': ranked[0]['id']}
        reason = {'chosen': ranked[0]['actionType'], 'source': ranked[0].get('source'),
                  'alternatives': [{'id': o['id'], 'value': score(o)} for o in ranked[:5]]}
    return {**original, 'choice': choice}, reason


def decide(dto, *, entries, environment):
    if dto['observation']['opponent']['hand'] is not None:
        raise ValueError('HIDDEN_HAND_EXPOSED')
    if deck_hash(entries) != PROFILE['deckHash'] or environment != PROFILE['environmentHash']:
        cmd, detail = h2.decide(dto)
        return cmd, {**detail, 'route': 'frozen-h2'}
    start = time.perf_counter()
    p = plan(dto)
    cmd, reason = expert(dto, p)
    fallback = not legal(dto, cmd) or time.perf_counter()-start > 1
    if fallback:
        cmd, _ = h2.decide(dto)
    assert legal(dto, cmd)
    return cmd, {'route': VERSION, 'fallback': fallback, 'plan': p, 'reason': reason,
                 'aiMilliseconds': (time.perf_counter()-start)*1000}
