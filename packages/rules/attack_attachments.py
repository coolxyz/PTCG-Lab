"""Consumable resistance Tools and Energy returned after its holder's attack."""


def berries(source, target, state):
    from packages.rules.modifiers import owner_of
    from packages.rules.tool_effects import enabled
    from packages.rules.pokemon_types import has_type
    if not source or not enabled(state) or owner_of(source, state) is owner_of(target, state):
        return []
    return [c for c in target.attachment if (r := (getattr(c, "spec", None) or {}).get("mechanic", {})).get("berryType") and has_type(source, r["berryType"], state)]


class CalculatedDamage(int):
    """Carry impact metadata without mutating the board during damage previews."""


def with_berries(amount, source, target, raw, state):
    attached = berries(source, target, state) if raw > 0 else []
    if not attached:
        return int(amount)
    result = CalculatedDamage(amount)
    result.berries = attached
    return result


def consume(source, target, amount, state):
    from packages.rules.modifiers import owner_of
    from packages.rules.zone_effects import discard_attached
    # Ignoring defensive effects still consumes the berry; prevention to zero
    # records the impending hit before the prevention is applied.
    owner = owner_of(target, state)
    consumed = getattr(amount, "berries", berries(source, target, state) if amount > 0 else [])
    for tool in consumed:
        if owner and tool in target.attachment:
            discard_attached(target, [tool], owner)


def begin(action, state):
    if hasattr(state, "attack_boomerangs"):
        return
    state.attack_boomerangs = [(action.source, e) for e in action.source.attachment
                              if (getattr(e, "spec", None) or {}).get("mechanic", {}).get("boomerang")]


def restore(state):
    from packages.rules.modifiers import owner_of
    from ptcg.core.enums import CardPosition
    from ptcg.utils.utils import move_cards
    for holder, energy in getattr(state, "attack_boomerangs", []):
        owner = owner_of(holder, state)
        if owner and energy in owner.discard:
            zone = CardPosition.ACTIVE_ATTACHMENT if holder in owner.active else CardPosition.BENCH_ATTACHMENT
            move_cards(energy, (owner.id, CardPosition.DISCARD), (owner.id, zone, holder.index), state)
    if hasattr(state, "attack_boomerangs"):
        del state.attack_boomerangs
