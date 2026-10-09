"""Disposable private simulation workers with wall-time and memory ceilings.

These workers are for offline validation. They receive private checkpoints and
must never be used as the observation input to an online search agent.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time


@dataclass(frozen=True)
class Budget:
    seconds: float = 5
    memory_mb: int = 512
    steps: int = 32

    def validate(self):
        if (
            not 0 < self.seconds <= 60
            or not 128 <= self.memory_mb <= 4096
            or not 1 <= self.steps <= 2500
        ):
            raise ValueError("INVALID_SIMULATION_BUDGET")


def simulate(continuation, budget=Budget(), cancel=None):
    budget.validate()
    if cancel is not None and cancel.is_set():
        return {"status": "cancelled", "seconds": 0, "steps": 0}
    request = json.dumps({"token": continuation.to_json(), "steps": budget.steps})
    root = Path(__file__).resolve().parents[2]
    began = time.monotonic()
    process = subprocess.Popen(
        [
            sys.executable,
            "-X",
            "utf8",
            "-m",
            "packages.simulation.simulation",
            "--worker",
            str(budget.memory_mb),
        ],
        cwd=root,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    while True:
        remaining = budget.seconds - (time.monotonic() - began)
        cancelled = cancel is not None and cancel.is_set()
        if cancelled or remaining <= 0:
            process.kill()
            process.communicate()
            return {
                "status": "cancelled" if cancelled else "timeout",
                "seconds": time.monotonic() - began,
                "steps": 0,
            }
        try:
            out, _ = process.communicate(
                request,
                timeout=min(0.05, remaining) if cancel is not None else remaining,
            )
            break
        except subprocess.TimeoutExpired:
            request = None  # communicate keeps buffered input after the first call.
    if process.returncode:
        return {
            "status": "worker_error",
            "seconds": time.monotonic() - began,
            "steps": 0,
            "exitCode": process.returncode,
        }
    result = json.loads(out)
    result["seconds"] = time.monotonic() - began
    return result


def memory_limit(megabytes):
    """An OS ceiling, not a check performed after the calculation finishes."""
    size = megabytes * 1024 * 1024
    if os.name != "nt":
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (size, size))
        return None
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [
            ("perProcess", ctypes.c_int64),
            ("perJob", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("minWorking", ctypes.c_size_t),
            ("maxWorking", ctypes.c_size_t),
            ("active", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class IO(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint64)
            for name in (
                "readOps",
                "writeOps",
                "otherOps",
                "readBytes",
                "writeBytes",
                "otherBytes",
            )
        ]

    class Extended(ctypes.Structure):
        _fields_ = [
            ("basic", Basic),
            ("io", IO),
            ("processMemory", ctypes.c_size_t),
            ("jobMemory", ctypes.c_size_t),
            ("peakProcess", ctypes.c_size_t),
            ("peakJob", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    limits.processMemory = size
    if (
        not handle
        or not kernel.SetInformationJobObject(
            handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
        )
        or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess())
    ):
        raise OSError(
            ctypes.get_last_error(), "Cannot enforce simulation memory budget"
        )
    return handle  # Keep open until this disposable worker exits.


def worker(megabytes):
    job = memory_limit(megabytes)
    from packages.simulation.fork import RuleFork, Continuation
    from packages.battle.runtime import view
    from packages.battle.decision import decide
    from packages.rules.invariants import check_conservation

    request = json.loads(sys.stdin.read(17000000))
    branch = RuleFork.restore(Continuation.from_json(request["token"]))
    steps = 0
    for _ in range(request["steps"]):
        if branch.game.done:
            break
        command, _ = decide(view(branch.game, branch.game.actor))
        branch.submit(branch.game.actor, command)
        check_conservation(branch.game.env.gamestate)
        steps += 1
    print(
        json.dumps(
            {
                "status": "ok",
                "steps": steps,
                "done": branch.game.done,
                "digest": branch.game.private_digest(),
                "memoryLimitMB": megabytes,
                "memoryEnforced": job is not None or os.name != "nt",
            }
        )
    )


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--worker":
        raise SystemExit("Private simulation worker entry point")
    worker(int(sys.argv[2]))
