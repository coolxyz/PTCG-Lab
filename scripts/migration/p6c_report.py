"""Publish bounded scheduling evidence only with current full API acceptance."""

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
    expected = dict(
        workload="fair-command-queue-v1",
        groups=3,
        uniqueJobs=7,
        mergedRetries=3,
        executions=7,
        order=["A0", "B0", "C0", "A1", "B1", "C1", "A2"],
        drained=True,
        capacityMeasured=False,
    )
    if not isinstance(value, dict) or any(
        value.get(k) != v for k, v in expected.items()
    ):
        raise ValueError("Incomplete scheduler workload")
    if (
        value["drained"] is not True
        or value["capacityMeasured"] is not False
        or any(
            type(value[k]) is not int
            for k in ("groups", "uniqueJobs", "mergedRetries", "executions")
        )
    ):
        raise ValueError("Invalid scheduler measurements")
    return {k: value[k] for k in expected}


def valid(root, *, historical=False):
    try:
        root = Path(root)
        report = json.loads((root / "releases/p6c-scheduler.json").read_text())
        baseline = json.loads((root / "releases/p6b-streams.json").read_text())
        service = json.loads((root / "releases/p4d-acceptance.json").read_text())
        summarize(report["workload"])
        if historical:
            successor = json.loads((root / "releases/p6d-workers.json").read_text())
            source_valid = (
                successor["baselineCommit"] == "691fc0a"
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
            report["stage"] == "P6-C"
            and report["status"] == "passed"
            and report["baselineCommit"] == "3ca9ca0"
            and report["baselineServerSourceSha256"] == baseline["serverSourceSha256"]
            and source_valid
            and report["tests"] >= 157
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
        "test_scheduler_fifo_round_robin_and_duplicate_merge",
        "test_scheduler_expiry_cancellation_shutdown_and_failure_release_budgets",
        "test_scheduler_rollback_switch_bypasses_only_queue",
        "test_scheduler_environment_has_explicit_rollback",
        "test_scheduled_http_duplicates_conflicts_and_durable_retry_bypass_full_queue",
        "test_merged_caller_is_reauthorized_after_other_callers_commit",
        "test_scheduler_disabled_api_preserves_command_contract",
        *(
            f"test_scheduler_admission_limits_are_independent[{budget}]"
            for budget in ("total", "per_match", "per_user", "waiters", "duplicates")
        ),
        *(
            f"test_queued_match_rechecks_authority_and_version_before_rule_work[{change}]"
            for change in ("revoke", "takeover", "resign")
        ),
    }
    if not required <= {case.attrib["name"] for case in cases} or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Complete passing scheduler acceptance required")
    service = json.loads((ROOT / "releases/p4d-acceptance.json").read_text())
    if service["tests"] != len(cases) or len(cases) < 157:
        raise ValueError("Service and scheduler acceptance runs differ")
    baseline = json.loads((ROOT / "releases/p6b-streams.json").read_text())
    measured = json.loads((directory / "p6c-scheduler.json").read_text())
    result = dict(
        schema=1,
        stage="P6-C",
        status="passed",
        tests=len(cases),
        serverSourceSha256=source_digest(),
        baselineCommit="3ca9ca0",
        baselineServerSourceSha256=baseline["serverSourceSha256"],
        workload=summarize(measured),
        capacityMeasured=False,
        productionRoutingChanged=False,
        scope="Deterministic single-lane barrier workload and real-SQL concurrent HTTP checks; full real-rule regression, no throughput or latency SLA.",
    )
    (directory / "P6C-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P6-C", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
