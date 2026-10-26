from copy import deepcopy
import pytest
from scripts.migration.p6a_report import summarize


def workload():
    return {
        "workload": "synthetic-service-sql-v1",
        "repeat": 3,
        "matchesPerSample": 5,
        "commandsPerMatch": 4,
        "idlePollsPerMatch": 20,
        "duplicateRetriesPerMatch": 4,
        "realRuleCapacityMeasured": False,
        "samples": [
            {
                "seconds": 1,
                "commandSeconds": [0.01] * 20,
                "idleSqlPer20Polls": 180,
                "diagnostics": {
                    "metrics": {
                        "db.connect": {"count": 1},
                        "db.transaction": {"count": 300},
                        "db.sql": {"count": 3040},
                    }
                },
            }
            for _ in range(3)
        ],
    }


def test_report_exports_aggregates_without_extra_private_input():
    value = workload()
    value["private"] = "canary"
    value["samples"][0]["diagnostics"]["private"] = "canary"
    result = summarize(value)
    assert "canary" not in str(result)
    assert result["commandWallSeconds"]["count"] == 60


@pytest.mark.parametrize("kind", ["sample", "failure", "capacity", "nan", "shape"])
def test_report_rejects_incomplete_or_incompatible_workloads(kind):
    value = deepcopy(workload())
    if kind == "sample":
        value["samples"].pop()
    elif kind == "failure":
        value["samples"][0]["diagnostics"]["metrics"]["db.rejected"] = {"count": 1}
    elif kind == "capacity":
        value["realRuleCapacityMeasured"] = True
    elif kind == "nan":
        value["samples"][0]["seconds"] = float("nan")
    else:
        value["samples"][0]["commandSeconds"].pop()
    with pytest.raises(ValueError):
        summarize(value)
