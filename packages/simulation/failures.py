"""Private, versioned reproduction bundles and bounded failing-prefix reduction."""

import json
from pathlib import Path

from packages.battle.runtime import Adapter, ENGINE_VERSION, PlayerId
from packages.rules.invariants import check_conservation
from packages.simulation.registry import VERSION


def replay(config, commands, invariant=check_conservation):
    game = Adapter(**config)
    for row in commands:
        game.submit(PlayerId[row["actor"]], row["command"])
        invariant(game.env.gamestate)
    return game


def capture(path, config, commands, error, invariant=check_conservation, max_calls=32):
    """Minimize the replay prefix, preserving the original exception signature.

    This deliberately does not claim a globally minimal deck or arbitrary trace:
    deleting commands changes legality, whereas truncating a deterministic replay
    preserves all earlier decisions and the physical sixty-card compositions.
    """
    if max_calls < 1:
        raise ValueError("INVALID_MINIMIZATION_BUDGET")
    signature = [type(error).__name__, str(error)]
    calls = 0

    def fails(rows):
        nonlocal calls
        calls += 1
        try:
            replay(config, rows, invariant)
        except Exception as exc:
            return [type(exc).__name__, str(exc)] == signature
        return False

    reproducible = fails(commands)
    low, high = 0, len(commands)
    if reproducible:
        while low < high and calls < max_calls:
            middle = (low + high) // 2
            if fails(commands[:middle]):
                high = middle
            else:
                low = middle + 1
    document = {
        "schema": "p33-private-failure-v1",
        "engineVersion": ENGINE_VERSION,
        "releaseVersion": VERSION,
        "config": config,
        "commands": commands,
        "signature": signature,
        "reproducible": reproducible,
        "minimization": {
            "method": "shortest-failing-prefix",
            "calls": calls,
            "complete": reproducible and low == high,
            "commands": commands[:high],
        },
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(destination)
    return {k: document[k] for k in ("schema", "signature", "reproducible")} | {
        "originalSteps": len(commands),
        "minimalSteps": high,
        "complete": document["minimization"]["complete"],
    }


def reproduce(path, invariant=check_conservation):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if (
        data["schema"] != "p33-private-failure-v1"
        or data["engineVersion"] != ENGINE_VERSION
        or data["releaseVersion"] != VERSION
    ):
        raise ValueError("FAILURE_VERSION_MISMATCH")
    return replay(data["config"], data["minimization"]["commands"], invariant)
