"""Aggregate the fixed P6-A workload only after complete service acceptance."""

import argparse
import json
from math import ceil, isfinite
from pathlib import Path
import platform
import statistics
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from scripts.migration.p4d_report import source_digest, evidence_valid  # noqa: E402


def summarize(value):
    if (
        value.get("workload") != "synthetic-service-sql-v1"
        or value.get("repeat") != 3
        or value.get("matchesPerSample") != 5
        or value.get("commandsPerMatch") != 4
        or value.get("idlePollsPerMatch") != 20
        or value.get("duplicateRetriesPerMatch") != 4
        or value.get("realRuleCapacityMeasured") is not False
        or len(value.get("samples", [])) != 3
    ):
        raise ValueError("Unexpected workload")
    rows = []
    times = []
    for sample in value["samples"]:
        metrics = sample["diagnostics"]["metrics"]
        if any(
            metrics.get(name, {}).get("count", 0)
            for name in ("db.error", "db.rejected")
        ):
            raise ValueError("Baseline has database failures")
        elapsed = sample["seconds"]
        commands = sample["commandSeconds"]
        if len(commands) != 20 or any(
            type(v) not in (int, float) or not isfinite(v) or v <= 0
            for v in [elapsed, *commands]
        ):
            raise ValueError("Invalid timing samples")
        times.extend(commands)
        rows.append(
            {
                "seconds": elapsed,
                "connections": metrics["db.connect"]["count"],
                "transactions": metrics["db.transaction"]["count"],
                "explicitSql": metrics["db.sql"]["count"],
                "idleSqlPer20Polls": sample["idleSqlPer20Polls"],
            }
        )
    times.sort()
    return {
        "samples": rows,
        "medianSeconds": statistics.median(r["seconds"] for r in rows),
        "commandWallSeconds": {
            "count": len(times),
            "p50": statistics.median(times),
            "p95": times[ceil(len(times) * 0.95) - 1],
            "max": max(times),
        },
    }


def generate(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / "var/p4d") or not evidence_valid(ROOT):
        raise ValueError("Current complete service acceptance required")
    cases = ET.parse(directory / "results.xml").findall(".//testcase")
    required = {
        "test_fixed_sql_baseline_with_same_durable_outcomes[new-connection]",
        "test_fixed_sql_baseline_with_same_durable_outcomes[pool]",
        "test_real_worker_metrics_separate_restore_and_compute_without_private_data",
    }
    if not required <= {case.attrib["name"] for case in cases} or any(
        case.find(kind) is not None
        for case in cases
        for kind in ("failure", "error", "skipped")
    ):
        raise ValueError("Complete passing baseline required")
    modes = {
        key: summarize(json.loads((directory / filename).read_text()))
        for key, filename in (
            ("newConnection", "p6a-new-connection.json"),
            ("pool", "p6a-pool.json"),
        )
    }
    for before, after in zip(
        modes["newConnection"]["samples"], modes["pool"]["samples"]
    ):
        if (
            before["connections"] != before["transactions"]
            or after["connections"] != 1
            or any(
                before[key] != after[key]
                for key in ("transactions", "explicitSql", "idleSqlPer20Polls")
            )
        ):
            raise ValueError("A/B workloads differ")
    rules = json.loads((directory / "p6a-rule-timings.json").read_text())
    if (
        rules.get("workload") != "approved-rule-cold-restore-v1"
        or rules.get("commands") != 5
        or rules.get("capacityMeasured") is not False
    ):
        raise ValueError("Missing rule timing evidence")
    # Export only known numeric summaries from the private worker run.
    rule_metrics = {
        name: {
            key: rules["metrics"]["metrics"][name][key]
            for key in ("count", "seconds", "maxSeconds")
        }
        for name in (
            "rule.load",
            "rule.restore",
            "rule.compute",
            "worker.command",
            "worker.project",
        )
    }
    if (
        rule_metrics["rule.restore"]["count"] != 15
        or rule_metrics["rule.compute"]["count"] != 6
    ):
        raise ValueError("Incomplete cold restore workload")
    result = {
        "schema": 1,
        "stage": "P6-A",
        "status": "passed",
        "tests": len(cases),
        "serverSourceSha256": source_digest(),
        "baselineCommit": "b792e37",
        "environment": {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "database": "isolated PostgreSQL 17 container, 1 CPU / 512 MiB",
            "apiQuota": "host process; not capped as a complete deployment",
        },
        "databaseBudget": {"connections": 4, "waiters": 16, "waitSeconds": 1},
        "workload": {
            "kind": "synthetic service/SQL; no HTTP/WSS timing",
            "samplesPerMode": 3,
            "matchesPerSample": 5,
            "commandsPerMatch": 4,
            "idlePollsPerMatch": 20,
            "duplicateRetriesPerMatch": 4,
        },
        "modes": modes,
        "coldRuleMetrics": rule_metrics,
        "capacityMeasured": False,
        "productionRoutingChanged": False,
    }
    (directory / "P6A-REPORT.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "P6-A", "status": "passed", "tests": len(cases)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    generate(parser.parse_args().directory)
