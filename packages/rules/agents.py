"""Replacement Bench-facing agent contract; never accepts State or raw actions.

Existing Bench agents using StateObserver/ActionExecutor require migration to
this boundary; importing an old agent unchanged is intentionally unsupported.
"""
import json
import random
from packages.engine_adapter.base import policy, wire


class DTOObserver:
    def observe(self, view):
        if not isinstance(view, dict) or 'observation' not in view:
            raise TypeError('PLAYER_DTO_REQUIRED')
        # Detached JSON, never an engine reference; viewer, not turn owner,
        # determines perspective during opponent-controlled reactions.
        result = wire(view)
        if result['observation']['opponent']['hand'] is not None:
            raise ValueError('HIDDEN_HAND_EXPOSED')
        return result

    def build_user_message(self, view):
        return json.dumps(self.observe(view), ensure_ascii=False, sort_keys=True)


class DTOAgent:
    def __init__(self, seed=0, mode='development'):
        self.seed, self.mode = seed, mode
        self.rng = random.Random(seed)
        self.observer = DTOObserver()

    def predict(self, view):
        return policy(self.observer.observe(view), self.rng, self.mode)

    def reset(self):
        self.rng.seed(self.seed)
