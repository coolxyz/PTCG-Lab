"""Bounded delta debugging for reproducible, offline synthetic failures."""


def minimize(items, reproduces, max_calls=100):
    if max_calls < 1:
        raise ValueError("INVALID_MINIMIZATION_BUDGET")
    current = list(items)
    calls = 1
    if not reproduces(current):
        raise ValueError("FAILURE_NOT_REPRODUCIBLE")
    granularity = 2
    while current and calls < max_calls:
        width = max(1, (len(current) + granularity - 1) // granularity)
        reduced = False
        for start in range(0, len(current), width):
            if calls >= max_calls:
                return {"items": current, "calls": calls, "complete": False}
            candidate = current[:start] + current[start + width :]
            calls += 1
            if reproduces(candidate):
                current = candidate
                granularity = max(2, granularity - 1)
                reduced = True
                break
        if not reduced:
            if width == 1:
                return {"items": current, "calls": calls, "complete": True}
            granularity = min(len(current), granularity * 2)
    return {"items": current, "calls": calls, "complete": not current}
