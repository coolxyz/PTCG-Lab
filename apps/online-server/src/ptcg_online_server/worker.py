"""Bounded subprocess adapter; baseline isolation before P6 worker pooling."""

import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
from ptcg_engine_provider.values import canonical
from .errors import ServiceError


class RuleWorker:
    def __init__(self, settings):
        self.settings = settings
        self.available = False
        self.lock = Lock()

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
            result = self.call("probe")
            self.available = (
                result.get("release") == self.settings.approved_rule_release_id
            )
        except ServiceError:
            self.available = False
        return self.available

    def call(self, operation, **payload):
        if not self.lock.acquire(blocking=False):
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
            return json.loads(result.stdout)
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
