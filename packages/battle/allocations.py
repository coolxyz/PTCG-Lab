"""Recognize and replay the repeated public target choices of two effects."""

from packages.simulation.a2 import legal

PHANTOM_DIVE = "phantom-dive"
PUNK_UP = "punk-up"


def effect(view):
    decision = view.get("decision")
    if (
        view.get("phase") != "playing"
        or not decision
        or decision["kind"] != "selection"
        or decision["hidden"]
        or decision["min"] != 1
        or decision["max"] != 1
    ):
        return None
    candidates = decision["candidates"]
    if not candidates or not all(card.get("boardRef") for card in candidates):
        return None
    sides = {card.get("side") for card in candidates}
    if (
        decision.get("sourceId") == "TWM-200"
        and sides == {"opponent"}
        and decision.get("zones") == ["CardPosition.BENCH"]
    ):
        return PHANTOM_DIVE
    if decision.get("sourceId") == "P4P-5485406C2C86" and sides == {"self"}:
        return PUNK_UP
    return None


def window(view, history):
    kind = effect(view)
    if kind is None:
        return None
    source = view["decision"]["sourceId"]
    used = 0
    for entry in reversed(history):
        action = entry.get("ownAction")
        if not action:
            return None
        if (
            kind == PHANTOM_DIVE
            and action.get("actionType") == "AttackAction"
            and (action.get("attack") or {}).get("name") == "Phantom Dive"
        ):
            total = 6
            break
        if action.get("sourceId") != source:
            return None
        if kind == PUNK_UP and action.get("zones") == ["CardPosition.LEFT"]:
            total = len(action["selected"])
            if not 1 <= total <= 5 or any(
                name != "Darkness Energy" for name in action["selected"]
            ):
                return None
            break
        if not action.get("boardTargets") or len(action["boardTargets"]) != 1:
            return None
        used += 1
    else:
        return None
    remaining = total - used
    if remaining <= 1:
        return None
    spec = {
        "effect": kind,
        "remaining": remaining,
        "unit": "damage-counter" if kind == PHANTOM_DIVE else "darkness-energy",
        "targetRefs": [card["ref"] for card in view["decision"]["candidates"]],
    }
    if kind == PHANTOM_DIVE:
        spec["damagePerUnit"] = 10
    return spec


def valid_plan(refs, window):
    return (
        isinstance(refs, list)
        and len(refs) == window["remaining"]
        and all(isinstance(ref, str) and ref in window["targetRefs"] for ref in refs)
    )


def candidate_objects(game, view):
    # Use physical objects only inside the transaction. They never enter the DTO.
    return {
        item["ref"]: game.actions.candidates[index]
        for item, index in zip(
            view["decision"]["candidates"], game._candidate_order(), strict=True
        )
    }


def checked_command(view, choice):
    version = view["stateVersion"]
    command = {
        "commandId": f"llm-{version}",
        "expectedStateVersion": version,
        "decisionId": view["decision"]["id"],
        "choice": choice,
    }
    return command if legal(view, command) else None


def continuations(game, first_view, steps, project, resolve):
    """Reproject after every submission. Never cross a player or phase boundary."""
    actor = game.actor
    current = first_view
    for step in steps:
        if game.done or game.actor != actor or current["phase"] != "playing":
            return
        command = resolve(game, current, step)
        if command is None:
            return
        yield current, command
        if game.done or game.actor != actor:
            return
        current = project(game, actor)


def follow(game, first_view, allocation, project):
    """Yield fresh legal commands, stopping at any actor, effect, or target change."""
    source = game.info["prompt"].source
    initial = candidate_objects(game, first_view)

    def resolve(game, current, ref):
        if (
            effect(current) != allocation["effect"]
            or game.info["prompt"].source is not source
        ):
            return None
        target = next(
            (
                key
                for key, card in candidate_objects(game, current).items()
                if card is initial[ref]
            ),
            None,
        )
        return checked_command(current, {"selectedRefs": [target]}) if target else None

    return continuations(game, first_view, allocation["targetRefs"], project, resolve)
