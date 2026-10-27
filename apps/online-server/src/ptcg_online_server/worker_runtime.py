"""Private, disposable cache inside one serial rule process.

Only a matching post-commit acknowledgement promotes a mutated candidate.
Memory is bounded by LRU entry/record budgets, idle expiry and an RSS recycle
watermark; record bytes are not claimed to measure Python object graph size.
"""

from collections import OrderedDict
from contextlib import contextmanager
from hashlib import sha256
import resource
import signal
import sys
import time
import uuid

from ptcg_core.factory import CoreFactory
from ptcg_core.identity import execution_identity
from ptcg_engine_provider.bundle import DirectoryEngineProvider
from ptcg_engine_provider.values import canonical


@contextmanager
def budget(seconds):
    def expired(*_):
        raise TimeoutError("RULE_BUDGET_EXCEEDED")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def record_digest(record):
    return sha256(canonical(record)).hexdigest()


class RuleRuntime:
    def __init__(self, request, metrics):
        with metrics.time("rule.load"):
            bundle = DirectoryEngineProvider(
                request["directory"], request["pin"]
            ).load()
            if execution_identity(bundle) != request["release"]:
                raise ValueError("UNAPPROVED_RULE")
            self.factory = CoreFactory(bundle)
        self.bundle = bundle
        self.limits = request.get("limits", {})
        self.cache = OrderedDict()
        self.pending = None

    def rss(self):
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)

    def prune(self, metrics):
        now = time.monotonic()
        if self.pending and now - self.pending[5] > self.limits.get("idle", 60):
            self.pending = None
        for key, row in list(self.cache.items()):
            if now - row[3] > self.limits.get("idle", 60):
                del self.cache[key]
                metrics.observe("cache.evicted")
        while self.cache and (
            len(self.cache) > self.limits.get("entries", 0)
            or sum(r[2] for r in self.cache.values()) > self.limits.get("bytes", 0)
        ):
            self.cache.popitem(last=False)
            metrics.observe("cache.evicted")
        return self.rss() > self.limits.get("rss", 512 * 1024 * 1024)

    def cached(self, request, metrics):
        self.prune(metrics)
        row = self.cache.get(request.get("match_id"))
        return bool(row and row[0] == record_digest(request["recovery"]))

    def restore(self, request, metrics):
        self.pending = None  # A lost acknowledgement never commits a candidate.
        hit = self.cached(request, metrics)
        if request.get("require_cached") and not hit:
            raise ValueError("CACHE_EXPIRED")
        row = self.cache.pop(request.get("match_id"), None)
        if hit:
            metrics.observe("cache.hit")
            return row[1]
        metrics.observe("cache.miss")
        with metrics.time("rule.restore"), budget(self.limits.get("restore", 20)):
            return self.factory.restore_match(request["recovery"])

    def remember(self, key, record, match, metrics):
        if key and not match._adapter.done and self.limits.get("entries", 0):
            self.cache[key] = (
                record_digest(record),
                match,
                len(canonical(record)),
                time.monotonic(),
            )
            self.cache.move_to_end(key)
            self.prune(metrics)

    def candidate(self, key, record, match):
        if not key or not self.limits.get("entries", 0):
            return None
        ticket = uuid.uuid4().hex
        self.pending = (
            ticket,
            key,
            record_digest(record),
            record,
            match,
            time.monotonic(),
        )
        return ticket

    def confirm(self, request, metrics):
        value = self.pending
        if not value or value[:3] != (
            request["ticket"],
            request["match_id"],
            request["digest"],
        ):
            return {"confirmed": False}
        self.pending = None
        self.remember(value[1], value[3], value[4], metrics)
        return {"confirmed": True}

    def discard(self, request):
        self.cache.pop(request["match_id"], None)
        if self.pending and self.pending[1] == request["match_id"]:
            self.pending = None
        return {"discarded": True}
