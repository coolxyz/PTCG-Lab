"""Versioned H2 product boundary: visible DTO only, validated fallback, provenance."""

import hashlib
import json
import time
from pathlib import Path

from packages.battle.h2_v4 import VERSION as VERSION


def fingerprint():
    root = Path(__file__).resolve().parents[2]
    paths = [Path(__file__), *sorted((root / "packages/battle/h2_v4").glob("*.py"))]
    paths += [root / p for p in ("data/cardpool/plain-pokemon.json", "data/simulation/effects.json")]
    binding = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    return hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest()


def decide(dto):
    from packages.battle.h2_v4.policy import decide as policy
    from packages.battle.decision import decide as baseline
    from packages.simulation.a2 import legal

    start = time.perf_counter()
    reason = None
    try:
        command, _ = policy(dto)
        if not legal(dto, command):
            raise ValueError("INVALID_PROPOSAL")
        if time.perf_counter() - start > 1:
            raise TimeoutError("DECISION_BUDGET")
    except Exception as exc:
        # Never expose arbitrary exception text or private DTO content to clients.
        reason = "budget" if isinstance(exc, TimeoutError) else "invalid-or-error"
        command, baseline_fallback = baseline(dto)
        if baseline_fallback:
            reason += ":legal-fallback"
        if not legal(dto, command):
            raise ValueError("H2_FALLBACK_INVALID") from exc
    command = dict(command, commandId=f"h2-app-{dto['stateVersion']}")
    return command, {"fallback": reason is not None, "fallbackReason": reason,
                     "aiMilliseconds": round((time.perf_counter() - start) * 1000, 3)}
