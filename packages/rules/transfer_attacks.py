"""Physical transfers and bounded counter rearrangement during attacks."""

from ptcg.core.card import PokemonCard, EnergyCard, ToolCard
from ptcg.core.enums import CardPosition, EnergyType, Stage
from ptcg.utils.utils import current_player, opponent_player, move_cards, shuffle_cards, switch_pokemon
from packages.rules.entry_effects import choice, transfer, reveal
from packages.rules.protection import blocked

OPERATIONS = {"transfer_counters", "transfer_energy", "strip_attached", "search_top", "window_bench", "opponent_hand_bench", "optional_push"}


def resolve(r, action, state):
    p, o = current_player(state), opponent_player(state)
    source, target = action.source, action.target
    op = r["operation"]
    if op == "transfer_counters":
        from packages.rules.attack_math import counters
        owner = o if r.get("opponent") else p
        donors = list(owner.active + owner.bench if r.get("anywhere") else owner.bench)
        if r.get("tag"):
            donors = [c for c in donors if getattr(getattr(c, "pokemonRule", None), "name", None) == r["tag"]]
        if r.get("rocket"):
            donors = [c for c in donors if c.name.startswith("Team Rocket's ")]
        if r.get("all"):
            chosen = yield from choice(source, donors, state)
            if chosen:
                donor = chosen[0]
                amount = counters(donor) * 10
                if donor in o.active + o.bench and blocked(donor, state, "effects", source):
                    amount = 0
                donor.hp += amount
                # Moving onto a protected recipient still removes counters
                # from the donor (official Advanced Manual, C-08).
                if not blocked(target, state, "counters", source):
                    target.hp -= amount
        else:
            budget = {c: counters(c) for c in donors}
            recipients = list(o.active + o.bench) if r.get("anywhere") else [target]
            while True:
                pool = [c for c in donors if budget[c] and any(t is not c for t in recipients)]
                chosen = yield from choice(source, pool, state, 0, 1)
                if not chosen:
                    break
                donor = chosen[0]
                recipient = (yield from choice(source, [c for c in recipients if c is not donor], state))[0]
                budget[donor] -= 1
                if not blocked(donor, state, "effects", source):
                    donor.hp += 10
                    if not blocked(recipient, state, "counters", source):
                        recipient.hp -= 10
        action.group_damage_targets = [target] if getattr(action, "damage_dealt", 0) else []
    elif op == "transfer_energy":
        if r.get("fromSelf"):
            if p.bench:
                from packages.rules.energy_selection import choose_units
                cards = yield from choose_units(source, r["count"], action, state)
                if cards:
                    recipient = (yield from choice(source, list(p.bench), state))[0]
                    transfer(source, recipient, cards)
        else:
            from packages.rules.core_fixes import refresh_energy
            for c in p.active + p.bench:
                refresh_energy(c)
            original = [(c, e) for c in p.active + p.bench for e in c.attachment
                        if isinstance(e, EnergyCard) and (not r.get("type") or any(energy_matches(t, r["type"]) for t in e.provides))]
            # Each original physical Energy can move once. Newly moved cards
            # never create an unbounded sequence of equivalent decisions.
            while original and len(p.active + p.bench) > 1:
                cards = yield from choice(source, [e for _, e in original], state, 0, 1)
                if not cards:
                    break
                energy = cards[0]
                donor = next(c for c, e in original if e is energy)
                recipient = (yield from choice(source, [c for c in p.active + p.bench if c is not donor], state))[0]
                transfer(donor, recipient, [energy])
                original = [(c, e) for c, e in original if e is not energy]
    elif op == "strip_attached":
        from packages.rules.zone_effects import discard_attached
        from ptcg.core.enums import PokemonType
        holders = list(o.active + o.bench if r.get("field") else o.active)
        if r.get("ex"):
            holders = [c for c in holders if c.pokemonType == PokemonType.EX]
        def matches(card):
            return isinstance(card, ToolCard) if r.get("tools") else isinstance(card, EnergyCard) and (not r.get("special") or card.energyType == EnergyType.SPECIAL)
        pairs = [(c, e) for c in holders for e in c.attachment if matches(e)]
        selected = yield from choice(source, [e for _, e in pairs], state, 0 if r.get("optional") else 1, r.get("count", 1))
        for holder, energy in pairs:
            if energy in selected and not blocked(holder, state, "effects", source):
                discard_attached(holder, [energy], o)
    elif op == "search_top":
        chosen = yield from choice(source, list(p.left), state, 0, r["count"])
        rest = [c for c in p.left if c not in chosen]
        shuffle_cards(rest, state)
        ordered = []
        while chosen:
            card = (yield from choice(source, chosen, state))[0]
            chosen.remove(card)
            ordered.append(card)
        p.left[:] = ordered + rest
        for i, card in enumerate(p.left):
            card.index = i + 1
    elif op in ("window_bench", "opponent_hand_bench"):
        owner = o if op == "opponent_hand_bench" else p
        zone = CardPosition.HAND if owner is o else CardPosition.LEFT
        window = list(o.hand) if owner is o else list(p.left[:r["count"]])
        if owner is o:
            reveal(window, state, o)
        elif window:
            yield from choice(source, window, state, len(window), len(window))
        pool = [c for c in window if isinstance(c, PokemonCard) and (owner is p or c.stage == Stage.BASIC)]
        count = min(r.get("maximum", len(pool)), max(0, owner.benchSize - len(owner.bench)))
        cards = yield from choice(source, pool, state, 0, count)
        move_cards(cards, (owner.id, zone), (owner.id, CardPosition.BENCH), state)
        for card in cards:
            card.firstTurnPlayed = True
        if owner is p:
            shuffle_cards(p.left, state)
    elif op == "optional_push":
        if o.bench and not blocked(target, state, "effects", source) and (yield from choice(source, [source], state, 0, 1)):
            from packages.rules.trainer_operations import select
            selected = yield from select(source, list(o.bench), state, o)
            switch_pokemon(target, selected[0], o)

from packages.rules.energy_units import matches as energy_matches
