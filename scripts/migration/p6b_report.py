"""P6-B metadata-query evidence, chained to the immutable P6-A checkpoint."""

import argparse
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.p4d_report import evidence_valid, source_digest  # noqa: E402


def summarize(value):
    if (
        not isinstance(value, dict)
        or value.get("workload") != "idle-stream-metadata-v1"
        or value.get("capacityMeasured") is not False
    ):
        raise ValueError("Unexpected stream workload")
    fields = ("subscriptions", "sweeps", "centralizedSql", "pollingSql", "frameReads")
    try:
        samples = [{k: sample[k] for k in fields} for sample in value["samples"]]
    except (KeyError, TypeError):
        raise ValueError("Incomplete stream measurements") from None
    expected = [
        dict(
            subscriptions=n,
            sweeps=10,
            centralizedSql=30,
            pollingSql=90 * n,
            frameReads=0,
        )
        for n in (1, 8, 32)
    ]
    if samples != expected or any(
        type(v) is not int for s in samples for v in s.values()
    ):
        raise ValueError("Incomplete or inconsistent stream measurements")
    return samples


def valid(root, *, historical=False):
    try:
        root = Path(root)
        report = json.loads((root / "releases/p6b-streams.json").read_text())
        baseline = json.loads((root / "releases/p6a-baseline.json").read_text())
        service = json.loads((root / "releases/p4d-acceptance.json").read_text())
        summarize(report["workload"])
        if historical:
            successor = json.loads((root / "releases/p6c-scheduler.json").read_text())
            source_valid = (
                successor["baselineCommit"] == "3ca9ca0"
                and report["serverSourceSha256"]
                == successor["baselineServerSourceSha256"]
            )
        else:
            source_valid = (
                report["serverSourceSha256"]
                == service["serverSourceSha256"]
                == source_digest(root)
                and report["tests"] == service["tests"]
                and evidence_valid(root)
            )
        return (
            report["stage"] == "P6-B"
            and report["status"] == "passed"
            and report["baselineCommit"] == "5f1c16f"
            and report["baselineServerSourceSha256"] == baseline["serverSourceSha256"]
            and source_valid
            and report["tests"] >= 131
            and report["capacityMeasured"] is False
            and report["productionRoutingChanged"] is False
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "var/p4d") or not evidence_valid(ROOT):
        raise ValueError("Current complete service acceptance required")
    cases = ET.parse(directory / "results.xml").findall(".//testcase")
    required = {
        "test_stream_commit_hints_only_after_commit_and_never_after_rollback",
        "test_stream_idle_sql_is_constant_and_private_frames_are_not_loaded",
        "test_stream_missing_commit_hint_catches_up_and_rejects_foreign_cursor",
        "test_stream_failed_scan_fails_closed_and_slow_peer_does_not_block",
        "test_stream_rollback_switch_keeps_snapshot_and_revoke",
        "test_stream_switch_rejects_ambiguous_values",
        "test_stream_ping_flood_keeps_previous_read_budget",
        *(
            f"test_stream_batch_revalidates_expiry_revocation_and_membership[{kind}]"
            for kind in ("access", "session", "revoke", "missing-member")
        ),
    }
    if not required <= {case.attrib["name"] for case in cases} or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Complete passing stream acceptance required")
    service = json.loads((ROOT / "releases/p4d-acceptance.json").read_text())
    if service["tests"] != len(cases) or len(cases) < 131:
        raise ValueError("Service and stream acceptance runs differ")
    baseline = json.loads((ROOT / "releases/p6a-baseline.json").read_text())
    measured = json.loads((directory / "p6b-stream-sql.json").read_text())
    result = {
        "schema": 1,
        "stage": "P6-B",
        "status": "passed",
        "tests": len(cases),
        "serverSourceSha256": source_digest(),
        "baselineCommit": "5f1c16f",
        "baselineServerSourceSha256": baseline["serverSourceSha256"],
        "workload": {
            "workload": "idle-stream-metadata-v1",
            "capacityMeasured": False,
            "samples": summarize(measured),
        },
        "scope": "Synthetic idle subscriptions on one match; service/SQL only. Full real-rule HTTPS/WSS regression is functional evidence, not capacity timing.",
        "capacityMeasured": False,
        "productionRoutingChanged": False,
    }
    (directory / "P6B-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P6-B", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
