import pytest
from scripts.migration.p6d_report import summarize


def workload():
    return dict(
        workload="persistent-interleaved-v1",
        workers=2,
        matches=3,
        commandsPerMatch=5,
        comparedViews=30,
        hotLoads=2,
        hotRestores=3,
        coldRestores=30,
        sameSeedEquivalent=True,
        uncommittedCandidateDiscarded=True,
        capacityMeasured=False,
    )


def test_worker_report_exports_only_aggregate_evidence():
    value = workload()
    value["recovery"] = "private-canary"
    assert "private-canary" not in str(summarize(value))


@pytest.mark.parametrize(
    "field,value",
    [
        ("workload", "capacity"),
        ("hotRestores", 0),
        ("coldRestores", 29),
        ("comparedViews", 15),
        ("workers", True),
        ("sameSeedEquivalent", 1),
        ("uncommittedCandidateDiscarded", False),
        ("capacityMeasured", True),
        ("matches", None),
    ],
)
def test_worker_report_rejects_incomplete_or_inconsistent_evidence(field, value):
    measured = workload()
    measured[field] = value
    with pytest.raises(ValueError):
        summarize(measured)
