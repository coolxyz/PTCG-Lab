import pytest
from scripts.migration.p6c_report import summarize


def workload():
    return dict(
        workload="fair-command-queue-v1",
        groups=3,
        uniqueJobs=7,
        mergedRetries=3,
        executions=7,
        order=["A0", "B0", "C0", "A1", "B1", "C1", "A2"],
        drained=True,
        capacityMeasured=False,
    )


def test_scheduler_report_exports_only_synthetic_evidence():
    value = workload()
    value["token"] = "private-canary"
    assert "private-canary" not in str(summarize(value))


@pytest.mark.parametrize(
    "kind", ["missing", "order", "executions", "drained", "capacity", "boolean"]
)
def test_scheduler_report_rejects_incomplete_and_inconsistent_evidence(kind):
    value = workload()
    if kind == "missing":
        del value["mergedRetries"]
    elif kind == "order":
        value["order"][1:3] = ["A1", "A2"]
    else:
        key, item = {
            "executions": ("executions", 10),
            "drained": ("drained", False),
            "capacity": ("capacityMeasured", True),
            "boolean": ("drained", 1),
        }[kind]
        value[key] = item
    with pytest.raises(ValueError):
        summarize(value)
