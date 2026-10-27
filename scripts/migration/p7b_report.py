"""Publish bounded P7-B progress while preserving immutable P7-A evidence."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from benchmarks.online.identity import runtime_digest, tools_digest  # noqa: E402
from benchmarks.online.acceptance import summarize  # noqa: E402
from scripts.migration.p4d_report import source_digest  # noqa: E402
from scripts.migration.p7a_report import number  # noqa: E402

PRIOR = "releases/p7b-before-driver-repair.json"
PRIOR_SHA256 = "dc5d33f1cd2bfd4b7ed54e48eb285fea0de29072bf0ea405e02975b740e05e62"


def previous(root):
    raw = (Path(root) / PRIOR).read_bytes()
    if sha256(raw).hexdigest() != PRIOR_SHA256:
        raise ValueError("IMMUTABLE_PRE_REPAIR_REPORT_REQUIRED")
    return json.loads(raw)


def repair_evidence():
    parent = ROOT / "var/p7/clienterror-investigation"
    old, new = (
        json.loads((parent / p / "probe-results.json").read_text())
        for p in ("clone", "clone-repaired-final")
    )
    assert old["serial"] == new["serial"] == {"passed": 10}
    assert old["concurrent"] == [
        dict(
            concurrency=n,
            results=(
                {"CAPACITY_EXCEEDED:503:True": 8, "passed": 2}
                if n == 10
                else {"passed": 10}
            ),
        )
        for n in (10, 10, 2)
    ]
    assert new["concurrent"] == [
        dict(concurrency=n, results={"passed": 10}) for n in (10, 10, 2)
    ]
    manifests = [
        json.loads((parent / p / "lab.json").read_text())
        for p in ("clone", "clone-repaired-final")
    ]
    prior = previous(ROOT)
    assert manifests[0]["runtimeSourceSha256"] == prior["runtimeSourceSha256"]
    assert manifests[1]["runtimeSourceSha256"] == runtime_digest(ROOT)
    assert (
        manifests[0]["baseImage"]
        == manifests[1]["baseImage"]
        == "sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43"
    )
    for p in ("clone", "clone-repaired-final"):
        guard = json.loads((parent / p / "guard-report.json").read_text())
        assert (
            guard["status"] == "passed" and guard["protectedServicesUnchanged"] is True
        )
        assert (parent / p / "database.dump").read_bytes()[:5] == b"PGDMP"
    cases = ET.parse(parent / "driver-regression.xml").findall(".//testcase")
    required = {
        "test_ten_concurrent_replays_respect_two_server_slots_and_verify_all_pages",
        "test_replay_rejection_is_not_retried_and_releases_its_slot",
        "test_command_path_does_not_wait_for_replay_slots_or_hide_a_retry",
        "test_replay_cursor_stall_fails_instead_of_hanging_the_matrix",
        "test_failure_metadata_never_contains_exception_messages_or_paths",
        "test_only_initialization_poll_403_is_counted_as_expected_pending",
        "test_initialization_poll_does_not_hide_a_permanent_failure",
        "test_load_keeps_structured_failures_even_outside_command_submission[False]",
        "test_load_keeps_structured_failures_even_outside_command_submission[True]",
    }
    assert required <= {c.attrib["name"] for c in cases}
    assert len(cases) >= 7 and not any(
        c.find(t) is not None for c in cases for t in ("failure", "error", "skipped")
    )
    return dict(
        status="passed",
        beforeRuntimeSourceSha256=manifests[0]["runtimeSourceSha256"],
        afterRuntimeSourceSha256=manifests[1]["runtimeSourceSha256"],
        serverImageUnchanged=manifests[0]["baseImage"],
        originalParallelRequests=10,
        originalDenied=8,
        originalPassed=2,
        repairedParallelRequests=10,
        repairedDenied=0,
        repairedPassed=10,
        repeats=2,
        replayReadConcurrency=2,
        serverLimitsUnchanged=True,
        noAutomaticRetries=True,
        protectedServicesUnchanged=True,
        regressionTests=len(cases),
        evidenceSha256=sha256(
            (parent / "clone/probe-results.json").read_bytes()
            + (parent / "clone-repaired-final/probe-results.json").read_bytes()
            + (parent / "driver-regression.xml").read_bytes()
        ).hexdigest(),
    )


def sample_valid(sample, runtime, *, formal):
    targets = sample["targets"]
    p95, p99 = sample["asgiP95UpperSeconds"], sample["asgiP99UpperSeconds"]
    if not (
        set(targets) == {"latencyP95", "latencyP99", "averageCpu", "driverHeadroom"}
        and all(v is None or number(v) for v in (p95, p99))
        and number(sample["steadyAverageCpuPercentOfTwoVcpus"])
        and number(sample["driverAverageCpuPercentOfOneVcpu"])
        and targets["latencyP95"] is (p95 is not None and p95 <= 0.5)
        and targets["latencyP99"] is (p99 is not None and p99 <= 1.5)
        and targets["averageCpu"] is (sample["steadyAverageCpuPercentOfTwoVcpus"] <= 70)
        and targets["driverHeadroom"]
        is (sample["driverAverageCpuPercentOfOneVcpu"] <= 70)
        and sample["status"]
        == ("passed" if all(targets.values()) else "threshold_failed")
    ):
        return False
    return (
        sample["formalSample"] is formal
        and sample["capacityValidated"] is False
        and sample["runtimeSourceSha256"] == runtime
        and sample["matches"] in (10, 25, 50, 100)
        and sample["players"] == 2 * sample["matches"]
        and sample["firstAttemptErrors"] == 0
        and sample["privateReplayVerified"] is True
        and sample["durableHistoryVerified"] is True
        and sample["protectedServicesUnchanged"] is True
        and sample["targetVcpus"] == 2
        and sample["targetMemoryBytes"] == 2 * 1024**3
        and sample["swapBytes"] == sample["oomKills"] == 0
        and sample["cacheEntriesTotal"] == 16
        and sample["replayReadConcurrency"] == 2
        and all(
            row["errors"] == 0
            and row["attempts"] == row["successes"] + row["pending"]
            and (row["pending"] == 0 or name == "initialization_wait")
            for phase in sample["requestOperations"].values()
            for name, row in phase.items()
        )
        and sample["status"] in ("passed", "threshold_failed")
        and sample["sql"]["seq"]
        == sample["sql"]["receipts"]
        == sample["acceptedCommands"]
        and sample["sql"]["frames"]
        == 2 * (sample["sql"]["seq"] + sample["sql"]["matches"])
        and (
            (not formal)
            or (
                type(sample["repeat"]) is int
                and sample["repeat"] >= 1
                and sample["warmupSeconds"] >= 300
                and sample["steadySeconds"] >= 1200
                and sample["measuredSteadySeconds"] >= 1200
                and sample["elapsedSeconds"] >= 1500
            )
        )
    )


def valid(root):
    root = Path(root)
    try:
        report = json.loads((root / "releases/p7b-matrix.json").read_text())
        baseline_path = root / "releases/p7a-isolation.json"
        baseline = json.loads(baseline_path.read_text())
        prior = previous(root)
        repair = report["driverRepair"]
        return (
            report["stage"] == "P7-B"
            and report["status"] == "in_progress"
            and report["baselineCommit"] == "0738d0c"
            and report["baselineReportSha256"]
            == sha256(baseline_path.read_bytes()).hexdigest()
            and report["baselineRuntimeSourceSha256"] == baseline["runtimeSourceSha256"]
            and report["baselineBenchmarkToolsSha256"]
            == baseline["benchmarkToolsSha256"]
            and report["serverSourceSha256"]
            == baseline["serverSourceSha256"]
            == source_digest(root)
            and report["runtimeSourceSha256"] == runtime_digest(root)
            and report["benchmarkToolsSha256"] == tools_digest(root)
            and report["capacityValidated"] is False
            and report["formalMatrixCompleted"] is False
            and report["soakCompleted"] is False
            and report["productionRoutingChanged"] is False
            and report["previousReportSha256"] == PRIOR_SHA256
            and report["historicalDiagnostics"] == prior["diagnostics"]
            and report["previousAttempt"] == prior["lastAttempt"]
            and prior["samples"] == []
            and prior["capacityValidated"] is False
            and prior["serverSourceSha256"] == report["serverSourceSha256"]
            and repair["status"] == "passed"
            and repair["beforeRuntimeSourceSha256"] == prior["runtimeSourceSha256"]
            and repair["afterRuntimeSourceSha256"] == report["runtimeSourceSha256"]
            and repair["serverImageUnchanged"]
            == "sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43"
            and (
                repair["originalParallelRequests"],
                repair["originalDenied"],
                repair["originalPassed"],
                repair["repeats"],
            )
            == (10, 8, 2, 2)
            and (
                repair["repairedParallelRequests"],
                repair["repairedDenied"],
                repair["repairedPassed"],
                repair["replayReadConcurrency"],
            )
            == (10, 0, 10, 2)
            and repair["serverLimitsUnchanged"] is True
            and repair["noAutomaticRetries"] is True
            and repair["protectedServicesUnchanged"] is True
            and repair["regressionTests"] >= 7
            and all(
                sample_valid(s, report["runtimeSourceSha256"], formal=True)
                for s in report["samples"]
            )
            and all(
                sample_valid(s, report["runtimeSourceSha256"], formal=False)
                for s in report["diagnostics"]
            )
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(samples, diagnostics):
    baseline_path = ROOT / "releases/p7a-isolation.json"
    baseline = json.loads(baseline_path.read_text())
    result = dict(
        schema=1,
        stage="P7-B",
        status="in_progress",
        baselineCommit="0738d0c",
        baselineReportSha256=sha256(baseline_path.read_bytes()).hexdigest(),
        baselineRuntimeSourceSha256=baseline["runtimeSourceSha256"],
        baselineBenchmarkToolsSha256=baseline["benchmarkToolsSha256"],
        serverSourceSha256=source_digest(ROOT),
        runtimeSourceSha256=runtime_digest(ROOT),
        benchmarkToolsSha256=tools_digest(ROOT),
        diagnostics=[summarize(p, ROOT) for p in diagnostics],
        samples=[summarize(p, ROOT) for p in samples],
        capacityValidated=False,
        formalMatrixCompleted=False,
        soakCompleted=False,
        productionRoutingChanged=False,
        previousReportSha256=PRIOR_SHA256,
        historicalDiagnostics=previous(ROOT)["diagnostics"],
        previousAttempt=previous(ROOT)["lastAttempt"],
        driverRepair=repair_evidence(),
    )
    if any(not s["formalSample"] for s in result["samples"]):
        raise ValueError("PILOT_CANNOT_BE_A_MATRIX_SAMPLE")
    (ROOT / "releases/p7b-matrix.json").write_text(json.dumps(result, indent=2) + "\n")
    print(
        json.dumps(
            dict(stage="P7-B", status="in_progress", samples=len(result["samples"]))
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", nargs="*", default=[])
    parser.add_argument("--diagnostics", nargs="*", default=[])
    args = parser.parse_args()
    generate(args.samples, args.diagnostics)
