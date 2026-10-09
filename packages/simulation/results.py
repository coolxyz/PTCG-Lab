"""Public outcome explanation; never rewrites the engine or persisted old frames."""


def explain(view):
    if not view.get("done"):
        return None
    status = view.get("status")
    if status in ("resigned", "truncated", "archived"):
        return {"reason": status, "conditions": [status]}
    obs = view["observation"]
    if obs.get("termination_reason"):
        reason = obs["termination_reason"]
        return {"reason": reason, "conditions": [reason]}
    # Use the confirmed winner and its public perspective; an empty active slot
    # alone is not a defeat, and temporary zero HP is not a terminal result.
    winner = view.get("winner")
    own_id = obs["self"].get("id")
    if not winner or not own_id:
        return {"reason": "unknown", "conditions": []}
    own_won = winner.upper() == own_id.upper()
    winning = obs["self"] if own_won else obs["opponent"]
    losing = obs["opponent"] if own_won else obs["self"]
    conditions = []
    if winning["prize_count"] == 0:
        conditions.append("prizes_taken")
    if not losing["active"] and not losing["bench"]:
        conditions.append("no_pokemon")
    return {
        "reason": conditions[0] if conditions else "unknown",
        "conditions": conditions,
    }


def decorate(view):
    view["result"] = explain(view)
    return view
