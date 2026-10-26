"""Bounded, process-local aggregates. Never retain SQL, IDs, tokens or payloads."""

from contextlib import contextmanager
from math import isfinite
from threading import Lock
from time import perf_counter

NAMES = frozenset(
    {
        "db.connect",
        "db.acquire",
        "db.transaction",
        "db.sql",
        "db.error",
        "db.rejected",
        "worker.rejected",
        *(
            "worker." + op
            for op in ("probe", "validate", "initialize", "project", "command")
        ),
        "rule.load",
        "rule.restore",
        "rule.compute",
    }
)
BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 20.0)


class Metrics:
    def __init__(self):
        self._lock = Lock()
        self._values = {}

    def observe(self, name, seconds=0.0):
        if name not in NAMES:
            raise ValueError("UNKNOWN_METRIC")
        if not isfinite(seconds) or seconds < 0:
            return
        with self._lock:
            row = self._values.setdefault(
                name,
                {
                    "count": 0,
                    "seconds": 0.0,
                    "maxSeconds": 0.0,
                    "buckets": [0] * (len(BUCKETS) + 1),
                },
            )
            row["count"] += 1
            row["seconds"] += seconds
            row["maxSeconds"] = max(row["maxSeconds"], seconds)
            bucket = next(
                (i for i, limit in enumerate(BUCKETS) if seconds <= limit), len(BUCKETS)
            )
            row["buckets"][bucket] += 1

    @contextmanager
    def time(self, name):
        start = perf_counter()
        try:
            yield
        finally:
            self.observe(name, perf_counter() - start)

    def snapshot(self):
        with self._lock:
            return {
                "bucketUpperSeconds": [*BUCKETS, None],
                "metrics": {
                    key: {**row, "buckets": list(row["buckets"])}
                    for key, row in sorted(self._values.items())
                },
            }
