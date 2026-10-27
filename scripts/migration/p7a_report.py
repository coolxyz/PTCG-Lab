"""P7-A isolation/pilot evidence. Deliberately cannot certify P7 capacity."""

import argparse
import json
from math import isfinite
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if __package__ in (None, ""):
    sys.path.insert(0, str(ROOT))
from benchmarks.online.identity import runtime_digest, tools_digest  # noqa: E402
from benchmarks.online.measure import histogram_quantile  # noqa: E402
from scripts.migration.p4d_report import source_digest  # noqa: E402


def number(value):
    return type(value) in (int, float) and isfinite(value) and value >= 0


def pilot(path, workers):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "var/p7"):
        raise ValueError("PRIVATE_P7_DIRECTORY_REQUIRED")
    manifest = json.loads((path / "lab.json").read_text())
    files = list((path / "results").glob("load-*.json"))
    if len(files) != 1:
        raise ValueError("ONE_COMPLETE_RUN_REQUIRED")
    run = json.loads(files[0].read_text())
    guard = json.loads((path / "guard-report.json").read_text())
    rows = [
        json.loads(line)
        for line in (path / "resource-samples.jsonl").read_text().splitlines()
    ]
    measures = [json.loads(p.read_text()) for p in (path / "measure").glob("*.json")]
    assert (path / "database.dump").read_bytes()[:5] == b"PGDMP"
    assert manifest["workers"] == workers and manifest[
        "runtimeSourceSha256"
    ] == runtime_digest(ROOT)
    assert (
        manifest["baseImage"]
        == "sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43"
    )
    assert (
        run["status"] == "passed"
        and run["label"] == "pilot"
        and run["formalSample"] is False
        and run["capacityValidated"] is False
    )
    assert (
        run["matches"] == 10
        and run["players"] == 20
        and run["warmupSeconds"] >= 10
        and run["steadySeconds"] >= 60
    )
    assert len(run["perGameCommands"]) == 10 and all(
        type(n) is int and n > 0 for n in run["perGameCommands"]
    )
    assert (
        sum(run["perGameCommands"]) >= 20
        and run["failures"] == []
        and run["privateReplayVerified"] is True
    )
    assert all(not run["phases"][p]["firstAttemptErrors"] for p in ("warmup", "steady"))
    assert (
        guard["status"] == "passed"
        and guard["protectedServicesUnchanged"] is True
        and guard["resourceSamples"] >= 5
    )
    assert len(rows) >= 5 and all(
        r["cpuMax"] == "200000 100000"
        and r["memoryMax"] == 2 * 1024**3
        and r["swapBytes"] == 0
        and r["memoryEvents"]["oom_kill"] == 0
        for r in rows
    )
    assert len(measures) == 1
    measure = measures[0]
    assert sum(measure["phases"][p]["count"] for p in ("warmup", "steady")) == sum(
        run["perGameCommands"]
    )
    assert all(
        not (set(measure["responses"][p]) - {"200"}) for p in ("warmup", "steady")
    )
    steady = [r for r in rows if r["phase"] == "steady"]
    assert len(steady) >= 3
    elapsed = steady[-1]["time"] - steady[0]["time"]
    cpu = (steady[-1]["cpu"]["usage_usec"] - steady[0]["cpu"]["usage_usec"]) / 1e6
    assert elapsed > 0
    stats = run["phases"]["steady"]
    counts = measure["metrics"]["metrics"]
    # Only allowlisted numeric aggregates leave the private run directory.
    configuration = {
        k: manifest["settings"].get(k, v)
        for k, v in {
            "WORKER_COUNT": 1,
            "WORKER_CACHE_ENTRIES": 8,
            "WORKER_RSS_MB": 256,
            "WORKER_COLD_LIMIT": 1,
            "MAX_INFLIGHT": 32,
            "MAX_STREAMS": 32,
            "CONNECTION_RESERVE": 8,
            "COMMAND_QUEUE_TOTAL": 32,
            "COMMAND_QUEUE_SECONDS": 2,
            "DB_POOL_SIZE": 4,
            "CREATE_PER_MINUTE": 10,
            "JOIN_PER_MINUTE": 20,
            "REQUESTS_PER_MINUTE": 240,
            "GLOBAL_PER_MINUTE": 1200,
            "REPLAY_PER_MINUTE": 120,
            "REPLAY_GLOBAL_PER_MINUTE": 360,
        }.items()
    }
    result = dict(
        workers=workers,
        configuration=configuration,
        imageId=manifest["imageId"],
        matches=10,
        players=20,
        warmupSeconds=run["warmupSeconds"],
        steadySeconds=run["steadySeconds"],
        acceptedCommands=sum(run["perGameCommands"]),
        steadyAccepted=stats["accepted"],
        firstAttemptErrors=0,
        privateReplayVerified=True,
        protectedServicesUnchanged=True,
        targetVcpus=2,
        targetMemoryBytes=2 * 1024**3,
        swapBytes=0,
        oomKills=0,
        groupPeakMemoryBytes=max(r["memoryPeakBytes"] for r in rows),
        steadyAverageCpuPercentOfTwoVcpus=round(100 * cpu / elapsed / 2, 3),
        asgiResponseStartP95UpperSeconds=histogram_quantile(
            [measure["phases"]["steady"]], 0.95
        ),
        asgiResponseStartP99UpperSeconds=histogram_quantile(
            [measure["phases"]["steady"]], 0.99
        ),
        clientHttpP95Seconds=stats["httpRoundTripSeconds"]["p95"],
        bothVisibleP95Seconds=stats["bothPlayersVisibleSeconds"]["p95"],
        bothVisibleP99Seconds=stats["bothPlayersVisibleSeconds"]["p99"],
        cacheHits=counts.get("cache.hit", {}).get("count", 0),
        cacheMisses=counts.get("cache.miss", {}).get("count", 0),
        dbSql=counts.get("db.sql", {}).get("count", 0),
        websocketJsonBytes=measure["websocketJsonBytes"],
        formalSample=False,
        capacityValidated=False,
    )
    return result


