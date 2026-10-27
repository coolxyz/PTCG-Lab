"""Explicit operator-pinned previous core for ongoing games during a rule update.

The previous core runs only in credential-free subprocesses, verifies its own
execution identity, and cannot initialize new games. Rule work is serialized
across releases; previous-release work evicts the default idle rule process.
"""

from dataclasses import replace
import os
from pathlib import Path
from threading import Lock

from .config import Settings
from .errors import ServiceError
from .worker import RuleWorker


class ReleaseWorkers:
    def __init__(self, settings, previous_release, previous_pythonpath, *, metrics=None):
        if not previous_release or previous_release == settings.approved_rule_release_id:
            raise ValueError("DISTINCT_PREVIOUS_RELEASE_REQUIRED")
        self.settings = settings
        self.default = RuleWorker(settings, metrics=metrics)
        self.previous = RuleWorker(
            replace(settings, approved_rule_release_id=previous_release,
                    worker_persistent=False, worker_count=1),
            metrics=metrics, pythonpath=previous_pythonpath,
        )
        self.lock = Lock()

    @property
    def available(self):
        return self.default.available and self.previous.available

    def supports_release(self, release):
        return release in (self.settings.approved_rule_release_id,
                           self.previous.settings.approved_rule_release_id)

    def _worker(self, recovery):
        release = recovery.get("implementation")
        if not self.supports_release(release):
            raise ServiceError("RULE_RELEASE_UNAVAILABLE", 503)
        return self.default if release == self.settings.approved_rule_release_id else self.previous

    def _run(self, worker, method, *args):
        if not self.lock.acquire(blocking=False):
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        try:
            if worker is self.previous:
                # Avoid retaining two full engine heaps under the same RSS budget.
                for slot in self.default.slots:
                    with slot.lock:
                        slot.close()
            return getattr(worker, method)(*args)
        finally:
            self.lock.release()

    def probe(self):
        default = self._run(self.default, "probe")
        previous = self._run(self.previous, "probe")
        return default and previous

    def work_class(self, group):
        return self.default.work_class(group)

    def validate(self, entries):
        return self._run(self.default, "validate", entries)

    def initialize(self, job):
        return self._run(self.default, "initialize", job)

    def project(self, recovery):
        return self._run(self._worker(recovery), "project", recovery)

    def project_for_match(self, recovery, match_id):
        return self._run(self._worker(recovery), "project_for_match", recovery, match_id)

    def command(self, recovery, seat, command):
        return self._run(self._worker(recovery), "command", recovery, seat, command)

    def command_for_match(self, recovery, seat, command, match_id):
        return self._run(self._worker(recovery), "command_for_match", recovery, seat, command, match_id)

    def finish_candidate(self, match_id, candidate, committed):
        try:
            return self._run(self._worker(candidate["recovery"]), "finish_candidate", match_id, candidate, committed)
        except ServiceError:
            return False  # A cache acknowledgement never invalidates a DB commit.

    def close(self):
        self.default.close()
        self.previous.close()


def create_app():
    from .api import create_app as api

    settings = Settings.from_env()
    directory = Path(os.environ["PTCG_ONLINE_V2_PREVIOUS_CORE_DIRECTORY"])
    if not directory.is_absolute() or not (directory / "ptcg_core/__init__.py").is_file():
        raise ValueError("PREVIOUS_CORE_DIRECTORY_REQUIRED")
    worker = ReleaseWorkers(settings, os.environ["PTCG_ONLINE_V2_PREVIOUS_RULE_RELEASE_ID"], directory)
    return api(settings, worker=worker)
