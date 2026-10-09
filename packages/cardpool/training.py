"""Versioned policy records built only from actor-visible DTOs."""

import copy
from packages.simulation.a2 import legal

FORBIDDEN = {
    "seed",
    "rng",
    "rngState",
    "privateReplay",
    "deck1",
    "deck2",
    "opponentDeck",
}


def _reject_private(value):
    if isinstance(value, dict):
        if FORBIDDEN.intersection(value):
            raise ValueError("PRIVATE_TRAINING_FIELD")
        for nested in value.values():
            _reject_private(nested)
    elif isinstance(value, list):
        for nested in value:
            _reject_private(nested)


def record(view, command, *, own_deck, versions, policy_version):
    """Accept a public decision, never a Game/State/private replay object."""
    if view["observation"]["opponent"]["hand"] is not None:
        raise ValueError("HIDDEN_HAND_EXPOSED")
    _reject_private(view)
    _reject_private(own_deck)
    _reject_private(versions)
    if not view.get("decision"):
        raise ValueError("NO_TRAINING_DECISION")
    if not legal(view, command):
        raise ValueError("ILLEGAL_TRAINING_ACTION")
    features = {
        key: copy.deepcopy(view[key])
        for key in (
            "observation",
            "decision",
            "phase",
            "startingPlayer",
            "setupEvents",
            "publicReveals",
        )
        if key in view
    }
    return {
        "schema": "p4-policy-record-v1",
        "actor": view["decision"]["actor"],
        "versions": copy.deepcopy(versions),
        "policyVersion": policy_version,
        "features": features,
        "ownDeck": copy.deepcopy(own_deck),
        "chosenAction": copy.deepcopy(command["choice"]),
        "outcome": None,
    }


def finish(records, winner):
    if winner not in ("PLAYER1", "PLAYER2", None):
        raise ValueError("INVALID_WINNER")
    for item in records:
        item["outcome"] = 0 if winner is None else 1 if winner == item["actor"] else -1
    return records
