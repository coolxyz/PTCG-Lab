from packages.simulation.fork import RuleFork
from packages.simulation.simulation import Budget, simulate
from packages.battle.runtime import Adapter
import pytest


def test_worker_is_deterministic_bounded_and_does_not_mutate_parent():
    branch = RuleFork(Adapter(17))
    original = branch.game.private_digest()
    results = [
        simulate(branch.checkpoint(), Budget(seconds=15, steps=12)) for _ in range(2)
    ]
    assert all(
        r["status"] == "ok" and r["steps"] == 12 and r["memoryEnforced"]
        for r in results
    ), results
    assert results[0]["digest"] == results[1]["digest"]
    assert branch.game.private_digest() == original


def test_wall_timeout_kills_worker_and_next_run_is_usable():
    token = RuleFork(Adapter(17)).checkpoint()
    result = simulate(token, Budget(seconds=0.001, steps=2500))
    assert result["status"] == "timeout"
    assert result["seconds"] < 5
    assert simulate(token, Budget(seconds=15, steps=1))["status"] == "ok"


def test_invalid_budget_rejected():
    with pytest.raises(ValueError, match="INVALID_SIMULATION_BUDGET"):
        simulate(None, Budget(memory_mb=0))


def test_os_memory_ceiling_rejects_large_allocation():
    import subprocess
    import sys

    code = """
from packages.simulation.simulation import memory_limit
job = memory_limit(128)
try:
    data = bytearray(512 * 1024 * 1024)
except MemoryError:
    print('bounded')
else:
    raise SystemExit('memory ceiling was not enforced')
"""
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0 and result.stdout.strip() == "bounded", result.stderr


def test_explicit_cancellation_terminates_worker_and_parent_survives():
    import threading
    from packages.battle.runtime import Adapter
    from packages.simulation.fork import RuleFork
    from packages.simulation.simulation import simulate, Budget

    branch = RuleFork(Adapter(51))
    before = branch.game.private_digest()
    cancel = threading.Event()
    timer = threading.Timer(0.05, cancel.set)
    timer.start()
    try:
        result = simulate(branch.checkpoint(), Budget(seconds=15, steps=2500), cancel)
    finally:
        timer.join()
    assert result["status"] == "cancelled" and result["seconds"] < 2
    assert branch.game.private_digest() == before
