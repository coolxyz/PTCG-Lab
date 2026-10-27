"""Small resource budgets; no payload queues or identity-bearing metrics."""

import json
import time

from .errors import ServiceError


def wire_size(value):
    return len(
        json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    )


class SendBudget:
    def __init__(self, rate, burst, *, clock=time.monotonic):
        self.rate, self.burst, self.clock = rate, burst, clock
        self.subscription = None
        self.authorize = None
        self.tokens = float(burst)
        self.updated = clock()

    def delay(self, size, maximum):
        now = self.clock()
        self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
        self.updated = now
        wait = max(0, (size - self.tokens) / self.rate)
        if size > self.burst or wait > maximum:
            raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
        # There is one sequential sender per connection. Reserve before sleeping.
        self.tokens -= size
        return wait


class StreamPage(list):
    def __init__(self, items=(), *, more=False):
        super().__init__(items)
        self.more = more


def page_keys(rows, limit, budget):
    """Select only a bounded prefix, retaining durable cursor order.

    PostgreSQL reports snapshot text bytes without returning private snapshots.
    Reserve envelope and per-frame JSON overhead; exact wire size is checked too.
    """
    used, selected = 1024, []
    for row in rows[:limit]:
        size = row["bytes"] + 256
        if used + size > budget:
            if not selected:
                raise ServiceError("CAPACITY_EXCEEDED", 503, retryable=True)
            break
        selected.append(row["cursor"])
        used += size
    return selected, len(rows) > len(selected)