def fault(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / "var/p7"):
        raise ValueError("PRIVATE_P7_DIRECTORY_REQUIRED")
    assert (path / "database.dump").read_bytes()[:5] == b"PGDMP"
    assert json.loads((path / "lab.json").read_text())[
        "runtimeSourceSha256"
    ] == runtime_digest(ROOT)
    value = json.loads((path / "fault-report.json").read_text())
    assert (
        value["status"] == "passed"
        and value["capacityValidated"] is False
        and value["preciseInflightCommitBoundary"] is False
    )
    assert len(value["steps"]) == 5 and all(
        s["status"] == "passed" for s in value["steps"]
    )
    proof = value["sql"]
    assert proof == dict(matches=1, seq=6, receipts=6, frames=14)
    return dict(
        workerDeath="passed",
        apiRestart="passed",
        databasePause="passed",
        exactRequestRecovery="passed",
        sql=proof,
        preciseInflightCommitBoundary=False,
    )


def valid(root):
    try:
        root = Path(root)
        value = json.loads((root / "releases/p7a-isolation.json").read_text())
        baseline = json.loads((root / "releases/p6e-budgets.json").read_text())
        if not (
            value["stage"] == "P7-A"
            and value["status"] == "passed"
            and value["capacityValidated"] is False
            and value["formalMatrixCompleted"] is False
            and value["soakCompleted"] is False
            and value["baselineCommit"] == "41fe0f1"
            and value["productionRoutingChanged"] is False
            and value["serverSourceSha256"]
            == baseline["serverSourceSha256"]
            == source_digest(root)
            and value["runtimeSourceSha256"] == runtime_digest(root)
            and value["benchmarkToolsSha256"] == tools_digest(root)
        ):
            return False
        if len(value["pilots"]) != 2 or [p["workers"] for p in value["pilots"]] != [
            1,
            2,
        ]:
            return False
        for p in value["pilots"]:
            if not (
                p["matches"] == 10
                and p["players"] == 20
                and p["warmupSeconds"] >= 10
                and p["steadySeconds"] >= 60
                and p["acceptedCommands"] >= 20
                and p["firstAttemptErrors"] == 0
                and p["privateReplayVerified"] is True
                and p["protectedServicesUnchanged"] is True
                and p["targetVcpus"] == 2
                and p["targetMemoryBytes"] == 2 * 1024**3
                and p["swapBytes"] == p["oomKills"] == 0
                and p["formalSample"] is False
                and p["capacityValidated"] is False
            ):
                return False
            if any(
                not number(p[k])
                for k in (
                    "groupPeakMemoryBytes",
                    "steadyAverageCpuPercentOfTwoVcpus",
                    "clientHttpP95Seconds",
                    "bothVisibleP95Seconds",
                    "bothVisibleP99Seconds",
                    "asgiResponseStartP95UpperSeconds",
                    "asgiResponseStartP99UpperSeconds",
                )
            ):
                return False
        return (
            value["faults"]["sql"] == dict(matches=1, seq=6, receipts=6, frames=14)
            and all(
                value["faults"][k] == "passed"
                for k in (
                    "workerDeath",
                    "apiRestart",
                    "databasePause",
                    "exactRequestRecovery",
                )
            )
            and value["faults"]["preciseInflightCommitBoundary"] is False
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def generate(single, dual, failures):
    result = dict(
        schema=1,
        stage="P7-A",
        status="passed",
        baselineCommit="41fe0f1",
        serverSourceSha256=source_digest(ROOT),
        runtimeSourceSha256=runtime_digest(ROOT),
        benchmarkToolsSha256=tools_digest(ROOT),
        baseApiImage="sha256:29ad6123c04a878e0d60c000436187adc38fcabadaa9f99ae6916e9eac462d43",
        pilots=[pilot(single, 1), pilot(dual, 2)],
        faults=fault(failures),
        capacityValidated=False,
        formalMatrixCompleted=False,
        soakCompleted=False,
        productionRoutingChanged=False,
        scope="Isolated cgroup enforcement, ten real-rule games and 20 HTTPS/WSS peers per pilot, first-attempt/replay checks and idle worker/API/DB failures. No P7 capacity claim; no 3x20-minute matrix, complex/long-history/fault-under-load suite or two-hour soak.",
    )
    target = ROOT / "releases/p7a-isolation.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    assert valid(ROOT)
    print(json.dumps(dict(stage="P7-A", status="passed", capacityValidated=False)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("single")
    parser.add_argument("dual")
    parser.add_argument("fault")
    args = parser.parse_args()
    generate(args.single, args.dual, args.fault)
