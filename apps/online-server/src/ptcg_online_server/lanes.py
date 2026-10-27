"""Stable worker affinity shared by the API scheduler and process adapter."""

from contextvars import ContextVar
from hashlib import sha256

current_lane = ContextVar("rule_lane", default=0)


def lane_for(group, count):
    return int.from_bytes(sha256(group.encode()).digest()[:8], "big") % count
