"""Explicit, lossless legacy-visible projection bridge; no engine imports.

Only call with an actor-scoped core projection, never raw engine state. Unknown
fields fail closed at every mapped object. Host supplies authorized envelope
counters; this module does not invent authentication, persistence or event IDs.
"""

from copy import deepcopy
from .validation import validate, validate_command, ProtocolViolation


def _keys(value, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ProtocolViolation("INVALID_PAYLOAD")


def _card(card):
    # Some public reveal/search cards lack an id, and attack choices are not cards.
    identity = card.get("id") or (
        f"{card['set_name']}-{card['number']}"
        if "set_name" in card
        else "attack-choice"
    )
    return {"cardId": identity, "name": card["name"], "presentation": deepcopy(card)}


def _uncard(card):
    return deepcopy(card["presentation"])


_SIDE = {
    "lost_zone": "lostZone",
    "hand_count": "handCount",
    "deck_count": "deckCount",
    "prize_count": "prizeCount",
}
_OBS = {
    "turn_number": "turnNumber",
    "is_choosing_card": "isChoosingCard",
    "termination_reason": "terminationReason",
    "auto_events": "autoEvents",
}
_ROOT = {
    "stateVersion",
    "done",
    "phase",
    "winner",
    "startingPlayer",
    "setupEvents",
    "publicReveals",
    "catalogVersion",
}


def _side(side):
    _keys(
        side,
        {
            "active",
            "bench",
            "discard",
            "lost_zone",
            "hand",
            "hand_count",
            "deck_count",
            "prize_count",
            "faceUpPrizes",
            "benchCapacity",
            "id",
            "turnFlags",
        },
    )
    result = {_SIDE.get(k, k): deepcopy(v) for k, v in side.items()}
    for zone in ("active", "bench"):
        result[zone] = [{"ref": c["ref"], "card": _card(c)} for c in side[zone]]
    for zone in ("discard", "lostZone", "hand"):
        if result[zone] is not None:
            result[zone] = [_card(c) for c in result[zone]]
    result["faceUpPrizes"] = [
        {"index": c["prizeIndex"], "card": _card(c)} for c in side["faceUpPrizes"]
    ]
    return result


def _decision(decision):
    if decision is None:
        return None
    result = deepcopy(decision)
    if result["kind"] == "selection":
        for candidate in result["candidates"]:
            if "card" in candidate:
                candidate["card"] = _card(candidate["card"])
        for row in result.get("deckInspection", {}).get("cards", []):
            row["card"] = _card(row["card"])
    return result


def snapshot(
    view,
    *,
    match_id,
    rule_release_id,
    viewer_seat,
    match_seq,
    controller_epoch,
    stream_cursor,
    visual_effects=(),
    result=None,
):
    _keys(view, _ROOT | {"observation", "decision"})
    obs = view["observation"]
    _keys(
        obs,
        {
            "turn",
            "turn_number",
            "self",
            "opponent",
            "stadium",
            "viewer",
            "timestep",
            "is_choosing_card",
            "termination_reason",
            "auto_events",
        },
    )
    if obs["viewer"].upper() != viewer_seat:
        raise ProtocolViolation("FORBIDDEN")
    observation = {_OBS.get(k, k): deepcopy(v) for k, v in obs.items()}
    observation["turn"] = obs["turn"].upper() if obs["turn"] else None
    for side in ("self", "opponent"):
        observation[side] = _side(obs[side])
    observation["stadium"] = [
        {"ref": c["ref"], "card": _card(c)} for c in obs["stadium"]
    ]
    payload = {k: deepcopy(v) for k, v in view.items() if k in _ROOT}
    payload.update(
        protocolVersion="2.0",
        matchId=match_id,
        ruleReleaseId=rule_release_id,
        viewerSeat=viewer_seat,
        matchSeq=match_seq,
        controllerEpoch=controller_epoch,
        streamCursor=stream_cursor,
        status="finished" if view["done"] else "playing",
        observation=observation,
        decision=_decision(view["decision"]),
        events=[],
        visualEffects=list(visual_effects),
        result=result,
    )
    return validate("MatchSnapshot", payload)


def legacy_view(payload):
    """Reverse this bridge for migration equivalence and future GUI adaptation."""
    payload = validate("MatchSnapshot", payload)
    result = {k: deepcopy(v) for k, v in payload.items() if k in _ROOT}
    obs = payload["observation"]
    reverse = {v: k for k, v in _OBS.items()}
    out = {reverse.get(k, k): deepcopy(v) for k, v in obs.items()}
    out["turn"] = obs["turn"].lower() if obs["turn"] else None
    reverse = {v: k for k, v in _SIDE.items()}
    for side in ("self", "opponent"):
        value = {reverse.get(k, k): deepcopy(v) for k, v in obs[side].items()}
        for zone in ("active", "bench"):
            value[zone] = [_uncard(c["card"]) for c in value[zone]]
        for zone in ("discard", "lost_zone", "hand"):
            if value[zone] is not None:
                value[zone] = [_uncard(c) for c in value[zone]]
        value["faceUpPrizes"] = [_uncard(c["card"]) for c in value["faceUpPrizes"]]
        out[side] = value
    out["stadium"] = [_uncard(c["card"]) for c in obs["stadium"]]
    result["observation"] = out
    decision = deepcopy(payload["decision"])
    if decision:
        if decision["kind"] == "selection":
            for candidate in decision["candidates"]:
                if "card" in candidate:
                    candidate["card"] = _uncard(candidate["card"])
            for row in decision.get("deckInspection", {}).get("cards", []):
                row["card"] = _uncard(row["card"])
    result["decision"] = decision
    return result


def replay_page(snapshots, *, next_cursor=None):
    """Read-only frames from one member's already authorized visible snapshots."""
    values = [validate("MatchSnapshot", s) for s in snapshots]
    if not 1 <= len(values) <= 32:
        raise ProtocolViolation("INVALID_PAYLOAD")
    first = values[0]
    identity = ("matchId", "ruleReleaseId", "viewerSeat")
    if any(any(v[k] != first[k] for k in identity) for v in values):
        raise ProtocolViolation("FORBIDDEN")
    excluded = {
        "protocolVersion",
        "matchId",
        "ruleReleaseId",
        "viewerSeat",
        "controllerEpoch",
        "streamCursor",
        "decision",
    }
    frames = [
        {
            **{k: deepcopy(v) for k, v in s.items() if k not in excluded},
            "frameSeq": s["matchSeq"],
        }
        for s in values
    ]
    return validate(
        "ReplayPage",
        {
            "protocolVersion": "2.0",
            "replaySchemaVersion": "visible-replay-v2",
            **{k: first[k] for k in identity},
            "frames": frames,
            "nextCursor": next_cursor,
        },
    )


def core_command(command, *, trusted_view, authenticated_seat):
    checked = validate_command(
        command, trusted_view=trusted_view, authenticated_seat=authenticated_seat
    )
    return {
        k: checked[k]
        for k in ("commandId", "expectedStateVersion", "decisionId", "choice")
    }
