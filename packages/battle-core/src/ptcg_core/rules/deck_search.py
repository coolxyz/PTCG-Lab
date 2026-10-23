"""Explicit full-deck visibility grants; never infer them from candidate zones."""
from ptcg.core.action import choose_card_actions as _choose


def choose_card_actions(*args, deck_owner=None, **kwargs):
    actions, prompt = _choose(*args, **kwargs)
    if deck_owner is not None:
        if prompt.hidden:
            raise ValueError("A hidden choice cannot reveal a deck")
        prompt.deck_inspection_owner = deck_owner.id
    return actions, prompt


def inspection(prompt, players, viewer, serialize):
    """Only called for the acting player's non-hidden selection."""
    owner_id = getattr(prompt, 'deck_inspection_owner', None)
    if owner_id is None:
        return None
    import json
    from collections import Counter
    owner = next(p for p in players if p.id == owner_id)
    # No physical indices, candidate refs, or deck order; duplicates are counts.
    allowed = {'id', 'name', 'kind', 'superType', 'trainerType', 'cardType',
               'stage', 'hp', 'attacks', 'abilities', 'effect', 'provides',
               'evolveFrom', 'retreatCost', 'weakness', 'resistance'}
    counts = Counter(json.dumps({k: v for k, v in serialize(c).items() if k in allowed},
                                sort_keys=True) for c in owner.left)
    return {'owner': 'self' if owner.id == viewer else 'opponent',
            'total': len(owner.left), 'ordered': False,
            'cards': [{'card': json.loads(key), 'count': counts[key]} for key in sorted(counts)]}
