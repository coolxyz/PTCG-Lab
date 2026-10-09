"""Bounded A2 worker interface: observation history in, legal root proposal out."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

VERSION = "a2-information-search-v1-experimental"
_CAPACITY = threading.BoundedSemaphore(1)


@dataclass(frozen=True)
class SearchBudget:
    seconds: float = 1.0
    memory_mb: int = 512
    particles: int = 2
    trials: int = 24
    candidates: int = 8
    depth: int = 6
    simulations: int = 32

    def validate(self):
        if not 0 < self.seconds <= 10 or not 128 <= self.memory_mb <= 2048:
            raise ValueError("INVALID_SEARCH_BUDGET")
        if (
            any(
                type(v) is not int
                for v in (
                    self.memory_mb,
                    self.particles,
                    self.trials,
                    self.candidates,
                    self.depth,
                    self.simulations,
                )
            )
            or not 1 <= self.particles <= self.trials <= 256
            or not 2 <= self.candidates <= 64
            or not 0 <= self.depth <= 32
            or not 2 <= self.simulations <= 2048
        ):
            raise ValueError("INVALID_SEARCH_BUDGET")


def legal(dto, candidate):
    d = dto["decision"]
    if (
        not isinstance(candidate, dict)
        or candidate.get("expectedStateVersion") != dto["stateVersion"]
        or candidate.get("decisionId") != d["id"]
    ):
        return False
    choice = candidate.get("choice", {})
    if not isinstance(choice, dict):
        return False
    if d["kind"] == "options":
        return (
            set(choice) == {"optionId"}
            and isinstance(choice["optionId"], str)
            and choice["optionId"] in {o["id"] for o in d["options"]}
        )
    refs = choice.get("selectedRefs")
    return (
        set(choice) == {"selectedRefs"}
        and isinstance(refs, list)
        and all(isinstance(r, str) for r in refs)
        and len(refs) == len(set(refs))
        and d["min"] <= len(refs) <= d["max"]
        and set(refs) <= {c["ref"] for c in d["candidates"]}
    )


def decide(info, budget=SearchBudget(), *, cancel=None, sampling_seed=0):
    # At most one expensive worker per API process. Busy requests immediately
    # receive the legal baseline, so requests cannot form an unbounded queue.
    acquired = _CAPACITY.acquire(blocking=False)
    try:
        return _decide(
            info, budget, cancel=cancel, sampling_seed=sampling_seed, busy=not acquired
        )
    finally:
        if acquired:
            _CAPACITY.release()


def _decide(info, budget, *, cancel, sampling_seed, busy):
    from packages.battle.decision import decide as baseline

    began = time.monotonic()
    budget.validate()
    info.validate()
    root = info.observations[-1]
    if root.get("decision") is None:
        raise ValueError("A2_NOT_ACTOR")
    fallback, _ = baseline(root)
    result = {
        "command": fallback,
        "status": "baseline",
        "simulations": 0,
        "particles": 0,
    }

    def fallback_result(reason):
        return {
            **result,
            "status": reason,
            "stopReason": reason,
            "seconds": time.monotonic() - began,
            "version": VERSION,
        }

    if cancel is not None and cancel.is_set():
        return fallback_result("cancelled")
    if busy:
        return fallback_result("busy")
    request = json.dumps(
        {
            "information": asdict(info),
            "budget": asdict(budget),
            "samplingSeed": sampling_seed,
        },
        ensure_ascii=False,
    )
    if len(request.encode("utf-8")) > 16000000:
        return fallback_result("input_budget")
    try:
        process = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                "-m",
                "packages.simulation.a2",
                "--worker",
                str(budget.memory_mb),
            ],
            cwd=Path(__file__).resolve().parents[2],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    except OSError:
        return fallback_result("worker_unavailable")
    stopped = None
    try:
        while True:
            remaining = budget.seconds - (time.monotonic() - began)
            if (cancel is not None and cancel.is_set()) or remaining <= 0:
                stopped = (
                    "cancelled" if cancel is not None and cancel.is_set() else "timeout"
                )
                process.kill()
                out, _ = process.communicate()
                break
            try:
                out, _ = process.communicate(request, timeout=min(0.05, remaining))
                break
            except subprocess.TimeoutExpired:
                request = None
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
    for line in out.splitlines():
        try:
            proposal = json.loads(line)
            if legal(root, proposal.get("command")):
                # Worker diagnostics are explicitly bounded and whitelisted.
                result = {
                    k: proposal[k]
                    for k in ("command", "status", "simulations", "particles")
                }
        except (ValueError, TypeError, KeyError):
            continue
    return {
        **result,
        "stopReason": stopped or ("worker_error" if process.returncode else "complete"),
        "seconds": time.monotonic() - began,
        "version": VERSION,
    }


def worker(memory_mb):
    from packages.simulation.simulation import memory_limit

    job = memory_limit(memory_mb)
    from packages.simulation.information import InformationSet
    from packages.simulation.search import search

    request = json.loads(sys.stdin.read(16000001))
    budget = SearchBudget(**request["budget"])
    budget.validate()
    if budget.memory_mb != memory_mb:
        raise ValueError("SEARCH_MEMORY_BUDGET_MISMATCH")

    def emit(result):
        print(json.dumps(result), flush=True)

    search(
        InformationSet(**request["information"]),
        budget,
        emit,
        sampling_seed=request["samplingSeed"],
    )
    return job


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Private A2 process entry point")
    worker(int(sys.argv[2]))
