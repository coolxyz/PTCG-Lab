"""Immunity to effects of opposing Pokemon abilities (not attacks)."""
def blocked(target, source, state):
    if not any(r['kind']=='ability_protection' for r in (getattr(target,'spec',None) or {}).get('abilities',[])):
        return False
    from packages.rules.modifiers import owner_of
    from packages.rules.abilities import enabled
    owner, actor = owner_of(target,state), owner_of(source,state)
    return bool(owner and actor and owner is not actor and enabled(target,state))
