import pytest
from scripts.migration.p6e_report import summarize


def workload():
    return dict(
        workload="weighted-budget-v1",
        hotWeight=2,
        coldWeight=1,
        maintenanceWeight=1,
        order=["block", "H1", "C1", "M1", "H2", "H3", "C2"],
        drained=True,
        capacityMeasured=False,
    )


def test_budget_report_exports_only_allowlisted_evidence():
    value = workload()
    value["recovery"] = "private-canary"
    assert "private-canary" not in str(summarize(value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workload", "capacity"),
        ("hotWeight", 0),
        ("coldWeight", True),
        ("maintenanceWeight", None),
        ("order", ["H1", "C1"]),
        ("drained", False),
        ("drained", 1),
        ("capacityMeasured", True),
    ],
)
def test_budget_report_rejects_incomplete_or_inconsistent_evidence(field, value):
    measured = workload()
    measured[field] = value
    with pytest.raises(ValueError):
        summarize(measured)
