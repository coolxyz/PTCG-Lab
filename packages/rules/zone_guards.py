"""Continuous source and destination restrictions shared by card effects."""

from ptcg.core.enums import CardPosition
from packages.rules.abilities import enabled


def rules(player, state, kind):
    return [(c, r) for c in player.active + player.bench for r in (getattr(c, "spec", None) or {}).get("abilities", []) if r["kind"] == kind and enabled(c, state)]


def hand_return_forbidden(owner, state):
    other = state.player2 if owner is state.player1 else state.player1
    return bool(rules(other, state, "no_field_hand"))


def trainer_immune(card, state):
    from packages.rules.modifiers import owner_of
    owner = owner_of(card, state)
    context = getattr(state, "effect_context", {})
    if context.get('kind')=='ability' and context.get('source'):
        from packages.rules.ability_protection import blocked
        return blocked(card,context['source'],state)
    if context.get("kind") == "attack":
        from packages.rules.protection import blocked
        return blocked(card, state, "effects")
    if owner is None or context.get("owner") == owner.id or not context.get("fromHand") or context.get("kind") not in ("item", "supporter"):
        return False
    return any((holder is card or r.get("team")) and (not r.get("activeOnly") or holder in owner.active) and context["kind"] in r["cards"] for holder, r in rules(owner, state, "trainer_protection"))


def stadium_immune(card, state):
    from packages.rules.modifiers import owner_of
    owner = owner_of(card, state)
    return bool(owner and any(any(c.name == r["requiresName"] for c in owner.active + owner.bench) for _, r in rules(owner, state, "stadium_protection")))


def allows(card, source_pos, target_pos, state):
    owner = state.player1 if source_pos[0] == state.player1.id else state.player2
    src, dst = source_pos[1], target_pos[1]
    context = getattr(state, "effect_context", {})
    if src == CardPosition.DISCARD and dst in (CardPosition.HAND, CardPosition.LEFT):
        if (getattr(card, "spec", None) or {}).get("mechanic", {}).get("discardLocked"):
            return False
        other = state.player2 if owner is state.player1 else state.player1
        if dst == CardPosition.LEFT and context.get('owner', state.turn) == owner.id and context.get('kind') in ('item','supporter'):
            from packages.rules.zone_effects import matches
            if matches(card,'trainer') and rules(other,state,'no_trainer_recycle'):
                return False
        if dst == CardPosition.HAND and context.get("owner", state.turn) == owner.id and context.get("kind", "attack") in ("ability", "item", "supporter", "stadium", "tool") and rules(other, state, "no_discard_hand"):
            return False
    if src == CardPosition.LEFT and dst == CardPosition.DISCARD and context.get("owner", state.turn) != owner.id and context.get("kind") != "stadium":
        from packages.rules.tool_effects import enabled as tools_enabled
        if tools_enabled(state) and any((getattr(t, "spec", None) or {}).get("mechanic", {}).get("protectDeck") for c in owner.active for t in c.attachment):
            return False
    field_zones = (CardPosition.ACTIVE, CardPosition.BENCH, CardPosition.ACTIVE_ATTACHMENT, CardPosition.BENCH_ATTACHMENT)
    if src in field_zones:
        holder = card if src in (CardPosition.ACTIVE, CardPosition.BENCH) else next((c for c in owner.active + owner.bench if card in c.attachment), None)
        if holder and trainer_immune(holder, state):
            return False
        if dst == CardPosition.HAND and hand_return_forbidden(owner, state):
            return False
    return True
