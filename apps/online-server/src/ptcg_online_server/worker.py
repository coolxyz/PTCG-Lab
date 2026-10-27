"""Bounded rule workers with advisory cache hints and post-commit confirmation."""

from collections import OrderedDict
import time
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock, BoundedSemaphore
from hashlib import sha256
from ptcg_engine_provider.values import canonical
from .errors import ServiceError
from .metrics import Metrics
from .lanes import current_lane, lane_for
from .worker_transport import WorkerSlot


class Candidate(dict):
    # Private acknowledgement metadata is not a dict key, JSON field or record.
    ticket = None


class RuleWorker:
    def __init__(self, settings, *, metrics=None):
        self.settings = settings
        self.metrics = metrics if metrics is not None else Metrics()
        self.available = False
        self.lock = Lock()
        self.closed = False
        self.hint_lock = Lock()
        self.hints = [OrderedDict() for _ in range(settings.worker_count)]
        self.cold = BoundedSemaphore(settings.worker_cold_limit)
        self.slots = [
            WorkerSlot(
                [
                    sys.executable,
                    "-m",
                    "ptcg_online_server.rule_worker",
                    "--persistent",
                ],
                dict(
                    cwd=Path(__file__).resolve().parents[1],
                    env={
                        **{
                            k: os.environ[k]
                            for k in ("PATH", "SYSTEMROOT", "LANG")
                            if k in os.environ
                        },
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                    },
                ),
            )
            for _ in range(settings.worker_count)
        ]

    def close(self):
        self.closed = True
        for slot in self.slots:
            with slot.lock:
                slot.close()

    def __del__(self):
        try:
            if hasattr(self, "slots"):
                self.close()
        except Exception:
            pass

    def work_class(self, group):
        if not group.startswith("match:"):
            return "hot"  # Ordinary foreground work shares the hot class.
        lane = lane_for(group, self.settings.worker_count)
        with self.hint_lock:
            hint = self.hints[lane].get(group[6:])
            hot = (
                self.settings.worker_persistent
                and hint
                and hint[0] == self.slots[lane].generation
                and time.monotonic() - hint[1] < self.settings.worker_cache_idle
            )
        return "hot" if hot else "cold"

    def cache_hint(self, lane, match_id, *, present):
        if not match_id:
            return
        with self.hint_lock:
            hints = self.hints[lane]
            hints.pop(match_id, None)
            if (
                present
                and self.slots[lane].generation
                and self.settings.worker_cache_entries
            ):
                hints[match_id] = (self.slots[lane].generation, time.monotonic())
                while len(hints) > self.settings.worker_cache_entries:
                    hints.popitem(last=False)

    def request(self, operation, payload):
        return {
            **payload,
            "operation": operation,
            "parent": os.getpid(),
            "directory": self.settings.rule_directory,
            "pin": self.settings.rule_manifest_sha256,
            "release": self.settings.approved_rule_release_id,
            "limits": {
                "entries": self.settings.worker_cache_entries,
                "bytes": self.settings.worker_cache_record_bytes,
                "idle": self.settings.worker_cache_idle,
                "rss": self.settings.worker_rss_mb * 1024 * 1024,
                "restore": self.settings.worker_restore_seconds,
                "compute": self.settings.worker_compute_seconds,
            },
        }

    def observe(self, value):
        timings = value.pop("_timings", {})
        if not isinstance(timings, dict):
            raise ValueError("INVALID_WORKER_TIMINGS")
        for name in (
            "rule.load",
            "rule.restore",
            "rule.compute",
            "cache.hit",
            "cache.miss",
            "cache.evicted",
        ):
            seconds = timings.get(name)
            if type(seconds) in (int, float) and 0 <= seconds <= 30:
                self.metrics.observe(name, seconds)
        value.pop("_rssBytes", None)
        return value

    def persistent_call(self, operation, payload, lane):
        slot = self.slots[lane]
        if not slot.lock.acquire(blocking=False):
            self.metrics.observe("worker.rejected")
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        cold = False
        try:
            if self.closed:
                raise ValueError("WORKER_CLOSED")
            request = self.request(operation, payload)
            if operation in {"command", "project"}:
                inspected = self.observe(
                    slot.exchange({**request, "operation": "inspect"}, 20)
                )
                if inspected.pop("_recycle", False):
                    slot.close()
                    inspected["cached"] = False
                if type(inspected.get("cached")) is not bool:
                    raise ValueError("INVALID_CACHE_HINT")
                request["require_cached"] = inspected["cached"]
                if not inspected["cached"]:
                    # A stale advisory hit must not keep bypassing cold scheduling.
                    self.cache_hint(lane, payload.get("match_id"), present=False)
                    cold = self.cold.acquire(blocking=False)
                    if not cold:
                        self.metrics.observe("worker.cold_rejected")
                        raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            if operation in {"command", "project"}:
                self.cache_hint(lane, payload.get("match_id"), present=False)
            value = self.observe(slot.exchange(request, 20))
            ticket = value.pop("_cacheTicket", None)
            result = Candidate(value)
            if ticket:
                result.ticket = (
                    lane,
                    slot.generation,
                    ticket,
                    sha256(canonical(result["recovery"])).hexdigest(),
                )
            if result.pop("_recycle", False):
                self.metrics.observe("worker.recycled")
                slot.close()
            if operation == "project":
                self.cache_hint(
                    lane,
                    payload.get("match_id"),
                    present=not result["views"]["PLAYER1"]["done"],
                )
            return result
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
            subprocess.TimeoutExpired,
        ):
            slot.close()
            self.metrics.observe("worker.failed")
            raise ServiceError(
                "RULE_RELEASE_UNAVAILABLE", 503, retryable=True
            ) from None
        finally:
            if cold:
                self.cold.release()
            slot.lock.release()

    def finish_candidate(self, match_id, candidate, committed):
        ticket = getattr(candidate, "ticket", None)
        if not ticket:
            return False
        lane, generation, ident, digest = ticket
        slot = self.slots[lane]
        if not slot.lock.acquire(blocking=False):
            return False  # No cache acknowledgement is safer than blocking another job.
        try:
            if slot.generation != generation or slot.process is None:
                return False
            request = self.request(
                "confirm" if committed else "discard",
                dict(match_id=match_id, ticket=ident, digest=digest),
            )
            result = self.observe(slot.exchange(request, 1))
            if result.pop("_recycle", False):
                slot.close()
            confirmed = result.get("confirmed", False)
            self.cache_hint(
                lane,
                match_id,
                present=confirmed and not candidate["views"]["PLAYER1"]["done"],
            )
            return confirmed
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            TimeoutError,
            subprocess.TimeoutExpired,
        ):
            slot.close()
            return (
                False  # A cache failure must never turn a durable commit into failure.
            )
        finally:
            slot.lock.release()

    def command_for_match(self, recovery, seat, command, match_id):
        if not self.settings.worker_persistent:
            return self.command(recovery, seat, command)
        return self.call(
            "command", recovery=recovery, seat=seat, command=command, match_id=match_id
        )

    def project_for_match(self, recovery, match_id):
        if not self.settings.worker_persistent:
            return self.project(recovery)
        return self.call("project", recovery=recovery, match_id=match_id)

    def probe(self):
        if not all(
            (
                self.settings.rule_directory,
                self.settings.rule_manifest_sha256,
                self.settings.approved_rule_release_id,
            )
        ):
            return False
        try:
            results = [
                self.call("probe", _lane=lane)
                for lane in range(
                    self.settings.worker_count if self.settings.worker_persistent else 1
                )
            ]
            result = results[-1]
            self.available = (
                result.get("release") == self.settings.approved_rule_release_id
            )
        except ServiceError:
            self.available = False
        return self.available

    def call(self, operation, **payload):
        if operation not in {"probe", "validate", "initialize", "project", "command"}:
            raise ValueError("INVALID_WORKER_OPERATION")
        with self.metrics.time("worker." + operation):
            lane = payload.pop("_lane", current_lane.get())
            if self.settings.worker_persistent:
                if payload.get("match_id"):
                    lane = lane_for(
                        "match:" + payload["match_id"], self.settings.worker_count
                    )
                return self.persistent_call(operation, payload, lane)
            return self._call(operation, **payload)

    def _call(self, operation, **payload):
        if not self.lock.acquire(blocking=False):
            self.metrics.observe("worker.rejected")
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        try:
            request = {
                **payload,
                "operation": operation,
                "parent": os.getpid(),
                "directory": self.settings.rule_directory,
                "pin": self.settings.rule_manifest_sha256,
                "release": self.settings.approved_rule_release_id,
            }
            result = subprocess.run(
                [sys.executable, "-m", "ptcg_online_server.rule_worker"],
                input=canonical(request),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=20,
                cwd=Path(__file__).resolve().parents[1],
                env={
                    **{
                        k: os.environ[k]
                        for k in ("PATH", "SYSTEMROOT", "LANG")
                        if k in os.environ
                    },
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
                },
            )
            if result.returncode or len(result.stdout) > 8 * 1024 * 1024:
                raise ValueError("WORKER_FAILURE")
            value = json.loads(result.stdout)
            value.pop("_cacheTicket", None)
            return self.observe(value)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.available = False
            raise ServiceError(
                "RULE_RELEASE_UNAVAILABLE", 503, retryable=True
            ) from None
        finally:
            self.lock.release()

    def validate(self, entries):
        if not self.available:
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503, retryable=True)
        result = self.call("validate", entries=entries)
        if result.get("rejected"):
            raise ServiceError("INVALID_PAYLOAD", 422)
        return result

    def initialize(self, job):
        if not self.available:
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503, retryable=True)
        result = self.call(
            "initialize", **{k: job[k] for k in ("seed", "as_of", "deck1", "deck2")}
        )
        record = result.get("recovery", {})
        if (
            record.get("schema") != "core-recovery-v2"
            or record.get("implementation") != job["rule_release_id"]
        ):
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503)
        return record

    def project(self, recovery):
        if not self.available:
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503, retryable=True)
        return self.call("project", recovery=recovery)

    def command(self, recovery, seat, command):
        if not self.available:
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503, retryable=True)
        return self.call("command", recovery=recovery, seat=seat, command=command)
