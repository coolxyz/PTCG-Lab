import pytest
from scripts.migration.p6b_report import summarize


def workload():
    return {
        "workload": "idle-stream-metadata-v1",
        "capacityMeasured": False,
        "samples": [
            dict(
                subscriptions=n,
                sweeps=10,
                centralizedSql=30,
                pollingSql=90 * n,
                frameReads=0,
            )
            for n in (1, 8, 32)
        ],
    }


def test_stream_report_only_exports_measured_numeric_fields():
    value = workload()
    value["samples"][0]["token"] = "private-canary"
    assert "private-canary" not in str(summarize(value))


@pytest.mark.parametrize(
    "kind", ["missing", "scope", "workload", "sql", "frames", "boolean"]
)
def test_stream_report_rejects_missing_or_inconsistent_evidence(kind):
    value = workload()
    if kind == "missing":
        value["samples"].pop()
    elif kind == "scope":
        value["capacityMeasured"] = True
    elif kind == "workload":
        value["workload"] = "other"
    else:
        key, new = {
            "sql": ("centralizedSql", 0),
            "frames": ("frameReads", 1),
            "boolean": ("subscriptions", True),
        }[kind]
        value["samples"][0][key] = new
    with pytest.raises(ValueError):
        summarize(value)
