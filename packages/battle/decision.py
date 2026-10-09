"""Validate A1 proposals and provide a deterministic legal fallback."""

import time
from packages.battle import agent


def decide(view):
    d = view["decision"]
    started = time.perf_counter()
    fallback = False
    try:
        command, _ = agent.predict(view)
        if (
            command.get("expectedStateVersion") != view["stateVersion"]
            or command.get("decisionId") != d["id"]
        ):
            raise ValueError("stale AI proposal")
        choice = command["choice"]
        if d["kind"] == "selection":
            refs = choice["selectedRefs"]
            valid = {c["ref"] for c in d["candidates"]}
            if (
                not isinstance(refs, list)
                or not d["min"] <= len(refs) <= d["max"]
                or len(set(refs)) != len(refs)
                or any(r not in valid for r in refs)
            ):
                raise ValueError("invalid selection")
        elif choice["optionId"] not in {o["id"] for o in d["options"]}:
            raise ValueError("invalid option")
        if time.perf_counter() - started > 1:
            raise TimeoutError("soft decision budget exceeded")
    except Exception:
        fallback = True
        choice = (
            {"selectedRefs": [c["ref"] for c in d["candidates"][: d["min"]]]}
            if d["kind"] == "selection"
            else {"optionId": d["options"][0]["id"]}
        )
    command = {
        "commandId": f"ai-{view['stateVersion']}",
        "expectedStateVersion": view["stateVersion"],
        "decisionId": d["id"],
        "choice": choice,
    }
    return command, fallback
