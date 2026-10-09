"""Resolve a revealed Supporter's effect without playing that physical card."""
from ptcg.core.card import SupporterCard
from ptcg.core.action import UseSupporterAction
from ptcg.utils.utils import current_player, opponent_player
from packages.rules.entry_effects import choice, reveal


def proxy_for(card):
    from packages.rules.plain import TRAINER_SPECS
    from packages.rules.trainers import CompiledTrainer
    spec = getattr(card, "spec", None)
    if not spec:
        spec = next((s for s in TRAINER_SPECS if s["name"] == card.name), None)
    if not spec:
        family = next((name for name in ("Boss's Orders", "Professor's Research") if card.name.startswith(name)), None)
        if family:
            spec = next((s for s in TRAINER_SPECS if s["name"].startswith(family)), None)
    if not spec and card.name == "Ciphermaniac's Codebreaking":
        spec = {"name": card.name, "text": card.text, "effectKey": card.id, "trainerType": "supporter", "mechanic": {"kind": "legacy_trainer", "handler": "Ciphermaniac"}}
    if not spec:
        raise ValueError("Missing Supporter copy adapter: " + card.name)
    cls = type("CopiedSupporter", (CompiledTrainer, SupporterCard), {"spec": spec})
    proxy = cls()
    proxy.copied_effect = True
    return proxy


def usable(proxy, p, state):
    # get_actions assumes the selected card is in hand. A temporary sentinel
    # restores that counting convention; it never enters an observable prompt.
    p.hand.append(proxy)
    try:
        return bool(proxy.get_actions(state))
    finally:
        p.hand.remove(proxy)


def resolve(source, state):
    p, o = current_player(state), opponent_player(state)
    reveal(list(o.hand), state, o)
    pairs = [(c, proxy_for(c)) for c in o.hand if isinstance(c, SupporterCard)]
    pairs = [(c, proxy) for c, proxy in pairs if usable(proxy, p, state)]
    chosen = yield from choice(source, [c for c, _ in pairs], state, 0, 1)
    if not chosen:
        return
    proxy = next(proxy for card, proxy in pairs if card is chosen[0])
    rule = proxy.spec["mechanic"]
    pending = getattr(state, "copy_attack_pending", False)
    state.copy_attack_pending = True
    try:
        if rule["kind"] == "legacy_trainer":
            from packages.rules.trainers import legacy_handler
            yield from legacy_handler(rule).reduce_action(proxy, UseSupporterAction(p.id, proxy), state)
        else:
            yield from proxy.resolve_effects(state)
    finally:
        state.copy_attack_pending = pending
