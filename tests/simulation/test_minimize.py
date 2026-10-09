from packages.simulation.minimize import minimize
import pytest


def test_removes_irrelevant_steps_and_keeps_interaction():
    items = list(range(20))
    result = minimize(items, lambda sequence: 3 in sequence and 14 in sequence)
    assert result["items"] == [3, 14] and result["complete"]
    assert items == list(range(20))


def test_budget_exhaustion_keeps_reproducer_and_reports_incomplete():
    result = minimize(list(range(20)), lambda sequence: 3 in sequence, max_calls=1)
    assert result["items"] == list(range(20)) and not result["complete"]
    with pytest.raises(ValueError, match="NOT_REPRODUCIBLE"):
        minimize([1], lambda _: False)
