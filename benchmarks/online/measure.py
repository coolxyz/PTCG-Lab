"""Aggregate-only measurement utilities; no identities or private snapshots."""

from math import ceil

LIMITS = (
    0.001,
    0.005,
    0.01,
    0.02,
    0.05,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.75,
    1,
    1.5,
    2,
    5,
    10,
    20,
    60,
)


class Histogram:
    def __init__(self):
        self.count, self.seconds, self.maximum = 0, 0.0, 0.0
        self.buckets = [0] * (len(LIMITS) + 1)

    def observe(self, seconds):
        self.count += 1
        self.seconds += seconds
        self.maximum = max(self.maximum, seconds)
        self.buckets[
            next((i for i, limit in enumerate(LIMITS) if seconds <= limit), len(LIMITS))
        ] += 1

    def snapshot(self):
        return dict(
            count=self.count,
            seconds=self.seconds,
            maximum=self.maximum,
            buckets=self.buckets[:],
        )


def quantile(values, probability):
    return (
        sorted(values)[max(0, ceil(len(values) * probability) - 1)] if values else None
    )


def latency(values):
    return dict(
        count=len(values),
        p50=quantile(values, 0.5),
        p95=quantile(values, 0.95),
        p99=quantile(values, 0.99),
        maximum=max(values, default=None),
    )


def histogram_quantile(rows, probability):
    buckets = [sum(r["buckets"][i] for r in rows) for i in range(len(LIMITS) + 1)]
    count = sum(buckets)
    if not count:
        return None
    target, seen = ceil(count * probability), 0
    for i, n in enumerate(buckets):
        seen += n
        if seen >= target:
            return LIMITS[i] if i < len(LIMITS) else None
