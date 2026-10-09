"""Return a physical Pokemon stack without treating it as a knockout."""

from ptcg.core.enums import CardPosition


def return_discard_stack(card, owner, destination, top=False):
    state = getattr(owner, "rules_state", None)
    if state is not None:
        from packages.rules.zone_guards import hand_return_forbidden, trainer_immune
        if trainer_immune(card, state) or destination == "hand" and hand_return_forbidden(owner, state):
            return []
    from packages.rules.core_fixes import discard_card
    def discard_children(c):
        for child in list(getattr(c,"attachment",[]))+list(getattr(c,"evolved",[])):
            discard_children(child)
            discard_card(owner, child)
        c.attachment = []
        c.evolved = []
    discard_children(card)
    result = return_stack(card, owner, destination)
    if top:
        zone = getattr(owner, destination)
        zone.remove(card)
        zone.insert(0, card)
        for i,c in enumerate(zone):
            c.index = i+1
    return result


def return_stack(card, owner, destination):
    state = getattr(owner, "rules_state", None)
    if state is not None:
        from packages.rules.zone_guards import hand_return_forbidden, trainer_immune
        if trainer_immune(card, state) or destination == "hand" and hand_return_forbidden(owner, state):
            return []
    (owner.active if card in owner.active else owner.bench).remove(card)
    stack = []

    # Attachments have no evolution/attachment attributes themselves.
    def flatten(c):
        for child in list(getattr(c, "attachment", [])) + list(
            getattr(c, "evolved", [])
        ):
            flatten(child)
        fresh = type(c)()
        vars(c).clear()
        vars(c).update(vars(fresh))
        stack.append(c)

    flatten(card)
    zone = getattr(owner, destination)
    zone.extend(stack)
    for i, c in enumerate(zone):
        c.index, c.cardPosition = i + 1, CardPosition[destination.upper()]
    for i, c in enumerate(owner.bench):
        c.index = i + 1
    return stack
